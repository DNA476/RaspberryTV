"""Fetch pinned upstream assets on a development PC; no privileged execution."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "adapter": "https://codeload.github.com/Sergeydigl3/zapret-discord-youtube-linux/tar.gz/69db771b527ba11ca71efe28728a7f9491eec352",
    "strategies": "https://codeload.github.com/Flowseal/zapret-discord-youtube/tar.gz/ef19845a801e4e743f7bdfdbd58f9745c6adbd60",
    "engine": "https://github.com/bol-van/zapret/releases/download/v72.9/zapret-v72.9.tar.gz",
}


def main():
    output = ROOT / ".run/zapret-assets"
    output.mkdir(parents=True, exist_ok=True)
    lock_path = ROOT / "deploy/zapret-sources.json"
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}

    def fetch(item):
        name, url = item
        path = output / (name + ".tar.gz")
        if not path.exists():
            with urlopen(url, timeout=120) as response:
                path.write_bytes(response.read())
        digest = sha256(path.read_bytes()).hexdigest()
        if lock and lock.get(name) != {"url": url, "sha256": digest}:
            raise ValueError(f"Pinned archive mismatch: {name}")
        return name, {"url": url, "sha256": digest}

    with ThreadPoolExecutor(max_workers=3) as pool:
        sources = dict(pool.map(fetch, SOURCES.items()))
    if not lock:
        lock_path.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    files = {}
    for name in SOURCES:
        with tarfile.open(output / (name + ".tar.gz")) as archive:
            for member in archive.getmembers():
                parts = Path(member.name).as_posix().split("/")[1:]
                if not member.isfile() or ".." in parts or not parts:
                    continue
                relative = "/".join(parts)
                if name == "adapter":
                    keep = relative.startswith(("src/", "custom-strategies/")) or relative in ("README.md", "service.sh", "LICENSE")
                    target = "adapter/" + relative
                elif name == "strategies":
                    keep = relative.startswith("lists/") or (relative.startswith("bin/") and relative.endswith(".bin")) or ("/" not in relative and relative.endswith(".bat") and relative.startswith(("general", "discord"))) or relative.startswith("LICENSE")
                    target = "strategies/" + relative
                else:
                    keep = relative == "binaries/linux-arm64/nfqws" or relative in ("docs/readme.en.md", "docs/readme.md", "LICENSE.txt")
                    target = "nfqws" if relative.endswith("/nfqws") else "engine/" + relative
                if keep:
                    files[target] = archive.extractfile(member).read()
    if "nfqws" not in files or "adapter/src/lib/common.sh" not in files or "strategies/general.bat" not in files:
        raise ValueError("Upstream archive layout changed")
    for name in ("list-general-user.txt", "list-exclude-user.txt", "ipset-exclude-user.txt", "ipset-exclude.txt"):
        files.setdefault("strategies/lists/" + name, b"")
    files["SOURCES.json"] = (json.dumps(sources, indent=2) + "\n").encode()
    files["SHA256SUMS"] = ("\n".join(f"{sha256(data).hexdigest()}  {path}" for path, data in sorted(files.items())) + "\n").encode()
    bundle = output / "raspberry-tv-zapret-assets.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        for path, data in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o755 if path == "nfqws" else 0o644, 1700000000
            archive.addfile(info, io.BytesIO(data))
    print(json.dumps(sources, indent=2))
    print(f"{bundle}: {len(files)} files, {bundle.stat().st_size} bytes")


if __name__ == "__main__":
    main()
