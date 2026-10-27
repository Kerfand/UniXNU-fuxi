#!/usr/bin/env python3
"""Build the pinned NativeUFS-fuxi transport and bind the existing iOS16 storage ABI.

IOBlockStorageDevice is compiled only as an ABI annotation object.  It is never
linked, so its constructors cannot register a duplicate Apple storage class.
The imports below resolve to the retained, rebased Apple IOStorageFamily.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/native-ufs'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
import native_prelink as np

SOURCE_COMMIT = 'e9ae0f6188a1e948491f5b11765bcaba904e4b93'
REFERENCE_SHA = 'c9e0debc23acc035a8b406e58088a3dd1a5f7e7a5203421b13b09b6e2a1abeb9'
source = ROOT / 'drivers/NativeUfs'


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def run(command, **kwargs):
    return subprocess.check_output([str(v) for v in command], timeout=120, **kwargs)


require(run(['git', '-C', source, 'rev-parse', 'HEAD'], text=True).strip() == SOURCE_COMMIT,
        'NativeUFS-fuxi source pin differs')
reference = Path(os.environ.get('FUXI_PMGR_REFERENCE_KERNEL', ''))
require(reference.is_file(), 'Require the same pinned reference as the fuxi PMGR builder')
candidate = reference.read_bytes()
require(hashlib.sha256(candidate).hexdigest() == REFERENCE_SHA, 'Reference SHA-256 differs')
native = np.macho(candidate)
exports = np.exports(candidate, native)
by_name = {name: va for va, names in exports.items() for name in names}


def read(address, size):
    at = np.backed_offset(native['segments'], address, size, len(candidate))
    return candidate[at:at + size]


def pointer(address):
    return struct.unpack('<Q', read(address, 8))[0]


record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))


def compile_source(path, destination, extra=()):
    arguments, command, index = record['arguments'], [], 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command += ['-x', 'c++', path]
        elif value == '-o':
            command += ['-o', destination]
            index += 1
        elif value in ('-MF', '-MJ'):
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W') and value not in ('-MD', '-MP'):
            command.append(value)
        index += 1
    command += ['-I', ROOT / 'build/storage-abi/include', '-I', ROOT / '.cache/xnu-work/osfmk',
                '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-deprecated-declarations',
                '-Wno-c99-designator', '-Wno-reorder-init-list', '-Wno-unknown-warning-option', '-Wno-unnecessary-virtual-specifier',
                '-Wno-deprecated-enum-enum-conversion', '-mgeneral-regs-only', *extra]
    (OUT / (destination.stem + '-command.json')).write_text(json.dumps(list(map(str, command)), indent=2) + '\n')
    result = subprocess.run(list(map(str, command)), cwd=record['directory'], capture_output=True, text=True)
    (OUT / (destination.stem + '.log')).write_text(result.stdout + result.stderr)
    require(result.returncode == 0, (result.stdout + result.stderr)[-6500:])


def object_info(path):
    data = path.read_bytes()
    image = np.macho(data)
    require(image['kind'] == 1, 'Require an ordinary ARM64 Mach-O object')
    at = next(at for cmd, at, _ in image['commands'] if cmd == 2)
    offset, count, strings, _ = struct.unpack_from('<4I', data, at + 8)
    symbols = []
    for i in range(count):
        ix, kind, _, _, value = struct.unpack_from('<IBBHQ', data, offset + i * 16)
        symbols.append((data[strings + ix:data.index(b'\0', strings + ix)].decode(), value, kind))
    return data, image, symbols


def vtable(path, name):
    data, image, symbols = object_info(path)
    address = next(value for symbol, value, _ in symbols if symbol == name)
    relocations = {}
    for segment in image['segments']:
        for section in segment['sections']:
            if section['va'] <= address < section['va'] + section['size']:
                offset, count = struct.unpack_from('<II', data, section['command_offset'] + 56)
                for i in range(count):
                    delta, bits = struct.unpack_from('<II', data, offset + i * 8)
                    if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                        relocations[section['va'] + delta] = symbols[bits & 0xffffff][0]
    entries = []
    while address + 16 + len(entries) * 8 in relocations:
        entries.append(relocations[address + 16 + len(entries) * 8])
    return entries, symbols


# Compile the public class as an annotation only, then identify its existing
# Apple vtable by every inherited native slot. No storage implementation object
# from this compilation is added to libNativeUfs.a.
annotation = OUT / 'IOBlockStorageDevice-reference.o'
compile_source(ROOT / 'refer/IOStorageFamily/IOBlockStorageDevice.cpp', annotation)
methods, _ = vtable(annotation, '__ZTV20IOBlockStorageDevice')
require(len(methods) == 190 and methods.count('___cxa_pure_virtual') == 14,
        f'Unexpected public storage vtable shape: {len(methods)}')
known = [(index, by_name[name]) for index, name in enumerate(methods) if name in by_name]
require(len(known) >= 160, 'Insufficient inherited methods to identify Apple storage vtable')
anchor_index, anchor_value = known[10]
matches = []
for segment in native['segments']:
    if segment['initprot'] & 4 or not segment['filesize']:
        continue
    blob = candidate[segment['fileoff']:segment['fileoff'] + segment['filesize']]
    start = 0
    while True:
        found = blob.find(struct.pack('<Q', anchor_value), start)
        if found < 0:
            break
        start = found + 1
        at = found - 16 - anchor_index * 8
        if at < 0 or at % 8 or at + 16 + 190 * 8 > len(blob) or blob[at:at + 16] != bytes(16):
            continue
        values = struct.unpack_from('<190Q', blob, at + 16)
        if all(values[index] == value for index, value in known):
            matches.append((segment['va'] + at, values))
require(len(matches) == 1, f'Expected one matching iOS16 Apple storage vtable; found {len(matches)}')
table, pointers = matches[0]


def branch(address, word):
    delta = word & 0x3ffffff
    if delta & 0x2000000:
        delta -= 0x4000000
    return address + delta * 4


def adrp(address, word):
    immediate = ((word >> 5 & 0x7ffff) << 2) | (word >> 29 & 3)
    if immediate & 0x100000:
        immediate -= 0x200000
    return (address & ~4095) + immediate * 4096


def thunk_names(address):
    page, load, jump = struct.unpack('<3I', read(address, 12))
    require(page & 0x9f00001f == 0x90000010 and load & 0xffc003ff == 0xf9400210 and jump == 0xd61f0200,
            'Unexpected original native import thunk')
    slot = adrp(address, page) + (load >> 10 & 4095) * 8
    return exports.get(pointer(slot), [])


constructors = []
for segment in native['segments']:
    if not segment['initprot'] & 4:
        continue
    for offset in range(0, segment['filesize'] - 32, 4):
        words = struct.unpack_from('<8I', candidate, segment['fileoff'] + offset)
        if (words[:2] == (0xa9bf7bfd, 0x910003fd) and words[2] & 0xfc000000 == 0x94000000 and
                words[3] & 0x9f00001f == 0x90000008 and words[4] & 0xffc003ff == 0x91000108 and
                words[5:] == (0xf9000008, 0xa8c17bfd, 0xd65f03c0)):
            address = segment['va'] + offset
            if adrp(address + 12, words[3]) + (words[4] >> 10 & 4095) == table + 16:
                constructors.append((address, branch(address + 8, words[2])))
require(len(constructors) == 1 and '__ZN9IOServiceC2EPK11OSMetaClass' in thunk_names(constructors[0][1]),
        'Original protected storage constructor must forward to native IOService')
destructor = pointers[0] - 4
word = struct.unpack('<I', read(destructor, 4))[0]
require(word & 0xfc000000 == 0x14000000 and '__ZN9IOServiceD2Ev' in thunk_names(branch(destructor, word)),
        'Original base storage destructor must forward to native IOService')
require(all(read(pointers[index], 4) == struct.pack('<I', 0xd4200020) for index in (0, 1)),
        'Abstract deleting/complete destructor must remain unreachable')
page, add, ret = struct.unpack('<3I', read(pointers[7], 12))
require(page & 0x9f00001f == 0x90000000 and add & 0xffc003ff == 0x91000000 and ret == 0xd65f03c0,
        'Unexpected storage metaclass getter')
metaclass = adrp(pointers[7], page) + (add >> 10 & 4095)

# Stage the user-selected driver. Refuse a shared context bank or a wildcard
# mapping overlapping the exact UFS stream before its new takeover path writes
# SMMU registers. The upstream exact-match path otherwise returns too early.
staged = OUT / 'source'
staged.mkdir(exist_ok=True)
for path in source.glob('*.h'):
    shutil.copyfile(path, staged / path.name)
shutil.copyfile(source / 'NativeUfs.cpp', staged / 'NativeUfs.cpp')
# Fork addition: the signed policy bundle arrives in /chosen/verified-root-policy
# instead of the kernel image. docs/concepts/security.md states the payload supplies
# "device tree properties that describe the device"; the trust cache path
# (/chosen/memory-map/TrustCache) would need DarwinBoot changes and this board's
# firmware volume is prebuilt, so a property is the delivery that needs no firmware.
ufs_cpp = staged / 'NativeUfs.cpp'
text = ufs_cpp.read_text()
assert text.count('#include <pexpert/pexpert.h>\n') == 1, 'NativeUfs.cpp include anchor missing'
text = text.replace('#include <pexpert/pexpert.h>\n', '#include <pexpert/pexpert.h>\n#include <pexpert/device_tree.h>\n', 1)
assert text.count('bool NativeUfsBlock::configureVerification ()\n') == 1, 'configureVerification anchor missing'
text = text.replace('bool NativeUfsBlock::configureVerification ()\n', '#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n// Bundle layout: "VRBNDL01", u32 count, u32 reserved, count u64 sizes, then the\n// manifests each padded to 8 bytes (scripts/make-vr-bundle.py). Select the one\n// whose signed image size equals this window. The anchor linked into the kernel\n// authenticates every candidate, so reading them from the device tree is safe.\nstatic bool adtBundleManifest (uint64_t targetBytes, const uint8_t **out, uint64_t *outBytes)\n{\n  DTEntry chosen = nullptr;\n  if (SecureDTLookupEntry (nullptr, "/chosen", &chosen) != kSuccess) {\n    return false;\n  }\n  const void *value = nullptr;\n  UInt32 size = 0;\n  if (SecureDTGetProperty (chosen, "verified-root-policy", &value, &size) != kSuccess || !value) {\n    return false;\n  }\n  const uint8_t *bundle = static_cast<const uint8_t *> (value);\n  if (size < 16 || memcmp (bundle, "VRBNDL01", 8)) {\n    return false;\n  }\n  uint32_t count = 0;\n  memcpy (&count, bundle + 8, 4);\n  if (!count || count > 16) {\n    return false;\n  }\n  uint64_t offset = 16 + 8ULL * count;\n  if (offset > size) {\n    return false;\n  }\n  for (uint32_t i = 0; i < count; ++i) {\n    uint64_t manifestBytes = 0;\n    memcpy (&manifestBytes, bundle + 16 + 8ULL * i, 8);\n    if (!manifestBytes || offset + manifestBytes > size) {\n      return false;\n    }\n    const uint8_t *candidate = bundle + offset;\n    if (manifestBytes >= VR_HEADER_BYTES + VR_SIGNATURE_BYTES && !memcmp (candidate, "VROOT01\\0", 8)) {\n      uint64_t imageBytes = 0;\n      for (unsigned b = 0; b < 8; ++b) {\n        imageBytes |= static_cast<uint64_t> (candidate[24 + b]) << (8 * b);\n      }\n      if (imageBytes == targetBytes) {\n        *out = candidate;\n        *outBytes = manifestBytes;\n        return true;\n      }\n    }\n    offset += manifestBytes + ((8 - (manifestBytes & 7)) & 7);\n  }\n  return false;\n}\n#endif\n\nbool NativeUfsBlock::configureVerification ()\n', 1)
assert text.count('  const uint8_t *manifest = nullptr;\n  uint64_t manifestBytes = 0;\n  if (!verified_root_policy_count || verified_root_policy_count > 16) {\n    gRootDiagnostic.stage = "policy count invalid";\n    return false;\n  }\n  for (uint64_t index = 0; index < verified_root_policy_count; ++index) {\n    const NativeVerifiedBlockPolicy &policy = verified_root_policies[index];\n    if (!policy.manifest || policy.manifestBytes < VR_HEADER_BYTES + VR_SIGNATURE_BYTES) {\n      gRootDiagnostic.stage = "policy envelope invalid";\n      return false;\n    }\n    uint64_t imageBytes = 0;\n    for (unsigned i = 0; i < 8; ++i) {\n      imageBytes |= static_cast<uint64_t> (policy.manifest[24 + i]) << (8 * i);\n    }\n    if (imageBytes != bytes) {\n      continue;\n    }\n    if (manifest) {\n      gRootDiagnostic.stage = "duplicate capacity-matching policy";\n      return false;\n    }\n    manifest = policy.manifest;\n    manifestBytes = policy.manifestBytes;\n  }\n') == 1, 'policy selection block missing'
text = text.replace('  const uint8_t *manifest = nullptr;\n  uint64_t manifestBytes = 0;\n  if (!verified_root_policy_count || verified_root_policy_count > 16) {\n    gRootDiagnostic.stage = "policy count invalid";\n    return false;\n  }\n  for (uint64_t index = 0; index < verified_root_policy_count; ++index) {\n    const NativeVerifiedBlockPolicy &policy = verified_root_policies[index];\n    if (!policy.manifest || policy.manifestBytes < VR_HEADER_BYTES + VR_SIGNATURE_BYTES) {\n      gRootDiagnostic.stage = "policy envelope invalid";\n      return false;\n    }\n    uint64_t imageBytes = 0;\n    for (unsigned i = 0; i < 8; ++i) {\n      imageBytes |= static_cast<uint64_t> (policy.manifest[24 + i]) << (8 * i);\n    }\n    if (imageBytes != bytes) {\n      continue;\n    }\n    if (manifest) {\n      gRootDiagnostic.stage = "duplicate capacity-matching policy";\n      return false;\n    }\n    manifest = policy.manifest;\n    manifestBytes = policy.manifestBytes;\n  }\n', '  const uint8_t *manifest = nullptr;\n  uint64_t manifestBytes = 0;\n  if (adtBundleManifest (bytes, &manifest, &manifestBytes)) {\n    gRootDiagnostic.policyCount = 1;\n  } else {\n    // Fallback for kernels that still embed the manifests.\n    if (!verified_root_policy_count || verified_root_policy_count > 16) {\n      gRootDiagnostic.stage = "policy count invalid";\n      return false;\n    }\n    for (uint64_t index = 0; index < verified_root_policy_count; ++index) {\n      const NativeVerifiedBlockPolicy &policy = verified_root_policies[index];\n      if (!policy.manifest || policy.manifestBytes < VR_HEADER_BYTES + VR_SIGNATURE_BYTES) {\n        gRootDiagnostic.stage = "policy envelope invalid";\n        return false;\n      }\n      uint64_t imageBytes = 0;\n      for (unsigned i = 0; i < 8; ++i) {\n        imageBytes |= static_cast<uint64_t> (policy.manifest[24 + i]) << (8 * i);\n      }\n      if (imageBytes != bytes) {\n        continue;\n      }\n      if (manifest) {\n        gRootDiagnostic.stage = "duplicate capacity-matching policy";\n        return false;\n      }\n      manifest = policy.manifest;\n      manifestBytes = policy.manifestBytes;\n    }\n  }\n', 1)
ufs_cpp.write_text(text)
# Owner root trust: offline-owner-root-binding-audit.patch asks
# native_verified_block_root_authorized() whether the device behind the mounted
# root carries an authenticated signed volume, and skips the Apple root-hash panic
# when it answers yes. The BSD device is the APFS volume, so its IOMedia carries the
# volume UUID; walk up from there to the UFS window and let the driver decide.
owner_source = r"""#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
extern "C" int native_verified_block_root_authorized (dev_t device);
extern "C" int native_verified_block_root_authorized (dev_t device)
{
  if (device == NODEV) {
    return 0;
  }
  OSDictionary *matching = IOService::serviceMatching ("IOMedia");
  if (!matching) {
    return 0;
  }
  OSIterator *services = IOService::getMatchingServices (matching);
  int authorized = 0;
  if (services) {
    while (!authorized) {
      IOService *media = OSDynamicCast (IOService, services->getNextObject ());
      if (!media) {
        break;
      }
      OSNumber *bsdMajor = OSDynamicCast (OSNumber, media->getProperty ("BSD Major"));
      OSNumber *bsdMinor = OSDynamicCast (OSNumber, media->getProperty ("BSD Minor"));
      if (!bsdMajor || !bsdMinor) {
        continue;
      }
      if (bsdMajor->unsigned32BitValue () != major (device) ||
          bsdMinor->unsigned32BitValue () != minor (device)) {
        continue;
      }
      OSString *uuid = OSDynamicCast (OSString, media->getProperty ("UUID"));
      if (!uuid || !uuid->getCStringNoCopy ()) {
        continue;
      }
      IOService *node = media;
      for (unsigned depth = 0; depth < 8 && node; ++depth) {
        IOService *parent = node->getProvider ();
        NativeUfsBlock *block = OSDynamicCast (NativeUfsBlock, parent);
        if (block) {
          authorized = block->authenticatesVolume (uuid->getCStringNoCopy ()) ? 1 : 0;
          break;
        }
        node = parent;
      }
    }
    services->release ();
  }
  matching->release ();
  return authorized;
}

// offline-owner-root-crypto-probe.patch calls this when offline-owner-root-check=1.
extern "C" void native_verified_root_probe (void);
extern "C" void native_verified_root_probe (void)
{
  kprintf ("NATIVE_UFS owner root probe: policies=%llu anchor=%u\n",
           verified_root_policy_count, (unsigned) VR_KEY_BYTES);
}
#endif

"""
owner_anchor = "bool NativeUfsBlock::start (IOService *provider)\n"
assert text.count(owner_anchor) == 1, 'NativeUfs.cpp start anchor missing'
text = text.replace(owner_anchor, owner_source + owner_anchor, 1)
ufs_cpp.write_text(text)
print('NativeUfs.cpp: owner root authorization added')
print('NativeUfs.cpp: policy bundle read from /chosen')

header = staged / 'SmmuTakeover.h'
text = header.read_text()
needle = '    p.slot = exactSlot; p.bank = exactBank; p.smr = exactSmr; p.s2cr = exactS2cr;'
require(text.count(needle) == 1, 'SMMU takeover patch context changed')
guard = '''    for (unsigned i = 0; i < count; ++i) {
      if (i == exactSlot) continue;
      uint32_t smr = read(0x800 + 4 * i);
      if (!(smr & 0x80000000U)) continue;
      uint32_t s2cr = read(0xc00 + 4 * i);
      uint32_t mask = (smr >> 16) & 0x7fff;
      if (((smr ^ stream) & 0xffff & ~mask) == 0 ||
          (((s2cr >> 16) & 3) == 0 && (s2cr & 255) == exactBank)) {
        p.error = "UFS takeover context is shared"; return p;
      }
    }
'''
header.write_text(text.replace(needle, guard + needle))
tests = staged / 'tests'
tests.mkdir(exist_ok=True)
for name in ('SmmuTakeover', 'RootPublication', 'RootWindowRead', 'RootDiagnostics'):
    shutil.copyfile(source / 'tests' / (name + '.cpp'), tests / (name + '.cpp'))
    if name == 'SmmuTakeover':
        test_file = tests / (name + '.cpp')
        test_text = test_file.read_text()
        regression = '''  // fuxi integration: exact UFS mapping must own its context exclusively.
  reset(); r[0x800/4]=0x80000060; r[0xc00/4]=1;
  r[0x804/4]=0x80000061; r[0xc04/4]=1;
  assert(plan().error);
  reset(); r[0x800/4]=0x80000060; r[0xc00/4]=1;
  r[0x804/4]=0x80010060; r[0xc04/4]=2;
  assert(plan().error);
'''
        require(test_text.count('  puts(') == 1, 'SMMU regression test context changed')
        test_file.write_text(test_text.replace('  puts(', regression + '  puts('))
    binary = OUT / ('test-' + name)
    run(['xcrun', 'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', tests / (name + '.cpp'), '-o', binary])
    print(run([binary], text=True).strip())
# Compile the verified-block transport. The owner policy bundle is linked
# from build/verified-root-fuxi: without it vr_open is undefined, and
# without NATIVE_VERIFIED_BLOCK the driver refuses to publish a root at all
# (startRoot's validate() returns false in that case).
obj = OUT / 'NativeUfs.o'
require((ROOT / 'kits/VerifiedRoot/include').is_dir(),
        'missing kits/VerifiedRoot; run git submodule update --init kits/VerifiedRoot')
require((ROOT / 'drivers/NativeVirtioBlock/VerifiedBlockPolicy.h').is_file(),
        'missing drivers/NativeVirtioBlock/VerifiedBlockPolicy.h')
compile_source(staged / 'NativeUfs.cpp', obj,
               extra=['-I', ROOT / 'kits/VerifiedRoot/include',
                      '-I', ROOT / 'drivers/NativeVirtioBlock',
                      '-DNATIVE_VERIFIED_BLOCK=1',
                      '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1',
                      # Pinned third-party code that only compiles once
                      # NATIVE_VERIFIED_BLOCK is on: the record's -Werror turns its
                      # pre-existing sign warnings into failures. Keep them out of
                      # this translation unit instead of editing the submodule.
                      '-Wno-sign-compare', '-Wno-sign-conversion'])
compiled, symbols = vtable(obj, '__ZTV14NativeUfsBlock')
require(len(compiled) == len(methods), f'NativeUfsBlock has {len(compiled)} slots, base has {len(methods)}')
for index, (actual, expected) in enumerate(zip(compiled, methods)):
    require(actual != '___cxa_pure_virtual', f'Concrete transport has pure virtual slot {index}')
    require(actual == expected or actual.startswith(('__ZN14NativeUfsBlock', '__ZNK14NativeUfsBlock')),
            f'Concrete transport inherited a different method at slot {index}')
possible = {name: pointers[index] for index, name in enumerate(methods) if name not in by_name}
possible.update({'__ZN20IOBlockStorageDeviceC2EPK11OSMetaClass': constructors[0][0],
                 '__ZN20IOBlockStorageDeviceD2Ev': destructor,
                 '__ZN20IOBlockStorageDevice10gMetaClassE': metaclass,
                 '__ZTV20IOBlockStorageDevice': table})
undefined = {name for name, _, kind in symbols if kind == 1 and 'IOBlockStorageDevice' in name}
require(undefined and undefined <= possible.keys(), f'Unaudited original storage imports: {undefined - possible.keys()}')
require(not any('IOBlockStorageDeviceD0Ev' in name or 'IOBlockStorageDeviceD1Ev' in name for name in undefined),
        'A concrete transport cannot invoke an abstract complete destructor')
bindings = {name: possible[name] for name in sorted(undefined)}
asm = OUT / 'original-bindings.S'
asm.write_text(''.join(f'.globl {name}\n.set {name}, {address:#x}\n' for name, address in bindings.items()))
run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-c', asm, '-o', OUT / 'original-bindings.o'])
archive = OUT / 'libNativeUfs.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, obj, OUT / 'original-bindings.o'])
report = dict(source_repository='https://github.com/ljhhkx529/NativeUFS-fuxi', source_commit=SOURCE_COMMIT,
              source_sha256=hashlib.sha256((source / 'NativeUfs.cpp').read_bytes()).hexdigest(),
              staged_smmu_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
              smmu_takeover_shared_context_guard=True,
              reference_sha256=REFERENCE_SHA, storage_method_slots=len(methods), inherited_slots_checked=len(known),
              storage_vtable=hex(table), storage_metaclass=hex(metaclass),
              original_bindings={name: hex(value) for name, value in bindings.items()},
              archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
              source_storage_implementation_linked=False, verified_block_policy=True,
              hardware_tested=False)
(OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
print('PASS: pinned NativeUFS-fuxi build, concrete storage slots and original Apple storage bindings')
