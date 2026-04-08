#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from threading import Event, Lock

DEFAULT_BROKER = "058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud"
DEFAULT_PORT = 8883
DEFAULT_USER = "Fozexe"
DEFAULT_PASS = "MySecurePassword123!"
DEFAULT_TOPIC_MOTOR = "robot/control/motor"
DEFAULT_TOPIC_STEER = "robot/control/steer"
DEFAULT_TOPIC_RELAY1 = "mechcode/relay1/set"
DEFAULT_TOPIC_RELAY2 = "mechcode/relay2/set"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "speaker_cm5_config.json")
REQUIRED_PIN_KEYS = ["DIN_SPEAKER", "ON_OFF_AUDIO", "LRCLK", "DOUT", "BCLK"]


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    pins = cfg.get("pins", {})
    missing = [k for k in REQUIRED_PIN_KEYS if k not in pins]
    if missing:
        raise ValueError(f"missing pin keys in config: {', '.join(missing)}")

    audio = cfg.get("audio", {})
    return {
        "pins": {k: int(v) for k, v in pins.items()},
        "audio": {
            "alsa_device": str(audio.get("alsa_device", "")).strip(),
        },
    }


def run_cmd(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def list_playback_devices():
    if not shutil.which("aplay"):
        return []
    code, out, err = run_cmd(["aplay", "-l"])
    if code != 0:
        raise RuntimeError(f"aplay -l failed: {err or 'unknown error'}")

    devices = []
    for line in out.splitlines():
        m = re.search(r"card\s+(\d+):\s+([^ ]+).*device\s+(\d+):", line)
        if not m:
            continue
        card_idx = int(m.group(1))
        card_name = m.group(2)
        dev_idx = int(m.group(3))
        devices.append(
            {
                "card_idx": card_idx,
                "card_name": card_name,
                "dev_idx": dev_idx,
                "alsa": f"plughw:{card_idx},{dev_idx}",
            }
        )
    return devices


def resolve_device(configured_device):
    dev = (configured_device or "").strip()
    if not dev or dev.lower() == "auto":
        dev = None

    devices = list_playback_devices()
    if not devices:
        return dev

    non_hdmi = [
        d
        for d in devices
        if "hdmi" not in d["card_name"].lower() and "vc4" not in d["card_name"].lower()
    ]
    preferred = non_hdmi[0]["alsa"] if non_hdmi else devices[0]["alsa"]

    if not dev:
        return preferred

    m = re.match(r"^plughw:CARD=([^,]+),DEV=(\d+)$", dev, flags=re.IGNORECASE)
    if not m:
        return dev

    want_name = m.group(1).lower()
    want_dev = int(m.group(2))
    for d in devices:
        if d["card_name"].lower() == want_name and d["dev_idx"] == want_dev:
            return d["alsa"]
    return preferred


def set_audio_enable(pins, high):
    if not pins:
        return
    if not shutil.which("pinctrl"):
        return
    mode = "dh" if high else "dl"
    code, _, err = run_cmd(["pinctrl", "set", str(pins["ON_OFF_AUDIO"]), "op", mode])
    if code != 0:
        raise RuntimeError(f"set ON_OFF_AUDIO failed: {err or 'unknown error'}")


class ThaiStatusSpeaker:
    def __init__(
        self,
        alsa_device,
        backend="auto",
        voice_cmd="",
        lang="th",
        rate=155,
        cooldown=0.9,
        openai_api_key="",
        openai_model="tts-1",
        openai_voice="alloy",
        openai_base_url="https://api.openai.com/v1",
        edge_voice="th-TH-PremwadeeNeural",
    ):
        self.alsa_device = (alsa_device or "").strip()
        self.cooldown = max(0.0, float(cooldown))
        self._last_emit = {}
        self._lock = Lock()
        self._proc = None
        self._tts_failed_permanent = False
        self.backend = (backend or "auto").strip().lower()
        self.openai_api_key = (openai_api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        self.openai_model = (openai_model or "tts-1").strip()
        self.openai_voice = (openai_voice or "alloy").strip()
        self.openai_base_url = (openai_base_url or "https://api.openai.com/v1").rstrip("/")
        self.edge_voice = (edge_voice or "th-TH-PremwadeeNeural").strip()

        raw = (voice_cmd or "").strip()
        self._custom_cmd = shlex.split(raw) if raw else None
        self._engine = None

        if self.backend not in {"auto", "chatgpt", "espeak", "custom", "edge"}:
            raise RuntimeError(f"unknown voice backend: {self.backend}")

        if self.backend == "auto" and os.name == "nt":
            # On Windows, OpenAI TTS (or custom cmd) is the most portable default.
            if self.openai_api_key:
                self.backend = "chatgpt"
            elif self._custom_cmd is not None:
                self.backend = "custom"

        # custom backend
        if self.backend == "custom":
            if self._custom_cmd is None:
                raise RuntimeError("voice backend custom requires --voice-cmd")
            return

        # edge-tts backend (แนะนำ)
        if self.backend == "edge" or self.backend == "auto":
            if shutil.which("edge-tts"):
                self.backend = "edge"
                return
            if self.backend == "edge":
                raise RuntimeError("voice backend edge requires edge-tts command")

        # OpenAI backend
        wants_chatgpt = self.backend == "chatgpt" or (self.backend == "auto" and bool(self.openai_api_key))
        if wants_chatgpt:
            if not self.openai_api_key:
                raise RuntimeError("voice backend chatgpt requires OPENAI_API_KEY")
            self.backend = "chatgpt"
            return

        # espeak fallback
        self.backend = "espeak"
        if not self._custom_cmd:
            if shutil.which("espeak-ng"):
                self._engine = ["espeak-ng", "-v", lang, "-s", str(rate)]
            elif shutil.which("espeak"):
                self._engine = ["espeak", "-v", lang, "-s", str(rate)]

        if self._custom_cmd is None and self._engine is None:
            raise RuntimeError(
                "ไม่พบ TTS engine สำหรับภาษาไทย กรุณาติดตั้ง edge-tts หรือ espeak-ng/espeak หรือกำหนด --voice-cmd"
            )

    def _enable_espeak_fallback(self):
        if self._engine is None:
            return False
        if self.backend != "espeak":
            self.backend = "espeak"
            print("[WARN] fallback to espeak backend")
        return True

    def _kill_running(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None

    def _build_custom_cmd(self, text):
        out = []
        has_placeholder = False
        for tok in self._custom_cmd:
            if "{text}" in tok:
                out.append(tok.replace("{text}", text))
                has_placeholder = True
            else:
                out.append(tok)
        if not has_placeholder:
            out.append(text)
        return out

    def _play_wav(self, wav_path):
        if shutil.which("aplay"):
            play_cmd = ["aplay", "-q"]
            if self.alsa_device:
                play_cmd += ["-D", self.alsa_device]
            play_cmd.append(wav_path)
            self._proc = subprocess.Popen(play_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return

        if os.name == "nt":
            try:
                import winsound

                winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except Exception as exc:
                raise RuntimeError(f"windows wav playback failed: {exc}") from exc

        raise RuntimeError("no wav player available (aplay missing)")

    def _speak_with_engine(self, text):
        with tempfile.NamedTemporaryFile(prefix="voice_status_", suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            tts_cmd = self._engine + ["-w", wav_path, text]
            subprocess.run(tts_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._play_wav(wav_path)
        finally:
            time.sleep(0.05)
            try:
                os.remove(wav_path)
            except OSError:
                pass

    def _speak_with_edge(self, text):
        tmp_dir = tempfile.mkdtemp(prefix="edge_tts_")
        mp3_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}.mp3")
        wav_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}.wav")
        try:
            subprocess.run(
                [
                    "edge-tts",
                    "--voice", self.edge_voice,
                    "--text", text,
                    "--write-media", mp3_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path, wav_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self._play_wav(wav_path)
        finally:
            time.sleep(0.05)
            for p in (mp3_path, wav_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def _speak_with_chatgpt(self, text):
        with tempfile.NamedTemporaryFile(prefix="voice_status_", suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            models = [m.strip() for m in self.openai_model.split(",") if m.strip()]
            if not models:
                models = ["tts-1", "tts-1-hd"]

            audio_bytes = None
            last_error = None
            for model_name in models:
                try:
                    url = f"{self.openai_base_url}/audio/speech"
                    body = json.dumps(
                        {
                            "model": model_name,
                            "voice": self.openai_voice,
                            "input": text,
                            "response_format": "wav",
                        }
                    ).encode("utf-8")
                    req = urllib.request.Request(
                        url,
                        data=body,
                        headers={
                            "Authorization": f"Bearer {self.openai_api_key}",
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=45) as resp:
                        audio_bytes = resp.read()
                    if audio_bytes:
                        break
                except urllib.error.HTTPError as exc:
                    detail = ""
                    try:
                        detail = exc.read().decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                    last_error = f"{exc.code}: {detail or exc.reason}"
                    continue
                except urllib.error.URLError as exc:
                    raise RuntimeError(f"chatgpt audio url error: {exc}") from exc

            if not audio_bytes:
                raise RuntimeError(
                    f"chatgpt audio failed for all models: {', '.join(models)}"
                    + (f" ({last_error})" if last_error else "")
                )

            with open(wav_path, "wb") as wf:
                wf.write(audio_bytes)

            self._play_wav(wav_path)
        finally:
            time.sleep(0.05)
            try:
                os.remove(wav_path)
            except OSError:
                pass

    def speak(self, text, key=None):
        if self._tts_failed_permanent:
            return
        cache_key = key or text
        now = time.monotonic()
        prev = self._last_emit.get(cache_key, 0.0)
        if (now - prev) < self.cooldown:
            return
        self._last_emit[cache_key] = now

        with self._lock:
            self._kill_running()

            if self.backend == "edge":
                try:
                    self._speak_with_edge(text)
                    return
                except Exception as exc:
                    print(f"[WARN] edge tts failed ({exc})")

            if self.backend == "chatgpt":
                try:
                    self._speak_with_chatgpt(text)
                    return
                except RuntimeError as exc:
                    if shutil.which("edge-tts"):
                        print(f"[WARN] chatgpt tts failed ({exc}); using edge-tts")
                        self.backend = "edge"
                        self._speak_with_edge(text)
                        return
                    if self._enable_espeak_fallback():
                        print(f"[WARN] chatgpt tts failed ({exc}); using espeak")
                        self._speak_with_engine(text)
                        return
                    self._tts_failed_permanent = True
                    print(f"[ERROR] chatgpt tts failed ({exc}) and no fallback backend is available")
                return

            if self.backend == "custom" or self._custom_cmd:
                cmd = self._build_custom_cmd(text)
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

            self._speak_with_engine(text)

    def close(self):
        with self._lock:
            self._kill_running()

class VoiceStatusMQTT:
    def __init__(self, args):
        self.args = args
        self.stop_event = Event()
        self.last_mqtt_retry = 0.0
        self.mqtt_connected = False
        self.auth_failed = False
        self.client = None
        self.mqtt_loop_started = False
        self.retry_delay = 1.0
        self.retry_max_delay = 30.0
        self.next_retry_delay = self.retry_delay
        self.next_retry_at = 0.0

        if args.pc_mode:
            self.pins = {}
            self.alsa_device = (args.device or "").strip()
            print("[INFO] running in PC mode (skip CM5 pin/audio power config)")
        else:
            cfg = load_config(args.config)
            self.pins = cfg["pins"]
            cfg_device = cfg["audio"]["alsa_device"]
            user_device = args.device if args.device is not None else cfg_device
            self.alsa_device = resolve_device(user_device)

        self.enable_on = not args.enable_active_low
        self.enable_off = not self.enable_on

        if not args.pc_mode:
            print(f"[INFO] speaker config: {args.config}")
        print(f"[INFO] ALSA device: {self.alsa_device or 'default'}")
        print(f"[INFO] voice backend: {args.voice_backend}")
        if not args.pc_mode:
            print(
                "[INFO] ON_OFF_AUDIO active level: "
                f"{'LOW' if args.enable_active_low else 'HIGH'}"
            )

        self.speaker = ThaiStatusSpeaker(
            alsa_device=self.alsa_device,
            backend=args.voice_backend,
            voice_cmd=args.voice_cmd,
            lang=args.voice_lang,
            rate=args.voice_rate,
            cooldown=args.voice_cooldown,
            openai_api_key=args.openai_api_key,
            openai_model=args.openai_model,
            openai_voice=args.openai_voice,
            openai_base_url=args.openai_base_url,
            edge_voice=args.edge_voice,
        )

    def _build_default_client_id(self):
        host = socket.gethostname() or "host"
        raw = f"petbox-status-{host}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        safe = re.sub(r"[^0-9a-zA-Z_-]+", "-", raw.lower())[:32]
        return f"{safe}-{digest}"

    def _safe_speak(self, text, key):
        try:
            self.speaker.speak(text, key=key)
        except Exception as exc:
            # Never allow callback exceptions to kill paho thread.
            print(f"[WARN] speak failed: {exc}")

    def _reason_code_value(self, reason_code):
        try:
            return int(reason_code)
        except Exception:
            pass
        try:
            return int(getattr(reason_code, "value"))
        except Exception:
            pass
        return 255

    def setup_mqtt(self):
        try:
            import paho.mqtt.client as mqtt
        except Exception:
            print("missing dependency: paho-mqtt")
            print("install: pip install paho-mqtt")
            raise SystemExit(2)

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

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties):
        rc = self._reason_code_value(reason_code)
        if rc != 0:
            self.mqtt_connected = False
            print(f"[WARN] MQTT rejected: rc={rc} ({reason_code})")
            if rc == 135:
                self.auth_failed = True
                print("[ERROR] MQTT authentication failed. Check MQTT_USER/MQTT_PASS in .env")
                print("[ERROR] For HiveMQ Cloud, use credentials from Access Management, not old defaults.")
                self.stop_event.set()
            return

        self.mqtt_connected = True
        self.next_retry_delay = self.retry_delay
        self.next_retry_at = 0.0
        print("[INFO] MQTT connected")
        self._safe_speak("เชื่อมต่อระบบเรียบร้อย", key="mqtt_connected")

        client.subscribe(self.args.topic_motor, qos=1)
        client.subscribe(self.args.topic_steer, qos=1)
        client.subscribe(self.args.topic_relay1, qos=1)
        client.subscribe(self.args.topic_relay2, qos=1)
        if getattr(self.args, "topic_qa", None):
            client.subscribe(self.args.topic_qa, qos=1)

    def _on_disconnect(self, _client, _userdata, _disconnect_flags, reason_code, _properties):
        self.mqtt_connected = False
        rc = self._reason_code_value(reason_code)
        now = time.monotonic()
        self.next_retry_at = now + self.next_retry_delay
        self.next_retry_delay = min(self.next_retry_delay * 2.0, self.retry_max_delay)
        print(f"[WARN] MQTT disconnected: rc={rc} ({reason_code})")
        if not self.auth_failed:
            self._safe_speak("ขาดการเชื่อมต่อ", key="mqtt_disconnected")

    def _on_message(self, _client, _userdata, msg):
        topic = msg.topic.strip()
        raw_payload = msg.payload.decode("utf-8", errors="ignore").strip()
        payload = raw_payload.lower()

        if topic == self.args.topic_motor:
            if payload == "forward":
                self._safe_speak("กำลังเดินหน้า", key="motor_forward")
            elif payload == "backward":
                self._safe_speak("กำลังถอยหลัง", key="motor_backward")
            elif payload in {"soft_stop", "hard_stop", "stop"}:
                self._safe_speak("หยุดการเคลื่อนที่", key="motor_stop")

        elif topic == self.args.topic_steer:
            if payload == "left":
                self._safe_speak("เลี้ยวซ้าย", key="steer_left")
            elif payload == "right":
                self._safe_speak("เลี้ยวขวา", key="steer_right")
            elif payload == "reset":
                self._safe_speak("ปรับทิศทางตรง", key="steer_reset")

        elif topic == self.args.topic_relay1:
            if payload in {"on", "1", "true", "turn_on"}:
                self._safe_speak("เปิดไฟดวงที่หนึ่ง", key="relay1_on")
            elif payload in {"off", "0", "false", "turn_off"}:
                self._safe_speak("ปิดไฟดวงที่หนึ่ง", key="relay1_off")

        elif topic == self.args.topic_relay2:
            if payload in {"on", "1", "true", "turn_on"}:
                self._safe_speak("เปิดไฟดวงที่สอง", key="relay2_on")
            elif payload in {"off", "0", "false", "turn_off"}:
                self._safe_speak("ปิดไฟดวงที่สอง", key="relay2_off")

        elif topic == getattr(self.args, "topic_qa", None):
            if raw_payload:
                print(f"[QA] Speaking: {raw_payload}")
                self._safe_speak(raw_payload, key=None)

    def run(self):
        if not self.args.pc_mode:
            set_audio_enable(self.pins, self.enable_on)
            print("[INFO] speaker power path enabled")

        self.setup_mqtt()
        while not self.stop_event.is_set():
            if (not self.auth_failed) and (not self.mqtt_connected):
                self._try_connect_mqtt()
            time.sleep(0.1)

        if self.client is not None:
            try:
                self.client.disconnect()
                if self.mqtt_loop_started:
                    self.client.loop_stop()
            except Exception:
                pass
        self.speaker.close()

        if not self.args.pc_mode:
            if self.args.keep_on:
                print("[INFO] keep ON_OFF_AUDIO enabled (--keep-on)")
            else:
                set_audio_enable(self.pins, self.enable_off)
                print("[INFO] speaker power path disabled")


def build_parser():
    p = argparse.ArgumentParser(description="Thai voice status listener for CM5 MQTT")
    p.add_argument("--broker", default=os.getenv("MQTT_BROKER", DEFAULT_BROKER))
    p.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", str(DEFAULT_PORT))))
    p.add_argument("--client-id", default=os.getenv("MQTT_CLIENT_ID_STATUS", ""))
    p.add_argument("--username", default=os.getenv("MQTT_USER", DEFAULT_USER))
    p.add_argument("--password", default=os.getenv("MQTT_PASS", DEFAULT_PASS))
    p.add_argument("--topic-motor", default=os.getenv("MQTT_TOPIC_MOTOR", DEFAULT_TOPIC_MOTOR))
    p.add_argument("--topic-steer", default=os.getenv("MQTT_TOPIC_STEER", DEFAULT_TOPIC_STEER))
    p.add_argument("--topic-relay1", default=os.getenv("MQTT_TOPIC_RELAY1", DEFAULT_TOPIC_RELAY1))
    p.add_argument("--topic-relay2", default=os.getenv("MQTT_TOPIC_RELAY2", DEFAULT_TOPIC_RELAY2))
    p.add_argument("--topic-qa", default=os.getenv("MQTT_TOPIC_QA", "petbox/qa/answer"))
    p.add_argument("--tls", action="store_true", default=True)
    p.add_argument("--no-tls", action="store_false", dest="tls")

    p.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to speaker config JSON")
    p.add_argument("--device", default=None, help="Optional ALSA device override")
    p.add_argument(
        "--pc-mode",
        action="store_true",
        help="Run on normal PC: skip CM5 pinctrl and speaker power pin handling",
    )
    p.add_argument(
        "--enable-active-low",
        action="store_true",
        help="Treat ON/OFF_AUDIO as active-low instead of active-high",
    )
    p.add_argument("--keep-on", action="store_true", help="Keep ON_OFF_AUDIO active after exit")
    p.add_argument(
        "--edge-voice",
        default=os.getenv("EDGE_TTS_VOICE", "th-TH-PremwadeeNeural"),
        help="Edge TTS Thai voice",
    )
    p.add_argument(
        "--voice-backend",
        default=os.getenv("PETBOX_VOICE_BACKEND", "edge"),
        choices=["auto", "chatgpt", "espeak", "custom", "edge"],
        help="TTS backend: auto/chatgpt/espeak/custom/edge",
    )
    p.add_argument("--voice-lang", default=os.getenv("PETBOX_VOICE_LANG", "th"))
    p.add_argument("--voice-rate", type=int, default=int(os.getenv("PETBOX_VOICE_RATE", "155")))
    p.add_argument("--voice-cmd", default=os.getenv("PETBOX_VOICE_CMD", ""))
    p.add_argument("--voice-cooldown", type=float, default=float(os.getenv("PETBOX_VOICE_COOLDOWN", "0.9")))
    p.add_argument("--openai-api-key", default=os.getenv("OPENAI_API_KEY", ""))
    p.add_argument(
        "--openai-model",
        default=os.getenv("OPENAI_TTS_MODEL", "tts-1"),
        help="OpenAI TTS model (used by chatgpt backend)",
    )
    p.add_argument(
        "--openai-voice",
        default=os.getenv("OPENAI_TTS_VOICE", "alloy"),
        help="OpenAI voice (used by chatgpt backend)",
    )
    p.add_argument(
        "--openai-base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI API base URL",
    )
    return p


def main():
    args = build_parser().parse_args()
    try:
        app = VoiceStatusMQTT(args)
    except Exception as exc:
        print(f"[ERROR] init failed: {exc}")
        return 2

    def _handle_signal(signum, _frame):
        print(f"[INFO] signal received: {signum}; stopping")
        app.stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        app.run()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        return 2
    except Exception as exc:
        print(f"[ERROR] unexpected: {exc}")
        return 2

    print("[INFO] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
