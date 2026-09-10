#!/usr/bin/env bash
# Official Raspbian package for Pi 5 / Trixie; run separately from the base installer.
set -euo pipefail
if (( EUID != 0 )); then
    echo 'Run: sudo bash scripts/install-moonlight-pi.sh' >&2; exit 1
fi
source /etc/os-release
if [[ "${VERSION_CODENAME:-}" != trixie || "$(dpkg --print-architecture)" != arm64 ]] ||
    ! tr -d '\0' </proc/device-tree/model | grep -q 'Raspberry Pi 5'; then
    echo 'This installer targets Raspberry Pi 5 / Raspberry Pi OS Trixie arm64.' >&2; exit 1
fi
if ! command -v gpg >/dev/null; then
    apt-get update
    apt-get install -y --no-install-recommends gnupg ca-certificates
fi
stage=$(mktemp -d /tmp/raspberry-tv-moonlight.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
python3 - "$stage/key.asc" <<'PY'
from pathlib import Path
import sys
import urllib.request
url = 'https://dl.cloudsmith.io/public/moonlight-game-streaming/moonlight-qt/gpg.2F6AE14E1C660D44.key'
with urllib.request.urlopen(url, timeout=30) as response:
    Path(sys.argv[1]).write_bytes(response.read())
PY
fingerprint=$(gpg --batch --show-keys --with-colons "$stage/key.asc" |
    awk -F: '$1 == "pub" {n++} $1 == "fpr" && !fp {fp=$10} END {if(n==1) print fp}')
if [[ "$fingerprint" != 402E1E45A2456EDCF32A5E0E2F6AE14E1C660D44 ]]; then
    echo 'Unexpected Moonlight repository key; no repository changes made.' >&2; exit 1
fi
gpg --batch --yes --dearmor --output "$stage/key.gpg" "$stage/key.asc"
key=/usr/share/keyrings/moonlight-game-streaming-moonlight-qt-archive-keyring.gpg
source_file=/etc/apt/sources.list.d/raspberry-tv-moonlight.list
for path in "$key" "$source_file"; do
    if [[ -e "$path" && ! -e "$path.before-raspberry-tv" ]]; then
        cp -a -- "$path" "$path.before-raspberry-tv"
    fi
done
install -d -m 755 /usr/share/keyrings /etc/apt/sources.list.d
install -m 644 "$stage/key.gpg" "$key"
printf 'deb [arch=arm64 signed-by=%s] https://dl.cloudsmith.io/public/moonlight-game-streaming/moonlight-qt/deb/raspbian trixie main\n' "$key" >"$source_file"
apt-get update
apt-get install -y --no-install-recommends moonlight-qt
echo 'Moonlight Qt installed. Open its launcher tile to pair with Sunshine.'
