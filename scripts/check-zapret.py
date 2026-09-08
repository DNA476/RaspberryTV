"""Unprivileged ARM validation: upstream parser + real nfqws --dry-run."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from raspberry_tv.zapret_service import firewall_rules, scoped_arguments, strategies

base = Path(sys.argv[1]).resolve()
failed = []
with tempfile.TemporaryDirectory() as directory:
    domains = Path(directory) / "domains.txt"
    domains.write_text("youtube.com\ngooglevideo.com\nytimg.com\n")
    for name, path in strategies(base).items():
        for tcp_flag, udp_flag in (("false", "false"), ("true", "false"), ("false", "true"), ("true", "true")):
            parser = subprocess.run(["bash", str(ROOT / "scripts/zapret-parse.sh"), str(path), tcp_flag, udp_flag],
                capture_output=True, check=True, env={**os.environ, "RASPBERRY_TV_ZAPRET_BASE": str(base)}, timeout=10)
            tcp, udp, *profiles = parser.stdout.decode().rstrip("\0").split("\0")
            firewall_rules(tcp, udp, "wlan0")
            args = scoped_arguments(profiles, domains)
            result = subprocess.run([str(base / "nfqws"), "--dry-run", "--qnum=221", *args],
                                    cwd=base / "strategies", capture_output=True, text=True, timeout=10)
            if result.returncode:
                failed.append((name, tcp_flag, udp_flag))
                print(name, tcp_flag, udp_flag, result.stdout[-1000:], result.stderr[-1000:])
    print(f"{len(strategies(base)) * 4} dry runs, {len(failed)} failures")
sys.exit(bool(failed))
