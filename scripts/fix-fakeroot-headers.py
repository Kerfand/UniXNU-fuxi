#!/usr/bin/env python3
"""Repair the fakeroot kernel SDK that the UniXNU Linux build needs.

Why this exists
---------------
On macOS the iPhoneOS SDK supplies the C library headers and the
Kernel.framework public/private headers.  On Linux the framework emulates that
SDK with ``.cache/fakeroot``, but the emulation is incomplete in ways that break
every kernel-adjacent archive (corecrypto runs first) and the XNU compile:

1. ``fakeroot/**/stddef.h`` and ``stdint.h`` are empty placeholders, so
   ``size_t``, ``uint8_t`` and friends are unknown.
2. ``Kernel.framework/Versions/A/{Headers,PrivateHeaders}`` only carry the
   availability headers, so ``lck_mtx_t``, ``mach_absolute_time``,
   ``mach-o/loader.h``, ``architecture/byte_order.h`` and
   ``task_suspend_stats_t`` are all missing.
3. The libdispatch/libplatform headers installed under ``usr/local/include`` are
   shadowed by the framework directories, so ``<os/firehose_buffer_private.h>``
   resolves to an incomplete copy and XNU loses
   ``FIREHOSE_BUFFER_KERNEL_DEFAULT_CHUNK_COUNT``.

What it does, in this order (later steps win):
* composes the framework headers from the XNU export set produced by an earlier
  bootstrap (``build/xnu/ios-obj/EXPORT_HDRS``), letting ``osfmk`` win over
  ``bsd`` for the overlapping ``kern``/``mach``/``vm``/``os`` directories;
* merges ``.cache/fakeroot/usr/local/include`` into the framework view;
* adds the headers outside the export set (``mach-o``, ``architecture``) from
  ``.cache/xnu-work/EXTERNAL_HEADERS``;
* copies clang's real ``stddef.h``/``stdint.h``/``stdbool.h``/``stdarg.h``/
  ``stdatomic.h``/``inttypes.h``/``limits.h``/``float.h`` over every placeholder.

Run it after XNU's header installation and before the compile/archives:

    python3 fix-fakeroot-headers.py [tree]     # defaults to the current directory

The script is idempotent: running it twice changes nothing.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

CLANG_INCLUDE = Path("/usr/lib/llvm-21/lib/clang/21/include")
BASIC_HEADERS = (
    "stddef.h",
    "stdint.h",
    "stdbool.h",
    "stdarg.h",
    "stdatomic.h",
    "inttypes.h",
    "limits.h",
    "float.h",
)
FRAMEWORK = "System/Library/Frameworks/Kernel.framework/Versions/A"
KERNEL_OWNED = ("kern", "mach", "vm", "os")


def find_clang_include() -> Path:
    if CLANG_INCLUDE.is_dir():
        return CLANG_INCLUDE
    candidates = sorted(Path("/usr/lib/llvm-21/lib/clang").glob("*/include"))
    if not candidates:
        sys.exit("no clang resource headers found")
    return candidates[-1]


def _place(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            destination.unlink()
        shutil.copy2(source, destination)


def compose_framework_headers(tree: Path, fakeroot: Path) -> int:
    """Rebuild Kernel.framework from the XNU export set."""
    export = tree / "build/xnu/ios-obj/EXPORT_HDRS"
    if not export.is_dir():
        print("  EXPORT_HDRS missing: run a kernel bootstrap pass first")
        return 0
    framework = fakeroot / FRAMEWORK
    placed = 0
    # osfmk last so its kern/mach/vm/os win over the bsd copies.
    for group in ("bsd", "iokit", "libkern", "pexpert", "san", "security", "osfmk"):
        source = export / group
        if not source.is_dir():
            continue
        for target_name in ("Headers", "PrivateHeaders"):
            target = framework / target_name
            target.mkdir(parents=True, exist_ok=True)
            for child in source.iterdir():
                _place(child, target / child.name)
                placed += 1
    return placed


def merge_local_includes(fakeroot: Path) -> int:
    """Make usr/local/include visible through the framework headers.

    libplatform and libdispatch install there, but XNU compiles with the
    framework directories first, so an incomplete Kernel.framework/os would
    shadow <os/firehose_buffer_private.h>.  Merging keeps one consistent view.
    """
    local = fakeroot / "usr/local/include"
    if not local.is_dir():
        return 0
    framework = fakeroot / FRAMEWORK
    copied = 0
    for target_name in ("Headers", "PrivateHeaders"):
        target = framework / target_name
        target.mkdir(parents=True, exist_ok=True)
        for child in local.iterdir():
            _place(child, target / child.name)
            copied += 1
    return copied


def add_external_headers(tree: Path, fakeroot: Path) -> int:
    work = tree / ".cache/xnu-work/EXTERNAL_HEADERS"
    if not work.is_dir():
        return 0
    framework = fakeroot / FRAMEWORK
    placed = 0
    for extra in ("mach-o", "architecture"):
        source = work / extra
        if not source.is_dir():
            continue
        for target_name in ("Headers", "PrivateHeaders"):
            _place(source, framework / target_name / extra)
            placed += 1
    return placed


def install_basic_headers(fakeroot: Path, clang: Path) -> int:
    """Overwrite every placeholder copy of the fundamental C headers."""
    written = 0
    for name in BASIC_HEADERS:
        source = clang / name
        if not source.is_file():
            continue
        payload = source.read_bytes()
        targets = list(fakeroot.rglob(name)) or [fakeroot / "usr/include" / name]
        for target in targets:
            if target.is_dir():
                continue
            if target.is_symlink():
                target.unlink()
            if not target.is_file() or target.read_bytes() != payload:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                written += 1
    return written


def main() -> None:
    if sys.platform != "linux":
        # On macOS the real SDK supplies everything this script repairs.
        print("not needed on this platform")
        return
    tree = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    fakeroot = tree / ".cache/fakeroot"
    if not fakeroot.is_dir():
        sys.exit(f"no fakeroot at {fakeroot}")
    clang = find_clang_include()
    # Order matters: the export set and the local includes land first, the
    # fundamental C headers last so nothing overwrites them with a placeholder.
    exported = compose_framework_headers(tree, fakeroot)
    local = merge_local_includes(fakeroot)
    external = add_external_headers(tree, fakeroot)
    basic = install_basic_headers(fakeroot, clang)
    print(f"framework entries: {exported}, local includes: {local}, "
          f"external: {external}, basic headers rewritten: {basic}")


if __name__ == "__main__":
    main()
