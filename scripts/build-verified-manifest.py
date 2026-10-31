#!/usr/bin/env python3
"""Compile the verified-block manifest entry point into an archive the kernel link force-loads."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/verified-manifest"
OUT.mkdir(parents=True, exist_ok=True)

record, _ = json.JSONDecoder().raw_decode(
    (ROOT / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json").read_text())
args = record["arguments"]
source_key = record.get("file") or record.get("source")

command = []
index = 0
while index < len(args):
    arg = args[index]
    if source_key and arg == source_key:
        command.append(str(ROOT / "kits/verified-manifest/VerifiedBlockManifest.c"))
        index += 1
        continue
    if arg == "-o":
        command += ["-o", str(OUT / "VerifiedBlockManifest.o")]
        index += 2
        continue
    if arg.startswith(("-MMD", "-MT", "-MF")):
        index += 1
        continue
    command.append(arg)
    index += 1
if "-c" not in command:
    command.append("-c")
command += ["-Wno-missing-designated-field-initializers", "-Wno-unused-variable"]

result = subprocess.run(command, cwd=record["directory"], capture_output=True, text=True)
if result.returncode:
    print(result.stderr[-3000:])
    sys.exit(result.returncode)

archive = OUT / "libVerifiedBlockManifest.a"
archive.unlink(missing_ok=True)
subprocess.run([(os.environ.get('UNIXNU_AR') or ('/home/xiaomi/unixnu-linux/CommandLineTools/usr/bin/ar' if os.path.exists('/home/xiaomi/unixnu-linux/CommandLineTools/usr/bin/ar') else 'ar')), "rcs", archive,
                str(OUT / "VerifiedBlockManifest.o")], check=True)
print("verified manifest archive:", archive, archive.stat().st_size, "bytes")
