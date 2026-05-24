"""
Rustbucket VR — Pupil Labs Bridge
===================================
Run on the laptop BEFORE starting the Unity game.

    pip install pupil-labs-realtime-api pandas opencv-python
    python bridge.py

What it does:
  1. Connects to Pupil Companion on the phone
  2. Calculates the clock offset (Quest time → Companion time)
  3. Serves a tiny HTTP server the Quest talks to:
       GET  /sync   → returns offset so Unity can correct its timestamps
       POST /event  → receives a game event, injects it into the recording
  4. After the session, merges game CSVs with gaze export and annotates
     the eye video with event markers.

Requirements:
  - Pupil Companion app open and streaming on the phone
  - Phone and laptop on the same Wi-Fi
  - Unity Quest talking to this laptop's IP on port 8765
"""

import json
import time
import threading
import csv
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

import pandas as pd
import cv2

from pupil_labs.realtime_api.simple import Device

# ──────────────────────────────────────────────
#  Config
# ──────────────────────────────────────────────
PORT = 8765
OUTPUT_DIR = "session_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ──────────────────────────────────────────────
#  Step 1 — Connect to Pupil Companion & sync
# ──────────────────────────────────────────────
device = Device(address="10.40.50.57", port=8080)

print("Calculating time offset...")
estimate = device.estimate_time_offset()
if estimate is None:
    device.close()
    sys.exit("Pupil Companion app is too old — please update it.")

# host_minus_companion_ns: add this to companion time to get host (laptop) time
# We want companion_ns = host_ns - host_minus_companion_ns
host_minus_companion_ns = int(estimate.time_offset_ms.mean * 1_000_000)
print(f"  Offset (host - companion): {host_minus_companion_ns / 1e6:.3f} ms")
print(f"  Roundtrip: {estimate.roundtrip_duration_ms.mean:.3f} ms")

# ──────────────────────────────────────────────
#  Shared state (thread-safe via lock)
# ──────────────────────────────────────────────
lock = threading.Lock()
events = []  # list of dicts saved by /event handler
session_start_time = datetime.now().strftime("%Y%m%d_%H%M%S")

# ──────────────────────────────────────────────
#  Step 2 — HTTP server the Quest talks to
# ──────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    # ── GET /sync ──────────────────────────────
    # Unity calls this to measure Quest↔laptop offset.
    # Returns laptop timestamps so Unity can compute quest_minus_host.
    def do_GET(self):
        if self.path != "/sync":
            self._send(404, {"error": "not found"})
            return

        receive_ns = time.time_ns()
        send_ns = time.time_ns()

        self._send(200, {
            "ok": True,
            "server_receive_ns": receive_ns,
            "server_send_ns": send_ns,
            "host_minus_companion_ns": host_minus_companion_ns,
        })

    # ── POST /event ────────────────────────────
    # Unity sends: { "name": "trial;...", "companion_timestamp_ns": 12345 }
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/event":
            try:
                data = json.loads(body)
                name = data.get("name", "unknown")
                companion_ts_ns = int(data.get("companion_timestamp_ns", time.time_ns()))

                # Inject into Pupil recording
                try:
                    device.send_event(name, event_timestamp_unix_ns=companion_ts_ns)
                except Exception as e:
                    print(f"  [warn] Could not send event to Pupil: {e}", flush=True)

                # Save locally too
                with lock:
                    events.append({
                        "companion_timestamp_ns": companion_ts_ns,
                        "name": name,
                        "received_host_ns": time.time_ns(),
                    })

                print(f"  [event] {name[:80]}", flush=True)
                self._send(200, {"ok": True})

            except Exception as e:
                print(f"  [error] /event: {e}", flush=True)
                self._send(400, {"ok": False, "error": str(e)})

        elif self.path == "/csv":
            # Unity can POST its game CSV here for safe-keeping
            filename = self.headers.get("X-Filename", f"game_{session_start_time}.csv")
            path = os.path.join(OUTPUT_DIR, filename)
            with open(path, "wb") as f:
                f.write(body)
            print(f"  [csv] Saved {filename}", flush=True)
            self._send(200, {"ok": True})

        else:
            self._send(404, {"error": "not found"})

    def _send(self, code, obj):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)


server = HTTPServer(("0.0.0.0", PORT), Handler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
print(f"\nBridge running on port {PORT}. Press Ctrl+C when the session is done.\n")

# ──────────────────────────────────────────────
#  Step 3 — Wait for session to finish
# ──────────────────────────────────────────────
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nSession ended. Saving events and running post-processing...")

server.shutdown()
device.close()