#!/usr/bin/env python3
"""shepherd-external-watcher — Layer 6 of the GPU-integrity plan.

Closes the "Shepherd watches Shepherd" gap (Forge's design pass): the integrity
canary (Layers 2-5) lives ON the herd. If the host running shepherd-control
itself regresses or stops responding, the canary stops too — and we'd discover
it the same way Garth notices CPU spikes today.

This script runs *off* the herd (operator's machine, a non-cluster-llm peer,
a small container on Phoenix, etc.) and polls shepherd-control's /herd/aggregate
endpoint at a fixed cadence. When the endpoint goes unreachable, stale, or
reports zero healthy peers, exits non-zero so the operator's cron/systemd/etc.
notification path fires (mail to root, push notification, channel post via
gateway script — whatever the operator wires up).

Usage:
    shepherd-external-watcher.py --url http://cluster-llm:40117/herd/aggregate \\
                                  --max-age-s 60 \\
                                  --min-healthy-peers 1

Exit codes:
    0 = herd healthy (aggregate fresh + >= min-healthy-peers reachable)
    1 = aggregate unreachable
    2 = aggregate stale (older than max-age-s)
    3 = too few healthy peers reachable
    4 = configuration / invocation error

Recommended cron pattern (on an off-herd host):

    */5 * * * * /home/operator/ai-stack/scripts/shepherd-external-watcher.py \\
        --url http://100.123.141.125:40117/herd/aggregate \\
        --max-age-s 60 \\
        --min-healthy-peers 1 \\
        >> /tmp/shepherd-external-watcher.log 2>&1 \\
        || echo "[$(date)] shepherd watcher exit=$?" | tee -a /tmp/shepherd-external-watcher.alerts

The `|| echo ...` is the notification hook — replace with `mail root`,
`curl -X POST <slack/etc-webhook>`, or whatever path delivers to the operator.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", required=True, help="shepherd-control /herd/aggregate URL")
    parser.add_argument(
        "--max-age-s", type=int, default=60, help="Max acceptable snapshot age in seconds (default 60)"
    )
    parser.add_argument(
        "--min-healthy-peers", type=int, default=1, help="Minimum reachable peers (default 1)"
    )
    parser.add_argument("--timeout-s", type=float, default=10.0, help="HTTP timeout (default 10s)")
    args = parser.parse_args()

    try:
        req = urllib.request.Request(args.url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=args.timeout_s) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        print(f"[shepherd-watcher] FAIL: aggregate unreachable: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[shepherd-watcher] FAIL: HTTP error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        print(f"[shepherd-watcher] FAIL: aggregate body not JSON: {e}", file=sys.stderr)
        return 1

    # Staleness check
    timestamp = data.get("timestamp")
    if isinstance(timestamp, (int, float)):
        age = time.time() - timestamp
        if age > args.max_age_s:
            print(
                f"[shepherd-watcher] FAIL: aggregate is stale "
                f"(age {int(age)}s > max {args.max_age_s}s) — shepherd-control poll loop may be stuck",
                file=sys.stderr,
            )
            return 2
    else:
        print(
            "[shepherd-watcher] WARN: aggregate has no timestamp field; skipping staleness check",
            file=sys.stderr,
        )

    # Reachable-peer count
    nodes = data.get("nodes", []) or []
    healthy = sum(1 for n in nodes if n.get("reachable") and n.get("data_quality") == "full")
    if healthy < args.min_healthy_peers:
        print(
            f"[shepherd-watcher] FAIL: only {healthy} healthy full peers "
            f"(min {args.min_healthy_peers}); herd is unhealthy",
            file=sys.stderr,
        )
        return 3

    print(f"[shepherd-watcher] OK: {healthy} healthy peers, aggregate fresh ({int(age)}s old)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
