"""Detector wrappers: Ultralytics path (mocked, no PyTorch needed), ONNX path, missing models."""
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from analytics.fire_detector import ERROR, LOADED, NOT_CONFIGURED, FireDetector
from analytics.object_detector import ModelNotAvailable, ObjectDetector, YoloModel

ROOT = Path(__file__).resolve().parent.parent


class _T:  # mimics a torch tensor: .cpu().numpy()
    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


def fake_ultralytics(monkeypatch, names, boxes):
    class Boxes:
        xyxy = _T([b[0] for b in boxes])
        conf = _T([b[1] for b in boxes])
        cls = _T([b[2] for b in boxes])

    class Result:
        pass

    class YOLO:
        def __init__(self, path):
            if "missing" in path:
                raise FileNotFoundError(path)
            self.names = names

        def predict(self, frame, **kw):
            r = Result()
            r.boxes = Boxes()
            return [r]

    mod = types.ModuleType("ultralytics")
    mod.YOLO = YOLO
    monkeypatch.setitem(sys.modules, "ultralytics", mod)


def test_ultralytics_backend_filters_classes(monkeypatch, tmp_path):
    names = {0: "person", 2: "car", 56: "chair"}
    fake_ultralytics(monkeypatch, names, [((0, 0, 10, 20), 0.9, 0), ((5, 5, 50, 40), 0.7, 2),
                                          ((1, 1, 2, 2), 0.8, 56)])
    pt = tmp_path / "yolov8n.pt"
    pt.write_bytes(b"x")
    det = ObjectDetector(pt)
    assert det.model.backend == "ultralytics"
    out = det.detect(np.zeros((100, 100, 3), np.uint8))
    assert [(d.class_name, round(d.confidence, 2)) for d in out] == [("person", 0.9), ("car", 0.7)]


def test_fire_detector_maps_class_names(monkeypatch, tmp_path):
    fake_ultralytics(monkeypatch, {0: "Fire", 1: "smoke", 2: "other"},
                     [((0, 0, 9, 9), 0.9, 0), ((0, 0, 9, 9), 0.6, 1), ((0, 0, 9, 9), 0.7, 2)])
    pt = tmp_path / "fire_smoke.pt"
    pt.write_bytes(b"x")
    fd = FireDetector(pt)
    assert fd.status == LOADED
    assert sorted(d.class_name for d in fd.detect(np.zeros((20, 20, 3), np.uint8))) == ["fire", "smoke"]


def test_fire_model_missing_is_not_configured(tmp_path):
    fd = FireDetector(tmp_path / "nope.pt")
    assert fd.status == NOT_CONFIGURED and not fd.available
    assert fd.detect(np.zeros((10, 10, 3), np.uint8)) == []
    assert FireDetector(None).status == NOT_CONFIGURED


def test_fire_model_without_fire_classes_is_error(monkeypatch, tmp_path):
    fake_ultralytics(monkeypatch, {0: "person"}, [])
    pt = tmp_path / "wrong.pt"
    pt.write_bytes(b"x")
    assert FireDetector(pt).status == ERROR


def test_missing_yolo_model_raises_cleanly(monkeypatch, tmp_path):
    fake_ultralytics(monkeypatch, {}, [])
    with pytest.raises(ModelNotAvailable):
        YoloModel(tmp_path / "missing.pt")
    with pytest.raises(ModelNotAvailable):
        YoloModel(tmp_path / "missing.onnx")


@pytest.mark.skipif(not (ROOT / "models" / "yolov8n.onnx").exists(), reason="yolov8n.onnx not present")
def test_onnx_detects_person_in_demo_video():
    import cv2
    video = ROOT / "videos" / "people-detection.mp4"
    if not video.exists():
        pytest.skip("demo video not present")
    det = ObjectDetector(ROOT / "models" / "yolov8n.onnx")
    cap = cv2.VideoCapture(str(video))
    found = False
    for _ in range(300):
        ok, frame = cap.read()
        if not ok:
            break
        if any(d.class_name == "person" and d.confidence > 0.5 for d in det.detect(frame)):
            found = True
            break
    assert found
