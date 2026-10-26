#!/usr/bin/env python3
"""Pack the signed per-window manifests into the bundle the kernel reads from /chosen.

The bundle is carried by the device tree rather than the kernel image. Two reasons:

  * 65 MB of block hashes in __DATA_CONST pushed every later segment up by 62 MB,
    which moved the zero padding that patch-xnu-entry-probe.py injects the panic
    screen into (it lives at fixed addresses), so the screen stopped working and
    the payload step refused to build.
  * docs/concepts/security.md already describes the payload as the supplier of the
    device tree properties that describe the device. The trust cache path
    (/chosen/memory-map/TrustCache) needs DarwinBoot to fill a memory-map slot and
    this board's firmware volume is prebuilt, so a property is the only input that
    needs no firmware change.

Keeping the manifests out of the image loses no security: the 395-byte anchor stays
in the kernel and authenticates whatever the bundle contains.

Layout (little endian):
    0x00  "VRBNDL01"
    0x08  u32 count
    0x0C  u32 reserved (0)
    0x10  count * u64 manifest size
    ...   the manifests, each padded to 8 bytes
"""
import argparse
import struct
from pathlib import Path

MAGIC = b"VRBNDL01"
WINDOWS = ("verified-root-os", "verified-root-systemos")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--policy-dir', type=Path, required=True,
                        help='directory holding verified-root-os/ and verified-root-systemos/')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    manifests = []
    for window in WINDOWS:
        path = args.policy_dir / window / 'manifest.bin'
        if not path.is_file():
            raise SystemExit(f'missing manifest: {path}')
        data = path.read_bytes()
        if data[:8] != b'VROOT01\0':
            raise SystemExit(f'{path} does not start with the VROOT01 envelope')
        image_bytes = struct.unpack_from('<Q', data, 24)[0]
        blocks = struct.unpack_from('<Q', data, 32)[0]
        if len(data) != 160 + 384 + blocks * 32:
            raise SystemExit(f'{path} size does not match its header')
        manifests.append(data)
        print(f'{window}: manifest {len(data):,} B, image {image_bytes:,} B, {blocks:,} blocks')

    header = MAGIC + struct.pack('<II', len(manifests), 0)
    header += b''.join(struct.pack('<Q', len(m)) for m in manifests)
    body = b''
    for manifest in manifests:
        body += manifest + b'\0' * ((-len(manifest)) % 8)
    bundle = header + body
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bundle)
    print(f'bundle: {len(bundle):,} B ({len(manifests)} manifests) -> {args.output}')


if __name__ == '__main__':
    main()
