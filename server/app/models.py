from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class Detection(BaseModel):
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)


class FramePacket(BaseModel):
    frame_id: int
    ts: float
    width: int
    height: int
    jpeg_b64: str
    detections: List[Detection]
