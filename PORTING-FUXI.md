# PORTING-FUXI — Xiaomi 13 (fuxi, SM8550) porting record

Format 1 device repository: the ADT comes from a board script (`scripts/make-sheng-adt.py`)
and the build selection lives in `board.toml`. `porting.toml` is the format 2 record and is
not used here; this file is the record.

| Field | Value |
| --- | --- |
| Device | Xiaomi 13 (fuxi), SM8550 |
| Target OS | iPhone10,3 (d22ap) iOS 16.7.16, build 20H392 |
| XNU source | `xnu-8796.141.3` source built as Darwin 23.6.0 (`feeds.toml`) |
| Firmware | Mu-Silicium fork `Sohytrs/uefi-fuxi`, branch `fuxi` |
| Record updated | 2026-10-26 |

## Current status

| Area | Status | Note |
| --- | --- | --- |
| Firmware and boot | device-verified | UniXNU boots, payload found, DarwinBoot enters XNU |
| UFS transport and SMMU | device-verified | controller adoption, apps-SMMU stream `0x60`, command queues |
| GPT and root windows | device-verified | LUN 0 GPTH, `userdata` entry, window placement and hashes |
| SHNGROOT manifest | device-verified | `manifest status=0`, 2 windows |
| Root publication | **not enabled** | `NativeUfs::signedVolumeUuid()` needs a signed per-capacity policy |
| Persistent Data | not started | depends on root publication |

## Reproduced build

| Input | Value |
| --- | --- |
| Framework commit | `Kerfand/UniXNU` @ `99f0abe5fb8ca8076c41c091480579e76f227a75` |
| Device commit | `Kerfand/UniXNU-fuxi` @ `cbd1faff21d88bd6a9536e80f99820f1f4c10a7a` |
| `defconfig` | `fuxi_defconfig` |
| Workflow `steps` | `kernel payload` |
| `pre_build` reference kernel | release asset `fuxi-pmgr-baseline/kernel.macho`, sha256 `c9e0debc23acc035a8b406e58088a3dd1a5f7e7a5203421b13b09b6e2a1abeb9` |
| Firmware volume | sha256 `af2fbfafd2e89fd96fad51652bb9a71db20cf7a1f45f2712be343ddece6b7be5` (3 MiB) |
| Board DTB | sha256 `f6be7b08c7e696cf144ded82a31af89918be7133b22d3a158070bb6e99798988` (800,010 B) |
| Payload | sha256 `35ce5737c07694bb13a018e339e8a20d04018727a2f8c96f56baa428d23e0dcf` |
| Boot image | sha256 `b8985cc310173febe992306b73d705e5f8da67e6c895f6edd8b952acd300c44b` (11,747,328 B) |

`build-fuxi-platform-power.py` and `build-fuxi-ufs.py` both require
`FUXI_PMGR_REFERENCE_KERNEL` to name a kernel with the sha256 above. A fork's
`GITHUB_TOKEN` cannot read another repository's workflow artifacts, so the file is
published as a release asset in the fork and downloaded in `pre_build` rather than
with `gh run download`.

## Storage and root

### Hardware inventory

| Item | Value | Source |
| --- | --- | --- |
| UFS host controller | `0x01d84000`, length `0x3000` | board DT `ufshc@1d84000` |
| UFS PHY | `0x01d80000`, `qcom,ufs-phy-qmp-v4-kalama` | board DT `ufsphy_mem@1d80000` |
| GCC | `0x00100000` | `GccUfs.h` constants |
| apps-SMMU stream | `0x60` | board DT `iommus = <&apps_smmu 0x60 0>` |
| Interrupt | `0 0x109 4` | board DT |
| LUN 0 `userdata` entry | LBAs `0x2f7840..0x3afc3fa`, 4096-byte LBAs | `board.toml [userdata]` |
| `OS` window | offset `0x100000`, 5,635,081,728 B | SHNGROOT manifest |
| `SystemOS` window | offset `0x150000000`, 2,743,074,816 B | SHNGROOT manifest |
| Root volume UUID | `DADD93F8-DBBF-4B17-A1DF-0F11E64172F7` | `board.toml [payload.adt] root_match_volume_uuid` |

`NativeUfs` takes its physical windows from `GccUfs.h` constants, not from the ADT, so
`/arm-io/ufshc` only needs `compatible = "qcom,sm8550-ufshc"` to match the personality.
The `OS` window carries the original dmg GPT (`EFI PART` at +0x200, one Apple APFS
partition at LBAs 34..11005985); the `SystemOS` window is a raw APFS container
(`NXSB` at block 0), which is why its signature word at +512 reads zero.

### Device evidence

Milestone A — `native-ufs-probe=10`, controller adoption, GPT, manifest
(Y2026-10-26 19:36, image `boot-fuxi-FIX061.img`):

```
NATIVE_UFS root check
userdata first=2f7840 last=3afc3fa
block0 magic 474e4853 544f4f52 ext4magic=0000 ext4blocks=0
manifest status=0 windows=2 @NativeUfs.cpp:1794
```

Milestone B — `native-ufs-probe=11`, window placement (2026-10-26 20:07 from a locally
packed image, and again 20:23 from boot image `b8985cc3…` built only from the CI payload):

```
NATIVE_UFS root windows
userdata first=2f7840 last=3afc3fa manifest status=0 windows=2
w0 OS off=100000 size=14fe08600 gpt=20494645 54524150 read=1
w1 SystemOS off=150000000 size=a3800000 gpt=00000000 00000000 read=1
w0 sha 6e87bfca a8eca952 e00b719f 333d1f10
w1 sha 9cd738f3 b0169055 549df94a d6d8d13e @NativeUfs.cpp:1827
```

Reading: `20494645 54524150` is little-endian `"EFI "` + `"PART"`, so the `OS` window
really landed at `userdataFirst * 4096 + 0x100000`. `size=14fe08600` is 5,635,081,728 and
`size=a3800000` is 2,743,074,816, both exactly the manifest values. The `sha` lines are
the recorded window digests; both match an independent offline computation from
`userdata-root.img`.

### Offline verification of the flashed image

`userdata-root.img` (8,380,219,392 B) was validated against the driver's own acceptance
rules before flashing:

- `SHNGROOT` magic, version 1, 2 windows, `image_bytes` equal to the file size
- `SHA256(block[0:4064])` equals the digest stored at offset 4064
- both windows 4096/512-byte aligned, `block_size = 512`, `flags = 1`
- per-window sha256 recomputed and matched
- root UUID `DADD93F8-…` found at offset `0xF0` inside the APFS volume superblocks of the
  `OS` window, so `/chosen/root-matching` can match

## Findings

1. **The stock CI image had no storage driver at all.** The kernel carried the prelinked
   Apple `IOStorageFamily` and `AppleAPFS` but no UFS, NVMe or AHCI controller provider,
   so `IOFindBSDRoot` timed out in `IOService::waitForService` and took the iOS branch
   `panic("Failed to mount root device")`. That panic means "no provider appeared", not
   "a provider failed".
2. **`CONFIG_XNU_NATIVE_UFS` alone is not enough.** `NativeUfs::start()` returns `false`
   unless `native-ufs-probe` or `native-ufs-root` is set, and `startRoot()` additionally
   needs a valid SHNGROOT manifest in the flashed `userdata` partition.
3. **BootShim loads only what its ARM64 image header says.** Offset `0x10` holds the load
   size; the firmware-only image says `0x00300200` (3,146,240) while an image with an
   appended payload needs `0x0C300200` (204,472,832). Everything else in the shim, and the
   whole 3 MiB firmware volume, is identical between the two.
4. **`/chosen/boot-manifest-hash` is required by the iOS 16 Image4 path.** Without it
   AppleImage4 answers `ENOENT(2)` and the kernel panics with
   `failed to get previous stage manifest hash: 2` before BSD init. XNU reads the property
   from `/chosen` (`iokit/bsddev/IOKitBSDInit.cpp`) and the original `/sbin/mount -P 1`
   exits 66 when it is absent. Fixed in `cbd1faff`; the value is a documented stand-in
   (`sha384(b'UniXNU boot manifest')`) until a real iBoot manifest hash is available.
5. **Root publication is a crypto gate, not a flag.** `NativeUfsBlock::signedVolumeUuid()`
   returns `false` unless a verified policy view exists, and
   `NativeUfsBlock::configureVerification()` refuses to build one when
   `verified_root_policy_count` is 0 — which is exactly what the weak stub in
   `scripts/fuxi-stub.c` provides. Enabling `-DNATIVE_VERIFIED_BLOCK=1` without the policy
   archive also fails at link time with an undefined `vr_open`.

## Limits

- Root publication is disabled: no owner key pair, no signed per-capacity manifests for
  the two window sizes, and no `libVerifiedRootProbe.a`.
- The kernel is built for Darwin 23.6.0 while the prelinked kexts come from the iOS 16.7.16
  kernelcache; userland (dyld shared cache, `launchd`) has not been reached.
- `sheng-pmgr=2` links sheng's PMGR table; fuxi power domains are not ported.
- Data persistence, display, touch, GPU and network are not started.

## Next action

1. Generate an RSA3072 owner key pair and a signed manifest per window
   (`5,635,081,728` and `2,743,074,816` bytes) with `kits/VerifiedRoot/tools/manifest.py`,
   then build `libVerifiedRootProbe.a` with `scripts/build-verified-root-probe.py
   --include-systemos`. Hash tables are about 42 MB and 21 MB, so decide first whether the
   policy travels in the kernel window or in the payload.
2. Add `-I kits/VerifiedRoot/include`, `-I drivers/NativeVirtioBlock`,
   `-DNATIVE_VERIFIED_BLOCK=1`, `-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1` and the archive to
   `scripts/build-fuxi-ufs.py`.
3. Switch `board.toml` from `native-ufs-probe=11` to `native-ufs-root=1` and re-run
   Milestone B; the expected change is that the two windows are published as block devices
   and `/chosen/root-matching` matches `DADD93F8-…`.

## Evidence index

| Kind | Reference | Result |
| --- | --- | --- |
| device | `b8985cc310173febe992306b73d705e5f8da67e6c895f6edd8b952acd300c44b` (payload `35ce5737…`) | Milestone B, Image4 passed, windows placed and readable |
| device | `boot-fuxi-FIX061.img` | Milestone A, controller, GPT and manifest accepted |
| host | offline parser on `userdata-root.img` | manifest `status=0`, window digests and root UUID match |
| source | `scripts/make-sheng-adt.py` @ `cbd1faff` | `/chosen/boot-manifest-hash` emitted |
| source | `scripts/build-fuxi-ufs.py` | pinned `NativeUFS-fuxi` @ `e9ae0f61`, storage ABI bindings |
