"""
Network / performance metrics, computed from real measurements only.

* Latency: from the timestamps stored per event in the `metrics` table.
* MQTT traffic: application-level counters of payload bytes received by the
  backend. These are NOT total network bandwidth (TCP/IP/MQTT headers,
  retransmissions and ACKs are not included) -> use Wireshark for on-wire totals.
* Video traffic: bytes of MJPEG sent to dashboards by this server, so the two
  can be compared side by side.
"""
from __future__ import annotations

import csv
import io
import os
import statistics
import time

try:
    import psutil
except ImportError:  # optional
    psutil = None


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "min": None, "max": None, "median": None, "p95": None}
    v = sorted(values)
    p95 = v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]
    return {"count": len(v), "avg": round(statistics.fmean(v), 2), "min": round(v[0], 2),
            "max": round(v[-1], 2), "median": round(statistics.median(v), 2), "p95": round(p95, 2)}


class MetricsService:
    def __init__(self, db, events, ws, engine, video_counter):
        self.db, self.events, self.ws, self.engine = db, events, ws, engine
        self.video = video_counter
        self._proc = psutil.Process(os.getpid()) if psutil else None
        if self._proc:
            self._proc.cpu_percent(None)  # prime the CPU counters
            psutil.cpu_percent(None)

    def latency(self) -> dict:
        rows = self.db.latency_rows()
        e2e = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
        mqtt = [r["mqtt_latency_ms"] for r in rows if r["mqtt_latency_ms"] is not None]

        def stage(a, b):
            return _stats([r[b] - r[a] for r in rows if r[a] is not None and r[b] is not None])

        return {
            "end_to_end_ms": _stats(e2e),
            "mqtt_ms": _stats(mqtt),
            "stages_ms": {
                "detection_to_publish": stage("detection_timestamp", "mqtt_publish_timestamp"),
                "publish_to_receive": stage("mqtt_publish_timestamp", "mqtt_receive_timestamp"),
                "receive_to_websocket": stage("mqtt_receive_timestamp", "websocket_timestamp"),
                "websocket_to_dashboard": stage("websocket_timestamp", "dashboard_receive_timestamp"),
            },
            "recent": [{"event_id": r["event_id"], "event_type": r["event_type"],
                        "latency_ms": r["latency_ms"] and round(r["latency_ms"], 2),
                        "mqtt_latency_ms": r["mqtt_latency_ms"] and round(r["mqtt_latency_ms"], 2),
                        "payload_bytes": r["payload_bytes"], "transport": r["transport"]}
                       for r in rows[:20]],
        }

    def summary(self) -> dict:
        cams = self.engine.cameras() if self.engine else []
        video_elapsed = max(1.0, time.time() - self.video["since"])
        resources = None
        if self._proc:
            mem = self._proc.memory_info().rss
            resources = {"process_cpu_percent": round(self._proc.cpu_percent(None), 1),
                         "system_cpu_percent": round(psutil.cpu_percent(None), 1),
                         "process_ram_mb": round(mem / 1024 / 1024, 1),
                         "system_ram_percent": round(psutil.virtual_memory().percent, 1),
                         "cpu_cores": psutil.cpu_count()}
        return {
            "generated_at": time.time(),
            "events_processed": self.db.count_events(),
            "events_by_type": self.db.event_counts(),
            "events_per_minute": self.events.events_per_minute(),
            "fallback_events": self.events.fallback_events,
            "mqtt": self.events.mqtt_totals(),
            "websocket": {"clients": len(self.ws.clients), "messages_sent": self.ws.messages_sent,
                          "bytes_sent": self.ws.bytes_sent},
            "video_mjpeg": {"bytes_sent": self.video["bytes"], "frames_sent": self.video["frames"],
                            "bytes_per_second": round(self.video["bytes"] / video_elapsed, 1),
                            "active_streams": self.video["active"]},
            "latency": self.latency(),
            "cameras": [{"id": c["id"], "name": c["name"], "status": c["status"],
                         "capture_fps": c["capture_fps"], "inference_fps": c["inference_fps"],
                         "inference_ms": c["inference_ms"], "people": c["people"],
                         "vehicles": c["vehicles"], "resolution": c["resolution"]} for c in cams],
            "cameras_online": sum(1 for c in cams if c["status"] == "ONLINE"),
            "cameras_total": len(cams),
            "resources": resources,
            "note": "MQTT/video byte counts are application payload counters, not total "
                    "network bandwidth. Use Wireshark for on-the-wire measurements.",
        }

    def csv_export(self) -> str:
        buf = io.StringIO()
        cols = ["event_id", "event_type", "camera_id", "transport", "payload_bytes",
                "detection_timestamp", "mqtt_publish_timestamp", "mqtt_receive_timestamp",
                "websocket_timestamp", "dashboard_receive_timestamp", "mqtt_latency_ms", "latency_ms"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in reversed(self.db.latency_rows()):
            w.writerow(r)
        return buf.getvalue()
