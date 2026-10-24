"""
Start the whole system:  python run.py

  * checks that the Mosquitto broker is reachable
  * starts the FastAPI server (which starts the AI engine, cameras and MQTT clients)
  * prints the dashboard URLs for this laptop and for phones on the same Wi-Fi
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys

from config.settings import BASE_DIR, settings


def broker_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Local Video Analytics & Security Engine")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--demo", action="store_true",
                        help="enable the demo-video camera (DEMO MODE) in addition to the others")
    args = parser.parse_args()

    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
                        datefmt="%H:%M:%S")
    os.chdir(BASE_DIR)

    if args.demo:
        settings.force_demo = True  # enables "type": "file" cameras for this run only

    print("=" * 66)
    print("  LOCAL VIDEO ANALYTICS & SECURITY ENGINE")
    print("  Offline Edge Surveillance & Disaster Management")
    print("=" * 66)
    if broker_reachable(settings.mqtt_host, settings.mqtt_port):
        print(f"  MQTT broker      : OK  ({settings.mqtt_host}:{settings.mqtt_port})")
    else:
        print(f"  MQTT broker      : NOT REACHABLE at {settings.mqtt_host}:{settings.mqtt_port}")
        print("                     Start Mosquitto (see README). The app keeps running and")
        print("                     retries; events use a local fallback until then.")
    from backend.context import lan_addresses
    print(f"  Dashboard (here) : http://localhost:{args.port}")
    for ip in lan_addresses():
        print(f"  Dashboard (LAN)  : http://{ip}:{args.port}")
    print(f"  API docs         : http://localhost:{args.port}/docs")
    print("=" * 66)

    import uvicorn
    uvicorn.run("backend.main:app", host=args.host, port=args.port,
                log_level="warning", ws_ping_interval=20)


if __name__ == "__main__":
    sys.exit(main())
