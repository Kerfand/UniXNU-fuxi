<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
    <img alt="UniXNU" src="assets/logo-light.svg" width="200">
  </picture>
</p>

<p align="center">
  <a href="https://unixnu.com"><img alt="Documentation" src="https://img.shields.io/badge/docs-unixnu.com-2A5BD7?logo=readthedocs&logoColor=white"></a>
  <a href="https://t.me/unixnu"><img alt="Telegram" src="https://img.shields.io/badge/Telegram-@unixnu-26A5E4?logo=telegram&logoColor=white"></a>
  <a href="https://x.com/unixnu"><img alt="X" src="https://img.shields.io/badge/X-@unixnu-000000?logo=x&logoColor=white"></a>
</p>

# UniXNU-fuxi

Device repository for the Xiaomi 13 (fuxi, SM8550). It holds the board file, kernel
defconfig, device scripts, drivers and package.

The target userland is the **iPhone 10,3 (d22ap) iOS 16.7.16, build 20H392** restore image.
The kernel source is `xnu-8796.141.3` built as Darwin 23.6.0.

**Current state (2026-10-26):** the UFS transport, the apps-SMMU takeover, LUN 0 GPT parsing
and the flashed root image layout are verified on hardware. Root publication is not enabled
yet, so the kernel still stops at a deliberate diagnostic panic instead of mounting a root
volume. Details and evidence: **[PORTING-FUXI.md](PORTING-FUXI.md)**.

- [Documentation](https://unixnu.com)
- [Device page](https://unixnu.com/devices/fuxi)
- [Porting guide](https://unixnu.com/porting/)
- [Porting record](PORTING-FUXI.md)
- [UFS porting](https://unixnu.com/porting/ufs)

Firmware: [Sohytrs/uefi-fuxi](https://github.com/Sohytrs/uefi-fuxi), branch `fuxi`.
The UFS driver is pinned to
[ljhhkx529/NativeUFS-fuxi](https://github.com/ljhhkx529/NativeUFS-fuxi) at `e9ae0f61`.

Build with the `unixnu` driver of [UniXNU](https://github.com/UniXNU/UniXNU). Clone both
repositories with `--recurse-submodules`; `unixnu fetch` also updates the module submodules.

## Build

The kernel needs a Mac. Without one, run the kernel and payload steps in the
[macOS runner workflow](https://github.com/UniXNU/UniXNU/actions/workflows/macos.yml):

| Input | Value |
| --- | --- |
| `device_repository` | `Kerfand/UniXNU-fuxi` |
| `device_ref` | `main` |
| `defconfig` | `fuxi_defconfig` |
| `steps` | `kernel payload` |

`scripts/build-fuxi-platform-power.py` and `scripts/build-fuxi-ufs.py` require
`FUXI_PMGR_REFERENCE_KERNEL` to name a kernel with sha256
`c9e0debc23acc035a8b406e58088a3dd1a5f7e7a5203421b13b09b6e2a1abeb9`. This repository publishes
it as the release asset `fuxi-pmgr-baseline/kernel.macho`, and `pre_build` downloads it:

```sh
mkdir -p "$RUNNER_TEMP/fuxi-pmgr-baseline"
GH_TOKEN="$GITHUB_TOKEN" gh release download fuxi-pmgr-baseline -R "$GITHUB_REPOSITORY" \
  -p kernel.macho -D "$RUNNER_TEMP/fuxi-pmgr-baseline"
echo "FUXI_PMGR_REFERENCE_KERNEL=$RUNNER_TEMP/fuxi-pmgr-baseline/kernel.macho" >> "$GITHUB_ENV"
```

A fork's `GITHUB_TOKEN` cannot read another repository's workflow artifacts, so
`gh run download -R <upstream>` does not work here; a release asset in this repository does.

The build produces `fuxi/kernel/kernel.macho` and `fuxi/payload/payload.img`. Packing a boot
image means appending the payload after the firmware volume and setting the BootShim load
size at image-header offset `0x10` to `shim + FV + payload` (`0x0C300200`), then gzip'ing and
appending the board DTB. See the findings in [PORTING-FUXI.md](PORTING-FUXI.md).

## Device support

| Feature | State | Notes |
| --- | --- | --- |
| UEFI, UniXNU, DarwinBoot | ✅ | reaches XNU |
| UFS transport and SMMU | ✅ | controller adoption, stream `0x60`, command queues |
| GPT and root image layout | ✅ | `manifest status=0`, both windows placed and readable |
| Root volume mount | ❌ | needs an owner-signed per-capacity policy bundle |
| Native UFS root | ❌ | same gate as above |
| Touch | ❌ | |
| Display | ❌ | |
| GPU | ❌ | |
| Wi-Fi, Bluetooth, USB, audio | ❌ | |
| Battery, sensors, suspend | ❌ | |

✅ verified on hardware, ⚠️ partial, ❌ not available.

## License

BSD-2-Clause-Patent. See [LICENSE](LICENSE).
