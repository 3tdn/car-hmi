"""Supervise the backend and restart only on its intentional restart exit code (75).

Use this launcher on hosts without systemd, including Render. A config restart
replaces only the child process, preserving its filesystem, arguments and environment.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import suppress


def main(argv: Sequence[str] | None = None) -> int:
    command = [
        sys.executable, "-m", "src.core.runner", *(argv if argv is not None else sys.argv[1:])
    ]
    child: subprocess.Popen[bytes] | None = None
    stopping = False

    def forward_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            with suppress(ProcessLookupError):
                child.send_signal(signum)

    previous_handlers = {
        signum: signal.signal(signum, forward_signal)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        while not stopping:
            try:
                child = subprocess.Popen(command)  # noqa: S603 -- fixed interpreter/module, no shell
            except OSError as exc:
                print(f"Unable to start CAN-HMI runner: {exc}", file=sys.stderr, flush=True)
                return 1

            # A termination signal may arrive between the loop condition and spawn.
            if stopping:
                with suppress(ProcessLookupError):
                    child.terminate()
            exit_code = child.wait()
            child = None
            if stopping:
                return 0
            if exit_code != 75:
                return exit_code if exit_code >= 0 else 128 - exit_code

            print("Backend restart requested (exit code 75); restarting in 1 second.", flush=True)
            time.sleep(1)
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
