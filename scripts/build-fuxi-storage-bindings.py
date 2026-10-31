#!/usr/bin/env python3
"""Bind fuxi block transports to the statically verified iOS 16 Apple storage ABI.

This checks compiled class layout and original code addresses. It does not
qualify live-device I/O, and never manufactures a live_native_4k audit record.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
import native_prelink as np
PIN = 'c9e0debc23acc035a8b406e58088a3dd1a5f7e7a5203421b13b09b6e2a1abeb9'


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def object_vtable(path, component):
    data = path.read_bytes()
    image = np.macho(data)
    require(image['kind'] == 1, 'Expected an ARM64 Mach-O object')
    at = next(at for cmd, at, _ in image['commands'] if cmd == 2)
    offset, count, strings, _ = struct.unpack_from('<4I', data, at + 8)
    symbols = []
    for i in range(count):
        ix, kind, _, _, value = struct.unpack_from('<IBBHQ', data, offset + i * 16)
        name = data[strings + ix:data.index(b'\0', strings + ix)].decode()
        symbols.append((name, value, kind))
    mangled = str(len(component)) + component
    address = next(value for name, value, _ in symbols if name == '__ZTV' + mangled)
    relocations = {}
    for segment in image['segments']:
        for section in segment['sections']:
            if section['va'] <= address < section['va'] + section['size']:
                ro, rc = struct.unpack_from('<II', data, section['command_offset'] + 56)
                for i in range(rc):
                    delta, bits = struct.unpack_from('<II', data, ro + i * 8)
                    if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                        relocations[section['va'] + delta] = symbols[bits & 0xffffff][0]
    methods = []
    while address + 16 + len(methods) * 8 in relocations:
        methods.append(relocations[address + 16 + len(methods) * 8])
    return data, symbols, methods, mangled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=['NativeVirtioBlock', 'NativeRamBlock'], required=True)
    args = parser.parse_args()
    ufs = ROOT / 'build/native-ufs'
    abi = json.loads((ufs / 'storage-static-abi.json').read_text())
    require(abi.get('schema') == 'fuxi-ios16-storage-static-abi-v1', 'Unknown static ABI schema')
    reference = Path(os.environ.get('FUXI_PMGR_REFERENCE_KERNEL', ''))
    require(reference.is_file() and hashlib.sha256(reference.read_bytes()).hexdigest() == PIN,
            'Require the pinned fuxi/iOS16 reference kernel')
    require(abi['reference_sha256'] == PIN and abi['method_slots'] == 190 and
            abi['inherited_slots_checked'] >= 160 and abi['abstract_complete_destructors_trap'],
            'Rebuild the fuxi UFS static class/code audit first')
    require(hashlib.sha256((ufs / 'IOBlockStorageDevice-reference.o').read_bytes()).hexdigest()
            == abi['annotation_sha256'], 'Storage class annotation changed')
    out = ROOT / 'build' / ('native-virtio-block' if args.component == 'NativeVirtioBlock' else 'native-ram-block')
    obj = out / (args.component + '.o')
    data, symbols, methods, mangled = object_vtable(obj, args.component)
    require(len(methods) == 190 and '___cxa_pure_virtual' not in methods,
            'Concrete block transport has missing/additional/pure virtual slots')
    expected = [row['name'] for row in abi['methods']]
    names = list(dict.fromkeys(methods + expected))
    readable = subprocess.check_output(['xcrun', 'c++filt'],
        input='\n'.join(name[1:] for name in names) + '\n', text=True).splitlines()
    readable = dict(zip(names, readable))
    overrides = 0
    for index, (actual, base) in enumerate(zip(methods, expected)):
        own = actual.startswith(('__ZN' + mangled, '__ZNK' + mangled))
        if base == '___cxa_pure_virtual':
            require(own, f'Missing concrete transport operation at slot {index}')
        elif actual != base:
            require(own and (index in (0, 1) or
                    readable[actual].split('::', 1)[1] == readable[base].split('::', 1)[1]),
                    f'Concrete block dispatch mismatch at slot {index}')
        overrides += own
    undefined = {name for name, _, kind in symbols if kind == 1 and 'IOBlockStorageDevice' in name}
    require(undefined and undefined <= abi['bindings'].keys(),
            'Unaudited original storage import: ' + str(undefined - abi['bindings'].keys()))
    require(not any(re.search(r'IOBlockStorageDeviceD[01]Ev$', name) for name in undefined),
            'Concrete transport must not invoke abstract complete/deleting destructor')
    bindings = {name: abi['bindings'][name] for name in sorted(undefined)}
    assembly = out / 'original-bindings.S'
    assembly.write_text(''.join(f'.globl {name}\n.set {name}, {address}\n'
                                for name, address in bindings.items()))
    binding_obj = out / 'original-bindings.o'
    subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0',
                    '-c', str(assembly), '-o', str(binding_obj)], check=True)
    archive = out / ('lib' + args.component + '.a')
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(obj), str(binding_obj)], check=True)
    (out / 'bindings.json').write_text(json.dumps({
        'qualification': 'static-compiled-class-and-pinned-code',
        'reference_sha256': PIN,
        'compiled_object_sha256': hashlib.sha256(data).hexdigest(),
        'virtual_methods_checked': 190, 'concrete_overrides': overrides,
        'unresolved_original_imports': 0, 'bindings': bindings,
        'original_code_modified': False, 'actual_io_qualified': False,
    }, indent=2) + '\n')
    print(f'PASS: {args.component}: 190 static slots, {overrides} overrides, {len(bindings)} original imports')
    print('Live device I/O and filesystem integration remain unqualified.')


if __name__ == '__main__':
    main()
