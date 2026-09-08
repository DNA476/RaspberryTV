"""Own only launcher-started processes; never use shell=True or killall."""

from dataclasses import dataclass
from contextlib import closing
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

class Unavailable(RuntimeError):
    pass


def run(argv: list[str], timeout: float = 12, check: bool = True) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, env={**os.environ, "LC_ALL": "C"}, check=False)
    except FileNotFoundError as exc:
        raise Unavailable(f"Не установлен компонент {Path(argv[0]).name}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unavailable("Устройство не ответило вовремя. Попробуй ещё раз") from exc
    if check and result.returncode:
        # Never include arguments in user-facing errors: they may contain Wi-Fi credentials.
        raise Unavailable(f"Не удалось выполнить действие ({Path(argv[0]).name})")
    return result.stdout.strip()


def app_command(app_id: str, state_dir: Path) -> list[str]:
    if app_id == "youtube":
        url = "https://www.youtube.com/"
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        if not chromium:
            raise Unavailable("Chromium ещё не установлен")
        return [chromium, "--kiosk", "--no-first-run", "--no-default-browser-check",
                "--disable-session-crashed-bubble", "--ozone-platform=x11",
                f"--class=raspberrytv-{app_id}", f"--user-data-dir={state_dir / 'browsers' / app_id}", url]
    executable = {"kodi": "kodi", "moonlight": "moonlight"}.get(app_id)
    if not executable or not shutil.which(executable):
        raise Unavailable("Приложение ещё не установлено")
    return [executable, "--standalone"] if app_id == "kodi" else [executable]


def x11_window_pids(windows: list[str]) -> dict[str, int]:
    """Ask Xorg for the owner when an app omits the _NET_WM_PID property."""
    if not windows:
        return {}
    from Xlib.display import Display
    from Xlib.error import XError
    from Xlib.ext import res

    owners = {}
    with closing(Display()) as display:
        if not display.has_extension(res.extname):
            return owners
        version = display.res_query_version()
        if (version.server_major, version.server_minor) < (1, 2):
            return owners
        for window in windows:
            try:
                reply = display.res_query_client_ids([
                    {"client": int(window, 16), "mask": res.LocalClientPIDMask}])
                for item in reply.ids:
                    if item.spec.mask == res.LocalClientPIDMask and item.value:
                        owners[window] = int(item.value[0])
            except XError:
                # The client may exit between listing windows and querying it.
                continue
    return owners


@dataclass
class RunningApp:
    process: subprocess.Popen
    log: object
    started: float
    window: str = ""
    closing: bool = False


class Applications:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.running: dict[str, RunningApp] = {}
        self.active = ""

    def launch(self, app_id: str) -> None:
        existing = self.running.get(app_id)
        if existing and existing.process.poll() is None:
            self.active = app_id
            self.resume()
            return
        command = app_command(app_id, self.state_dir)
        if app_id == "kodi":
            from .kodi import prepare_cec
            prepare_cec(Path.home() / ".kodi" / "userdata" / "peripheral_data")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log = open(self.state_dir / f"{app_id}.log", "ab", buffering=0)
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True)
        except OSError:
            log.close()
            raise Unavailable("Не удалось запустить приложение")
        self.running[app_id] = RunningApp(process, log, time.monotonic())
        self.active = app_id

    def discover_windows(self) -> None:
        if not self.running:
            return
        rows = run(["wmctrl", "-lp"], check=False).splitlines()
        windows = {}
        for row in rows:
            parts = row.split(None, 4)
            if len(parts) >= 4 and parts[2].isdigit():
                windows[parts[0]] = int(parts[2])
        windows.update(x11_window_pids([window for window, pid in windows.items() if not pid]))
        # Chromium/Kodi can create the window in a child process.
        parents = {}
        for entry in Path("/proc").glob("[0-9]*/stat"):
            try:
                tail = entry.read_text().rsplit(")", 1)[1].split()
                parents[int(entry.parent.name)] = int(tail[1])
            except (OSError, ValueError, IndexError):
                continue
        for app in self.running.values():
            descendants = {app.process.pid}
            for _ in range(12):
                more = {pid for pid, parent in parents.items() if parent in descendants}
                if more.issubset(descendants):
                    break
                descendants |= more
            for window, pid in windows.items():
                if pid in descendants:
                    if app.window != window:
                        app.window = window
                        run(["wmctrl", "-ir", app.window, "-b", "add,fullscreen"], check=False)
                    break

    def resume(self) -> None:
        app = self.running.get(self.active)
        if app and app.window:
            run(["wmctrl", "-ia", app.window])

    def wait_ready(self, app_id: str, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        app = self.running[app_id]
        while time.monotonic() < deadline:
            if app.process.poll() is not None:
                raise Unavailable("Приложение завершилось во время запуска")
            self.discover_windows()
            if app.window:
                return
            time.sleep(0.25)
        self.close(app_id)
        raise Unavailable("Приложение не открыло окно вовремя")

    def minimize(self) -> None:
        app = self.running.get(self.active)
        if app and app.window:
            run(["xdotool", "windowminimize", app.window], check=False)

    def close(self, app_id: str | None = None) -> None:
        app_id = app_id or self.active
        app = self.running.get(app_id)
        if app and app.process.poll() is None:
            app.closing = True
            if app.window:
                run(["wmctrl", "-ic", app.window], check=False)
            try:
                app.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._terminate(app)
        if app_id == self.active:
            self.active = ""

    @staticmethod
    def _terminate(app: RunningApp) -> None:
        try:
            os.killpg(app.process.pid, signal.SIGTERM)
            app.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(app.process.pid, signal.SIGKILL)
            app.process.wait(timeout=3)
        except ProcessLookupError:
            pass

    def poll(self) -> list[tuple[str, bool]]:
        exited = []
        for app_id, app in list(self.running.items()):
            code = app.process.poll()
            if code is not None:
                app.log.close()
                exited.append((app_id, code != 0 and not app.closing))
                del self.running[app_id]
                if self.active == app_id:
                    self.active = ""
        return exited

    def shutdown(self) -> None:
        for app_id in list(self.running):
            self.close(app_id)
        self.poll()
