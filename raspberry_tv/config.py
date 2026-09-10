"""Validated settings, atomic writes, and recovery from interrupted/corrupt saves."""

from copy import deepcopy
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlsplit, urlunsplit


DEFAULTS = {
    "version": 1,
    "scale": "normal",
    "cec": True,
    "browser": {"start_url": "https://www.google.com/"},
    "controller": {"home": 316, "menu": 315, "accept": 304, "back": 305,
                   "fullscreen": 307, "escape": 308},
    "zapret": {"profile": "YouTube", "domains": ["youtube.com", "googlevideo.com", "ytimg.com"],
               "strategy": "general.bat", "interface": "", "tcp": False, "udp": False, "backend": "nftables"},
}
PROFILES = {
    "YouTube": ["youtube.com", "googlevideo.com", "ytimg.com"],
    "Discord": ["discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media"],
}


def normalize_url(value: str) -> str:
    """Accept web addresses only; never interpret an address as a command or file."""
    if not isinstance(value, str):
        raise ValueError("Нужен адрес сайта")
    value = value.strip()
    if not value or len(value) > 4096 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("Введи адрес сайта без пробелов")
    if "://" not in value:
        # Permit host:port, but not javascript:, data:, file: or Chromium switches.
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value) and not re.match(r"^[^/:]+:\d+(?:[/#?]|$)", value):
            raise ValueError("Поддерживаются только адреса http:// и https://")
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if parts.scheme not in ("http", "https") or not host or parts.username is not None or parts.password is not None:
            raise ValueError
        if ":" in host:
            host = "[" + str(ipaddress.IPv6Address(host)) + "]"
        else:
            host = host.encode("idna").decode("ascii").lower()
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                      for label in host.rstrip(".").split(".")):
                raise ValueError
        return urlunsplit((parts.scheme, host + (f":{port}" if port is not None else ""),
                           quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                           quote(parts.query, safe="/?%:@!$&'()*+,;=-._~"),
                           quote(parts.fragment, safe="/?%:@!$&'()*+,;=-._~")))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Нужен корректный http:// или https:// адрес без логина и пароля") from exc


def normalize_domains(text: str) -> list[str]:
    result = []
    for raw in re.split(r"[\s,;]+", text.strip()):
        if not raw:
            continue
        try:
            domain = raw.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError(f"Некорректный домен: {raw[:60]}") from exc
        labels = domain.split(".")
        if len(domain) > 253 or len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
        ):
            raise ValueError(f"Нужен домен без https:// и пути: {raw[:60]}")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError("В список нужно добавить домен, а не IP-адрес")
        if domain not in result:
            result.append(domain)
    if len(result) > 500:
        raise ValueError("Можно сохранить не более 500 доменов")
    return result


def validate(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("version", 1) != 1:
        raise ValueError("Неизвестный формат настроек")
    merged = deepcopy(DEFAULTS)
    for key in merged:
        if key in data:
            if isinstance(merged[key], dict):
                if not isinstance(data[key], dict):
                    raise ValueError("Повреждён раздел настроек")
                merged[key].update({k: v for k, v in data[key].items() if k in merged[key]})
            else:
                merged[key] = data[key]
    if merged["scale"] not in ("small", "normal", "large"):
        raise ValueError("Некорректный размер интерфейса")
    if not isinstance(merged["cec"], bool):
        raise ValueError("Некорректная настройка HDMI-CEC")
    merged["browser"]["start_url"] = normalize_url(merged["browser"]["start_url"])
    mappings = merged["controller"]
    if any(type(v) is not int or not 0 <= v <= 767 for v in mappings.values()) or len(set(mappings.values())) != len(mappings):
        raise ValueError("Кнопки контроллера должны быть разными")
    zapret = merged["zapret"]
    if not isinstance(zapret["domains"], list) or not all(isinstance(x, str) for x in zapret["domains"]):
        raise ValueError("Повреждён список доменов")
    zapret["domains"] = normalize_domains("\n".join(zapret["domains"]))
    if zapret["profile"] not in (*PROFILES, "Пользовательский"):
        raise ValueError("Неизвестный профиль")
    if not re.fullmatch(r"[\w .()-]+\.bat", zapret["strategy"], flags=re.ASCII):
        raise ValueError("Некорректное имя стратегии")
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{0,15}", zapret["interface"]):
        raise ValueError("Некорректное имя сетевого интерфейса")
    if any(type(zapret[k]) is not bool for k in ("tcp", "udp")):
        raise ValueError("Некорректные переключатели GameFilter")
    if zapret["backend"] != "nftables":
        raise ValueError("Доступен только nftables")
    return merged


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Settings:
    def __init__(self, directory: Path):
        self.path = directory / "settings.json"
        self.backup = directory / "settings.backup.json"
        self.notice = ""
        self.data = deepcopy(DEFAULTS)
        if self.path.exists():
            for path in (self.path, self.backup):
                try:
                    self.data = validate(json.loads(path.read_text(encoding="utf-8")))
                    if path == self.backup:
                        self.notice = "Настройки восстановлены из резервной копии"
                    break
                except (OSError, ValueError, TypeError, AttributeError):
                    self.notice = "Не удалось прочитать настройки. Используются значения по умолчанию"

    def save(self, **changes) -> None:
        updated = validate({**deepcopy(self.data), **changes})
        atomic_json(self.backup, self.data)
        atomic_json(self.path, updated)
        self.data = updated

    def reset(self) -> None:
        self.save(**deepcopy(DEFAULTS))
