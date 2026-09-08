"""Restore X11 output after 20 seconds or parent death; survives the GUI process."""

import json
import select
import subprocess
import sys


def main():
    previous = json.loads(sys.argv[1])
    if select.select([sys.stdin], [], [], 20)[0] and sys.stdin.readline().strip() == "keep":
        return
    subprocess.run(previous, timeout=5, check=False)


if __name__ == "__main__":
    main()
