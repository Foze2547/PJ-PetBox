from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2

from .config import settings
from .inference import Detector
from .models import Detection, FramePacket
from .stream_receiver import FrameReceiver


@dataclass
class SharedState:
    latest_packet: Optional[FramePacket] = None
    latest_detections: list[Detection] = field(default_factory=list)
    sequence: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class Pipeline:
    def __init__(self, receiver: FrameReceiver, detector: Detector) -> None:
        self.receiver = receiver
        self.detector = detector
        self.state = SharedState()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self.receiver.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.receiver.stop()

    def get_latest_packet(self) -> Optional[FramePacket]:
        with self.state.lock:
            return self.state.latest_packet

    def get_latest_detections(self) -> list[Detection]:
        with self.state.lock:
            return list(self.state.latest_detections)

    def get_sequence(self) -> int:
        with self.state.lock:
            return self.state.sequence

    def _loop(self) -> None:
        last_detections: list[Detection] = []
        frame_id = 0
        last_emit = 0.0

        while not self._stop.is_set():
            frame = self.receiver.get_latest_frame()
            if frame is None:
                time.sleep(0.02)
                continue

            if settings.swap_rb:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            now = time.time()
            if now - last_emit < (1.0 / settings.max_fps):
                time.sleep(0.001)
                continue
            last_emit = now

            frame_id += 1
            if frame_id % max(1, settings.inference_every_n_frames) == 0:
                last_detections = self.detector.detect(frame)

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), settings.jpeg_quality],
            )
            if not ok:
                continue

            packet = FramePacket(
                frame_id=frame_id,
                ts=now,
                width=frame.shape[1],
                height=frame.shape[0],
                jpeg_b64=base64.b64encode(encoded.tobytes()).decode("ascii"),
                detections=last_detections,
            )

            with self.state.lock:
                self.state.latest_packet = packet
                self.state.latest_detections = list(last_detections)
                self.state.sequence += 1
