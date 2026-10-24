"""
YOLO object detection.

`YoloModel` wraps a YOLO model behind one small interface so the rest of the
pipeline never depends on how the model is executed:

* ``.pt`` files   -> Ultralytics (PyTorch)          [default, as in the PPT]
* ``.onnx`` files -> ONNX Runtime (no PyTorch needed, fast on laptop CPUs)

`ObjectDetector` uses a COCO-trained YOLOv8n and keeps only the classes this
project cares about: person, car, motorcycle, bus, truck.
"""
from __future__ import annotations

import ast
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]

PERSON = "person"
VEHICLE_CLASSES = ("car", "motorcycle", "bus", "truck")
TARGET_CLASSES = (PERSON, *VEHICLE_CLASSES)


@dataclass
class Detection:
    """One detected object in pixel coordinates (x1, y1, x2, y2)."""
    box: tuple[float, float, float, float]
    confidence: float
    class_name: str


class ModelNotAvailable(RuntimeError):
    pass


class YoloModel:
    """Runs a YOLO model (Ultralytics .pt or ONNX Runtime .onnx) on BGR frames."""

    def __init__(self, path: Path, backend: str = "auto", img_size: int = 640):
        self.path = Path(path)
        self.img_size = img_size
        self._lock = threading.Lock()  # one inference at a time (shared by all cameras)
        if not self.path.exists() and not (self.path.suffix == ".pt" and backend != "onnxruntime"):
            raise ModelNotAvailable(f"Model file not found: {self.path}")

        if backend == "auto":
            backend = "onnxruntime" if self.path.suffix.lower() == ".onnx" else "ultralytics"
        self.backend = backend

        if backend == "ultralytics":
            self._load_ultralytics()
        elif backend == "onnxruntime":
            self._load_onnx()
        else:
            raise ValueError(f"Unknown DETECTOR_BACKEND '{backend}'")
        log.info("Loaded %s via %s (%d classes)", self.path.name, self.backend, len(self.names))

    # ------------------------------------------------------------------ loading
    def _load_ultralytics(self):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ModelNotAvailable("ultralytics is not installed (pip install ultralytics)") from exc
        if not self.path.exists():
            # Ultralytics can fetch official weights (e.g. yolov8n.pt) during setup,
            # when Internet is still available. At runtime (offline) this fails cleanly.
            log.warning("%s not found locally; asking Ultralytics to download it", self.path.name)
        try:
            self._model = YOLO(str(self.path) if self.path.exists() else self.path.name)
        except Exception as exc:
            raise ModelNotAvailable(f"Could not load {self.path.name}: {exc}") from exc
        names = self._model.names
        self.names = dict(names) if isinstance(names, dict) else dict(enumerate(names))

    def _load_onnx(self):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ModelNotAvailable("onnxruntime is not installed (pip install onnxruntime)") from exc
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                     if p in ort.get_available_providers()]
        self._session = ort.InferenceSession(str(self.path), providers=providers)
        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        if isinstance(inp.shape[2], int):
            self.img_size = inp.shape[2]
        out_shape = self._session.get_outputs()[0].shape  # (1, 4 + num_classes, anchors)
        num_classes = out_shape[1] - 4 if isinstance(out_shape[1], int) else 80
        # Ultralytics stores class names in the ONNX metadata when exporting.
        meta = self._session.get_modelmeta().custom_metadata_map
        if "names" in meta:
            self.names = {int(k): v for k, v in ast.literal_eval(meta["names"]).items()}
        elif num_classes == 80:
            self.names = dict(enumerate(COCO_NAMES))
        else:
            self.names = {i: f"class_{i}" for i in range(num_classes)}

    # ---------------------------------------------------------------- inference
    def predict(self, frame: np.ndarray, conf: float, iou: float = 0.45,
                keep: set[str] | None = None) -> list[Detection]:
        with self._lock:
            if self.backend == "ultralytics":
                dets = self._predict_ultralytics(frame, conf, iou)
            else:
                dets = self._predict_onnx(frame, conf, iou)
        if keep is not None:
            dets = [d for d in dets if d.class_name in keep]
        return dets

    def _predict_ultralytics(self, frame, conf, iou):
        result = self._model.predict(frame, conf=conf, iou=iou, imgsz=self.img_size, verbose=False)[0]
        boxes = result.boxes
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        return [Detection(tuple(map(float, b)), float(c), self.names.get(int(k), str(k)))
                for b, c, k in zip(xyxy, confs, clss)]

    def _predict_onnx(self, frame, conf, iou):
        h, w = frame.shape[:2]
        size = self.img_size
        # Letterbox: resize keeping aspect ratio, pad to size x size.
        scale = min(size / w, size / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = cv2.resize(frame, (nw, nh))
        blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, swapRB=True)

        out = self._session.run(None, {self._input_name: blob})[0][0].T  # (anchors, 4 + C)
        scores_all = out[:, 4:]
        class_ids = scores_all.argmax(axis=1)
        scores = scores_all[np.arange(len(out)), class_ids]
        mask = scores >= conf
        if not mask.any():
            return []
        cx, cy, bw, bh = out[mask, 0], out[mask, 1], out[mask, 2], out[mask, 3]
        scores, class_ids = scores[mask], class_ids[mask]
        x1 = (cx - bw / 2 - pad_x) / scale
        y1 = (cy - bh / 2 - pad_y) / scale
        boxes_xywh = np.stack([x1, y1, bw / scale, bh / scale], axis=1)

        keep = cv2.dnn.NMSBoxesBatched(boxes_xywh.tolist(), scores.tolist(),
                                       class_ids.tolist(), conf, iou)
        dets = []
        for i in np.array(keep).flatten():
            x, y, bw_, bh_ = boxes_xywh[i]
            box = (max(0.0, x), max(0.0, y), min(float(w), x + bw_), min(float(h), y + bh_))
            dets.append(Detection(tuple(map(float, box)), float(scores[i]),
                                  self.names.get(int(class_ids[i]), str(class_ids[i]))))
        return dets


class ObjectDetector:
    """YOLOv8n person + vehicle detector (COCO classes)."""

    def __init__(self, model_path: Path, backend: str = "auto", img_size: int = 640,
                 low_confidence: float = 0.1):
        self.model = YoloModel(model_path, backend, img_size)
        # ByteTrack also uses LOW-confidence boxes (its second association stage),
        # so the detector returns everything above `low_confidence`; the tracker
        # decides which boxes start or continue a track.
        self.low_confidence = low_confidence
        self.keep = set(TARGET_CLASSES)

    @property
    def name(self) -> str:
        return f"{self.model.path.name} ({self.model.backend})"

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return self.model.predict(frame, conf=self.low_confidence, keep=self.keep)
