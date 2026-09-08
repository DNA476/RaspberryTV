#!/usr/bin/env bash
# Intentionally usable over SSH or a second text console when the GUI fails.
set -euo pipefail
if ((EUID != 0)); then echo 'Run with sudo.' >&2; exit 1; fi
conf=/etc/lightdm/lightdm.conf.d/80-raspberry-tv.conf
if [[ -f "$conf" ]]; then mv -- "$conf" "$conf.disabled"; fi
systemctl disable --now lightdm.service
systemctl set-default multi-user.target
echo 'TV autologin disabled. SSH and normal console login remain available.'
echo 'Re-enable by rerunning the installer with --enable-session after fixing the problem.'
