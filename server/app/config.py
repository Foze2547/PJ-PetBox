from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    source_mode: str = os.getenv("SOURCE_MODE", "stream_url").lower()
    stream_url: str = os.getenv(
        "STREAM_URL",
        "udp://0.0.0.0:5000?overrun_nonfatal=1&fifo_size=5000000",
    )
    numeric_bind_host: str = os.getenv("NUMERIC_BIND_HOST", "0.0.0.0")
    numeric_bind_port: int = int(os.getenv("NUMERIC_BIND_PORT", "9000"))
    jpeg_quality: int = int(os.getenv("JPEG_QUALITY", "80"))
    inference_every_n_frames: int = int(os.getenv("INFERENCE_EVERY_N_FRAMES", "2"))
    detector_backend: str = os.getenv("DETECTOR_BACKEND", "dummy").lower()
    yolo_model_path: str = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")
    yolo_confidence: float = float(os.getenv("YOLO_CONFIDENCE", "0.35"))
    yolo_device: str = os.getenv("YOLO_DEVICE", "auto").lower()
    max_fps: float = float(os.getenv("MAX_OUTPUT_FPS", "12"))
    swap_rb: bool = os.getenv("SWAP_RB", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


settings = Settings()
