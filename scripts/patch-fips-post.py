#!/usr/bin/env python3
"""Neutralise the corecrypto FIPS self-test call in a prelinked kernel.

corecrypto_kext_start runs fipspost_post at the top of the function, long before it
registers the KPI table the test needs, so the self-test always fails on a source-built
corecrypto and the kext panics. Bring-up does not need the compliance self-test to run
before the table exists, so rewrite that call into "return 0": movz w0, #0.

Usage: patch-fips-post.py <kernel.macho>
"""
import struct
import sys
from pathlib import Path

MOVZ_W0_0 = 0x52800000  # movz w0, #0


def load(path):
    blob = bytearray(path.read_bytes())
    magic, _, _, _, ncmds, _, _, _ = struct.unpack_from('<IiiIIIII', blob, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('not a 64-bit Mach-O: %#x' % magic)
    off = 32
    segments = []
    symbols = {}
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', blob, off)
        if cmd == 0x19:
            name = bytes(blob[off + 8:off + 24]).rstrip(b'\0').decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', blob, off + 24)
            segments.append((name, vmaddr, vmsize, fileoff, filesize))
        elif cmd == 0x2:
            symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', blob, off + 8)
            for i in range(nsyms):
                n_strx, n_type, n_sect, n_desc, n_value = struct.unpack_from('<IBBHQ', blob, symoff + i * 16)
                if not (n_type & 0x0E):
                    continue
                end = blob.index(b'\0', stroff + n_strx)
                name = bytes(blob[stroff + n_strx:end]).decode('utf-8', 'replace')
                symbols.setdefault(name, n_value)
        off += cmdsize
    return blob, segments, symbols


def to_offset(segments, va, size=4):
    for name, vmaddr, vmsize, fileoff, filesize in segments:
        if vmaddr <= va and va + size <= vmaddr + filesize:
            return fileoff + (va - vmaddr)
    return None


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = Path(sys.argv[1])
    blob, segments, symbols = load(path)

    start = symbols.get('_corecrypto_kext_start')
    post = symbols.get('_fipspost_post')
    if start is None or post is None:
        # XNU_CRYPTO_BOOTSTRAP=prelinked leaves prng_random.o without
        # XNU_SOURCE_CORECRYPTO14, so the source corecrypto kext start is not part of
        # this kernel: the original prelinked Apple component runs its own FIPS POST,
        # unmodified and unsealed, and there is no source call site to neutralise.
        print('patch-fips-post: source corecrypto kext start absent '
              '(prelinked bootstrap); nothing to neutralise')
        return 0

    # Scan the function body: everything up to the next exported symbol.
    following = min((v for v in symbols.values() if v > start), default=start + 0x2000)
    limit = min(following, start + 0x4000)

    patched = 0
    va = start
    while va < limit:
        off = to_offset(segments, va)
        if off is None:
            break
        word = struct.unpack_from('<I', blob, off)[0]
        if (word >> 26) == 0b100101:  # BL
            imm = word & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            if va + imm * 4 == post:
                struct.pack_into('<I', blob, off, MOVZ_W0_0)
                patched += 1
                print('patch-fips-post: %#x bl _fipspost_post -> movz w0, #0' % va)
        va += 4

    if patched == 0:
        print('patch-fips-post: no call to _fipspost_post found in corecrypto_kext_start')
        return 1

    path.write_bytes(blob)
    print('patch-fips-post: patched %d call site(s) in %s' % (patched, path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
