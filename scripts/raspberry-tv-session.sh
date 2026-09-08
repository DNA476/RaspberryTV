#!/usr/bin/env bash
set -euo pipefail
export XDG_SESSION_TYPE=x11
export QT_QPA_PLATFORM=xcb
export PYTHONPATH=/opt/raspberry-tv
cd /opt/raspberry-tv
xsetroot -solid black
xset s off
xset -dpms || true
systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS
systemctl --user start pipewire.service pipewire-pulse.service wireplumber.service || true
openbox --config-file /etc/raspberry-tv/openbox.xml &
wm_pid=$!
picom --config /etc/raspberry-tv/picom.conf &
compositor_pid=$!
trap 'systemctl --user stop raspberry-tv.service; kill "$compositor_pid" "$wm_pid" 2>/dev/null || true' EXIT
systemctl --user daemon-reload
# A newly installed unit has no loaded state to reset yet.
if systemctl --user is-failed --quiet raspberry-tv.service; then
    systemctl --user reset-failed raspberry-tv.service
fi
systemctl --user start raspberry-tv.service
if command -v kdeconnectd >/dev/null; then
    kdeconnectd >/dev/null 2>&1 &
elif [[ -x /usr/lib/aarch64-linux-gnu/libexec/kdeconnectd ]]; then
    /usr/lib/aarch64-linux-gnu/libexec/kdeconnectd >/dev/null 2>&1 &
fi
wait "$wm_pid"
