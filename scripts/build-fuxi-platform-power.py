#!/usr/bin/env python3
"""Build a guarded PMGR discovery instrument for the pinned iPhone10,3 cache.

This publishes no real power controls. Unsupported calls stop at a panic.
The input reference is an independently compiled, hash-pinned baseline kernel.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/sheng-platform-power'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
from native_prelink import macho, backed_offset, exports


def run(command, **kwargs):
    return subprocess.check_output([str(v) for v in command], timeout=90, **kwargs)


def require(value, message):
    if not value:
        raise SystemExit(message)


reference = Path(os.environ.get('FUXI_PMGR_REFERENCE_KERNEL', ''))
require(reference.is_file(), 'FUXI_PMGR_REFERENCE_KERNEL must name the pinned baseline kernel')
require(hashlib.sha256(reference.read_bytes()).hexdigest() ==
        'c9e0debc23acc035a8b406e58088a3dd1a5f7e7a5203421b13b09b6e2a1abeb9',
        'PMGR reference kernel differs from FIX058 baseline')
candidate = reference.read_bytes()
meta = macho(candidate)
native_names = {n: va for va, names in exports(candidate, meta).items() for n in names}


def ptr(va):
    return struct.unpack_from('<Q', candidate, backed_offset(meta['segments'], va, 8, len(candidate)))[0]


provider_vtable = 0xfffffff005c9faf8
require(ptr(provider_vtable + 312 * 8) == 0xfffffff004681e04 and
        ptr(provider_vtable + 313 * 8) == 0xfffffff004681e0c and
        ptr(provider_vtable + 325 * 8) == 0,
        'iOS16 ApplePMGR provider layout differs')
require(ptr(0xfffffff005cb56b8) == 0xfffffff004685734 and
        ptr(0xfffffff005cb56c0) == 0xfffffff004683dbc,
        'AMFI ApplePMGRNub imported methods differ')
header = '''// Generated exact storage/slot ABI; private fields stay opaque.
#pragma once
#include <IOKit/IOPMGR.h>
class ApplePMGR : public IOPMGR {
 OSDeclareAbstractStructors(ApplePMGR)
 unsigned char originalPrivate[0x36968-sizeof(IOPMGR)];
public:
'''
guards = ''
for i in range(177, 325):
    if i == 312:
        header += ' virtual uint32_t readAon();\n'
    elif i == 313:
        header += ' virtual void writeAon(uint64_t);\n'
    else:
        header += f' virtual void opaque{i}() = 0;\n'
        guards += f' void opaque{i}() override {{ unavailable({i}); }}\n'
header += '};\nstatic_assert(sizeof(ApplePMGR)==0x36968,"Original provider storage");\n'
(OUT / 'OriginalPMGRABI.h').write_text(header)
(OUT / 'GuardedPMGRSlots.inc').write_text(guards)
source = ROOT / 'kits/fuxi/ShengPlatformPower.cpp'
obj = OUT / 'ShengPlatformPower.o'
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
command, i = [], 0
arguments = record['arguments']
while i < len(arguments):
    arg = arguments[i]
    if arg == record['file']:
        command.append(source)
    elif arg == '-o':
        command += ['-o', obj]
        i += 1
    elif arg == '-Xclang' and arguments[i + 1].startswith('-W'):
        i += 1
    elif not arg.startswith('-W'):
        command.append(arg)
    i += 1
command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator',
            '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion',
            '-Wno-unnecessary-virtual-specifier', '-I', OUT, '-I', ROOT / '.cache/xnu-work/osfmk']
run(command, cwd=record['directory'])
raw = obj.read_bytes()
info = macho(raw)
require(info['kind'] == 1, 'Require actual ARM64 subtype-0 Mach-O object')
build_version = [struct.unpack_from('<2I', raw, at + 8) for cmd, at, _ in info['commands'] if cmd == 0x32]
require(build_version == [(2, 0x110000)], 'Require iOS17 build target')
sym_at = next(at for cmd, at, _ in info['commands'] if cmd == 2)
at, count, strings, _ = struct.unpack_from('<4I', raw, sym_at + 8)
symbols = []
for i in range(count):
    ix, kind, _, _, address = struct.unpack_from('<IBBHQ', raw, at + i * 16)
    symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), address, kind))
vtable = next(va for name, va, _ in symbols if name == '__ZTV18ShengPlatformPower')
relocations = {}
for segment in info['segments']:
    for section in segment['sections']:
        if section['va'] <= vtable < section['va'] + section['size']:
            at, count = struct.unpack_from('<2I', raw, section['command_offset'] + 56)
            for i in range(count):
                offset, bits = struct.unpack_from('<2I', raw, at + i * 8)
                if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                    relocations[section['va'] + offset] = symbols[bits & 0xffffff][0]
entries = [relocations.get(vtable + 16 + i * 8) for i in range(325)]
require(len(entries) == 325 and all(entries) and
        vtable + 16 + 325 * 8 not in relocations, 'Exact 325-entry provider table')


bindings = {'__ZN9ApplePMGRC2EPK11OSMetaClass': 0xfffffff00465703c,
            '__ZN9ApplePMGRD2Ev': 0xfffffff004657164,
            '__ZN9ApplePMGR10gMetaClassE': 0xfffffff006a0b410}
# These original ApplePMGR overrides are deliberately replaced by the native
# IORegistryEntry/IOService implementations in this diagnostic object. In
# particular, original start/stop and power callbacks may touch Apple MMIO.
# Pin both the original target and the native inherited symbol, rather than
# silently treating their different vtables as binary compatible.
native_inherited = {
    44: (0xfffffff00465aec0, '__ZN15IORegistryEntry13setPropertiesEP8OSObject'),
    81: (0xfffffff00465b7a4, '__ZN9IOService15configureReportEP19IOReportChannelListjPvS2_'),
    82: (0xfffffff00465b7ac, '__ZN9IOService12updateReportEP19IOReportChannelListjPvS2_'),
    86: (0xfffffff0046571e0, '__ZN9IOService5startEPS_'),
    87: (0xfffffff00465961c, '__ZN9IOService4stopEPS_'),
    117: (0xfffffff00465962c, '__ZN9IOService20callPlatformFunctionEPK8OSSymbolbPvS3_S3_S3_'),
    159: (0xfffffff004681974, '__ZN9IOService22powerStateWillChangeToEmmPS_'),
    160: (0xfffffff004681850, '__ZN9IOService21powerStateDidChangeToEmmPS_'),
}
for slot, name in enumerate(entries):
    if slot in (0, 1, 7) or 168 <= slot < 325 and slot not in (312, 313):
        require('18ShengPlatformPower' in name, f'Own guard/lifecycle required at slot {slot}')
    elif slot in (312, 313):
        require('9ApplePMGR' in name, 'Only actual original AON implementations inherited')
        bindings[name] = ptr(provider_vtable + slot * 8)
    elif slot in native_inherited:
        original, native = native_inherited[slot]
        require(ptr(provider_vtable + slot * 8) == original and name == native and
                native_names.get(name) == ptr(native_names['__ZTV6IOPMGR'] + 16 + slot * 8),
                f'Audited native inheritance changed at slot {slot}')
    else:
        require(native_names.get(name) == ptr(native_names['__ZTV6IOPMGR'] + 16 + slot * 8),
                f'Native IOPMGR/IOService entry differs at {slot}')
undefined = {n for n, _, k in symbols if k == 1 and 'ApplePMGR' in n}
require(undefined == set(bindings), 'All and only audited original base imports')
bindings.update({'_sheng_original_pmgr_nub_create': 0xfffffff0046832d4,
                 '_sheng_original_pmgr_nub_read': 0xfffffff004685734,
                 '_sheng_original_pmgr_nub_write': 0xfffffff004683dbc})
asm = OUT / 'original-bindings.S'
asm.write_text(''.join(f'.globl {name}\n.set {name}, {va:#x}\n' for name, va in bindings.items()))
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', asm, '-o', OUT / 'original-bindings.o'])
late = OUT / 'ShengPlatformPower.late.o'
run(['llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
     '--globalize-symbol=__GLOBAL__sub_I_ShengPlatformPower.cpp', obj, late])


def text_section(blob):
    section = next(s for seg in macho(blob)['segments'] for s in seg['sections'] if s['name'] == '__text')
    return blob[section['fileoff']:section['fileoff'] + section['size']]


require(text_section(raw) == text_section(late.read_bytes()), 'Late registration cannot change instructions')
archive = OUT / 'libShengPlatformPower.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, late, OUT / 'original-bindings.o'])
report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
          'bindings': {name: hex(va) for name, va in bindings.items()}, 'virtual_entries': 325,
          'mode': 'iPhone10,3 opt-in lifecycle/publication/refusal instrument; not a functional power backend',
          'native_inherited_deviations': sorted(native_inherited),
          'runtime_qualified': False, 'original_executable_modified': False}
(OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS: ordinary ARM64/iOS17 build; exact native and original bindings; unimplemented power slots stop')
