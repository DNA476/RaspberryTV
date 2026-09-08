"""Root service: private nft table, upstream nfqws, durable reversible settings.

Only this installed, root-owned module handles privileged operations. Clients
send validated configuration values, never commands, arguments or file paths.
"""
from copy import deepcopy
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import socketserver
import subprocess
import threading
import time

from .config import DEFAULTS, atomic_json, validate
from .zapret import MAX_MESSAGE, SOCKET

BASE = Path("/opt/raspberry-tv-zapret")
STATE = Path("/var/lib/raspberry-tv-zapret")
TABLE = "raspberry_tv_zapret"
QUEUE = 221
MARK = "0x40000000"
LOG = logging.getLogger(__name__)


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, timeout=12, **kwargs)
    if result.returncode:
        LOG.error("Command %s: %s", args[0], result.stderr[-2000:])
        raise ValueError("Ошибка сетевой службы. Подробности сохранены в журнале")
    return result.stdout


def strategies(base=BASE):
    result = {}
    for directory in (base / "strategies", base / "adapter/custom-strategies"):
        for path in sorted(directory.glob("*.bat")):
            if re.fullmatch(r"[\w .()-]+\.bat", path.name, re.ASCII) and path.name.startswith(("general", "discord")):
                result[path.name] = path
    return result


def interfaces():
    return sorted(p.name for p in Path("/sys/class/net").iterdir() if p.name != "lo")


def validated_config(data, catalog, devices):
    config = validate({"zapret": data})["zapret"]
    if config["strategy"] not in catalog:
        raise ValueError("Стратегия не установлена")
    if config["interface"] not in devices:
        raise ValueError("Выбери существующий сетевой интерфейс")
    if not config["domains"]:
        raise ValueError("Добавь хотя бы один домен")
    return config


def port_set(value):
    if not re.fullmatch(r"[0-9,-]+", value):
        raise ValueError("Некорректные порты в стратегии")
    for part in value.split(","):
        bounds = part.split("-")
        if len(bounds) > 2 or not all(1 <= int(n) <= 65535 for n in bounds) or int(bounds[0]) > int(bounds[-1]):
            raise ValueError("Некорректный диапазон портов")
    return "{ " + value.replace(",", ", ") + " }"


def firewall_rules(tcp, udp, interface):
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,15}", interface):
        raise ValueError("Некорректный интерфейс")
    # Default Sunshine ports; all local addresses remain excluded even with
    # custom ports. Public hosts using custom ports need explicit integration.
    private4 = "{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/3 }"
    private6 = "{ ::/128, ::1/128, ::ffff:0:0/96, fc00::/7, fe80::/10, ff00::/8 }"
    lines = [f"add table inet {TABLE}",
             f"add chain inet {TABLE} post {{ type filter hook postrouting priority mangle; policy accept; }}",
             f"add chain inet {TABLE} pre {{ type filter hook prerouting priority mangle; policy accept; }}"]
    for chain in ("post", "pre"):
        prefix = f"add rule inet {TABLE} {chain} "
        for field in (("daddr",) if chain == "post" else ("saddr",)):
            lines += [prefix + f"ip {field} {private4} return", prefix + f"ip6 {field} {private6} return"]
        for field in ("sport", "dport"):
            lines += [prefix + f"tcp {field} {{ 22, 47984, 47989, 47990, 48010 }} return",
                      prefix + f"udp {field} {{ 47998-48000, 48002, 48010 }} return"]
        lines += [prefix + f"meta mark & {MARK} != 0 return"]
    for protocol, ports in (("tcp", tcp), ("udp", udp)):
        lines.append(f'add rule inet {TABLE} post oifname "{interface}" {protocol} dport {port_set(ports)} queue num {QUEUE} bypass')
    lines.append(f'add rule inet {TABLE} pre iifname "{interface}" tcp sport {port_set(tcp)} tcp flags & (syn|ack) == (syn|ack) queue num {QUEUE} bypass')
    return "\n".join(lines) + "\n"


def scoped_arguments(profiles, domains_file):
    """Keep upstream DPI parameters, replace destination scope with GUI domains.

    nfqws positive hostlists are additive, so merely appending a GUI list would
    still process upstream-wide ipsets and inline domains. Exclusions remain.
    """
    result = []
    for profile in profiles:
        args = shlex.split(profile)
        args = [arg for arg in args if arg != "--new" and not arg.startswith((
            "--hostlist=", "--hostlist-domains=", "--ipset=", "--ipset-ip=", "-ipset="))]
        args = ["-" + arg if arg.startswith("-ipset-exclude=") else arg for arg in args]
        if any(arg.startswith(("--hostlist-auto", "--daemon", "--user", "--uid", "--qnum", "--debug", "--pidfile")) for arg in args):
            raise ValueError("Стратегия требует неподдерживаемого режима")
        if result:
            result.append("--new")
        result.extend([*args, "--hostlist=" + str(domains_file)])
    if not result:
        raise ValueError("В стратегии нет правил")
    return result


class Engine:
    def __init__(self, base=BASE, directory=STATE):
        self.base, self.directory, self.child = base, directory, None

    def running(self):
        return self.child is not None and self.child.poll() is None

    def clear(self):
        # Deleting one owned table is safe when another firewall is installed.
        subprocess.run(["/usr/sbin/nft", "delete", "table", "inet", TABLE],
                       capture_output=True, timeout=5)

    def stop(self):
        self.clear()
        if self.running():
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=3)
        self.child = None

    def start(self, config):
        parsed = command(["/bin/bash", "/opt/raspberry-tv/scripts/zapret-parse.sh",
                          str(strategies(self.base)[config["strategy"]]),
                          str(config["tcp"]).lower(), str(config["udp"]).lower()])
        tcp, udp, *profiles = parsed.decode().rstrip("\0").split("\0")
        domains = self.directory / "domains.txt"
        domains.write_text("\n".join(config["domains"]) + "\n", encoding="ascii")
        domains.chmod(0o644)
        args = scoped_arguments(profiles, domains)
        rules = firewall_rules(tcp, udp, config["interface"])
        try:
            self.child = subprocess.Popen([str(self.base / "nfqws"), "--user=nobody",
                f"--qnum={QUEUE}", f"--dpi-desync-fwmark={MARK}", *args], cwd=self.base / "strategies")
            time.sleep(0.5)
            if not self.running():
                raise ValueError("Движок не принял стратегию. Выбери другую")
            command(["/usr/sbin/nft", "-f", "-"], input=rules.encode())
        except Exception:
            self.stop()
            raise


def probe(domain, interface):
    # DNS is resolved once and pinned in curl. No redirects, proxy or LAN probes.
    started = time.monotonic()
    try:
        lookup = subprocess.run(["getent", "ahosts", domain], capture_output=True, text=True, timeout=4)
        address = lookup.stdout.split()[0] if lookup.returncode == 0 and lookup.stdout.strip() else "0.0.0.0"
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("Локальный адрес исключён")
        resolved = f"[{address}]" if ":" in address else address
        response = subprocess.run(["curl", "--silent", "--output", "/dev/null", "--noproxy", "*",
            "--interface", interface, "--resolve", f"{domain}:443:{resolved}", "--connect-timeout", "3", "--max-time", "5",
            "--range", "0-0", "--max-filesize", "1048576", "--write-out", "%{http_code}", f"https://{domain}/"],
            capture_output=True, text=True, timeout=7)
        code = response.stdout.strip()
        # An HTTP error still proves TLS reached the server; distinguish it in UI.
        ok = len(code) == 3 and code.isdigit() and 100 <= int(code) <= 599 and response.returncode in (0, 63)
        return {"domain": domain, "ok": ok, "http": code, "seconds": round(time.monotonic() - started, 2)}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"domain": domain, "ok": False, "http": "", "seconds": round(time.monotonic() - started, 2)}


class Manager:
    def __init__(self, directory=STATE, engine=None, catalog=None, devices=None):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.engine = engine or Engine(directory=directory)
        self.catalog = catalog or strategies()
        self.devices = devices or interfaces
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.tuning = False
        self.results = []
        self.progress = ""
        self.error = ""
        self.deadline = 0
        path = directory / "state.json"
        self.state = json.loads(path.read_text()) if path.exists() else {
            "active": {"enabled": False, "config": deepcopy(DEFAULTS["zapret"])}, "previous": None, "pending": None}
        # A crash or reboot during a trial always restores the previous state.
        if self.state["pending"]:
            self.state["active"] = self.state["pending"]
            self.state["pending"] = None
            self.save()
        try:
            self.restore(self.state["active"])
        except Exception:
            LOG.exception("Unable to start saved configuration")
            self.engine.stop()
            self.error = "Не удалось запустить сохранённую стратегию. Выбери другую или откати изменение"

    def save(self):
        atomic_json(self.directory / "state.json", self.state)

    def restore(self, snapshot):
        self.engine.stop()
        if snapshot["enabled"]:
            self.engine.start(validated_config(snapshot["config"], self.catalog, self.devices()))

    def status(self):
        return {"running": self.engine.running(), "enabled": self.state["active"]["enabled"],
                "active": deepcopy(self.state["active"]["config"]), "pending": bool(self.state["pending"]),
                "remaining": max(0, int(self.deadline - time.monotonic())), "rollback": bool(self.state["previous"]),
                "error_message": self.error, "tuning": self.tuning, "progress": self.progress,
                "results": deepcopy(self.results), "strategies": sorted(self.catalog), "interfaces": self.devices(),
                "backends": ["nftables"]}

    def rollback(self):
        snapshot = self.state["pending"] or self.state["previous"]
        if snapshot is None:
            raise ValueError("Нет сохранённого изменения")
        self.restore(snapshot)
        self.state.update(active=deepcopy(snapshot), pending=None, previous=None)
        self.deadline = 0
        self.save()

    def apply(self, config, enabled):
        previous = deepcopy(self.state["active"])
        old_backup = deepcopy(self.state["previous"])
        self.state["pending"] = previous
        self.save()  # recovery is durable before touching any packet rules
        self.deadline = time.monotonic() + 60
        try:
            candidate = {"config": config, "enabled": enabled}
            self.restore(candidate)
            self.state.update(active=candidate, previous=previous)
            self.save()
            self.error = ""
        except Exception:
            self.restore(previous)
            self.state.update(active=previous, pending=None, previous=old_backup)
            self.deadline = 0
            self.save()
            raise

    def tick(self):
        with self.lock:
            if self.tuning:
                return
            if self.state["pending"] and time.monotonic() >= self.deadline:
                self.rollback()
                self.error = "Изменение отменено: время подтверждения истекло"
            if self.state["active"]["enabled"] and not self.engine.running():
                self.engine.stop()
                if self.state["pending"]:
                    self.rollback()
                self.error = "Движок остановился. Можно откатить изменение или повторить запуск"

    def handle(self, payload):
        action = payload.get("action")
        with self.lock:
            if action == "status":
                return self.status()
            if action == "cancel":
                self.cancel.set()
                return self.status()
            if self.tuning:
                raise ValueError("Сначала останови подбор стратегии")
            if action == "confirm":
                if not self.state["pending"] or time.monotonic() >= self.deadline:
                    raise ValueError("Время подтверждения истекло")
                self.state["pending"] = None
                self.deadline = 0
                self.save()
            elif action == "rollback":
                self.rollback()
                self.error = ""
            elif action in ("apply", "enable", "disable", "tune"):
                if self.state["pending"]:
                    raise ValueError("Подтверди или отмени предыдущее изменение")
                config = (deepcopy(self.state["active"]["config"]) if action == "disable" else
                          validated_config(payload.get("config", self.state["active"]["config"]), self.catalog, self.devices()))
                if action == "tune":
                    if len(config["domains"]) > 10:
                        raise ValueError("Для подбора оставь не больше 10 доменов")
                    self.state["pending"] = deepcopy(self.state["active"])
                    self.save()
                    self.cancel.clear()
                    self.tuning, self.results = True, []
                    threading.Thread(target=self.tune, args=(config,), daemon=True).start()
                else:
                    self.apply(config, action != "disable")
            else:
                raise ValueError("Неизвестная операция")
            return self.status()

    def tune(self, config):
        try:
            for index, strategy in enumerate(self.catalog):
                if self.cancel.is_set():
                    break
                candidate = {**config, "strategy": strategy}
                with self.lock:
                    self.progress = f"{index + 1}/{len(self.catalog)} · {strategy}"
                    try:
                        self.restore({"enabled": True, "config": candidate})
                    except Exception as exc:
                        self.results.append({"strategy": strategy, "passed": 0, "total": len(config["domains"]), "error": str(exc), "checks": []})
                        continue
                checks = []
                for domain in config["domains"]:
                    if self.cancel.is_set():
                        break
                    checks.append(probe(domain, config["interface"]))
                with self.lock:
                    self.results.append({"strategy": strategy, "passed": sum(c["ok"] for c in checks),
                                         "total": len(config["domains"]), "checks": checks})
        except Exception:
            LOG.exception("Tuning failed")
            self.error = "Подбор прерван из-за ошибки"
        finally:
            with self.lock:
                try:
                    self.restore(self.state["pending"])
                    self.state["pending"] = None
                    self.save()
                except Exception:
                    self.engine.stop()
                    LOG.exception("Unable to restore after tuning")
                    self.error = "Не удалось вернуть прежнюю стратегию. Обработка остановлена"
                self.tuning = False
                self.progress = "Подбор остановлен" if self.cancel.is_set() else "Проверка завершена"


def main():
    logging.basicConfig(level=logging.INFO)
    if os.geteuid() != 0:
        raise SystemExit("Run through the installed system service")
    manager = Manager()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5)
            try:
                raw = self.rfile.readline(MAX_MESSAGE + 1)
                if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                    raise ValueError("Слишком большой запрос")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("Некорректный запрос")
                result = manager.handle(payload)
            except Exception as exc:
                LOG.exception("Control request failed")
                result = {"error": str(exc) if isinstance(exc, ValueError) else "Ошибка сетевой службы"}
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    sock = Path(SOCKET)
    sock.unlink(missing_ok=True)
    with Server(str(sock), Handler) as server:
        sock.chmod(0o660)
        server.timeout = 0.5
        stopped = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stopped.set())
        try:
            while not stopped.is_set():
                server.handle_request()
                manager.tick()
        finally:
            manager.cancel.set()
            # systemd terminates the whole service cgroup; pending persists.
            with manager.lock:
                manager.engine.stop()


if __name__ == "__main__":
    main()
