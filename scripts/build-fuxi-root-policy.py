#!/usr/bin/env python3
"""Build and verify one verified-root policy for a fuxi root window.

The manifest is signed with the owner key and then every 4 KiB block is checked
through the kit's own C verifier (vr_host_open / vr_host_block). Unlike the
upstream tools/verify_image.py this driver reads the window from a byte range of
a regular image or a block device instead of requiring a standalone file, because
the windows only exist as ranges inside the flashed userdata image and inside the
attached IPSW disk images.

Inputs are the owner key (never in the repository; Actions secret FUXI_OWNER_KEY)
and the window bytes. Outputs follow kits/VerifiedRoot conventions:
    manifest.bin  trusted-key.bin  header.bin  signature.bin  verification.json
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

BLOCK = 4096
ROOT = Path(__file__).resolve().parents[1]


class Window:
    """A byte range of a file or block device, positioned like a standalone image."""

    def __init__(self, path, offset, size):
        self.fh = open(path, 'rb', buffering=0)
        self.base = offset
        self.size = size
        self.fh.seek(offset)

    def read(self, n):
        if n <= 0:
            return b''
        data = self.fh.read(n)
        return data

    def seek0(self):
        self.fh.seek(self.base)

    def close(self):
        self.fh.close()


class View(ctypes.Structure):
    _fields_ = [('hashes', ctypes.c_void_p), ('image_bytes', ctypes.c_uint64),
                ('block_count', ctypes.c_uint64), ('generation', ctypes.c_uint64),
                ('volume_uuid', ctypes.c_uint8 * 16), ('image_sha256', ctypes.c_uint8 * 32)]


def build_host_library(kit, workdir, openssl_prefix=None):
    library = workdir / 'libverified-root-host.so'
    command = ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
               '-I', str(kit / 'include'), str(kit / 'src/verified_root.c'),
               str(kit / 'tests/openssl_provider.c')]
    if openssl_prefix:
        command += ['-I', str(Path(openssl_prefix) / 'include'), '-L', str(Path(openssl_prefix) / 'lib')]
    command += ['-lcrypto', '-o', str(library)]
    subprocess.run(command, check=True)
    return library


def verify(library, manifest, anchor, volume_uuid, generation, window):
    native = ctypes.CDLL(str(library))
    native.vr_host_open.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                    ctypes.c_uint64, ctypes.POINTER(View)]
    native.vr_host_open.restype = ctypes.c_int
    native.vr_host_block.argtypes = [ctypes.POINTER(View), ctypes.c_uint64,
                                     ctypes.c_void_p, ctypes.c_size_t]
    native.vr_host_block.restype = ctypes.c_int

    encoded = ctypes.create_string_buffer(manifest.read_bytes())
    key_bytes = anchor.read_bytes()
    if len(key_bytes) != 395:
        raise SystemExit('trusted-key.bin must be the 395-byte anchor')
    key = ctypes.create_string_buffer(key_bytes)
    view = View()
    status = native.vr_host_open(encoded, len(encoded) - 1, key, generation, ctypes.byref(view))
    if status != 0:
        raise SystemExit(f'vr_host_open rejected the manifest (status {status})')
    if bytes(view.volume_uuid) != volume_uuid.bytes:
        raise SystemExit('signed volume identity differs from the requested one')
    if view.image_bytes != window.size:
        raise SystemExit('signed image size differs from the window size')

    window.seek0()
    digest = hashlib.sha256()
    for index in range(view.block_count):
        block = window.read(min(BLOCK, view.image_bytes - index * BLOCK))
        if len(block) == 0:
            raise SystemExit('window ended early at block ' + str(index))
        if native.vr_host_block(ctypes.byref(view), index, block, len(block)) != 0:
            raise SystemExit('block authentication failed at index ' + str(index))
        digest.update(block)
    if digest.digest() != bytes(view.image_sha256):
        raise SystemExit('image digest differs from the signed metadata')
    return view, digest.hexdigest(), hashlib.sha256(key_bytes).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, required=True,
                        help='regular image or block device that holds the window')
    parser.add_argument('--offset', type=lambda v: int(v, 0), default=0)
    parser.add_argument('--image-bytes', type=lambda v: int(v, 0), required=True)
    parser.add_argument('--volume-uuid', type=uuid.UUID, required=True)
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--generation', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kit', type=Path, default=ROOT / 'kits/VerifiedRoot')
    parser.add_argument('--openssl-prefix', default=None,
                        help='prefix of a Homebrew openssl@3 on macOS')
    parser.add_argument('--keep-unverified', action='store_true')
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location('verified_manifest',
                                                  args.kit / 'tools/manifest.py')
    manifest_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest_tool)

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    window = Window(args.source, args.offset, args.image_bytes)
    try:
        record = manifest_tool.make(window, args.image_bytes, args.private_key, args.public_key,
                                    args.volume_uuid, args.generation, out)
        manifest = out / 'manifest.bin'
        anchor = out / 'trusted-key.bin'
        print(f'signed manifest: {manifest.stat().st_size:,} B, '
              f'{record["block_count"]:,} blocks, image {record["image_sha256"][:32]}')

        workdir = Path(tempfile.mkdtemp(prefix='fuxi-root-policy-'))
        try:
            library = build_host_library(args.kit, workdir, args.openssl_prefix)
            view, digest, anchor_sha = verify(library, manifest, anchor, args.volume_uuid,
                                              args.generation, window)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        report = dict(image_bytes=view.image_bytes, blocks_verified=view.block_count,
                      image_sha256=digest, volume_uuid=str(args.volume_uuid).upper(),
                      generation=view.generation, trusted_key_sha256=anchor_sha,
                      real_c_signature_and_block_verification=True,
                      original_image_modified=False, host_only=True,
                      boot_root_authenticated=False, qemu_tested=False, hardware_tested=False,
                      source=str(args.source), source_offset=args.offset,
                      verifier='kits/VerifiedRoot tests/openssl_provider.c vr_host_open/vr_host_block',
                      verifier_io='byte range of the source, not a standalone file')
        (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'real C signature + {view.block_count:,} blocks verified; '
              f'image_sha256={digest[:32]} anchor={anchor_sha[:32]}')
    finally:
        window.close()


if __name__ == '__main__':
    main()
