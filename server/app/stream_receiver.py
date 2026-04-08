from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Optional, Protocol

import cv2
import numpy as np


class FrameReceiver(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def get_latest_frame(self) -> Optional[np.ndarray]: ...


class StreamReceiver:
    def __init__(self, stream_url: str) -> None:
        self.stream_url = stream_url
        self._capture: Optional[cv2.VideoCapture] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._capture is not None:
            self._capture.release()

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def _open_capture(self) -> cv2.VideoCapture:
        capture = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
        if not capture.isOpened():
            raise RuntimeError(f"Unable to open stream: {self.stream_url}")
        return capture

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._capture = self._open_capture()
                while not self._stop.is_set():
                    ok, frame = self._capture.read()
                    if not ok:
                        time.sleep(0.1)
                        break
                    with self._lock:
                        self._latest_frame = frame
            except Exception:
                time.sleep(1.0)
            finally:
                if self._capture is not None:
                    self._capture.release()
                    self._capture = None


class NumericTcpReceiver:
    MAGIC = b"PBX1"
    HEADER_FMT = "!4sIHHdI"
    HEADER_SIZE = struct.calcsize(HEADER_FMT)

    def __init__(self, bind_host: str, bind_port: int) -> None:
        self.bind_host = bind_host
        self.bind_port = bind_port
        self._server: Optional[socket.socket] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n and not self._stop.is_set():
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("peer closed")
            buf.extend(chunk)
        if len(buf) != n:
            raise ConnectionError("receive interrupted")
        return bytes(buf)

    def _decode_jpeg(self, payload: bytes) -> np.ndarray:
        arr = np.frombuffer(payload, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("invalid jpeg payload")
        return frame

    def _open_server(self) -> socket.socket:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.bind_host, self.bind_port))
        server.listen(1)
        server.settimeout(1.0)
        return server

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._server = self._open_server()
                while not self._stop.is_set():
                    try:
                        conn, _addr = self._server.accept()
                    except TimeoutError:
                        continue
                    except OSError:
                        break

                    with conn:
                        conn.settimeout(5.0)
                        while not self._stop.is_set():
                            header = self._recv_exact(conn, self.HEADER_SIZE)
                            magic, _fid, _w, _h, _ts, payload_size = struct.unpack(
                                self.HEADER_FMT, header
                            )
                            if magic != self.MAGIC:
                                raise ValueError("bad frame magic")
                            payload = self._recv_exact(conn, payload_size)
                            frame = self._decode_jpeg(payload)
                            with self._lock:
                                self._latest_frame = frame
            except Exception:
                time.sleep(1.0)
            finally:
                if self._server is not None:
                    try:
                        self._server.close()
                    except OSError:
                        pass
                    self._server = None
