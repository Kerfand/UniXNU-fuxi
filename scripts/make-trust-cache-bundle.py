#!/usr/bin/env python3
"""Build the trust cache bundle DarwinBoot accepts from the IPSW's .trustcache files.

The IPSW ships each cache as an IM4P (DER: SEQUENCE { "IM4P", "trcs"/"rtsc", OCTET STRING }).
DarwinBoot does not parse IM4P: ValidTrustCaches() (DarwinBoot.c:277) expects

    u32 count
    u32 starts[count]          byte offsets, 4-byte aligned, >= 4 + count*4
    cache                      version==2 (u32) | uuid[16] | entries (u32 at +20) |
                               entries * 24 bytes { hash[20], type 1..4, pad[3] }

with each cache sized exactly 24 + entries*24 and its hashes strictly ascending.
This script unwraps the IM4P octet strings, packs that bundle, and re-checks the
result against the same rules so a rejected boot is a host-side failure instead.
"""
import hashlib
import struct
import sys
from pathlib import Path


def der_octet_strings(blob):
    """Yield the contents of every OCTET STRING in a small DER blob."""
    def walk(data, depth=0):
        i = 0
        while i + 2 <= len(data):
            tag = data[i]
            i += 1
            length = data[i]
            i += 1
            if length & 0x80:
                count = length & 0x7F
                length = int.from_bytes(data[i:i + count], 'big')
                i += count
            body = data[i:i + length]
            i += length
            if tag == 0x04:
                yield body
            elif tag in (0x30, 0x31):
                yield from walk(body, depth + 1)
    yield from walk(blob)


def cache_from_img4(path):
    blob = path.read_bytes()
    for body in der_octet_strings(blob):
        if len(body) >= 24 and struct.unpack_from('<I', body, 0)[0] == 2:
            entries = struct.unpack_from('<I', body, 20)[0]
            if len(body) == 24 + entries * 24:
                return body
    raise SystemExit(f'{path.name}: no version-2 trust cache payload found')


def validate(bundle):
    size = len(bundle)
    if size < 8:
        raise SystemExit('bundle too small')
    count = struct.unpack_from('<I', bundle, 0)[0]
    if not 1 <= count <= 128 or count > (size - 4) // 4:
        raise SystemExit(f'count {count} is out of range')
    for index in range(count):
        start = struct.unpack_from('<I', bundle, 4 + index * 4)[0]
        end = struct.unpack_from('<I', bundle, 8 + index * 4)[0] if index + 1 < count else size
        if start < 4 + count * 4 or start & 3 or end > size or end < start or end - start < 24:
            raise SystemExit(f'cache {index}: bad span {start}..{end}')
        if struct.unpack_from('<I', bundle, start)[0] != 2:
            raise SystemExit(f'cache {index}: version is not 2')
        entries = struct.unpack_from('<I', bundle, start + 20)[0]
        if entries > (end - start - 24) // 24 or end - start != 24 + entries * 24:
            raise SystemExit(f'cache {index}: {entries} entries do not fit {end - start} bytes')
        for entry in range(entries):
            at = start + 24 + entry * 24
            kind = bundle[at + 20]
            if not 1 <= kind <= 4:
                raise SystemExit(f'cache {index}: entry {entry} type {kind} is out of range')
            if entry and bundle[at - 24:at - 4] >= bundle[at:at + 20]:
                raise SystemExit(f'cache {index}: entry {entry} hashes are not ascending')
    return count


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    output = Path(sys.argv[1])
    sources = [Path(p) for p in sys.argv[2:]]

    caches = []
    for path in sources:
        body = cache_from_img4(path)
        entries = struct.unpack_from('<I', body, 20)[0]
        print(f'{path.name}: version 2, {entries:,} entries, {len(body):,} B payload')
        caches.append(body)

    header = struct.pack('<I', len(caches))
    offset = 4 + 4 * len(caches)
    starts = []
    for body in caches:
        starts.append(offset)
        offset += len(body)
    bundle = header + b''.join(struct.pack('<I', s) for s in starts) + b''.join(caches)

    count = validate(bundle)
    output.write_bytes(bundle)
    digest = hashlib.sha256(bundle).hexdigest()
    print(f'bundle: {len(bundle):,} B, {count} caches -> {output}')
    print(f'bundle sha256: {digest}')


if __name__ == '__main__':
    main()
