"""Fake "checkout-api" for the demo. Standard library only.

* Pushes JSON-ish log lines to Loki's push API once per second.
* Serves Prometheus metrics on :8000 (http_requests_total{app,status}).
* Periodically simulates an incident: a database connection pool is exhausted,
  5xx ratio jumps, and error logs (some containing fake secrets and an email,
  to exercise the assistant's redaction) flood in.

All "secrets" below are obviously fake placeholders.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100").rstrip("/")
EVERY = int(os.environ.get("INCIDENT_EVERY_SECONDS", "360"))
DURATION = int(os.environ.get("INCIDENT_DURATION_SECONDS", "150"))
FIRST = int(os.environ.get("FIRST_INCIDENT_AFTER_SECONDS", "60"))
APP = "checkout-api"
NAMESPACE = "demo"
POD = "checkout-api-6f7c9d8b4f-q8zlm"

_counts: dict[str, int] = {"200": 0, "400": 0, "500": 0, "503": 0}
_lock = threading.Lock()
_started = time.time()


def in_incident() -> bool:
    t = time.time() - _started - FIRST
    return t >= 0 and (t % EVERY) < DURATION


def normal_lines() -> list[tuple[str, str]]:
    out = []
    for _ in range(random.randint(5, 12)):
        ms = random.randint(8, 120)
        order = random.randint(10_000, 99_999)
        out.append(("info", f"POST /api/checkout order={order} status=200 duration={ms}ms"))
        with _lock:
            _counts["200"] += 1
    if random.random() < 0.3:
        out.append(("warn", f"cart {uuid.uuid4()} not found, returning 400"))
        with _lock:
            _counts["400"] += 1
    return out


def incident_lines() -> list[tuple[str, str]]:
    out = normal_lines()[:3]
    for _ in range(random.randint(6, 14)):
        wait = random.randint(4990, 5010)
        out.append(
            (
                "error",
                "PoolTimeoutError: timed out after "
                f"{wait}ms acquiring connection from pool 'payments-db' (active=50 idle=0 max=50)",
            )
        )
        with _lock:
            _counts["503"] += 1
    if random.random() < 0.5:
        out.append(
            (
                "error",
                "payment provider call failed for customer jane.doe@example.com "
                "Authorization: Bearer demo0000fake0000token0000 status=502",
            )
        )
        with _lock:
            _counts["500"] += 1
    if random.random() < 0.2:
        out.append(
            (
                "error",
                "reconnect failed: postgres://checkout:demo-password@payments-db:5432/payments "
                "too many connections for role \"checkout\"",
            )
        )
    return out


def push_loop() -> None:
    while True:
        lines = incident_lines() if in_incident() else normal_lines()
        now = time.time_ns()
        streams: dict[str, list[list[str]]] = {}
        for i, (level, msg) in enumerate(lines):
            streams.setdefault(level, []).append([str(now + i), f"level={level} {msg}"])
        body = {
            "streams": [
                {
                    "stream": {"namespace": NAMESPACE, "app": APP, "pod": POD, "level": level},
                    "values": values,
                }
                for level, values in streams.items()
            ]
        }
        req = urllib.request.Request(
            f"{LOKI_URL}/loki/api/v1/push",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except OSError as exc:  # Loki not up yet
            print(f"push failed: {exc}")
        time.sleep(1)


class Metrics(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        with _lock:
            rows = [
                f'http_requests_total{{app="{APP}",status="{code}"}} {n}'
                for code, n in _counts.items()
            ]
        payload = (
            "# TYPE http_requests_total counter\n"
            + "\n".join(rows)
            + f"\n# TYPE demo_incident_active gauge\ndemo_incident_active {int(in_incident())}\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    threading.Thread(target=push_loop, daemon=True).start()
    print(f"log-generator up; first incident in {FIRST}s, every {EVERY}s for {DURATION}s")
    ThreadingHTTPServer(("0.0.0.0", 8000), Metrics).serve_forever()  # noqa: S104
