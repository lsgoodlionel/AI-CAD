"""训练图纸设备检测 YOLO 权重（弱监督自动标注数据集）。

产物：apps/api/data/models/drawing_elements.pt（yolo_detector 约定路径，
存在时自动替代通用 yolov8n 权重）。

用法：
    python scripts/train_yolo_drawings.py <dataset.yaml> [--epochs 30] [--imgsz 960]
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

TARGET = Path(__file__).parents[1] / "data" / "models" / "drawing_elements.pt"


def train(dataset_yaml: Path, epochs: int, imgsz: int) -> None:
    import os

    import torch
    from ultralytics import YOLO

    # MPS 在 torch 2.12 + YOLO TAL 分配器上存在索引越界问题，可用 YOLO_DEVICE 强制指定
    device = os.environ.get("YOLO_DEVICE") or (
        "mps" if torch.backends.mps.is_available() else "cpu"
    )
    print(f"训练设备: {device} | 数据集: {dataset_yaml}")
    model = YOLO("yolov8n.pt")
    results = model.train(
        data=str(dataset_yaml),
        epochs=epochs,
        imgsz=imgsz,
        device=device,
        batch=8,
        patience=10,
        project=str(dataset_yaml.parent / "runs"),
        name="drawing_equipment",
        exist_ok=True,
        verbose=False,
    )
    best = Path(results.save_dir) / "weights" / "best.pt"
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(best, TARGET)
    print(f"训练完成，权重已写入: {TARGET}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=960)
    args = parser.parse_args()
    train(args.dataset, args.epochs, args.imgsz)
