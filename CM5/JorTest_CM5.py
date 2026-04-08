#!/usr/bin/env python3
import argparse
import hashlib
import math
import os
import random
import re
import signal
import socket
import sys
import time
from threading import Event

from PIL import Image, ImageDraw, ImageFont

board = None
digitalio = None
busio = None
ili9341 = None


# =========================
# MQTT defaults (shared with your current setup)
# =========================
DEFAULT_BROKER = "058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud"
DEFAULT_PORT = 8883
DEFAULT_USER = "Fozexe"
DEFAULT_PASS = "MySecurePassword123!"
DEFAULT_TOPIC_MOTOR = "robot/control/motor"
DEFAULT_TOPIC_STEER = "robot/control/steer"
DEFAULT_TOPIC_RELAY1 = "mechcode/relay1/set"
DEFAULT_TOPIC_RELAY2 = "mechcode/relay2/set"

# =========================
# CM5 pin mapping
# =========================
# 31 -> GPIO12 : LCD_RST
# 37 -> GPIO7  : LCD_RS/DC
# 38 -> GPIO11 : LCD_SCLK
# 39 -> GPIO8  : LCD_CS
# 40 -> GPIO9  : LCD_MISO
# 44 -> GPIO10 : LCD_MOSI


def resolve_board_pin(env_key, default_name):
    raw = os.getenv(env_key, default_name)
    name = raw.strip().upper()

    candidates = [name]
    if name.startswith("GPIO") and name[4:].isdigit():
        candidates.append("D" + name[4:])
    elif name.isdigit():
        candidates.append("D" + name)
        candidates.append("GPIO" + name)

    for cand in candidates:
        if hasattr(board, cand):
            return getattr(board, cand), cand

    raise RuntimeError(
        f"Invalid pin '{raw}' from {env_key}. "
        "Use board pin name like D7, D8, D12 or GPIO7"
    )


WIDTH = 320
HEIGHT = 240
ROTATION = 90

# =========================
# Eye parameters
# =========================
EYE_RADIUS = 60
IRIS_RADIUS = 40
PUPIL_RADIUS = 14
EYE_CENTER_Y = 120
LEFT_EYE_X = 80
RIGHT_EYE_X = 225

# Colors
PINK_LIGHT = (255, 0, 255)
PINK_MID = (220, 0, 180)
PURPLE_MID = (145, 0, 145)
PURPLE_DARK = (72, 16, 72)
BLUE_GLOW = (0, 150, 255)
SCLERA_COLOR = (235, 235, 235)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
CYAN = (56, 228, 255)
GREEN = (78, 230, 120)
YELLOW = (255, 210, 58)
RED = (255, 102, 125)
BG_DARK = (8, 8, 14)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class RobotFaceDisplay:
    def __init__(self, args):
        self.args = args
        self.spi = None
        self.display = None
        self.image = None
        self.draw = None
        self.font = ImageFont.load_default()

        self.stop_event = Event()

        self.state = "normal"
        self.state_text = "IDLE"
        self.state_until = 0.0

        self.last_blink_time = time.monotonic()
        self.next_blink_interval = random.uniform(2.0, 5.0)

        self.mqtt = None
        self.client = None
        self.mqtt_connected = False
        self.last_mqtt_retry = 0.0
        self.next_retry_at = 0.0
        self.next_retry_delay = 1.0
        self.retry_max_delay = 30.0
        self.mqtt_loop_started = False

        self.relay1 = "?"
        self.relay2 = "?"

    # -------------------------
    # Setup
    # -------------------------
    def setup_display(self):
        self._load_display_libs()
        tft_rst_pin, tft_rst_name = resolve_board_pin("PETBOX_TFT_RST_PIN", "D12")
        tft_dc_pin, tft_dc_name = resolve_board_pin("PETBOX_TFT_DC_PIN", "D7")
        tft_cs_pin, tft_cs_name = resolve_board_pin("PETBOX_TFT_CS_PIN", "D8")

        print("Eyes - Cute MQTT Face Mode")
        print(f"[INFO] TFT pins: DC={tft_dc_name} RST={tft_rst_name} CS={tft_cs_name}")
        spi = busio.SPI(clock=board.SCLK, MOSI=board.MOSI, MISO=board.MISO)

        try:
            dc = digitalio.DigitalInOut(tft_dc_pin)
        except Exception as exc:
            if "GPIO busy" in str(exc):
                raise RuntimeError(
                    f"LCD DC pin busy ({tft_dc_pin}). ปิดโปรเซสเดิมก่อน: pkill -f JorTest_CM5.py"
                ) from exc
            raise

        try:
            cs = digitalio.DigitalInOut(tft_cs_pin)
        except Exception as exc:
            if "GPIO busy" in str(exc):
                print(f"warning: {tft_cs_pin} busy; using hardware SPI CS fallback.")
                cs = None
            else:
                raise

        try:
            rst = digitalio.DigitalInOut(tft_rst_pin)
        except Exception as exc:
            if "GPIO busy" in str(exc):
                raise RuntimeError(
                    f"LCD RST pin busy ({tft_rst_pin}). มี service อื่นจับจออยู่"
                ) from exc
            raise

        self.display = ili9341.ILI9341(
            spi,
            cs=cs,
            dc=dc,
            rst=rst,
            baudrate=8000000,
            polarity=1,
            phase=1,
            rotation=ROTATION,
        )
        self.display.write(0x21)

        self.image = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
        self.draw = ImageDraw.Draw(self.image)

    def _load_display_libs(self):
        global board, digitalio, busio, ili9341
        if all(x is not None for x in (board, digitalio, busio, ili9341)):
            return
        try:
            import board as _board
            import digitalio as _digitalio
            import busio as _busio
            from adafruit_rgb_display import ili9341 as _ili9341
        except ImportError:
            print("กรุณาติดตั้งไลบรารีก่อน:")
            print("pip install adafruit-circuitpython-rgb-display pillow")
            raise SystemExit(1)
        board = _board
        digitalio = _digitalio
        busio = _busio
        ili9341 = _ili9341

    def setup_mqtt(self):
        try:
            import paho.mqtt.client as mqtt
        except Exception:
            print("missing dependency: paho-mqtt")
            print("install: pip install paho-mqtt")
            return

        self.mqtt = mqtt
        client_id = (self.args.client_id or "").strip() or self._build_default_client_id()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        if self.args.username:
            self.client.username_pw_set(self.args.username, self.args.password or None)
        if self.args.tls:
            self.client.tls_set()
        self.client.reconnect_delay_set(min_delay=1, max_delay=int(self.retry_max_delay))

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        if not self.mqtt_loop_started:
            self.client.loop_start()
            self.mqtt_loop_started = True
        self._try_connect_mqtt()

    def _build_default_client_id(self):
        host = socket.gethostname() or "host"
        raw = f"petbox-face-{host}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        safe = re.sub(r"[^0-9a-zA-Z_-]+", "-", raw.lower())[:32]
        return f"{safe}-{digest}"

    def _try_connect_mqtt(self):
        if self.client is None:
            return
        now = time.monotonic()
        if self.mqtt_connected or now < self.next_retry_at:
            return
        try:
            self.client.connect(self.args.broker, self.args.port, keepalive=30)
            self.last_mqtt_retry = now
        except Exception as exc:
            self.mqtt_connected = False
            self.last_mqtt_retry = now
            self.next_retry_at = now + self.next_retry_delay
            self.next_retry_delay = min(self.next_retry_delay * 2.0, self.retry_max_delay)
            print(f"[WARN] MQTT connect failed: {exc}")

    # -------------------------
    # MQTT callbacks
    # -------------------------
    def _reason_code_value(self, reason_code):
        # paho-mqtt v2 can pass ReasonCode object instead of int.
        try:
            return int(reason_code)
        except Exception:
            pass
        try:
            return int(getattr(reason_code, "value"))
        except Exception:
            pass
        return 255

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties):
        rc = self._reason_code_value(reason_code)
        if rc != 0:
            self.mqtt_connected = False
            print(f"[WARN] MQTT rejected: rc={rc} ({reason_code})")
            return

        self.mqtt_connected = True
        self.next_retry_delay = 1.0
        self.next_retry_at = 0.0
        print("[INFO] MQTT connected")

        client.subscribe(self.args.topic_motor, qos=1)
        client.subscribe(self.args.topic_steer, qos=1)
        client.subscribe(self.args.topic_relay1, qos=1)
        client.subscribe(self.args.topic_relay2, qos=1)

    def _on_disconnect(self, _client, _userdata, _disconnect_flags, reason_code, _properties):
        self.mqtt_connected = False
        rc = self._reason_code_value(reason_code)
        now = time.monotonic()
        self.next_retry_at = now + self.next_retry_delay
        self.next_retry_delay = min(self.next_retry_delay * 2.0, self.retry_max_delay)
        print(f"[WARN] MQTT disconnected: rc={rc} ({reason_code})")

    def _on_message(self, _client, _userdata, msg):
        topic = msg.topic.strip()
        payload = msg.payload.decode("utf-8", errors="ignore").strip().lower()

        if topic == self.args.topic_motor:
            if payload == "forward":
                self.set_state("walk", "EXCITED", duration=2.8)
            elif payload == "backward":
                self.set_state("walk", "EXCITED", duration=2.8)
            elif payload in {"soft_stop", "hard_stop", "stop"}:
                self.set_state("normal", "CALM", duration=1.2)

        elif topic == self.args.topic_steer:
            if payload == "left":
                self.set_state("turn_left", "FOCUSED LEFT", duration=2.0)
            elif payload == "right":
                self.set_state("turn_right", "FOCUSED RIGHT", duration=2.0)
            elif payload == "reset":
                self.set_state("normal", "CALM", duration=1.2)

        elif topic == self.args.topic_relay1:
            self.relay1 = payload.upper()
            if payload in {"on", "1", "true", "turn_on"}:
                self.set_state("light_on", "HAPPY", duration=2.2)
            elif payload in {"off", "0", "false", "turn_off"}:
                self.set_state("light_off", "SLEEPY", duration=2.2)

        elif topic == self.args.topic_relay2:
            self.relay2 = payload.upper()
            if payload in {"on", "1", "true", "turn_on"}:
                self.set_state("light_on", "HAPPY", duration=2.2)
            elif payload in {"off", "0", "false", "turn_off"}:
                self.set_state("light_off", "SLEEPY", duration=2.2)

    # -------------------------
    # Rendering helpers
    # -------------------------
    def set_state(self, new_state, label, duration=2.0):
        self.state = new_state
        self.state_text = label
        # Keep emotion until a new MQTT command arrives.
        self.state_until = 0.0

    def clear(self, color=BLACK):
        w, h = self.image.size
        self.draw.rectangle((0, 0, w, h), fill=color)

    def show(self):
        candidates = [
            self.image,
            self.image.transpose(Image.Transpose.ROTATE_90),
            self.image.transpose(Image.Transpose.ROTATE_270),
        ]
        last_error = None
        for frame in candidates:
            try:
                self.display.image(frame)
                return
            except ValueError as exc:
                if "must not exceed dimensions of display" not in str(exc):
                    raise
                last_error = exc
        raise last_error

    def draw_label_bar(self, text, color):
        # Hidden by request: show only eyes.
        return

    def draw_upper_lid(self, center_x, center_y, lid_h, color=(12, 13, 20)):
        lid_h = clamp(lid_h, 0, EYE_RADIUS * 2)
        self.draw.rounded_rectangle(
            (
                center_x - EYE_RADIUS,
                center_y - EYE_RADIUS,
                center_x + EYE_RADIUS,
                center_y - EYE_RADIUS + lid_h,
            ),
            radius=18,
            fill=color,
        )

    def draw_brow(self, center_x, center_y, tilt=0, color=(18, 18, 24)):
        x0 = center_x - EYE_RADIUS + 4
        x1 = center_x + EYE_RADIUS - 4
        y = center_y - EYE_RADIUS - 12
        self.draw.line((x0, y + tilt, x1, y - tilt), fill=color, width=5)

    def draw_full_eye(self, center_x, center_y, pupil_dx=0, pupil_dy=0, glow=BLUE_GLOW):
        # background
        self.draw.ellipse(
            (
                center_x - (EYE_RADIUS + 5),
                center_y - (EYE_RADIUS + 5),
                center_x + (EYE_RADIUS + 5),
                center_y + (EYE_RADIUS + 5),
            ),
            fill=BLACK,
        )

        # sclera
        self.draw.ellipse(
            (
                center_x - (EYE_RADIUS - 5),
                center_y - (EYE_RADIUS - 5),
                center_x + (EYE_RADIUS - 5),
                center_y + (EYE_RADIUS - 5),
            ),
            fill=SCLERA_COLOR,
        )

        # iris gradient
        for r in range(IRIS_RADIUS, 0, -2):
            if r > IRIS_RADIUS * 0.75:
                color = PURPLE_DARK
            elif r > IRIS_RADIUS * 0.45:
                color = PURPLE_MID
            elif r > IRIS_RADIUS * 0.25:
                color = PINK_MID
            else:
                color = PINK_LIGHT
            self.draw.ellipse((center_x - r, center_y - r, center_x + r, center_y + r), fill=color)

        self.draw.ellipse(
            (
                center_x - IRIS_RADIUS,
                center_y - IRIS_RADIUS,
                center_x + IRIS_RADIUS,
                center_y + IRIS_RADIUS,
            ),
            outline=glow,
            width=2,
        )

        px = center_x + pupil_dx
        py = center_y + pupil_dy
        self.draw.ellipse(
            (
                px - PUPIL_RADIUS,
                py - PUPIL_RADIUS,
                px + PUPIL_RADIUS,
                py + PUPIL_RADIUS,
            ),
            fill=BLACK,
        )

        self.draw.ellipse((center_x - 17, center_y - 17, center_x - 3, center_y - 3), fill=WHITE)
        self.draw.ellipse((center_x - 16, center_y - 16, center_x - 8, center_y - 8), fill=WHITE)

    def draw_normal_face(self, t):
        self.clear(BG_DARK)

        self.draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y)
        self.draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y)
        # gentle blink in calm mode
        if int(t * 2.0) % 13 == 0:
            self.draw_upper_lid(LEFT_EYE_X, EYE_CENTER_Y, 28, color=(10, 10, 18))
            self.draw_upper_lid(RIGHT_EYE_X, EYE_CENTER_Y, 28, color=(10, 10, 18))

    def draw_walk_face(self, t):
        self.clear((8, 20, 14))

        wave = int(8 * math.sin(t * 8.0))
        self.draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y + wave // 3, pupil_dy=-2, glow=GREEN)
        self.draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y - wave // 3, pupil_dy=-2, glow=GREEN)
        self.draw_brow(LEFT_EYE_X, EYE_CENTER_Y, tilt=2, color=(90, 255, 140))
        self.draw_brow(RIGHT_EYE_X, EYE_CENTER_Y, tilt=-2, color=(90, 255, 140))

    def draw_turn_left_face(self, t):
        self.clear((16, 16, 30))

        self.draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y, pupil_dx=-8, glow=YELLOW)
        self.draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y, pupil_dx=-10, glow=YELLOW)
        self.draw_brow(LEFT_EYE_X, EYE_CENTER_Y, tilt=-6, color=(255, 220, 120))
        self.draw_brow(RIGHT_EYE_X, EYE_CENTER_Y, tilt=-6, color=(255, 220, 120))

    def draw_turn_right_face(self, t):
        self.clear((16, 16, 30))

        self.draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y, pupil_dx=10, glow=YELLOW)
        self.draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y, pupil_dx=8, glow=YELLOW)
        self.draw_brow(LEFT_EYE_X, EYE_CENTER_Y, tilt=6, color=(255, 220, 120))
        self.draw_brow(RIGHT_EYE_X, EYE_CENTER_Y, tilt=6, color=(255, 220, 120))

    def draw_light_on_face(self, t):
        self.clear((24, 20, 6))

        self.draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y, glow=(255, 220, 80))
        self.draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y, glow=(255, 220, 80))
        self.draw_upper_lid(LEFT_EYE_X, EYE_CENTER_Y, 16, color=(42, 34, 8))
        self.draw_upper_lid(RIGHT_EYE_X, EYE_CENTER_Y, 16, color=(42, 34, 8))

    def draw_light_off_face(self, t):
        self.clear((8, 10, 20))

        self.draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y, pupil_dy=8, glow=RED)
        self.draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y, pupil_dy=8, glow=RED)

        # sleepy eyelids
        lid_h = 46 + int(3 * math.sin(t * 4.5))
        for cx in (LEFT_EYE_X, RIGHT_EYE_X):
            self.draw_upper_lid(cx, EYE_CENTER_Y, lid_h, color=(14, 16, 24))

    # -------------------------
    # Main loop
    # -------------------------
    def draw_current(self):
        t = time.monotonic()

        if self.state == "walk":
            self.draw_walk_face(t)
        elif self.state == "turn_left":
            self.draw_turn_left_face(t)
        elif self.state == "turn_right":
            self.draw_turn_right_face(t)
        elif self.state == "light_on":
            self.draw_light_on_face(t)
        elif self.state == "light_off":
            self.draw_light_off_face(t)
        else:
            self.draw_normal_face(t)

        self.show()

    def run(self):
        self.setup_display()
        self.setup_mqtt()

        while not self.stop_event.is_set():
            if not self.mqtt_connected:
                self._try_connect_mqtt()

            self.draw_current()
            time.sleep(0.06)

        if self.client is not None:
            try:
                self.client.disconnect()
                if self.mqtt_loop_started:
                    self.client.loop_stop()
            except Exception:
                pass


def acquire_single_instance_lock(lock_path="/tmp/jor_cm5_face.lock"):
    import fcntl

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            holder_pid = os.read(fd, 64).decode("utf-8", errors="ignore").strip()
        except Exception:
            holder_pid = ""
        if holder_pid:
            raise RuntimeError(
                f"JorTest_CM5.py is already running (pid={holder_pid}). "
                f"ปิดก่อนด้วย: kill {holder_pid} หรือ pkill -f JorTest_CM5.py"
            )
        raise RuntimeError("JorTest_CM5.py is already running (lock busy)")
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode("utf-8"))
    return fd


def build_parser():
    p = argparse.ArgumentParser(description="Cute MQTT robot face for CM5 ILI9341")
    p.add_argument("--broker", default=os.getenv("MQTT_BROKER", DEFAULT_BROKER))
    p.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", str(DEFAULT_PORT))))
    p.add_argument("--client-id", default=os.getenv("MQTT_CLIENT_ID_FACE", ""))
    p.add_argument("--username", default=os.getenv("MQTT_USER", DEFAULT_USER))
    p.add_argument("--password", default=os.getenv("MQTT_PASS", DEFAULT_PASS))
    p.add_argument("--topic-motor", default=os.getenv("MQTT_TOPIC_MOTOR", DEFAULT_TOPIC_MOTOR))
    p.add_argument("--topic-steer", default=os.getenv("MQTT_TOPIC_STEER", DEFAULT_TOPIC_STEER))
    p.add_argument("--topic-relay1", default=os.getenv("MQTT_TOPIC_RELAY1", DEFAULT_TOPIC_RELAY1))
    p.add_argument("--topic-relay2", default=os.getenv("MQTT_TOPIC_RELAY2", DEFAULT_TOPIC_RELAY2))
    p.add_argument("--tls", action="store_true", default=True)
    p.add_argument("--no-tls", action="store_false", dest="tls")
    p.add_argument(
        "--single-instance",
        action="store_true",
        dest="single_instance",
        default=True,
        help="Enable lock file guard (default: on)",
    )
    p.add_argument(
        "--no-single-instance",
        action="store_false",
        dest="single_instance",
        help="Disable lock file guard",
    )
    return p


def main():
    args = build_parser().parse_args()
    stop_event = Event()

    if args.single_instance:
        try:
            _lock_fd = acquire_single_instance_lock()
        except RuntimeError as exc:
            print(f"[ERROR] {exc}")
            return 1

    def _handle_signal(signum, _frame):
        print(f"[INFO] signal received: {signum}; stopping")
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    face = RobotFaceDisplay(args)
    face.stop_event = stop_event
    face.run()
    print("[INFO] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
