#!/usr/bin/env bash
# Install only on the Pi. Source bundle is prepared without privileges first.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --check ]]; then
    exec apt-get --simulate --no-install-recommends install nftables curl ca-certificates
fi
if ((EUID != 0)) || [[ "$(dpkg --print-architecture)" != arm64 ]]; then
    echo 'Run with sudo on the arm64 Pi.' >&2; exit 1
fi
assets="$(realpath -- "${1:?Pass the prepared assets archive}")"
getent group raspberry-tv >/dev/null
test -f /opt/raspberry-tv/raspberry_tv/__init__.py
temporary="$(mktemp -d /opt/raspberry-tv-zapret-install.XXXXXXXX)"
# Delete only the literal mktemp directory, whose parent/name are checked.
cleanup() {
    if [[ "$temporary" == /opt/raspberry-tv-zapret-install.* && -d "$temporary" && ! -L "$temporary" ]]; then
        rm -rf -- "$temporary"
    fi
}
trap cleanup EXIT
python3 - "$assets" "$temporary" "$project_dir/deploy/zapret-sources.json" <<'PY'
from hashlib import sha256
import json
from pathlib import Path
import sys
import tarfile
archive, target, lock = sys.argv[1:]
root = Path(target)
with tarfile.open(archive) as stream:
    if any(not (m.isfile() or m.isdir()) for m in stream.getmembers()):
        raise SystemExit('Links/devices are not allowed in the asset bundle')
    stream.extractall(root, filter='data')
if json.loads((root / 'SOURCES.json').read_text()) != json.loads(Path(lock).read_text()):
    raise SystemExit('Upstream version mismatch')
for line in (root / 'SHA256SUMS').read_text().splitlines():
    digest, name = line.split('  ', 1)
    path = (root / name).resolve()
    if not path.is_relative_to(root) or sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit('Bundle checksum mismatch')
PY
apt-get update
apt-get install -y --no-install-recommends nftables curl ca-certificates
# Enable the NFQUEUE modules before restricting the service capabilities.
modprobe nfnetlink_queue
modprobe nft_queue
printf 'nfnetlink_queue\nnft_queue\n' >/etc/modules-load.d/raspberry-tv-zapret.conf
systemctl stop raspberry-tv-zapret.service 2>/dev/null || true
if [[ -d /opt/raspberry-tv-zapret ]]; then
    backup="$(mktemp -d /opt/raspberry-tv-zapret-backup.XXXXXXXX)"
    cp -a /opt/raspberry-tv-zapret/. "$backup/"
    echo "Previous assets: $backup"
fi
install -d -m 755 /opt/raspberry-tv-zapret /opt/raspberry-tv/scripts
cp -a "$temporary/." /opt/raspberry-tv-zapret/
chown -R root:root /opt/raspberry-tv-zapret
chmod -R go-w /opt/raspberry-tv-zapret
chmod -R a+rX /opt/raspberry-tv-zapret
chmod 755 /opt/raspberry-tv-zapret/nfqws
cp -a "$project_dir/raspberry_tv/." /opt/raspberry-tv/raspberry_tv/
chown -R root:root /opt/raspberry-tv/raspberry_tv
chmod -R go-w /opt/raspberry-tv/raspberry_tv
install -m 755 "$project_dir/scripts/zapret-parse.sh" /opt/raspberry-tv/scripts/zapret-parse.sh
install -m 644 "$project_dir/deploy/raspberry-tv-zapret.service" /etc/systemd/system/raspberry-tv-zapret.service
systemctl daemon-reload
systemctl enable --now raspberry-tv-zapret.service
echo 'Installed. Filtering is initially OFF; configure and apply in Raspberry TV.'
echo 'Emergency stop: sudo systemctl stop raspberry-tv-zapret.service'
