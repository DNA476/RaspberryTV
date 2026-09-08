"""Keep Kodi's CEC shutdown from turning off the entire TV launcher."""

import os
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET


def prepare_cec(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    paths = list(directory.glob("cec_*.xml")) or [directory / "cec_CEC_Adapter.xml"]
    for path in paths:
        try:
            root = ET.parse(path).getroot() if path.exists() else ET.Element("settings")
        except ET.ParseError as exc:
            raise ValueError("Повреждены настройки CEC в Kodi. Восстанови их перед запуском") from exc
        if root.tag != "settings":
            raise ValueError("Неизвестный формат настроек CEC в Kodi")
        changed = False
        # 231 is Kodi's 'None' choice for the list of devices to put in standby.
        for key, value in {"standby_devices": "231", "standby_tv_on_pc_standby": "0",
                           "send_inactive_source": "0"}.items():
            setting = root.find(f"setting[@id='{key}']")
            if setting is None:
                setting = ET.SubElement(root, "setting", id=key)
            if setting.get("value") != value:
                setting.set("value", value)
                changed = True
        if not changed:
            continue
        backup = path.with_suffix(".xml.before-raspberry-tv")
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".cec-", delete=False) as output:
                temporary = Path(output.name)
                ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
