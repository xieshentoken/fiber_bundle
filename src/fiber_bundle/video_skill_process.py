"""Own a script's process group and stop it when the application's pipe closes."""

import json
import os
import select
import signal
import subprocess
import sys


def main() -> int:
    if os.getpgrp() != os.getpid():
        return 2
    command = json.loads(sys.argv[1])
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
        return 2
    stopped = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    try:
        while child.poll() is None:
            readable, _, _ = select.select([sys.stdin], [], [], 0.1)
            if stopped or (readable and not sys.stdin.buffer.read(1)):
                os.killpg(os.getpid(), signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpid(), signal.SIGKILL)
                return 130
        return int(child.returncode or 0)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
