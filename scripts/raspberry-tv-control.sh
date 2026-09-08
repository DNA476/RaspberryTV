#!/usr/bin/env bash
set -euo pipefail
cd /opt/raspberry-tv
exec /usr/bin/python3 -m raspberry_tv --control "${1:?Expected home, menu, or power}"
