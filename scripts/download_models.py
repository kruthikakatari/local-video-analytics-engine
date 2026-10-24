"""Run ONCE while connected to the Internet: downloads yolov8n.pt into models/
(and exports an ONNX copy). After this the system runs fully offline."""
import shutil
from pathlib import Path

from ultralytics import YOLO

MODELS = Path(__file__).resolve().parent.parent / "models"
MODELS.mkdir(exist_ok=True)

model = YOLO("yolov8n.pt")                 # downloads to the current folder if missing
src = Path(model.ckpt_path)
dst = MODELS / "yolov8n.pt"
if src.resolve() != dst.resolve():
    shutil.copy(src, dst)
print("Saved", dst)

if not (MODELS / "yolov8n.onnx").exists():
    onnx = YOLO(str(dst)).export(format="onnx", imgsz=640)
    print("Exported", onnx)
