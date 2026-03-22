#!/usr/bin/env python3
"""CLI helper to update processor settings in config/bus.yaml and optionally POST to running API."""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    import requests
except Exception:  # pragma: no cover - optional dependency
    requests = None

from src.core.config_manager import update_processor_config


def main() -> None:
    p = argparse.ArgumentParser(prog="set-processor-config")
    p.add_argument("--config", "-c", default="config/bus.yaml")
    p.add_argument("--max-queue-size", type=int, help="Max queue size for processor")
    p.add_argument("--queue-policy", choices=["drop_oldest", "block", "reject"])
    p.add_argument(
        "--apply-http",
        help="Optional HTTP endpoint to POST the new processor config (e.g. http://localhost:8000/config/processor)",
    )
    args = p.parse_args()

    cfg_path = Path(args.config)
    updated = update_processor_config(max_queue_size=args.max_queue_size, queue_policy=args.queue_policy, path=cfg_path)
    print("Updated config written to", str(cfg_path))
    if args.apply_http:
        if requests is None:
            print("requests library not available; cannot POST to API (pip install requests)")
            return
        try:
            payload = {"max_queue_size": args.max_queue_size, "queue_policy": args.queue_policy}
            resp = requests.post(args.apply_http, json=payload, timeout=5)
            print("Apply HTTP response:", resp.status_code, resp.text)
        except Exception as exc:
            print("Failed to POST to running API:", exc)


if __name__ == "__main__":
    main()
