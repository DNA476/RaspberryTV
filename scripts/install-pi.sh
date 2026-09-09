#!/usr/bin/env bash
# Run on Raspberry Pi, NOT against an attached drive on the development PC.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target_user="${SUDO_USER:-}"
enable_session=false
check_only=false
while (($#)); do
    case "$1" in
        --user) target_user="${2:?Missing username}"; shift 2 ;;
        --enable-session) enable_session=true; shift ;;
        --check) check_only=true; shift ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if ! $check_only && ((EUID != 0)); then
    echo 'Run with sudo on the Pi.' >&2; exit 1
fi
if [[ ! "$target_user" =~ ^[a-z_][a-z0-9_-]*$ ]] || [[ "$target_user" == root ]] || ! id "$target_user" >/dev/null 2>&1; then
    echo 'Pass --user followed by your existing, non-root Pi username.' >&2; exit 1
fi
# /etc/os-release is root-owned system metadata.
source /etc/os-release
if [[ "${VERSION_CODENAME:-}" != trixie ]] || [[ "$(dpkg --print-architecture)" != arm64 ]]; then
    echo 'This installer targets Raspberry Pi OS Lite Trixie, arm64.' >&2; exit 1
fi
if ! tr -d '\0' </proc/device-tree/model | grep -q 'Raspberry Pi 5'; then
    echo 'This installation profile targets Raspberry Pi 5.' >&2; exit 1
fi
packages=(
    python3 python3-pyside6.qtcore python3-pyside6.qtgui python3-pyside6.qtqml
    python3-pyside6.qtquick python3-pyside6.qtnetwork python3-evdev python3-xlib
    qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-layouts
    qml6-module-qtquick-window qml6-module-qtqml-workerscript qml6-module-qtquick-templates
    libqt6svg6 fonts-dejavu-core xserver-xorg xinit x11-xserver-utils polkitd
    openbox picom lightdm lightdm-gtk-greeter dbus-user-session dbus-x11
    wmctrl xdotool network-manager bluez kdeconnect cec-utils
    pipewire pipewire-pulse wireplumber rtkit chromium kodi
)
if $check_only; then
    exec apt-get --simulate --no-install-recommends install "${packages[@]}"
fi
apt-get update
apt-get install -y --no-install-recommends "${packages[@]}"
install -d -m 755 /opt/raspberry-tv /etc/raspberry-tv /etc/systemd/user /usr/share/xsessions /etc/X11/xorg.conf.d
cp -a "$project_dir/raspberry_tv" /opt/raspberry-tv/
chown -R root:root /opt/raspberry-tv
chmod -R go-w /opt/raspberry-tv
install -m 644 "$project_dir/deploy/openbox.xml" /etc/raspberry-tv/openbox.xml
install -m 644 "$project_dir/deploy/picom.conf" /etc/raspberry-tv/picom.conf
install -m 644 "$project_dir/deploy/99-raspberry-tv-vc4.conf" /etc/X11/xorg.conf.d/99-raspberry-tv-vc4.conf
install -m 644 "$project_dir/deploy/raspberry-tv.service" /etc/systemd/user/raspberry-tv.service
install -m 644 "$project_dir/deploy/raspberry-tv.desktop" /usr/share/xsessions/raspberry-tv.desktop
install -m 644 "$project_dir/deploy/70-raspberry-tv-input.rules" /etc/udev/rules.d/70-raspberry-tv-input.rules
install -d -m 755 /etc/modules-load.d
printf 'uinput\n' >/etc/modules-load.d/raspberry-tv-uinput.conf
modprobe uinput
getent group raspberry-tv >/dev/null || groupadd --system raspberry-tv
usermod -aG raspberry-tv "$target_user"
install -m 644 "$project_dir/deploy/50-raspberry-tv.rules" /etc/polkit-1/rules.d/50-raspberry-tv.rules
install -m 755 "$project_dir/scripts/raspberry-tv-session.sh" /usr/local/bin/raspberry-tv-session
install -m 755 "$project_dir/scripts/raspberry-tv-control.sh" /usr/local/bin/raspberry-tv-control
install -m 755 "$project_dir/scripts/recovery.sh" /usr/local/bin/raspberry-tv-recovery
systemctl enable bluetooth.service NetworkManager.service
udevadm control --reload-rules
udevadm trigger --subsystem-match=misc --sysname-match=uinput
udevadm settle
if $enable_session; then
    install -d -m 755 /etc/lightdm/lightdm.conf.d
    conf=/etc/lightdm/lightdm.conf.d/80-raspberry-tv.conf
    if [[ -f "$conf" && ! -f "$conf.before-raspberry-tv" ]]; then
        cp -a "$conf" "$conf.before-raspberry-tv"
    fi
    printf '[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\nautologin-session=raspberry-tv\nuser-session=raspberry-tv\n' "$target_user" >"$conf"
    systemctl enable lightdm.service
    systemctl set-default graphical.target
    echo 'TV session enabled for the next boot. Reboot when ready.'
else
    echo 'Files installed. To enable TV autologin, rerun with --enable-session.'
fi
echo 'Moonlight and zapret are separate integrations. See docs/STATUS.md before hardware acceptance.'
echo 'Recovery over SSH: sudo raspberry-tv-recovery'
