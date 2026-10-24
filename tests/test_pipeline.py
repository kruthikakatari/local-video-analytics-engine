"""Whole analytics pipeline on the demo video:
file camera -> frame sampling -> YOLO -> ByteTrack -> rules -> Event Manager (fire stubbed)."""
import time
from pathlib import Path

import pytest

from analytics.camera_manager import CameraSource
from analytics.event_manager import EventManager
from analytics.fire_detector import FireDetector
from analytics.object_detector import Detection, ObjectDetector
from analytics.video_processor import CameraProcessor
from config.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
MODEL, VIDEO = ROOT / "models" / "yolov8n.onnx", ROOT / "videos" / "people-detection.mp4"


class StubFire(FireDetector):
    """Pretends a fire is visible so the fire path can be tested without a fire model."""
    def __init__(self):
        super().__init__(None)
        self.status = "loaded"

    @property
    def available(self):
        return True

    def detect(self, frame):
        return [Detection((10, 10, 80, 80), 0.88, "fire")]


@pytest.mark.skipif(not (MODEL.exists() and VIDEO.exists()), reason="demo model/video not present")
def test_pipeline_generates_events(tmp_path):
    s = Settings()
    s.process_fps, s.fire_every_n_frames, s.crowd_threshold = 5, 1, 1
    sent = []
    events = EventManager(sent.append, tmp_path, fire_confirm_frames=2)
    zone = {"id": "z", "name": "Door", "points": [[0.0, 0.4], [0.5, 0.4], [0.5, 1.0], [0.0, 1.0]]}
    src = CameraSource({"id": "cam_t", "name": "Test", "type": "file", "source": str(VIDEO)})
    proc = CameraProcessor(src, ObjectDetector(MODEL), StubFire(), events,
                           get_zones=lambda _: [zone], is_disaster=lambda: True, settings=s)
    src.start()
    proc.start()
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            kinds = {m["event_type"] for m in sent}
            if {"FIRE_DETECTED", "PERSON_DETECTED", "INTRUSION"} <= kinds:
                break
            time.sleep(0.5)
    finally:
        proc.stop()
        src.stop()
    kinds = [m["event_type"] for m in sent]
    assert kinds.count("FIRE_DETECTED") == 1          # continuous fire = one incident
    assert "PERSON_DETECTED" in kinds and "INTRUSION" in kinds
    assert all(m["snapshot_path"] for m in sent if m["action"] == "created")
    assert proc.frames_analysed > 0
