#!/usr/bin/env python3
"""Reject source storage classes mixed with the prelinked Apple storage family.

Inspect the native kernel's symbol table, not a strings count: imported kexts
have their own symbols and the class name legitimately occurs several times.
This is a build-time collision check, not a hardware boot qualification.
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


def inspect(path):
    data = path.read_bytes()
    if len(data) < 32 or struct.unpack_from('<I', data)[0] != 0xfeedfacf:
        raise ValueError('expected a 64-bit Mach-O kernel')
    table = None
    cursor = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, cursor)
        if size < 8 or cursor + size > len(data):
            raise ValueError('invalid Mach-O load command')
        if command == 2:  # LC_SYMTAB of the native kernel, not nested kexts
            table = struct.unpack_from('<4I', data, cursor + 8)
        cursor += size
    if table is None:
        raise ValueError('native symbol table missing; cannot verify class ownership')
    symbol_offset, count, string_offset, string_size = table
    if symbol_offset + count * 16 > len(data) or string_offset + string_size > len(data):
        raise ValueError('symbol table outside kernel')
    classes = ('IOBlockStorageDevice', 'IOBlockStorageDriver', 'IOMedia',
               'IOStorage', 'IOPartitionScheme', 'IOFilterScheme')
    definitions = []
    for index in range(count):
        name_index, kind, section, _, address = struct.unpack_from(
            '<IBBHQ', data, symbol_offset + index * 16)
        if kind & 0xe0 or kind & 0x0e != 0x0e or not section:
            continue  # debug entries and undefined imports are not definitions
        if name_index >= string_size:
            raise ValueError('invalid symbol string index')
        end = data.find(b'\0', string_offset + name_index, string_offset + string_size)
        if end == -1:
            raise ValueError('unterminated symbol name')
        name = data[string_offset + name_index:end].decode('ascii', 'replace')
        if any(re.match(r'^__(?:ZNK?|ZTVN?|ZTI|ZTS)' + f'{len(cls)}{cls}', name)
               or name == f'__GLOBAL__sub_I_{cls}.cpp' for cls in classes):
            definitions.append({'symbol': name, 'address': hex(address)})
    apple_family = b'com.apple.iokit.IOStorageFamily\0' in data
    return {'kernel_sha256': hashlib.sha256(data).hexdigest(),
            'prelinked_apple_storage_family': apple_family,
            'native_storage_definitions': definitions,
            'collision': apple_family and bool(definitions),
            'hardware_tested': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = inspect(args.kernel)
    output = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(output)
    print(output, end='')
    if report['collision']:
        raise SystemExit('IOStorageFamily collision: remove source storage archives and rebuild kernel AND payload')


if __name__ == '__main__':
    main()
