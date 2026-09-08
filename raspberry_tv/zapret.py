"""Unprivileged client for the fixed-operation local zapret service."""
import json
import socket

from .processes import Unavailable

SOCKET = "/run/raspberry-tv-zapret/control.sock"
MAX_MESSAGE = 262144


def request(action="status", **payload):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(20)
            connection.connect(SOCKET)
            connection.sendall(json.dumps({"action": action, **payload}).encode() + b"\n")
            with connection.makefile("rb") as stream:
                raw = stream.readline(MAX_MESSAGE + 1)
        if len(raw) > MAX_MESSAGE:
            raise ValueError("Слишком большой ответ службы")
        result = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise Unavailable("Служба сетевых правил недоступна") from exc
    if "error" in result:
        raise Unavailable(result["error"])
    return result


def status():
    try:
        value = request()
        label = "Подбор стратегии…" if value["tuning"] else (
            "Работает" if value["running"] else "Ошибка" if value["error_message"] else "Остановлен")
        if value["pending"] and not value["tuning"]:
            label += f" · откат через {value['remaining']} с"
        return {"zapret_status": label, "zapret": value}
    except Unavailable:
        return {"zapret_status": "Служба недоступна", "zapret": {}}
