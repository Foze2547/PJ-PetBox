from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .inference import build_detector
from .pipeline import Pipeline
from .stream_receiver import NumericTcpReceiver, StreamReceiver

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if settings.source_mode == "numeric_tcp":
    receiver = NumericTcpReceiver(
        bind_host=settings.numeric_bind_host,
        bind_port=settings.numeric_bind_port,
    )
else:
    receiver = StreamReceiver(settings.stream_url)
detector = build_detector()
pipeline = Pipeline(receiver, detector)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline.start()
    try:
        yield
    finally:
        pipeline.stop()


app = FastAPI(title="Camera AI Streaming", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    packet = pipeline.get_latest_packet()
    active_device = getattr(detector, "device", None)
    return {
        "ok": True,
        "source_mode": settings.source_mode,
        "stream_url": settings.stream_url,
        "numeric_bind_host": settings.numeric_bind_host,
        "numeric_bind_port": settings.numeric_bind_port,
        "detector_backend": settings.detector_backend,
        "yolo_device": settings.yolo_device,
        "active_device": active_device,
        "has_frame": packet is not None,
        "last_frame_id": packet.frame_id if packet else None,
    }


@app.get("/api/detections/latest")
def latest_detections() -> dict:
    packet = pipeline.get_latest_packet()
    return {
        "frame_id": packet.frame_id if packet else None,
        "detections": pipeline.get_latest_detections(),
    }


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    last_sequence = -1
    try:
        while True:
            sequence = pipeline.get_sequence()
            if sequence == last_sequence:
                await asyncio.sleep(0.03)
                continue

            packet = pipeline.get_latest_packet()
            if packet is None:
                await asyncio.sleep(0.03)
                continue

            last_sequence = sequence
            await websocket.send_json(packet.model_dump())
    except WebSocketDisconnect:
        return
