from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import numpy as np

from .config import settings
from .models import Detection

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

try:
    import torch
except ImportError:
    torch = None


class Detector(ABC):
    @abstractmethod
    def detect(self, frame: np.ndarray) -> List[Detection]:
        raise NotImplementedError


class DummyDetector(Detector):
    def detect(self, frame: np.ndarray) -> List[Detection]:
        return []


class YoloDetector(Detector):
    def __init__(self, model_path: str, confidence: float, device: str) -> None:
        if YOLO is None:
            raise RuntimeError("ultralytics is not installed")
        self.model = YOLO(model_path)
        self.confidence = confidence
        self.device = self._resolve_device(device)

    def _resolve_device(self, requested_device: str) -> str:
        normalized = requested_device.strip().lower()
        if normalized in {"", "auto"}:
            if torch is not None and torch.cuda.is_available():
                return "cuda:0"
            return "cpu"

        if normalized.startswith("cuda") and torch is not None and not torch.cuda.is_available():
            # Fallback keeps service alive on machines without CUDA.
            return "cpu"

        return normalized

    def detect(self, frame: np.ndarray) -> List[Detection]:
        height, width = frame.shape[:2]
        results = self.model.predict(
            frame,
            conf=self.confidence,
            verbose=False,
            device=self.device,
        )
        detections: List[Detection] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                cls_id = int(box.cls.item())
                label = names.get(cls_id, str(cls_id))
                conf = float(box.conf.item())
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(
                    Detection(
                        label=label,
                        confidence=conf,
                        x1=max(0.0, min(1.0, x1 / width)),
                        y1=max(0.0, min(1.0, y1 / height)),
                        x2=max(0.0, min(1.0, x2 / width)),
                        y2=max(0.0, min(1.0, y2 / height)),
                    )
                )
        return detections


def build_detector() -> Detector:
    if settings.detector_backend == "yolo":
        return YoloDetector(
            settings.yolo_model_path,
            settings.yolo_confidence,
            settings.yolo_device,
        )
    return DummyDetector()
