#!/usr/bin/env python3
"""Build and qualify the selected fuxi USB archive using actual XNU records."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
module = root / '.cache/fuxi-qud-module'
out = root / '.cache/fuxi-qud-build'
records = root / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
llvm = Path('/opt/homebrew/opt/llvm/bin')
def run(arguments):
    command = [str(v) for v in arguments]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    print(result.stdout, end='')
    if result.returncode:
        print(result.stderr, end='')
        for base in (out, root / '.cache/fuxi-qud-xnu-check'):
            if base.is_dir():
                for log in base.glob('*.log'):
                    content = log.read_text(errors='replace')
                    if 'error:' in content:
                        print(log.name, content[-6500:])
        raise SystemExit(result.returncode)

run([sys.executable, module / 'tests/check.py', '--target-clang', llvm / 'clang',
     '--output', root / '.cache/fuxi-qud-model-check'])
matches = list(records.glob('**/pe_kprintf.o.json'))
if len(matches) != 1:
    raise SystemExit('missing or ambiguous actual PE compile record')
run([sys.executable, module / 'scripts/build.py', '--compile-record', matches[0],
     '--soc', 'soc/FuxiSm8550.h', '--ar', llvm / 'llvm-ar', '--nm', llvm / 'llvm-nm', '--output', out])
run([sys.executable, module / 'tests/check_xnu.py', '--xnu', root / '.cache/xnu-work',
     '--records', records, '--archive', out / 'libQcomXnuUsbDebug.a', '--nm', llvm / 'llvm-nm',
     '--output', root / '.cache/fuxi-qud-xnu-check'])
for name in ('check-report.json', 'xnu-check-report.json'):
    directory = root / ('.cache/fuxi-qud-model-check' if name == 'check-report.json' else '.cache/fuxi-qud-xnu-check')
    print((directory / name).read_text())
print((out / 'build-report.json').read_text())
print('PASS: fuxi QcomXnuUsbDebug archive, models and six-unit enabled/disabled XNU integration')
