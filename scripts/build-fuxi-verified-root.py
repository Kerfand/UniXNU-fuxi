#!/usr/bin/env python3
"""Archive the verified-root kit and this board's signed policy bundle.

Produces build/verified-root-fuxi/libVerifiedRootProbe.a holding

    verified_root_key             the 395-byte owner anchor
    verified_root_policies[]      one {manifest, manifestBytes} record per window
    verified_root_policy_count    2
    vr_open / vr_verify_block     kits/VerifiedRoot/src/verified_root.c
    the XNU crypto adapter        kits/VerifiedRoot/adapters/xnu_crypto.c

NativeUfsBlock::configureVerification() selects the policy whose signed image
size equals the window size, so the OS and SystemOS windows each need their own
manifest. Unlike the upstream builder this archive carries no QEMU probe object
and no sample blocks: nothing in the kernel path reads them.
"""
import argparse
import hashlib
import os
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json'
BLOCK = 4096


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def compile_source(source, destination, record, extra):
    arguments, command, index = record['arguments'], [], 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value in ('-MF', '-MJ'):
            index += 1
        elif value not in ('-MD', '-MP'):
            command.append(value)
        index += 1
    command += list(extra)
    result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    (destination.parent / (destination.stem + '.log')).write_text(result.stdout + result.stderr)
    require(result.returncode == 0, f'{source.name} failed:\n{(result.stdout + result.stderr)[-4000:]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--policy-dir', type=Path,
                        default=Path(os.environ.get('FUXI_ROOT_POLICY', '')),
                        help='directory holding verified-root-os/ and verified-root-systemos/ '
                             '(default: $FUXI_ROOT_POLICY)')
    parser.add_argument('--os-manifest', type=Path, default=None)
    parser.add_argument('--systemos-manifest', type=Path, default=None)
    parser.add_argument('--trusted-key', type=Path, default=None)
    parser.add_argument('--kit', type=Path, default=ROOT / 'kits/VerifiedRoot')
    parser.add_argument('--out', type=Path, default=ROOT / 'build/verified-root-fuxi')
    args = parser.parse_args()
    if args.policy_dir is not None and str(args.policy_dir):
        policy = args.policy_dir if args.policy_dir.is_absolute() else ROOT / args.policy_dir
        args.os_manifest = args.os_manifest or policy / 'verified-root-os/manifest.bin'
        args.systemos_manifest = args.systemos_manifest or policy / 'verified-root-systemos/manifest.bin'
        args.trusted_key = args.trusted_key or policy / 'verified-root-os/trusted-key.bin'
    require(args.os_manifest is not None, 'no policy directory: set FUXI_ROOT_POLICY or pass --policy-dir')

    for path in (args.os_manifest, args.systemos_manifest, args.trusted_key):
        require(path.is_file(), f'missing input: {path}')
    key = args.trusted_key.read_bytes()
    require(len(key) == 395 and key[:8] == b'VROOTKEY', 'trusted-key.bin must be the 395-byte anchor')

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    os_size = args.os_manifest.stat().st_size
    sys_size = args.systemos_manifest.stat().st_size
    for name, path, size in (('os', args.os_manifest, os_size), ('systemos', args.systemos_manifest, sys_size)):
        header = path.read_bytes()[:160]
        require(header[:8] == b'VROOT01\0', f'{name} manifest header magic differs')
        image_bytes = struct.unpack_from('<Q', header, 24)[0]
        blocks = struct.unpack_from('<Q', header, 32)[0]
        require(size == 160 + 384 + blocks * 32, f'{name} manifest size does not match its header')
        print(f'{name}: manifest {size:,} B, image {image_bytes:,} B, {blocks:,} blocks')

    assembly = out / 'inputs.S'
    assembly.write_text(
        '// Generated owner policy bundle. Signed by the board owner key, not by Apple.\n'
        '.section __DATA_CONST,__const\n'
        '.balign 8\n'
        '.globl _verified_root_manifest, _verified_root_systemos_manifest\n'
        '_verified_root_manifest:\n'
        f'.incbin "{args.os_manifest.resolve()}"\n'
        '_verified_root_systemos_manifest:\n'
        f'.incbin "{args.systemos_manifest.resolve()}"\n'
        '.balign 8\n'
        '.globl _verified_root_key\n'
        '_verified_root_key:\n'
        f'.incbin "{args.trusted_key.resolve()}"\n'
        '.balign 8\n'
        '.globl _verified_root_policies, _verified_root_policy_count\n'
        '_verified_root_policies:\n'
        f'.quad _verified_root_manifest, {os_size}\n'
        f'.quad _verified_root_systemos_manifest, {sys_size}\n'
        '_verified_root_policy_count:\n'
        '.quad 2\n')
    print(f'assembly: {assembly}')

    record = json.loads(RECORD.read_text().strip().rstrip(','))
    include = ['-I', str(args.kit / 'include'), '-Werror']
    objects = []
    for source in (args.kit / 'src/verified_root.c', args.kit / 'adapters/xnu_crypto.c'):
        destination = out / (source.stem + '.o')
        compile_source(source, destination, record, include)
        objects.append(destination)

    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
    inputs_o = out / 'inputs.o'
    subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk,
                    '-c', str(assembly), '-o', str(inputs_o)], check=True)
    objects.append(inputs_o)

    for obj in objects:
        data = obj.read_bytes()
        require(struct.unpack_from('<4I', data) == (0xfeedfacf, 0x100000c, 0, 1),
                f'{obj.name} is not an ordinary ARM64 object')
        cursor, deployment = 32, None
        for _ in range(struct.unpack_from('<I', data, 16)[0]):
            command, length = struct.unpack_from('<II', data, cursor)
            if command == 0x32:
                deployment = struct.unpack_from('<II', data, cursor + 8)
            cursor += length
        require(deployment == (2, 0x110000), f'{obj.name} does not target iOS 17')

    archive = out / 'libVerifiedRootProbe.a'
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', str(archive), *map(str, objects)], check=True)

    report = dict(os_manifest_sha256=hashlib.sha256(args.os_manifest.read_bytes()).hexdigest(),
                  os_manifest_bytes=os_size,
                  systemos_manifest_sha256=hashlib.sha256(args.systemos_manifest.read_bytes()).hexdigest(),
                  systemos_manifest_bytes=sys_size,
                  trusted_key_sha256=hashlib.sha256(key).hexdigest(),
                  policy_count=2, qemu_probe_linked=False, sample_blocks_linked=False,
                  archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  archive_bytes=archive.stat().st_size, hardware_tested=False)
    (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'archive: {archive} ({archive.stat().st_size:,} B)')


if __name__ == '__main__':
    main()
