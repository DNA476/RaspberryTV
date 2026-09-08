"""Pair with a live BlueZ agent and verify persisted pairing, not just a link."""

import os
from pathlib import Path
import subprocess
import time

from .processes import run, Unavailable


def device_info(address: str) -> dict[str, str]:
    output = run(["bluetoothctl", "info", address], check=False)
    return dict(line.strip().split(": ", 1) for line in output.splitlines() if ": " in line)


def input_ready(address: str) -> bool:
    for path in Path("/sys/class/input").glob("event*/device/uniq"):
        try:
            if (path.read_text().strip().lower() == address.lower()
                    and (path.parent / "id/bustype").read_text().strip() == "0005"):
                return True
        except OSError:
            continue
    return False


def pair_with_agent(address: str) -> None:
    # BlueZ 5.82 skips --agent registration in one-command noninteractive mode.
    # Keep stdin open and wait for registration before sending the pair command.
    import select

    with subprocess.Popen(["bluetoothctl", "--agent", "NoInputNoOutput"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          bufsize=0, env={**os.environ, "LC_ALL": "C"}) as process:
        def wait_for(expected: bytes, timeout: float):
            deadline = time.monotonic() + timeout
            output = b""
            while time.monotonic() < deadline:
                if not select.select([process.stdout], [], [], min(.5, max(0, deadline - time.monotonic())))[0]:
                    continue
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                output = (output + chunk)[-16384:]
                if b"Failed" in output or b"not available" in output:
                    break
                if expected in output:
                    return
            raise Unavailable("Сопряжение не завершено. Отключи USB, зажми PS + Create до быстрого мигания и повтори")

        try:
            wait_for(b"Agent registered", 8)
            process.stdin.write(f"pair {address}\n".encode("ascii"))
            wait_for(b"Pairing successful", 35)
        finally:
            if process.poll() is None:
                try:
                    process.stdin.write(b"quit\n")
                    process.wait(timeout=2)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
