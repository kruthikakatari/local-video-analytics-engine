# Models

| File | Purpose | How to get it |
|---|---|---|
| `yolov8n.pt` | Person + vehicle detection (Ultralytics, default) | `python scripts/download_models.py` while you still have Internet |
| `yolov8n.onnx` | Same model for ONNX Runtime (no PyTorch needed, faster on CPU) | Included. Or `yolo export model=models/yolov8n.pt format=onnx` |
| `fire_smoke.pt` / `.onnx` | Fire / smoke detection | Any YOLOv8 fire/smoke model, see below |

Choose the model in `.env`:

```
YOLO_MODEL=models/yolov8n.pt      # or models/yolov8n.onnx
FIRE_MODEL=models/fire_smoke.pt   # any file name; class names are read from the model
```

## Fire / smoke model

The fire detector is a separate, replaceable module (`analytics/fire_detector.py`).
It works with any YOLO model whose class names contain `fire`/`flame` and/or `smoke`
(e.g. classes `["fire", "smoke"]` or `["Fire", "Smoke", "default"]`).

Options:
1. Download a pretrained YOLOv8 fire/smoke model (`best.pt`) from GitHub, Roboflow Universe
   or Kaggle (search "yolov8 fire smoke"), rename it to `fire_smoke.pt`, put it here.
2. Train your own on Google Colab with a public fire/smoke dataset:
   `yolo detect train data=fire_smoke.yaml model=yolov8n.pt epochs=50 imgsz=640`

If the file is missing the app still runs and the dashboard shows
**Fire Model: NOT CONFIGURED**.

Note: YOLOv8 weights are released by Ultralytics under AGPL-3.0.
