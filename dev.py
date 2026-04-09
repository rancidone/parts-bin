#!/usr/bin/env -S uv run python
"""
Dev CLI — manage the parts-bin development environment.

Usage:
  ./dev.py [start] [--log-file FILE] [--log-level LEVEL]

Ctrl-C stops both services.
"""

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent


def start(log_file: str | None, log_level: str = "INFO") -> None:
    env = os.environ.copy()
    env["LOG_LEVEL"] = log_level
    if log_file:
        env["LOG_FILE"] = log_file
        print(f"Logging to {log_file} at level {log_level}")
    else:
        print(f"Log level: {log_level}")

    print("Starting API...")
    api = subprocess.Popen(
        ["uv", "run", "uvicorn", "server:app", "--reload"],
        cwd=REPO_ROOT,
        env=env,
        start_new_session=True,
    )

    print("Starting UI...")
    ui = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=REPO_ROOT / "ui",
        start_new_session=True,
    )

    print("API: http://localhost:8000")
    print("UI:  http://0.0.0.0:5173")
    print("Press Ctrl-C to stop.")

    def shutdown(sig, frame):
        print("\nStopping...")
        for proc in (api, ui):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        for proc in (api, ui):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    api.wait()
    ui.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parts Bin dev CLI")
    sub = parser.add_subparsers(dest="cmd")

    for p in (parser, sub.add_parser("start")):
        p.add_argument("--log-file", metavar="FILE", help="Write JSON logs to this file")
        p.add_argument(
            "--log-level",
            metavar="LEVEL",
            default="INFO",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            help="Log level (default: INFO)",
        )

    args = parser.parse_args()
    start(log_file=args.log_file, log_level=args.log_level)


if __name__ == "__main__":
    main()
