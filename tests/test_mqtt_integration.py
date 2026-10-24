"""End-to-end MQTT test through a real Mosquitto broker (skipped if none is running)."""
import json
import socket
import threading
import time

import pytest

from mqtt.mqtt_client import MqttClient


def broker_up(host="localhost", port=1883):
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not broker_up(), reason="Mosquitto not running on localhost:1883")
def test_publish_subscribe_roundtrip():
    got, done = [], threading.Event()
    sub = MqttClient("test-sub", "localhost", 1883)
    sub.subscribe("surveillance/test/events", lambda t, p, ts: (got.append((json.loads(p), ts)), done.set()))
    pub = MqttClient("test-pub", "localhost", 1883)
    sub.start()
    pub.start()
    deadline = time.time() + 5
    while not (sub.connected and pub.connected) and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)  # let the SUBSCRIBE reach the broker
    sent_at = time.time()
    size = pub.publish("surveillance/test/events", {"event_id": "evt_x", "event_type": "INTRUSION"}, qos=1)
    assert size > 0 and size < 100
    assert done.wait(5)
    payload, received_at = got[0]
    assert payload["event_id"] == "evt_x"
    assert (received_at - sent_at) * 1000 < 1000   # delivered over the broker in < 1 s
    pub.stop()
    sub.stop()
