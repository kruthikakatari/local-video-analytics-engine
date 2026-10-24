"""
Fire / smoke detection (separate, replaceable YOLO model).

Any YOLO model trained on fire/smoke can be used: set FIRE_MODEL in `.env`
to a `.pt` (Ultralytics) or `.onnx` file. Class names are read from the model
and mapped by name:  "fire" / "flame" -> fire,  "smoke" -> smoke.

If no model is configured (or the file is missing) the rest of the system keeps
running and the dashboard shows "Fire model: NOT CONFIGURED".
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .object_detector import Detection, ModelNotAvailable, YoloModel

log = logging.getLogger(__name__)

NOT_CONFIGURED = "not_configured"
LOADED = "loaded"
ERROR = "error"


def canonical_class(name: str) -> str | None:
    n = name.lower()
    if "smoke" in n:
        return "smoke"
    if "fire" in n or "flame" in n:
        return "fire"
    return None


class FireDetector:
    def __init__(self, model_path: Path | None, backend: str = "auto", img_size: int = 640,
                 confidence: float = 0.5):
        self.confidence = confidence
        self.model: YoloModel | None = None
        self.status = NOT_CONFIGURED
        self.message = "FIRE_MODEL not set"
        self.model_name = None

        if model_path is None:
            return
        if not Path(model_path).exists():
            self.message = f"{Path(model_path).name} not found in models/"
            log.warning("Fire/smoke model not configured: %s", self.message)
            return
        try:
            self.model = YoloModel(model_path, backend, img_size)
        except (ModelNotAvailable, Exception) as exc:  # never crash the engine
            self.status, self.message = ERROR, str(exc)
            log.error("Fire/smoke model failed to load: %s", exc)
            return

        self.model_name = Path(model_path).name
        self._class_map = {n: canonical_class(n) for n in self.model.names.values()}
        usable = sorted({c for c in self._class_map.values() if c})
        if not usable:
            self.status = ERROR
            self.message = f"{self.model_name} has no fire/smoke classes ({', '.join(list(self.model.names.values())[:5])}...)"
            log.error(self.message)
            self.model = None
            return
        self.status = LOADED
        self.message = f"{self.model_name}: detects {', '.join(usable)}"
        log.info("Fire/smoke model loaded: %s", self.message)

    @property
    def available(self) -> bool:
        return self.model is not None

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self.model is None:
            return []
        out = []
        for d in self.model.predict(frame, conf=self.confidence):
            kind = self._class_map.get(d.class_name)
            if kind:
                out.append(Detection(d.box, d.confidence, kind))
        return out
