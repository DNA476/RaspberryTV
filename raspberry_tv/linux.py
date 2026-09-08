"""Linux adapters. Imported on Windows for parser tests, never executed in preview."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .processes import run, Unavailable
from .bluetooth import device_info, input_ready, pair_with_agent
from . import zapret


def split_nmcli(line: str) -> list[str]:
    fields, value, escaped = [], "", False
    for char in line:
        if escaped:
            value += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append(value)
            value = ""
        else:
            value += char
    fields.append(value)
    return fields


def parse_outputs(text: str) -> list[dict]:
    outputs, current = [], None
    for line in text.splitlines():
        match = re.match(r"^(\S+) connected(?: primary)?(?: (\d+x\d+)\+[-\d]+\+[-\d]+)?", line)
        if match:
            current = {"name": match[1], "modes": [], "current": match[2] or "", "rate": ""}
            outputs.append(current)
        elif line and not line[0].isspace():
            current = None
        elif current:
            mode = re.match(r"\s+(\d+x\d+)\s+(.+)", line)
            if mode:
                for raw_rate in mode[2].split():
                    rate = raw_rate.rstrip("*+")
                    if re.fullmatch(r"\d+(?:\.\d+)?", rate):
                        current["modes"].append({"mode": mode[1], "rate": rate})
                        if "*" in raw_rate:
                            current["current"], current["rate"] = mode[1], rate
    return outputs


@dataclass
class DisplayChange:
    """A separate watchdog restores the old mode even if the GUI crashes."""
    process: subprocess.Popen
    previous: list[str]

    def confirm(self):
        if self.process.poll() is not None:
            raise Unavailable("Время подтверждения истекло. Экран возвращён к прежнему режиму")
        try:
            self.process.stdin.write(b"keep\n")
            self.process.stdin.flush()
            self.process.stdin.close()
        except BrokenPipeError as exc:
            raise Unavailable("Режим уже отменён") from exc
        self.process.wait(timeout=5)

    def rollback(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=6)
        else:
            run(self.previous)


class LinuxSystem:
    def display_outputs(self) -> list[dict]:
        outputs = parse_outputs(run(["xrandr", "--query"], timeout=3))
        # Openbox does not activate an output after the TV reconnects by itself.
        if outputs and not any(output["current"] for output in outputs):
            run(["xrandr", "--output", outputs[0]["name"], "--auto", "--primary"], timeout=5)
            outputs = parse_outputs(run(["xrandr", "--query"], timeout=3))
        return outputs

    def status(self) -> dict:
        state = {"network": "Нет подключения", "wifi": False, "bluetooth": False,
                 "ip": "—", "devices": [], "outputs": [], "zapret_status": "Не установлен"}
        try:
            rows = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], timeout=5)
            for row in rows.splitlines():
                device, kind, status, name = (split_nmcli(row) + [""] * 4)[:4]
                if status == "connected" and kind in ("wifi", "ethernet"):
                    state["network"] = name if kind == "wifi" else "Ethernet"
                    state["wifi"] |= kind == "wifi"
                    state["ip"] = run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", device], timeout=3) or "—"
        except Unavailable:
            state["network"] = "Сеть недоступна"
        try:
            state["bluetooth"] = "Powered: yes" in run(["bluetoothctl", "show"], timeout=3)
            lines = run(["bluetoothctl", "devices", "Connected"], timeout=3).splitlines()
            lines += run(["bluetoothctl", "devices", "Bonded"], timeout=3).splitlines()
            for line in dict.fromkeys(lines):
                if m := re.match(r"Device ([0-9A-F:]{17}) (.+)", line):
                    info = device_info(m[1])
                    connected = info.get("Connected") == "yes"
                    bonded = info.get("Paired") == "yes" and info.get("Bonded") == "yes"
                    ready = connected and (info.get("Icon") != "input-gaming" or (bonded and input_ready(m[1])))
                    state["devices"].append({"address": m[1], "name": m[2], "connected": connected, "ready": ready})
        except Unavailable:
            pass
        try:
            state["outputs"] = self.display_outputs()
        except Unavailable:
            pass
        state.update(zapret.status())
        return state

    def wifi_scan(self) -> list[dict]:
        raw = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,DEVICE", "device", "wifi", "list", "--rescan", "yes"], timeout=25)
        networks = {}
        for line in raw.splitlines():
            fields = split_nmcli(line)
            if len(fields) == 4 and fields[0]:
                ssid, strength, security, device = fields
                row = {"ssid": ssid, "signal": int(strength or 0), "security": security, "device": device}
                if ssid not in networks or row["signal"] > networks[ssid]["signal"]:
                    networks[ssid] = row
        return sorted(networks.values(), key=lambda row: -row["signal"])

    def wifi_connect(self, ssid: str, device: str, password: str):
        if not ssid or not re.fullmatch(r"[\w.:-]{1,15}", device) or any(c in password for c in "\r\n\0"):
            raise ValueError("Проверь имя сети и пароль")
        # --ask keeps the passphrase out of the process argument list and logs.
        result = subprocess.run(["nmcli", "--ask", "device", "wifi", "connect", ssid, "ifname", device],
                                input=password + "\n", capture_output=True, text=True, timeout=50,
                                env={**os.environ, "LC_ALL": "C"})
        if result.returncode:
            raise Unavailable("Не удалось подключиться. Проверь пароль и доступность сети")

    def bluetooth_scan(self) -> list[dict]:
        run(["bluetoothctl", "power", "on"])
        run(["bluetoothctl", "--timeout", "10", "scan", "on"], timeout=15, check=False)
        raw = run(["bluetoothctl", "devices"])
        return [{"address": m[1], "name": m[2]} for line in raw.splitlines()
                if (m := re.match(r"Device ([0-9A-F:]{17}) (.+)", line))]

    def bluetooth(self, operation: str, address: str):
        if operation not in ("pair", "connect", "disconnect") or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address):
            raise ValueError("Некорректное устройство")
        if operation == "disconnect":
            run(["bluetoothctl", "disconnect", address], timeout=20)
            return
        info = device_info(address)
        if info.get("Paired") != "yes" or info.get("Bonded") != "yes":
            if info.get("Paired") == "yes":
                # A temporary pairing has no retained key; renew this device only.
                run(["bluetoothctl", "remove", address])
            pair_with_agent(address)
            info = device_info(address)
            if info.get("Paired") != "yes" or info.get("Bonded") != "yes":
                raise Unavailable("Bluetooth не сохранил сопряжение. Включи PS + Create и повтори")
        run(["bluetoothctl", "trust", address])
        result = run(["bluetoothctl", "connect", address], timeout=20)
        if "Failed" in result or "not available" in result:
            raise Unavailable("Устройство недоступно. Нажми PS на сопряжённом контроллере")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            info = device_info(address)
            if (info.get("Connected") == "yes"
                    and (info.get("Icon") != "input-gaming" or input_ready(address))):
                return
            time.sleep(.3)
        raise Unavailable("Bluetooth подключён, но устройство ввода не готово. Выключи контроллер и нажми PS снова")

    def phones(self) -> list[dict]:
        raw = run(["kdeconnect-cli", "--list-devices"])
        return [{"name": m[1], "id": m[2], "paired": "paired" in m[3] and "unpaired" not in m[3]}
                for line in raw.splitlines() if (m := re.match(r"- (.+): ([\w-]+) (.+)", line))]

    def pair_phone(self, device_id: str):
        if not re.fullmatch(r"[\w-]{1,100}", device_id):
            raise ValueError("Некорректное устройство")
        run(["kdeconnect-cli", "--device", device_id, "--pair"])

    def display(self, output: str, mode: str, rate: str) -> DisplayChange:
        outputs = parse_outputs(run(["xrandr", "--query"]))
        selected = next((row for row in outputs if row["name"] == output), None)
        if not selected or not selected["current"] or {"mode": mode, "rate": rate} not in selected["modes"]:
            raise ValueError("Этот режим экрана недоступен")
        previous = ["xrandr", "--output", output, "--mode", selected["current"], "--rate", selected["rate"]]
        watchdog = subprocess.Popen([sys.executable, "-m", "raspberry_tv.display_guard", json.dumps(previous)],
                                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
        change = DisplayChange(watchdog, previous)
        try:
            run(["xrandr", "--output", output, "--mode", mode, "--rate", rate])
        except Exception:
            change.rollback()
            raise
        return change

    def power(self, action: str):
        if action not in ("reboot", "poweroff"):
            raise ValueError("Неизвестное действие")
        run(["systemctl", action])
