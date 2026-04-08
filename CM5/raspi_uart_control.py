#!/usr/bin/env python3
"""UART control script for Raspberry Pi CM5 -> ESP32-S3.

CM5 side (40-pin header mapping):
- Pin 8  / GPIO14 / UART0_TXD
- Pin 10 / GPIO15 / UART0_RXD

ESP32-S3 side (as requested):
- IO18 = RX  (connect from Pi GPIO14 TX)
- IO17 = TX  (connect to   Pi GPIO15 RX)
- GND  <-> GND
"""

import argparse
import json
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import serial
except ImportError:
    print("Missing dependency: pyserial")
    print("Install with: pip install pyserial")
    sys.exit(1)


DEFAULT_PORT = "/dev/serial0"
DEFAULT_BAUD = 115200

VALID_COMMANDS = {
    "forward",
    "backward",
    "stop",
    "left",
    "right",
    "center",
}


class UartController:
    def __init__(self, port: str, baud: int, timeout: float = 0.2):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._lock = threading.Lock()
        self._ser = None

    def open(self) -> None:
        self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout)

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()

    def send(self, cmd: str, read_seconds: float = 0.3):
        cmd = cmd.strip()
        if not cmd:
            return []

        with self._lock:
            if not self._ser or not self._ser.is_open:
                raise serial.SerialException("UART is not open")

            self._ser.write((cmd + "\n").encode("utf-8"))
            self._ser.flush()
            return self._read_lines_locked(read_seconds)

    def _read_lines_locked(self, duration: float):
        lines = []
        deadline = time.monotonic() + max(0.0, duration)
        while time.monotonic() < deadline:
            line = self._ser.readline().decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)
        return lines


def write_command(ser: serial.Serial, cmd: str) -> None:
    cmd = cmd.strip()
    if not cmd:
        return

    if cmd.startswith("speed:"):
        pass
    elif cmd not in VALID_COMMANDS:
        print(f"warning: unknown command '{cmd}', sending anyway")

    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    print(f"-> {cmd}")


def read_lines(ser: serial.Serial, duration: float) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if line:
            print(f"<- {line}")


def run_api_server(controller: UartController, host: str, port: int, default_read_seconds: float) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return None

        def do_GET(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path == "/":
                body = {
                    "service": "petbox-uart-api",
                    "health": "/health",
                    "command": {
                        "methods": ["POST", "GET"],
                        "path": "/command",
                        "json": {"cmd": "forward", "read_seconds": 0.8},
                        "query": "/command?cmd=forward&read_seconds=0.8",
                    },
                }
                self._send_json(HTTPStatus.OK, body)
                return

            if parsed.path == "/health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return

            if parsed.path == "/command":
                qs = parse_qs(parsed.query)
                cmd = (qs.get("cmd", [""])[0] or "").strip()
                read_seconds_raw = qs.get("read_seconds", [default_read_seconds])[0]

                if not cmd:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": "cmd is required",
                            "example": "/command?cmd=forward&read_seconds=0.8",
                        },
                    )
                    return

                try:
                    read_seconds = float(read_seconds_raw)
                except (TypeError, ValueError):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "read_seconds must be a number"})
                    return

                if not cmd.startswith("speed:") and cmd not in VALID_COMMANDS:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": "invalid command",
                            "allowed": sorted(list(VALID_COMMANDS)) + ["speed:<-255..255>"],
                        },
                    )
                    return

                try:
                    lines = controller.send(cmd, read_seconds=read_seconds)
                except serial.SerialException as exc:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"uart error: {exc}"})
                    return

                payload = {"sent": cmd, "responses": lines, "method": "GET"}
                if not lines:
                    payload["warning"] = "no response from esp; check ESP TX->Pi RX wiring (IO17->GPIO15), GND, and baud"
                self._send_json(HTTPStatus.OK, payload)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/command":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            data = self._read_json_body()
            if not isinstance(data, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                return

            cmd = str(data.get("cmd", "")).strip()
            read_seconds = data.get("read_seconds", default_read_seconds)

            try:
                read_seconds = float(read_seconds)
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "read_seconds must be a number"})
                return

            if not cmd:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "cmd is required"})
                return

            if not cmd.startswith("speed:") and cmd not in VALID_COMMANDS:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "invalid command",
                        "allowed": sorted(list(VALID_COMMANDS)) + ["speed:<-255..255>"],
                    },
                )
                return

            try:
                lines = controller.send(cmd, read_seconds=read_seconds)
            except serial.SerialException as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"uart error: {exc}"})
                return

            payload = {"sent": cmd, "responses": lines}
            if not lines:
                payload["warning"] = "no response from esp; check ESP TX->Pi RX wiring (IO17->GPIO15), GND, and baud"
            self._send_json(HTTPStatus.OK, payload)

        def log_message(self, fmt: str, *args) -> None:
            # Keep logs concise for service mode.
            print(f"[api] {self.address_string()} - {fmt % args}")

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"API server listening on http://{host}:{port}")
    print("POST /command with JSON: {\"cmd\": \"forward\"}")
    print("GET  /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def run_interactive(ser: serial.Serial) -> None:
    print("Interactive mode. Type commands and press Enter.")
    print("Examples: forward, stop, left, right, center, speed:180")
    print("Type 'quit' or 'exit' to end.")

    while True:
        try:
            cmd = input("uart> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if cmd.lower() in {"quit", "exit"}:
            break

        write_command(ser, cmd)
        read_lines(ser, 0.3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send commands from Raspberry Pi to ESP32-S3 over UART.")
    parser.add_argument("--port", default=DEFAULT_PORT, help="UART device (default: /dev/serial0)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baudrate (default: 115200)")
    parser.add_argument("--cmd", help="Single command to send (example: forward)")
    parser.add_argument("--read-seconds", type=float, default=2.0, help="How long to read responses after --cmd")
    parser.add_argument("--api", action="store_true", help="Run as HTTP API server")
    parser.add_argument("--host", default="0.0.0.0", help="API listen host (default: 0.0.0.0)")
    parser.add_argument("--api-port", type=int, default=8080, help="API listen port (default: 8080)")
    args = parser.parse_args()

    controller = UartController(args.port, args.baud)

    try:
        controller.open()
        print(f"Opened {args.port} @ {args.baud}")

        if args.api:
            run_api_server(controller, args.host, args.api_port, default_read_seconds=args.read_seconds)
        else:
            # CLI mode keeps compatibility with the original script behavior.
            ser = controller._ser
            if args.cmd:
                write_command(ser, args.cmd)
                read_lines(ser, max(0.0, args.read_seconds))
            else:
                run_interactive(ser)
    except serial.SerialException as exc:
        print(f"Cannot open UART: {exc}")
        return 1
    finally:
        controller.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
