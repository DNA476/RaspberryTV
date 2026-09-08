"""Qt view model. All slow platform work is serialized away from the GUI thread."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from pathlib import Path
import subprocess

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from . import __version__
from .config import Settings, DEFAULTS, PROFILES, normalize_domains, normalize_url
from .linux import LinuxSystem
from .processes import Applications, Unavailable, run
from . import zapret

LOG = logging.getLogger(__name__)
TITLES = {"kodi": "Kodi", "youtube": "YouTube", "moonlight": "Moonlight", "browser": "Браузер"}
SECTIONS = {"devices": "Устройства", "display": "Экран", "network": "Сеть",
            "zapret": "Сетевые правила", "system": "Система"}


def row(title, subtitle="", action="", value="", enabled=True):
    return dict(title=title, subtitle=subtitle, action=action, value=value, enabled=enabled)


class Bridge(QObject):
    changed = Signal()
    rowsChanging = Signal()
    rowsChanged = Signal()
    notice = Signal(str, str)
    navigation = Signal(str)
    surface = Signal(str)
    surfaceChanging = Signal()
    completed = Signal(object, object, object, object)
    controllerEvent = Signal(str)
    controllerStatus = Signal(str)

    def __init__(self, config_dir: Path, preview: bool, parent=None):
        super().__init__(parent)
        self.settings = Settings(config_dir)
        self.preview = preview
        self.platform = None if preview else LinuxSystem()
        self.apps = Applications(config_dir / "state")
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="system")
        self.pending = False
        self.input_reader = None
        self._text_target = None
        self._typing = False
        self.display_change = None
        self._return_page = "home"
        self._state = {"page": "home", "section": "devices", "rows": [], "busy": False,
                       "preview": preview, "version": __version__, "activeApp": "", "running": [],
                       "controller": "Контроллер не подключён", "network": "Предпросмотр" if preview else "Проверка сети…",
                       "wifi": False, "bluetooth": False, "ip": "—", "devices": [], "outputs": [],
                       "zapret_status": "Нужна Raspberry Pi" if preview else "Проверка…", "zapret": {},
                       "editor": {}, "confirm": {}, "countdown": 0, "keyboardAvailable": False,
                       "settings": deepcopy(self.settings.data)}
        self.completed.connect(self._complete)
        self.controllerEvent.connect(self._controller)
        self.controllerStatus.connect(self._controller_status)
        self.poll_timer = QTimer(self, interval=1500)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self.countdown_timer = QTimer(self, interval=1000)
        self.countdown_timer.timeout.connect(self._countdown)
        self._poll_count = 0
        if self.settings.notice:
            QTimer.singleShot(800, lambda: self.notice.emit("Настройки", self.settings.notice))
        if not preview:
            self._job(self.platform.status, self._status, busy=False)

    @Property("QVariantMap", notify=changed)
    def state(self):
        return self._state

    def _update(self, **data):
        if "rows" in data:
            self.rowsChanging.emit()
        self._state.update(data)
        self.changed.emit()
        if "rows" in data:
            self.rowsChanged.emit()

    def _page(self, page):
        if self.input_reader:
            if not self.input_reader.set_capture(page in ("quick", "power") or self._typing):
                self._text_target = None
                self._update(keyboardAvailable=False)
                self.notice.emit("Контроллер", "Не удалось перехватить кнопки геймпада")
        self.surfaceChanging.emit()
        self._update(page=page)
        self.surface.emit(page)

    def _job(self, function, callback=lambda value: None, busy=True, on_error=None):
        if self.pending:
            if busy:
                self.notice.emit("Секунду", "Дождись завершения текущего действия")
            return False
        self.pending = True
        if busy:
            self._update(busy=True)

        def work():
            try:
                self.completed.emit(callback, function(), None, on_error)
            except Exception as exc:
                LOG.exception("System action failed")
                self.completed.emit(callback, None, exc, on_error)
        self.pool.submit(work)
        return True

    @Slot(object, object, object, object)
    def _complete(self, callback, result, error, on_error):
        self.pending = False
        self._update(busy=False)
        if error:
            if on_error:
                on_error()
            message = str(error) if isinstance(error, (Unavailable, ValueError)) else "Не удалось выполнить действие. Попробуй ещё раз"
            self.notice.emit("Не получилось", message)
            return
        try:
            callback(result)
        except Exception:
            LOG.exception("Unable to present system result")
            self.notice.emit("Не получилось", "Не удалось обработать ответ устройства")

    def _status(self, result):
        self._update(**result)
        if self._state["page"] == "settings" and self._state["section"] in SECTIONS:
            self._section(self._state["section"])

    def _poll(self):
        if self.preview or self.pending:
            return
        self._poll_count += 1

        def poll():
            self.apps.discover_windows()
            status = self.platform.status() if self._poll_count % 8 == 0 else None
            if status is None and (self._state["section"].startswith("zapret") or self._state["zapret"].get("pending")):
                status = zapret.status()
            return self.apps.poll(), status

        def result(data):
            exited, status = data
            self._update(running=list(self.apps.running), activeApp=self.apps.active)
            if status:
                self._status(status)
            if self._text_target and any(app_id == self._text_target.app_id for app_id, _ in exited):
                self._text_target = None
                self._update(editor={}, keyboardAvailable=False)
                self._page("home")
            if exited and self._state["page"] == "application" and not self.apps.active:
                self._page("home")
            for app_id, failed in exited:
                if failed:
                    self.notice.emit(f"{TITLES[app_id]} закрылся", "Возвращаю тебя на главный экран")
        self._job(poll, result, busy=False)

    def _saved(self, **changes):
        self.settings.save(**changes)
        self._update(settings=deepcopy(self.settings.data))
        self._section(self._state["section"])
        self.notice.emit("Готово", "Настройки сохранены")

    def _need_pi(self):
        if self.preview:
            raise Unavailable("Эта функция станет доступна на Raspberry Pi")

    @Slot(str, str)
    def action(self, action, value=""):
        try:
            self._action(action, value)
        except (ValueError, Unavailable, OSError) as exc:
            self.notice.emit("Не получилось", str(exc))

    def _action(self, action, value):
        if self._typing:
            if action not in ("home", "back", "menu", "power"):
                return
            self.apps.text_cancelled.set()
            self._typing = False
            self._text_target = None
            self._update(editor={}, keyboardAvailable=False)
            self._page("home")
            return
        if action == "home":
            self._text_target = None
            self._update(keyboardAvailable=False)
            self._update(editor={}, confirm={})
            self._page("home")
        elif action == "section":
            self._section(value)
        elif action == "back":
            if self._state["editor"]:
                self._update(editor={})
            elif self._state["confirm"]:
                self._update(confirm={})
            elif self._state["page"] in ("quick", "power"):
                self._page(self._return_page)
            elif self._state["section"] not in SECTIONS and self._state["page"] == "settings":
                self._section("zapret" if self._state["section"].startswith("zapret") else
                              "devices" if self._state["section"] in ("bluetooth", "phones") else "network")
            else:
                self._page("home")
        elif action in ("menu", "power"):
            if self._state["editor"].get("external"):
                self._update(editor={})
                self._page("quick")
                return
            if self._state["editor"] or self._state["confirm"] or self._state["countdown"]:
                return
            if action == "menu" and self._state["page"] == "application":
                self._text_target = None
                self._update(keyboardAvailable=False)
                def prepare():
                    try:
                        return self.apps.text_target()
                    except Unavailable:
                        return None
                def show_menu(target):
                    if self._state["page"] != "application":
                        return
                    self._text_target = target
                    self._return_page = "application"
                    self._update(keyboardAvailable=target is not None)
                    self._page("quick")
                self._job(prepare, show_menu)
                return
            if self._state["page"] == ("quick" if action == "menu" else "power"):
                self._page(self._return_page)
            else:
                if self._state["page"] not in ("quick", "power"):
                    self._return_page = self._state["page"]
                self._page("quick" if action == "menu" else "power")
        elif action == "launch":
            if value == "settings":
                self._section("devices")
                return
            if value not in TITLES:
                raise ValueError("Неизвестное приложение")
            self._need_pi()

            def launched(_):
                self._update(activeApp=value, running=list(self.apps.running))
                self._page("application")
            def launch():
                self.apps.launch(value, self.settings.data["browser"]["start_url"] if value == "browser" else None)
                self.apps.wait_ready(value)
            self._job(launch, launched)
        elif action == "browser_address":
            if self._state["activeApp"] != "browser":
                raise Unavailable("Сначала открой браузер")
            self._edit("Открыть сайт", "Адрес сайта · откроется в новой вкладке", action, "", submit="Открыть")
        elif action == "keyboard":
            self._need_pi()
            if not self._text_target or not self._state["keyboardAvailable"]:
                raise Unavailable("Выбери поле в приложении и открой Options заново")
            self._edit("Клавиатура · " + TITLES[self._text_target.app_id],
                       "Текст появится в выбранном поле приложения", "external_text", "",
                       external=True, submit="Вставить")
        elif action == "editor_send_enter":
            if self._state["editor"].get("external"):
                self._send_text(value, enter=True)
        elif action == "browser_home":
            self._edit("Стартовая страница браузера", "Открывается при новом запуске браузера", action,
                       self.settings.data["browser"]["start_url"])
        elif action in ("browser_back", "browser_forward", "browser_reload"):
            self._browser_action(action.removeprefix("browser_"))
        elif action == "resume":
            if self.apps.active:
                self._job(self.apps.resume, lambda _: self._page("application"))
            else:
                self._page("home")
        elif action in ("close", "minimize"):
            self._need_pi()
            operation = self.apps.close if action == "close" else self.apps.minimize
            self._job(operation, lambda _: (self._update(activeApp=self.apps.active), self._page("home")))
        elif action == "scale":
            values = ["small", "normal", "large"]
            self._saved(scale=values[(values.index(self.settings.data["scale"]) + 1) % 3])
        elif action == "domains":
            self._edit("Список доменов", "Один домен на строку. Без https:// и пути", action,
                       "\n".join(self.settings.data["zapret"]["domains"]), multiline=True)
        elif action == "profile":
            profiles = [*PROFILES, "Пользовательский"]
            draft = deepcopy(self.settings.data["zapret"])
            draft["profile"] = profiles[(profiles.index(draft["profile"]) + 1) % len(profiles)]
            if draft["profile"] in PROFILES:
                draft["domains"] = PROFILES[draft["profile"]]
            self._saved(zapret=draft)
        elif action in ("tcp", "udp"):
            draft = deepcopy(self.settings.data["zapret"])
            draft[action] = not draft[action]
            self._saved(zapret=draft)
        elif action in ("strategy", "interface"):
            self._need_pi()
            key = "strategies" if action == "strategy" else "interfaces"
            self._list("zapret_" + action, [row(item, "Выбрать", "zapret_choose", json.dumps([action, item]))
                                          for item in self._state["zapret"].get(key, [])])
        elif action == "zapret_choose":
            key, item = json.loads(value)
            if key not in ("strategy", "interface"):
                raise ValueError("Неизвестная настройка")
            self._saved(zapret={**self.settings.data["zapret"], key: item})
            self._section("zapret")
        elif action == "editor_save":
            self._save_editor(value)
        elif action == "wifi_scan":
            self._need_pi()
            self._job(self.platform.wifi_scan, lambda items: self._list("wifi", [
                row(n["ssid"], f"Сигнал {n['signal']}% · {n['security'] or 'Открытая сеть'}", "wifi_select", json.dumps(n)) for n in items]))
        elif action == "wifi_select":
            network = json.loads(value)
            self._edit(network["ssid"], "Введи пароль сети", "wifi_connect", "", secret=True, payload=network)
        elif action == "bluetooth_scan":
            self._need_pi()
            self._job(self.platform.bluetooth_scan, lambda items: self._list("bluetooth", [
                row(n["name"], n["address"], "bluetooth_pair", n["address"]) for n in items]))
        elif action in ("bluetooth_pair", "bluetooth_disconnect"):
            self._need_pi()
            operation = "pair" if action.endswith("pair") else "disconnect"
            def bluetooth_done(_):
                self.notice.emit("Устройство", "Подключено" if operation == "pair" else "Отключено")
                self._section("devices")
                self._job(self.platform.status, self._status, busy=False)
            self._job(lambda: self.platform.bluetooth(operation, value), bluetooth_done)
        elif action == "phones":
            self._need_pi()
            self._job(self.platform.phones, lambda items: self._list("phones", [
                row(n["name"], "Подключён · клавиатура и тачпад" if n["paired"] else "Запросить сопряжение",
                    "" if n["paired"] else "phone_pair", n["id"], not n["paired"]) for n in items]))
        elif action == "phone_pair":
            self._need_pi()
            self._job(lambda: self.platform.pair_phone(value), lambda _: self.notice.emit("KDE Connect", "Подтверди сопряжение на телефоне"))
        elif action == "display_modes":
            self._need_pi()
            rows = [row(f"{m['mode']} · {m['rate']} Гц", output["name"], "display_apply",
                        json.dumps([output["name"], m["mode"], m["rate"]]))
                    for output in self._state["outputs"] for m in output["modes"]]
            self._list("modes", rows)
        elif action == "display_apply":
            self._need_pi()
            if self.display_change:
                raise Unavailable("Сначала подтверди или отмени текущий режим")
            self._job(lambda: self.platform.display(*json.loads(value)), self._display_started)
        elif action in ("display_keep", "display_rollback"):
            if self.display_change:
                change = self.display_change
                if self._job(change.confirm if action == "display_keep" else change.rollback,
                             lambda _: self._section("display")):
                    self.countdown_timer.stop()
                    self._update(countdown=0)
                    self.display_change = None
        elif action in ("reset", "reboot", "poweroff"):
            self._update(confirm={"title": {"reset": "Сбросить настройки оболочки?", "reboot": "Перезапустить приставку?",
                                           "poweroff": "Выключить приставку?"}[action],
                                  "body": "Настройки сети и данные приложений сохранятся" if action == "reset" else "Текущие приложения будут закрыты",
                                  "action": action})
        elif action == "confirmed":
            confirmed = self._state["confirm"].get("action")
            self._update(confirm={})
            if confirmed == "reset":
                self._saved(**deepcopy(DEFAULTS))
            elif confirmed in ("reboot", "poweroff"):
                self._need_pi()
                self._job(lambda: self.platform.power(confirmed))
            elif confirmed and confirmed.startswith("zapret_"):
                operation = confirmed.removeprefix("zapret_")
                config = deepcopy(self.settings.data["zapret"])
                self._job(lambda: zapret.request(operation, config=config), self._zapret_result)
        elif action == "refresh":
            self._need_pi()
            self._job(self.platform.status, self._status)
        elif action == "cec":
            self.notice.emit("HDMI-CEC", "Подключение и проверка пульта будут доступны после настройки на Pi")
        elif action == "controller_test":
            self._page("controller")
        elif action in ("zapret_apply", "zapret_toggle", "zapret_tune"):
            self._need_pi()
            if action == "zapret_toggle":
                action = "zapret_disable" if self._state["zapret"].get("enabled") else "zapret_enable"
            self._update(confirm={"title": "Начать подбор стратегий?" if action == "zapret_tune" else "Изменить сетевые правила?",
                                  "body": "Соединения могут прерваться. Прежние правила будут сохранены.", "action": action})
        elif action in ("zapret_confirm", "zapret_rollback", "zapret_cancel"):
            self._need_pi()
            self._job(lambda: zapret.request(action.removeprefix("zapret_")), self._zapret_result)
        elif action == "zapret_results":
            self._list("zapret_results", [row(item["strategy"],
                item.get("error") or f"HTTPS: {item['passed']}/{item['total']} · выбрать в черновик",
                "zapret_choose", json.dumps(["strategy", item["strategy"]]))
                for item in sorted(self._state["zapret"].get("results", []), key=lambda item: -item["passed"])])
        elif action == "updates":
            raise Unavailable("Эта интеграция ещё не подключена. Состояние системы не изменено")

    def _zapret_result(self, result):
        self._status({"zapret": result})
        self._job(zapret.status, self._status, busy=False)

    def _edit(self, title, subtitle, action, text, **extra):
        self._update(editor=dict(title=title, subtitle=subtitle, action=action, text=text, **extra))

    def _browser_action(self, action, value=""):
        self._need_pi()
        if self.pending:
            raise Unavailable("Дождись завершения текущего действия")
        previous_page = self._state["page"]
        editor = self._state["editor"]
        def finished(_):
            if self._state["editor"] is editor:
                self._update(editor={})
        def failed():
            if self._state["page"] == "application":
                self._page(previous_page)
        # Remove the always-on-top menu before the worker activates Chromium.
        self._page("application")
        self._job(lambda: self.apps.browser_action(action, value),
                  finished, on_error=failed)

    def _save_editor(self, value):
        editor = self._state["editor"]
        target = editor.get("action")
        if target == "external_text":
            self._send_text(value)
            return
        elif target == "browser_address":
            url = normalize_url(value)
            self._browser_action("address", url)
            return
        elif target == "browser_home":
            self._saved(browser={"start_url": normalize_url(value)})
        elif target in ("domains", "strategy", "interface"):
            zapret = deepcopy(self.settings.data["zapret"])
            zapret[target] = normalize_domains(value) if target == "domains" else value.strip()
            if target == "domains":
                zapret["profile"] = "Пользовательский"
            self._saved(zapret=zapret)
        elif target == "wifi_connect":
            self._need_pi()
            network = editor["payload"]
            if not self._job(lambda: self.platform.wifi_connect(network["ssid"], network["device"], value),
                             lambda _: self.notice.emit("Сеть", "Подключение установлено")):
                return
        self._update(editor={})

    def _send_text(self, text, enter=False):
        self._need_pi()
        from .text_input import validate_text, InputFailure
        try:
            validate_text(text)
        except InputFailure as exc:
            raise Unavailable(str(exc)) from None
        if self.pending or not self._text_target:
            raise Unavailable("Дождись завершения действия или открой клавиатуру заново")
        if self.input_reader and not self.input_reader.set_capture(True):
            raise Unavailable("Не удалось перехватить кнопки геймпада. Открой клавиатуру заново")
        target = self._text_target
        self.apps.text_cancelled.clear()
        self._typing = True
        self._update(editor={}, keyboardAvailable=False)
        self._page("application")
        def finished(_=None):
            if not self._typing:
                return
            self._typing = False
            self._text_target = None
            if self.input_reader:
                self.input_reader.set_capture(False)
        def failed():
            was_typing = self._typing
            finished()
            if was_typing:
                self._page("quick")
        self._job(lambda: self.apps.insert_text(target, text, enter), finished, on_error=failed)

    def _list(self, section, rows):
        self._update(section=section, rows=rows or [row("Пока ничего не найдено", "Проверь подключение и повтори поиск", enabled=False)])
        self._page("settings")

    def _section(self, section):
        if section not in SECTIONS:
            return
        settings, state = self.settings.data, self._state
        unavailable = "Доступно на Raspberry Pi" if self.preview else ""
        if section == "devices":
            rows = [row("Добавить Bluetooth-устройство", unavailable or "DualSense: удерживай PS + Create", "bluetooth_scan"),
                    row("Телефон · KDE Connect", unavailable or "Открой KDE Connect на телефоне в той же сети", "phones"),
                    row("HDMI-CEC", "Нужна проверка на телевизоре", "cec"),
                    row("Проверить контроллер", state["controller"], "controller_test")]
            for device in state["devices"]:
                if device.get("ready"):
                    subtitle, action = "Подключён · нажми, чтобы отключить", "bluetooth_disconnect"
                elif device.get("connected"):
                    subtitle, action = "Ввод не готов · нажми для сопряжения", "bluetooth_pair"
                else:
                    subtitle, action = "Сохранён · нажми PS и выбери для подключения", "bluetooth_pair"
                rows.append(row(device["name"], subtitle, action, device["address"]))
        elif section == "display":
            scale = {"small": "Маленький", "normal": "Обычный", "large": "Крупный"}[settings["scale"]]
            mode = next((f"{x['current']} · {x['rate']} Гц" for x in state["outputs"] if x["current"]), unavailable or "Экран не найден")
            rows = [row("Размер интерфейса", scale, "scale"), row("Разрешение и частота", mode, "display_modes"),
                    row("Безопасное переключение", "Без подтверждения вернём прежний режим через 20 секунд", enabled=False)]
        elif section == "network":
            rows = [row("Подключение", state["network"], enabled=False), row("IP-адрес", state["ip"], enabled=False),
                    row("Выбрать Wi-Fi", unavailable or "Найти доступные сети", "wifi_scan"),
                    row("Обновить состояние", "Wi-Fi и Ethernet", "refresh")]
        elif section == "zapret":
            z = settings["zapret"]
            runtime = state["zapret"]
            ready = bool(runtime) and not runtime.get("pending") and not runtime.get("tuning")
            rows = [row("Сервис zapret", state["zapret_status"], "zapret_toggle"),
                    row("Профиль", z["profile"], "profile"), row("Домены", f"{len(z['domains'])} в списке · черновик", "domains"),
                    row("Стратегия", z["strategy"], "strategy"), row("Сетевой интерфейс", z["interface"] or "Не выбран", "interface"),
                    row("GameFilter TCP", "Включён" if z["tcp"] else "Выключен", "tcp"),
                    row("GameFilter UDP", "Включён" if z["udp"] else "Выключен", "udp"),
                    row("Firewall", "nftables · доступный backend", enabled=False),
                    row("Применить и включить", "Без подтверждения откат через 60 секунд", "zapret_apply", enabled=ready),
                    row("Откатить изменение", "Вернуть прежнее состояние", "zapret_rollback", enabled=bool(runtime.get("rollback") or runtime.get("pending")) and not runtime.get("tuning")),
                    row("Подобрать стратегию", "Проверить HTTPS доменов · до 10 доменов", "zapret_tune", enabled=ready),
                    row("Область обработки", "Домены и поддомены · локальная сеть исключена", enabled=False),
                    row("GameFilter", "Расширяет порты; ограничение по доменам сохраняется", enabled=False)]
            if runtime.get("pending") and not runtime.get("tuning"):
                rows.insert(0, row("Оставить изменения", f"Автоматический откат через {runtime['remaining']} с", "zapret_confirm"))
            if runtime.get("tuning"):
                rows.insert(0, row("Остановить подбор", runtime.get("progress", ""), "zapret_cancel"))
            if runtime.get("results"):
                rows.append(row("Результаты подбора", runtime.get("progress", ""), "zapret_results"))
            if runtime.get("error_message"):
                rows.insert(0, row("Состояние сети", runtime["error_message"], enabled=False))
        else:
            rows = [row("Raspberry TV", f"Версия {__version__} · " + ("предпросмотр" if self.preview else "Raspberry Pi"), enabled=False),
                    row("Стартовая страница браузера", settings["browser"]["start_url"], "browser_home"),
                    row("Обновления", "Канал обновлений ещё не настроен", enabled=False),
                    row("Перезапустить", "Перезапустить приставку", "reboot"),
                    row("Выключить", "Безопасное завершение работы", "poweroff"),
                    row("Сбросить настройки оболочки", "Сеть и данные приложений сохранятся", "reset")]
        self._update(section=section, rows=rows)
        if state["page"] != "settings":
            self._page("settings")

    def _display_started(self, change):
        self.display_change = change
        self._update(countdown=15)
        self.countdown_timer.start()

    def _countdown(self):
        self._update(countdown=max(0, self._state["countdown"] - 1))
        if self._state["countdown"] == 0:
            self.action("display_rollback")

    @Slot(str)
    def _controller(self, action):
        if self._typing and action not in ("home", "menu", "power", "back"):
            return
        if action in ("home", "menu", "power"):
            self.action(action)
        elif self._state["page"] == "application":
            if self.apps.active in ("youtube", "browser"):
                key = {"accept": "Return", "back": "Escape", "up": "Up", "down": "Down", "left": "Left", "right": "Right"}.get(action)
                if self.apps.active == "browser" and action in ("left", "right"):
                    key = "shift+Tab" if action == "left" else "Tab"
                if key:
                    self._job(lambda: run(["xdotool", "key", "--clearmodifiers", key]), busy=False)
        else:
            self.navigation.emit(action)

    @Slot(str)
    def _controller_status(self, name):
        if name in ("Контроллер отключён", "Перехват геймпада недоступен"):
            self.apps.text_cancelled.set()
            self._typing = False
            self._text_target = None
            self._update(editor={}, keyboardAvailable=False)
            if self.input_reader:
                self.input_reader.set_capture(False)
        self._update(controller=name)
        self.notice.emit("Контроллер", name)

    def shutdown(self):
        self.apps.text_cancelled.set()
        if self.input_reader:
            self.input_reader.set_capture(False)
        self.poll_timer.stop()
        self.countdown_timer.stop()
        self.pool.shutdown(wait=True, cancel_futures=True)
        if self.display_change:
            self.display_change.rollback()
        if not self.preview:
            self.apps.shutdown()
