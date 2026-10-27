#!/usr/bin/env bash
set -euo pipefail
FUXI_TREE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 "$FUXI_TREE/scripts/run-fuxi-usb-kernel.py"
