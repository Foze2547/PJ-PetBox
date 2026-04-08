#!/usr/bin/env python3
import os
import time
import json
import queue
import socket
import struct
import threading
import hashlib
import logging
import subprocess
import random
import math
import ssl
import re
import cv2

# Third-party imports
import paho.mqtt.client as mqtt
import pygame
import speech_recognition as sr
from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
import cv2

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

# --- Hardware / GPIO Constants ---
GPIO_SPEAKER_ENABLE = "45"
LCD_RST_PIN = "D12"
LCD_DC_PIN = "D7"
LCD_CS_PIN = "D8"

# --- Network / PC Constants ---
PC_HOST = os.getenv("PC_HOST", "100.110.201.13")
IMAGE_PORT = 9000
AUDIO_PORT = 9001
RECONNECT_SEC = 2.0

# --- MQTT Constants ---
MQTT_BROKER = os.getenv("MQTT_BROKER", "058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER = os.getenv("MQTT_USER", "pikub")
MQTT_PASS = os.getenv("MQTT_PASS", "Password123!")

# --- Display Constants ---
WIDTH, HEIGHT = 320, 240
ROTATION = 90
EYE_RADIUS = 60
IRIS_RADIUS = 40
PUPIL_RADIUS = 14
EYE_CENTER_Y = 120
LEFT_EYE_X = 80
RIGHT_EYE_X = 225

# Colors
BLACK, WHITE = (0, 0, 0), (255, 255, 255)
BG_DARK = (8, 8, 14)
BLUE_GLOW = (0, 150, 255)
PURPLE_DARK, PURPLE_MID = (72, 16, 72), (145, 0, 145)
PINK_MID, PINK_LIGHT = (220, 0, 180), (255, 0, 255)
SCLERA_COLOR = (235, 235, 235)
GREEN, YELLOW, RED = (78, 230, 120), (255, 210, 58), (255, 102, 125)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("PetboxDaemon")

# --- Hardware Setup ---
def setup_speaker_hw(enable=True):
    try:
        val = "dh" if enable else "dl"
        subprocess.run(["pinctrl", "set", GPIO_SPEAKER_ENABLE, "op", val], check=True)
        logger.info(f"Speaker amplifier {'enabled' if enable else 'disabled'}")
    except Exception as e:
        logger.warning(f"Could not configure GPIO {GPIO_SPEAKER_ENABLE}: {e}")

# --- Shared State ---
class PetboxState:
    def __init__(self):
        self.eye_state = "normal"
        self.eye_label = "IDLE"
        self.device_states = {
            "motor": "stop",
            "steer": "reset",
            "relay1": "off",
            "relay2": "off"
        }
        self.running = True
        self.tts_queue = queue.Queue()
        self.lock = threading.Lock()

    def set_eye_state(self, state, label=""):
        with self.lock:
            self.eye_state = state
            if label: self.eye_label = label
            logger.debug(f"State Change: {state} ({label})")

    def stop(self):
        self.running = False


# --- Threads ---

class DisplayThread(threading.Thread):
    def __init__(self, state):
        super().__init__(name="DisplayThread", daemon=True)
        self.state = state
        self.display = None
        self.image = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
        self.draw = ImageDraw.Draw(self.image)

    def _init_lcd(self):
        try:
            import board, digitalio, busio
            from adafruit_rgb_display import ili9341

            def resolve_pin(name):
                if name.startswith("D"): return getattr(board, name)
                return getattr(board, f"D{name}")

            spi = busio.SPI(clock=board.SCLK, MOSI=board.MOSI, MISO=board.MISO)
            
            tft_dc_pin = resolve_pin(LCD_DC_PIN)
            tft_cs_pin = resolve_pin(LCD_CS_PIN)
            tft_rst_pin = resolve_pin(LCD_RST_PIN)

            try:
                dc = digitalio.DigitalInOut(tft_dc_pin)
            except Exception as e:
                if "busy" in str(e).lower():
                    logger.warning(f"DC pin {LCD_DC_PIN} busy. Forcing...")
                    # Possible to use gpiod/lgpio to reclaim? For now, re-raise or assume fatal
                raise e

            try:
                cs = digitalio.DigitalInOut(tft_cs_pin)
            except Exception as e:
                if "busy" in str(e).lower():
                    logger.warning(f"CS pin {LCD_CS_PIN} busy; using hardware SPI CS fallback.")
                    cs = None
                else: raise e

            try:
                rst = digitalio.DigitalInOut(tft_rst_pin)
            except Exception as e:
                if "busy" in str(e).lower():
                    logger.warning(f"RST pin {LCD_RST_PIN} busy.")
                    rst = None # Try without reset if busy
                else: raise e

            self.display = ili9341.ILI9341(
                spi, cs=cs, dc=dc, rst=rst,
                baudrate=24000000, rotation=ROTATION
            )
            self.display.write(0x21) # Invert display
            logger.info("✅ Display Initialized")
        except Exception as e:
            logger.error(f"LCD Hardware Init failed: {e}. Face will not be shown.")
            self.display = None

    def run(self):
        try:
            self._init_lcd()
        except Exception as e:
            logger.error(f"Failed to init LCD: {e}")
            return

        while self.state.running:
            t = time.monotonic()
            with self.state.lock:
                s = self.state.eye_state
            
            self.draw.rectangle((0, 0, WIDTH, HEIGHT), fill=BG_DARK)
            if s == "walk": self._draw_walk(t)
            elif s == "turn_left": self._draw_turn(t, -8)
            elif s == "turn_right": self._draw_turn(t, 8)
            elif s == "light_on": self._draw_light(t, True)
            elif s == "light_off": self._draw_light(t, False)
            elif s == "listening": self._draw_listening(t)
            else: self._draw_normal(t)

            frame = self.image.transpose(Image.Transpose.ROTATE_90)
            if self.display:
                try:
                    self.display.image(frame)
                except ValueError as ve:
                    logger.warning(f"Display size mismatch: image {frame.size}, display {self.display.width}x{self.display.height}. {ve}")
                except Exception as e:
                    logger.warning(f"Display update failed: {e}")
            time.sleep(0.06)

    def _draw_full_eye(self, cx, cy, pdx=0, pdy=0, glow=BLUE_GLOW):
        self.draw.ellipse((cx-65, cy-65, cx+65, cy+65), fill=BLACK)
        self.draw.ellipse((cx-55, cy-55, cx+55, cy+55), fill=SCLERA_COLOR)
        for r in range(IRIS_RADIUS, 0, -2):
            if r > 30: c = PURPLE_DARK
            elif r > 18: c = PURPLE_MID
            elif r > 10: c = PINK_MID
            else: c = PINK_LIGHT
            self.draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=c)
        self.draw.ellipse((cx-IRIS_RADIUS, cy-IRIS_RADIUS, cx+IRIS_RADIUS, cy+IRIS_RADIUS), outline=glow, width=2)
        px, py = cx+pdx, cy+pdy
        self.draw.ellipse((px-PUPIL_RADIUS, py-PUPIL_RADIUS, px+PUPIL_RADIUS, py+PUPIL_RADIUS), fill=BLACK)
        self.draw.ellipse((cx-17, cy-17, cx-3, cy-3), fill=WHITE)

    def _draw_normal(self, t):
        self._draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y)
        self._draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y)
        if int(t * 2) % 15 == 0:
            self.draw.rounded_rectangle((LEFT_EYE_X-60, EYE_CENTER_Y-60, LEFT_EYE_X+60, EYE_CENTER_Y-30), radius=10, fill=(10, 10, 20))
            self.draw.rounded_rectangle((RIGHT_EYE_X-60, EYE_CENTER_Y-60, RIGHT_EYE_X+60, EYE_CENTER_Y-30), radius=10, fill=(10, 10, 20))

    def _draw_walk(self, t):
        wave = int(10 * math.sin(t * 8))
        self._draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y + wave//3, pdy=-2, glow=GREEN)
        self._draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y - wave//3, pdy=-2, glow=GREEN)

    def _draw_turn(self, t, dx):
        self._draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y, pdx=dx, glow=YELLOW)
        self._draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y, pdx=dx, glow=YELLOW)

    def _draw_light(self, t, is_on):
        glow = YELLOW if is_on else RED
        self._draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y, glow=glow)
        self._draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y, glow=glow)

    def _draw_listening(self, t):
        glow = (100, 255, 255)
        self._draw_full_eye(LEFT_EYE_X, EYE_CENTER_Y, glow=glow)
        self._draw_full_eye(RIGHT_EYE_X, EYE_CENTER_Y, glow=glow)


class MqttThread(threading.Thread):
    def __init__(self, state):
        super().__init__(name="MqttThread", daemon=True)
        self.state = state
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def run(self):
        self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT Error: {e}")

    def on_connect(self, client, userdata, flags, rc, props):
        logger.info("MQTT Connected")
        client.subscribe("robot/control/#")
        client.subscribe("mechcode/#")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = msg.payload.decode().lower()
        except:
            return

        with self.state.lock:
            if topic == "robot/control/motor":
                if payload in ["forward", "backward"]: self.state.set_eye_state("walk", "MOVING")
                elif "stop" in payload: self.state.set_eye_state("normal", "IDLE")
            elif topic == "robot/control/steer":
                if payload == "left": self.state.set_eye_state("turn_left")
                elif payload == "right": self.state.set_eye_state("turn_right")
                else: self.state.set_eye_state("normal")
            elif "relay" in topic:
                if payload in ["on", "1", "true"]: self.state.set_eye_state("light_on")
                else: self.state.set_eye_state("light_off")


class CameraThread(threading.Thread):
    def __init__(self, state):
        super().__init__(name="CameraThread", daemon=True)
        self.state = state

    def run(self):
        if not Picamera2: return
        cam = Picamera2()
        cfg = cam.create_video_configuration(main={"size": (640, 480), "format": "BGR888"})
        cam.configure(cfg)
        cam.start()

        while self.state.running:
            sock = None
            try:
                sock = socket.create_connection((PC_HOST, IMAGE_PORT), timeout=5)
                while self.state.running:
                    frame = cam.capture_array()
                    _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    data = buf.tobytes()
                    header = struct.pack("!4sIHHdI", b"PBX1", 0, 640, 480, time.time(), len(data))
                    sock.sendall(header + data)
                    time.sleep(1.0/12.0)
            except Exception as e:
                logger.warning(f"Camera Stream error: {e}. Retrying...")
                if sock: sock.close()
                time.sleep(RECONNECT_SEC)
        cam.stop()


class AudioThread(threading.Thread):
    def __init__(self, state):
        super().__init__(name="AudioThread", daemon=True)
        self.state = state
        self.recognizer = sr.Recognizer()

    def run(self):
        setup_speaker_hw(True)
        try:
            pygame.mixer.init()
        except:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)

        mics = sr.Microphone.list_microphone_names()
        logger.info(f"Available mics: {mics}")
        
        # Try to find dmic, then pulse/pipewire, then default
        mic_idx = None
        for i, m in enumerate(mics):
            if "dmic" in m.lower(): 
                mic_idx = i
                break
        
        if mic_idx is None:
            for i, m in enumerate(mics):
                if any(kw in m.lower() for kw in ["pulse", "pipewire", "default"]):
                    mic_idx = i
                    break
        
        logger.info(f"Using mic index: {mic_idx}")
        
        while self.state.running:
            try:
                with sr.Microphone(device_index=mic_idx) as source:
                    logger.info("Listening for Wake Word...")
                    self.recognizer.adjust_for_ambient_noise(source, duration=1.5)
                    audio = self.recognizer.listen(source, timeout=None, phrase_time_limit=3)
                    # Recognize Wake Word
                    text = self.recognizer.recognize_google(audio, language="th-TH").lower()
                    
                    if any(w in text for w in ["petbox", "hello", "\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35", "\u0e40\u0e1e\u0e17\u0e1a\u0e47\u0e2d\u0e01"]):
                        logger.info("Wake Word Detected!")
                        self.state.set_eye_state("listening")
                        self.state.tts_queue.put("\u0e21\u0e35\u0e2d\u0e20\u0e44\u0e23\u0e43\u0e2b\u0e49\u0e0a\u0e48\u0e27\u0e22\u0e44\u0e2b\u0e21\u0e04\u0e30") 
                        
                        logger.info("Recording command...")
                        audio_cmd = self.recognizer.listen(source, timeout=5, phrase_time_limit=8)
                        
                        resp_text = self._offload_to_pc(audio_cmd)
                        if resp_text:
                            self.state.tts_queue.put(resp_text)
                        
                        self.state.set_eye_state("normal")
            except Exception as e:
                logger.debug(f"Audio loop: {e}")
                time.sleep(0.1)

    def _offload_to_pc(self, audio_data):
        try:
            with socket.create_connection((PC_HOST, AUDIO_PORT), timeout=10) as s:
                raw_data = audio_data.get_wav_data()
                header = struct.pack("!4sI", b"PBXA", len(raw_data))
                s.sendall(header + raw_data)
                resp_data = s.recv(1024).decode().strip()
                if resp_data:
                    return json.loads(resp_data).get("text", "\u0e02\u0e2d\u0e2d\u0e20\u0e31\u0e22\u0e04\u0e30 \u0e0a\u0e31\u0e19\u0e44\u0e21\u0e48\u0e40\u0e02\u0e49\u0e32\u0e43\u0e08")
        except Exception as e:
            logger.error(f"Failed to offload audio to PC: {e}")
            return "\u0e02\u0e2d\u0e2d\u0e20\u0e31\u0e22\u0e04\u0e30 \u0e0a\u0e31\u0e19\u0e44\u0e21\u0e48\u0e2a\u0e32\u0e21\u0e32\u0e23\u0e16\u0e15\u0e34\u0e14\u0e15\u0e48\u0e2d\u0e40\u0e04\u0e23\u0e37\u0e48\u0e2d\u0e07\u0e1b\u0e23\u0e30\u0e21\u0e27\u0e21\u0e1c\u0e25\u0e44\u0e14\u0e49"
        return None


class SpeakerThread(threading.Thread):
    def __init__(self, state):
        super().__init__(name="SpeakerThread", daemon=True)
        self.state = state
        self.cache_dir = "tts_cache"
        os.makedirs(self.cache_dir, exist_ok=True)

    def run(self):
        while self.state.running:
            try:
                text = self.state.tts_queue.get(timeout=1)
                self.speak(text)
            except queue.Empty:
                continue

    def speak(self, text):
        hash_val = hashlib.md5(text.encode()).hexdigest()
        path = os.path.join(self.cache_dir, f"{hash_val}.mp3")
        if not os.path.exists(path):
            gTTS(text, lang='th').save(path)
        
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

def main():
    state = PetboxState()
    threads = [DisplayThread(state), MqttThread(state), CameraThread(state), AudioThread(state), SpeakerThread(state)]
    for t in threads: t.start()
    try:
        while state.running: time.sleep(1)
    except KeyboardInterrupt:
        state.stop()
        setup_speaker_hw(False)

if __name__ == "__main__":
    main()
