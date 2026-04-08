#!/usr/bin/env python3
"""Pi sender: capture frames and send as numeric bytes over TCP.

Protocol (network byte order):
- magic: 4 bytes (PBX1)
- frame_id: uint32
- width: uint16
- height: uint16
- timestamp: float64 (epoch seconds)
- payload_size: uint32
- payload: JPEG bytes
"""

from __future__ import annotations

import argparse
import socket
import struct
import time
from io import BytesIO

try:
    import cv2
except ImportError:
    cv2 = None

from PIL import Image
from picamera2 import Picamera2

MAGIC = b"PBX1"
HEADER_FMT = "!4sIHHdI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send camera frames as numeric bytes to PC")
    p.add_argument("--server-host", default="100.110.201.13", help="PC receiver IP")
    p.add_argument("--server-port", type=int, default=9000, help="PC receiver TCP port")
    p.add_argument("--width", type=int, default=960)
    p.add_argument("--height", type=int, default=540)
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--jpeg-quality", type=int, default=70)
    p.add_argument("--camera-id", type=int, default=0)
    p.add_argument("--list-cameras", action="store_true", help="List detected cameras and exit")
    p.add_argument("--reconnect-sec", type=float, default=2.0)
    return p.parse_args()


def connect_loop(host: str, port: int, reconnect_sec: float) -> socket.socket:
    while True:
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print(f"[INFO] Connected to {host}:{port}")
            return s
        except OSError as exc:
            print(f"[WARN] connect failed: {exc}; retry in {reconnect_sec:.1f}s")
            time.sleep(reconnect_sec)


def encode_jpeg(frame, quality: int) -> bytes | None:
    if cv2 is not None:
        ok, enc = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), quality],
        )
        return enc.tobytes() if ok else None

    # Picamera2 frame is BGR; Pillow expects RGB.
    rgb = frame[:, :, ::-1]
    buf = BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def main() -> int:
    args = parse_args()

    camera_info = Picamera2.global_camera_info()
    if args.list_cameras:
        if not camera_info:
            print("[ERROR] No cameras detected by Picamera2/libcamera")
            print("[HINT] Check camera cable, power, and camera interface settings")
            return 1

        for idx, info in enumerate(camera_info):
            model = info.get("Model", "unknown")
            cam_num = info.get("Num", idx)
            print(f"[{idx}] Num={cam_num} Model={model}")
        return 0

    if not camera_info:
        print("[ERROR] No cameras detected by Picamera2/libcamera")
        print("[HINT] Try: python3 send_numeric_tcp.py --list-cameras")
        print("[HINT] Also verify camera cable and that camera support is enabled")
        return 2

    if args.camera_id < 0 or args.camera_id >= len(camera_info):
        print(
            f"[ERROR] Invalid --camera-id {args.camera_id}; "
            f"detected camera indices are 0..{len(camera_info) - 1}"
        )
        print("[HINT] Run with --list-cameras to inspect detected devices")
        return 2

    cam = Picamera2(args.camera_id)
    frame_dur = int(1_000_000 / max(1, args.fps))
    cfg = cam.create_video_configuration(
        main={"size": (args.width, args.height), "format": "BGR888"},
        controls={"FrameDurationLimits": (frame_dur, frame_dur)},
    )
    cam.configure(cfg)
    cam.start()

    sock = connect_loop(args.server_host, args.server_port, args.reconnect_sec)
    frame_id = 0
    sent = 0
    stat_t0 = time.time()

    try:
        while True:
            frame = cam.capture_array()
            payload = encode_jpeg(frame, args.jpeg_quality)
            if payload is None:
                continue

            frame_id += 1
            ts = time.time()
            header = struct.pack(
                HEADER_FMT,
                MAGIC,
                frame_id,
                frame.shape[1],
                frame.shape[0],
                ts,
                len(payload),
            )

            try:
                sock.sendall(header)
                sock.sendall(payload)
                sent += 1
            except OSError:
                try:
                    sock.close()
                except OSError:
                    pass
                sock = connect_loop(args.server_host, args.server_port, args.reconnect_sec)
                continue

            if sent % 60 == 0:
                dt = max(1e-6, time.time() - stat_t0)
                print(f"[INFO] sent={sent} fps={sent / dt:.2f} size={len(payload)}B")

    except KeyboardInterrupt:
        print("\n[INFO] stopped")
    finally:
        try:
            sock.close()
        except OSError:
            pass
        cam.stop()
        cam.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
