#!/usr/bin/env python3
"""Build the launchd job policy (sheng-disable-jobs / sheng-disable-watchdogd).

The policy needs only BSD headers, so it compiles with the kern_exec.o record instead of dragging in
the display stack that build-boot-progress.py requires. The archive is force-loaded by the kernel
link, and kern_exec.c calls sheng_boot_watchdog_policy() unconditionally before launchd runs.
"""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/watchdog-policy"
OUT.mkdir(parents=True, exist_ok=True)

record, _ = json.JSONDecoder().raw_decode(
    (ROOT / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json").read_text())
args = record["arguments"]
source = record.get("file") or record.get("source")

command = []
index = 0
while index < len(args):
    arg = args[index]
    if source and arg == source:
        command.append(str(ROOT / "kits/sheng/BootWatchdogPolicy.c"))
        index += 1
        continue
    if arg == "-o":
        command += ["-o", str(OUT / "BootWatchdogPolicy.o")]
        index += 2
        continue
    if arg in ("-MF", "-MJ", "-MT", "-MQ"):
        index += 2
        continue
    if arg in ("-MD", "-MMD", "-MP") or arg.startswith(("-MF", "-MJ", "-MT", "-MQ")):
        index += 1
        continue
    command.append(arg)
    index += 1
if "-c" not in command:
    command.append("-c")

result = subprocess.run(command, cwd=record["directory"], capture_output=True, text=True)
if result.returncode:
    print(result.stderr[-4000:])
    raise SystemExit(result.returncode)

archive = OUT / "libWatchdogPolicy.a"
archive.unlink(missing_ok=True)
subprocess.run([(os.environ.get('UNIXNU_AR') or ('/home/xiaomi/unixnu-linux/CommandLineTools/usr/bin/ar' if os.path.exists('/home/xiaomi/unixnu-linux/CommandLineTools/usr/bin/ar') else 'ar')), "rcs", archive,
                str(OUT / "BootWatchdogPolicy.o")], check=True)
print(f"watchdog policy archive: {archive} ({archive.stat().st_size} B)")
