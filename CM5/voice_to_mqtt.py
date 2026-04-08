#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_default_model_path():
    models_root = os.path.join(BASE_DIR, "models")
    preferred = os.path.join(models_root, "model")
    if os.path.isfile(os.path.join(preferred, "am", "final.mdl")):
        return preferred

    legacy = os.path.join(models_root, "vosk-th", "model")
    if os.path.isdir(legacy):
        return legacy

    if os.path.isfile(os.path.join(models_root, "am", "final.mdl")):
        return models_root

    if os.path.isdir(models_root):
        try:
            for name in sorted(os.listdir(models_root)):
                cand = os.path.join(models_root, name)
                if os.path.isfile(os.path.join(cand, "am", "final.mdl")):
                    return cand
        except OSError:
            pass

    return legacy


DEFAULT_MODEL = _find_default_model_path()
DEFAULT_BROKER = "058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud"
DEFAULT_PORT = 8883
DEFAULT_USER = "Fozexe"
DEFAULT_PASS = "MySecurePassword123!"
DEFAULT_TOPIC_RELAY1 = "mechcode/relay1/set"
DEFAULT_TOPIC_RELAY2 = "mechcode/relay2/set"
DEFAULT_PAYLOAD_ON = "ON"
DEFAULT_PAYLOAD_OFF = "OFF"
DEFAULT_DEVICE = "" if os.name == "nt" else "dsnoop:CARD=PetboxAudio,DEV=0"

PHRASE_MAP = {
    ("on", 1): [
        "เปิดไฟดวงที่ 1",
        "เปิดไฟดวงที่1",
        "เปิดไฟหนึ่ง",
        "เปิดไฟดวงหนึ่ง",
        "เปิดรีเลย์ 1",
        "เปิดรีเลย์1",
    ],
    ("off", 1): [
        "ปิดไฟดวงที่ 1",
        "ปิดไฟดวงที่1",
        "ปิดไฟหนึ่ง",
        "ปิดไฟดวงหนึ่ง",
        "ปิดรีเลย์ 1",
        "ปิดรีเลย์1",
    ],
    ("on", 2): [
        "เปิดไฟดวงที่ 2",
        "เปิดไฟดวงที่2",
        "เปิดไฟสอง",
        "เปิดไฟดวงสอง",
        "เปิดรีเลย์ 2",
        "เปิดรีเลย์2",
    ],
    ("off", 2): [
        "ปิดไฟดวงที่ 2",
        "ปิดไฟดวงที่2",
        "ปิดไฟสอง",
        "ปิดไฟดวงสอง",
        "ปิดรีเลย์ 2",
        "ปิดรีเลย์2",
    ],
}

ON_KEYWORDS = {
    "เปิด",
    "ติด",
    "สว่าง",
    "on",
    "สั่งเปิด",
}

OFF_KEYWORDS = {
    "ปิด",
    "ดับ",
    "off",
    "หยุด",
    "สั่งปิด",
}

# Hard-priority phrases that should map to OFF even if "เปิด" appears in sentence.
OFF_PRIORITY_PHRASES = {
    "ไม่ต้องเปิด",
    "อย่าเปิด",
    "ไม่เปิด",
}

TARGET_KEYWORDS = {
    "ไฟ",
    "ดวง",
    "หลอด",
    "โคม",
    "รีเลย์",
    "relay",
    "switch",
}

LIGHT_SYNONYMS = {
    1: {"1", "๑", "หนึ่ง", "ดวงหนึ่ง", "แรก", "one", "เลข1", "หมายเลข1"},
    2: {"2", "๒", "สอง", "ดวงสอง", "second", "two", "เลข2", "หมายเลข2"},
}

NORMALIZE_REPLACEMENTS = {
    "รีเรย์": "รีเลย์",
    "ริเลย์": "รีเลย์",
    "หมายเลข": "เลข",
    "เบอร์": "เลข",
    "ลำดับ": "เลข",
}

THAI_DIGITS_MAP = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def build_default_client_id():
    host = socket.gethostname() or "host"
    raw = f"petbox-voicecmd-{host}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^0-9a-zA-Z_-]+", "-", raw.lower())[:32]
    return f"{safe}-{digest}"


def normalize_text(text):
    return " ".join(text.strip().split()).lower()


def canonicalize_text(text):
    t = normalize_text(text).translate(THAI_DIGITS_MAP)
    # Keep Thai/English letters and digits, convert others to spaces.
    t = re.sub(r"[^0-9a-zA-Zก-๙]+", " ", t)
    t = " ".join(t.split())
    for src, dst in NORMALIZE_REPLACEMENTS.items():
        t = t.replace(src, dst)
    return t


def _contains_any(text, keywords):
    return any(k in text for k in keywords)


def detect_action(text):
    if _contains_any(text, OFF_PRIORITY_PHRASES):
        return "off"

    on_score = sum(1 for k in ON_KEYWORDS if k in text)
    off_score = sum(1 for k in OFF_KEYWORDS if k in text)
    if on_score == 0 and off_score == 0:
        return None
    return "on" if on_score >= off_score else "off"


def detect_light_id(text):
    # Use collapsed form for robust matching, e.g. "ดวงที่1", "เลข 2"
    collapsed = text.replace(" ", "")
    for light_id, variants in LIGHT_SYNONYMS.items():
        for v in variants:
            if v.replace(" ", "") in collapsed:
                return light_id
    return None


def detect_light_command(text):
    t = canonicalize_text(text)
    collapsed = t.replace(" ", "")

    # Priority: explicit negation phrases should force OFF if light index is present.
    if _contains_any(t, OFF_PRIORITY_PHRASES):
        light_id = detect_light_id(t)
        if light_id:
            return "off", light_id

    # 1) Fast path: explicit phrase variants
    for (action, light_id), variants in PHRASE_MAP.items():
        for v in variants:
            if canonicalize_text(v).replace(" ", "") in collapsed:
                return action, light_id

    # 2) Intent path: action + light index with synonym support
    action = detect_action(t)
    light_id = detect_light_id(t)
    has_target = _contains_any(t, TARGET_KEYWORDS)
    if action and light_id and (has_target or "เลข" in t or "ดวงที่" in collapsed):
        return action, light_id
    return None


class ChatGPTIntentResolver:
    def __init__(self, api_key="", model="gpt-4o-mini,gpt-4o,gpt-4-turbo", base_url="https://api.openai.com/v1", timeout=6.0):
        self.api_key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        self.model = (model or "gpt-4o-mini,gpt-4o,gpt-4-turbo").strip()
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = max(1.0, float(timeout))

    def enabled(self):
        return bool(self.api_key)

    def resolve(self, text):
        if not self.enabled():
            return None

        prompt = (
            "You are a Thai smart-home command parser.\n"
            "Map the user text to relay control intent.\n"
            "Return JSON only with this schema:\n"
            '{"action":"on|off|none","light":1|2|null}\n'
            "Rules:\n"
            "- If intent is unclear, return action=none and light=null.\n"
            "- Understand Thai wording and common ASR mistakes.\n"
            "- Respect negation, e.g. 'ไม่ต้องเปิดไฟ 1' means off light 1.\n"
            f"User text: {text}"
        )

        models = [m.strip() for m in self.model.split(",") if m.strip()]
        if not models:
            models = ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"]

        last_err = None
        for model_name in models:
            body = json.dumps(
                {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "response_format": {"type": "json_object"}
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
                content = (
                    payload.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if not content:
                    continue
                parsed = parse_intent_from_json(content)
                if parsed is not None:
                    if parsed[0] == "none":
                        return None
                    return parsed
                last_err = f"model {model_name} returned non-json/non-parseable content"
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                code = ""
                try:
                    parsed = json.loads(detail)
                    code = str(parsed.get("error", {}).get("code") or "")
                except Exception:
                    pass
                print(f"warn: intent chatgpt http error ({model_name}) {exc.code}: {detail or exc.reason}")
                if exc.code in (400, 401, 403, 404, 429, 500, 502, 503, 504):
                    last_err = f"{exc.code} {code}"
                    continue
                return None
            except urllib.error.URLError as exc:
                print(f"warn: intent chatgpt url error: {exc}")
                return None
            except Exception as exc:
                print(f"warn: intent chatgpt request failed: {exc}")
                return None

        if last_err:
            print(f"warn: all intent models failed: {', '.join(models)} ({last_err})")
        return None


def parse_intent_from_json(raw_text):
    # Handle extra wrappers such as ```json ... ```
    s = raw_text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    if not s.startswith("{"):
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            return None
        s = m.group(0)
    try:
        obj = json.loads(s)
    except Exception:
        return None

    action = str(obj.get("action", "")).strip().lower()
    light = obj.get("light", None)
    if action == "none":
        return ("none", None)
    if action not in {"on", "off"}:
        return None
    if light not in (1, 2):
        return None
    return action, int(light)


def resolve_light_command(text, args, intent_resolver):
    backend = (args.intent_backend or "auto").strip().lower()
    if backend == "rule":
        return detect_light_command(text), "rule"

    if backend == "chatgpt":
        cmd = intent_resolver.resolve(text) if intent_resolver else None
        if cmd is not None:
            return cmd, "chatgpt"
        cmd = detect_light_command(text)
        return cmd, ("rule-fallback" if cmd else "none")

    # auto: keep rule fast path, use chatgpt only when rule misses.
    cmd = detect_light_command(text)
    if cmd is not None:
        return cmd, "rule"
    cmd = intent_resolver.resolve(text) if intent_resolver else None
    if cmd is not None:
        return cmd, "chatgpt"
    return None, "none"


def env_bool(name, default=False):
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def configure_console_encoding():
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        text = sep.join(str(a) for a in args)
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        repaired = text.encode(enc, errors="replace").decode(enc, errors="replace")
        print(repaired, end=end)


def is_question_like(text):
    t = canonicalize_text(text)
    if not t:
        return False
    markers = {
        "?",
        "ไหม",
        "มั้ย",
        "อะไร",
        "ทำไม",
        "อย่างไร",
        "ยังไง",
        "เท่าไหร่",
        "กี่",
        "ช่วย",
        "อธิบาย",
        "บอก",
    }
    if any(m in text for m in {"?", "？"}):
        return True
    return any(m in t for m in markers)


class ChatGPTQA:
    def __init__(
        self,
        api_key="",
        model="gpt-4o-mini,gpt-4o,gpt-4-turbo",
        base_url="https://api.openai.com/v1",
        timeout=10.0,
        system_prompt="คุณคือผู้ช่วยภาษาไทย ตอบสั้น กระชับ เข้าใจง่าย",
        max_tokens=220,
    ):
        self.api_key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        self.model = (model or "gpt-4o-mini,gpt-4o,gpt-4-turbo").strip()
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self.system_prompt = (system_prompt or "").strip()
        self.max_tokens = max(32, int(max_tokens))

    def enabled(self):
        return bool(self.api_key)

    def ask(self, user_text):
        if not self.enabled():
            return None
        models = [m.strip() for m in self.model.split(",") if m.strip()]
        if not models:
            models = ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"]

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_text})

        last_err = None
        for model_name in models:
            body = json.dumps(
                {
                    "model": model_name,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": self.max_tokens,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
                answer = (
                    payload.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if answer:
                    return answer
                last_err = f"empty answer from {model_name}"
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                code = ""
                try:
                    parsed = json.loads(detail)
                    code = str(parsed.get("error", {}).get("code") or "")
                except Exception:
                    pass
                print(f"warn: qa http error ({model_name}) {exc.code}: {detail or exc.reason}")
                if exc.code in (400, 401, 403, 404, 429, 500, 502, 503, 504):
                    last_err = f"{exc.code} {code}"
                    continue
                return None
            except urllib.error.URLError as exc:
                print(f"warn: qa url error: {exc}")
                return None
            except Exception as exc:
                print(f"warn: qa request failed: {exc}")
                return None
        if last_err:
            print(f"warn: all qa models failed: {', '.join(models)} ({last_err})")
        return None


def build_custom_tts_cmd(template_cmd, text):
    out = []
    has_placeholder = False
    for tok in template_cmd:
        if "{text}" in tok:
            out.append(tok.replace("{text}", text))
            has_placeholder = True
        else:
            out.append(tok)
    if not has_placeholder:
        out.append(text)
    return out


def speak_answer(text, args, speaker_obj=None):
    if not args.qa_speak:
        return
    if speaker_obj:
        try:
            speaker_obj.speak(text)
            return
        except Exception as exc:
            print(f"warn: speaker_obj play failed: {exc}")

    cmd_raw = (args.qa_voice_cmd or "").strip()
    try:
        if cmd_raw:
            cmd = build_custom_tts_cmd(shlex.split(cmd_raw), text)
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        if shutil.which("espeak-ng"):
            subprocess.Popen(
                ["espeak-ng", "-v", args.qa_voice_lang, "-s", str(args.qa_voice_rate), text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        if shutil.which("espeak"):
            subprocess.Popen(
                ["espeak", "-v", args.qa_voice_lang, "-s", str(args.qa_voice_rate), text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        print("warn: qa-speak enabled but no TTS engine found (espeak-ng/espeak)")
    except Exception as exc:
        print(f"warn: qa speak failed: {exc}")


def convert_s32_to_s16(raw_bytes):
    if len(raw_bytes) < 4:
        return raw_bytes
    n = len(raw_bytes) - (len(raw_bytes) % 4)
    out = bytearray(n // 2)
    in_b = bytes(raw_bytes)
    out[0::2] = in_b[2:n:4]  # type: ignore
    out[1::2] = in_b[3:n:4]  # type: ignore
    return bytes(out)


def build_arecord_cmd(device, rate, channels, fmt="S16_LE"):
    return [
        "arecord",
        "-q",
        "-D",
        device,
        "-f",
        fmt,
        "-r",
        str(rate),
        "-c",
        str(channels),
        "-t",
        "raw",
    ]


class SoundDeviceInput:
    def __init__(self, device_hint="", sample_rate=16000, channels=1, blocksize_bytes=4000):
        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:
            raise RuntimeError("sounddevice is not installed") from exc

        self._sd = sd
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._q = queue.Queue(maxsize=24)
        self._closed = False
        self._blocksize_frames = max(1, int(blocksize_bytes // (2 * self.channels)))

        dev = self._parse_device_hint(device_hint)

        def _callback(indata, frames, time_info, status):
            if status:
                return
            try:
                self._q.put_nowait(bytes(indata))
            except queue.Full:
                try:
                    _ = self._q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._q.put_nowait(bytes(indata))
                except queue.Full:
                    pass

        self._stream = self._sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self._blocksize_frames,
            device=dev,
            channels=self.channels,
            dtype="int16",
            callback=_callback,
        )
        self._stream.start()

    def _parse_device_hint(self, hint):
        dev = (hint or "").strip()
        if not dev or dev.lower() in {"auto", "default"}:
            return None
        if dev.isdigit():
            return int(dev)
        return dev

    def read(self, _size):
        if self._closed:
            return b""
        try:
            return self._q.get(timeout=1.0)
        except queue.Empty:
            return b""

    def poll(self):
        return None

    def get_stderr_text(self):
        return ""

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass


class ARecordInput:
    def __init__(self, proc, fmt="S16_LE"):
        self._proc = proc
        self.fmt = fmt

    def read(self, size):
        if self.fmt == "S32_LE":
            raw = self._proc.stdout.read(size * 2)
            return convert_s32_to_s16(raw)
        return self._proc.stdout.read(size)

    def poll(self):
        return self._proc.poll()

    def get_stderr_text(self):
        try:
            return self._proc.stderr.read().decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def terminate(self):
        return self._proc.terminate()

    def wait(self, timeout=None):
        return self._proc.wait(timeout=timeout)


def build_capture_device_candidates(device):
    dev = (device or "").strip()
    if not dev:
        return [DEFAULT_DEVICE]

    # Prefer requested device first, then add shareable dsnoop variants.
    candidates = [dev]

    m_short = re.match(r"^(?:plughw|hw):(\d+),(\d+)$", dev, flags=re.IGNORECASE)
    if m_short:
        card_idx, dev_idx = m_short.groups()
        candidates.append(f"dsnoop:{card_idx},{dev_idx}")

    m_named = re.match(r"^(?:plughw|hw):CARD=([^,]+),DEV=(\d+)$", dev, flags=re.IGNORECASE)
    if m_named:
        card_name, dev_idx = m_named.groups()
        candidates.append(f"dsnoop:CARD={card_name},DEV={dev_idx}")

    if dev.lower().startswith("plughw:"):
        candidates.append(dev.replace("plughw:", "hw:", 1))

    # De-duplicate while preserving order.
    out = []
    for c in candidates:
        if c not in out:
            out.append(c)
    return out


def start_audio_stream(device):
    errors = []
    for candidate in build_capture_device_candidates(device):
        for fmt in ["S16_LE", "S32_LE"]:
            cmd1 = build_arecord_cmd(device=candidate, rate=16000, channels=1, fmt=fmt)
            p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(0.2)
            if p1.poll() is None:
                return p1, 1, candidate, fmt
            p1.wait(timeout=1.0)
            err1 = p1.stderr.read().decode("utf-8", errors="ignore").strip()

            cmd2 = build_arecord_cmd(device=candidate, rate=16000, channels=2, fmt=fmt)
            p2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(0.2)
            if p2.poll() is None:
                return p2, 2, candidate, fmt
            err2 = p2.stderr.read().decode("utf-8", errors="ignore").strip()

            errors.append(f"{candidate} ({fmt}): mono=({err1 or 'failed'}) stereo=({err2 or 'failed'})")

    raise RuntimeError("arecord failed for all capture devices:\n" + "\n".join(errors))


import array
import math

def calculate_rms(chunk):
    if len(chunk) < 2:
        return 0.0
    arr = array.array('h', chunk)
    mean_sq = sum(float(v) * float(v) for v in arr) / len(arr)
    return math.sqrt(mean_sq) / 32768.0

def maybe_downmix_to_mono(raw_chunk, channels):
    if channels == 1:
        return raw_chunk
    # 16-bit stereo interleaved -> mix both channels (handles mics wired to right channel)
    if len(raw_chunk) < 4:
        return b""
    arr = array.array('h', raw_chunk)
    mono = array.array('h')
    for i in range(0, len(arr) - 1, 2):
        val = arr[i] + arr[i+1]
        if val > 32767: val = 32767
        elif val < -32768: val = -32768
        mono.append(val)
    return mono.tobytes()


def open_audio_input(device):
    # Linux path: prefer arecord when available because it supports ALSA device names.
    if shutil.which("arecord"):
        proc, in_channels, active_device, fmt = start_audio_stream(device)
        return ARecordInput(proc, fmt=fmt), in_channels, active_device, f"arecord({fmt})"

    # Cross-platform fallback: sounddevice (Windows/macOS/Linux).
    source = SoundDeviceInput(device_hint=device, sample_rate=16000, channels=1, blocksize_bytes=4000)
    active_device = (device or "default").strip() or "default"
    return source, 1, active_device, "sounddevice"


def validate_vosk_model(model_path):
    required = [
        os.path.join(model_path, "am", "final.mdl"),
        os.path.join(model_path, "graph", "HCLG.fst"),
        os.path.join(model_path, "graph", "words.txt"),
        os.path.join(model_path, "ivector", "final.ie"),
    ]
    bad = []
    for p in required:
        if not os.path.isfile(p):
            bad.append((p, "missing"))
            continue
        try:
            if os.path.getsize(p) <= 0:
                bad.append((p, "empty"))
        except OSError:
            bad.append((p, "unreadable"))
    return bad


def transcribe_whisper_openai(pcm_bytes, api_key, base_url, timeout=10.0):
    import urllib.request, json, io, uuid, wave
    
    if not pcm_bytes:
        return None
        
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_bytes)
    wav_bytes = wav_io.getvalue()

    boundary = uuid.uuid4().hex
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}"
    }
    
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    body.extend(b'whisper-1\r\n')
    
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="language"\r\n\r\n')
    body.extend(b'th\r\n')
    
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n')
    body.extend(b'Content-Type: audio/wav\r\n\r\n')
    body.extend(wav_bytes)
    body.extend(b'\r\n')
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    
    req = urllib.request.Request(f"{base_url}/audio/transcriptions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("text", "").strip()
    except Exception as e:
        print(f"warn: whisper transcribing failed: {e}")
        return None

def transcribe_google_free(pcm_bytes, language="th-TH"):
    if not pcm_bytes:
        return None
    try:
        import speech_recognition as sr
        import io
        import wave
        
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm_bytes)
            
        r = sr.Recognizer()
        audio_file = sr.AudioFile(wav_io)
        with audio_file as source:
            audio_data = r.record(source)
            
        text = r.recognize_google(audio_data, language=language)
        return text.strip()
    except ImportError:
        print("warn: SpeechRecognition library not installed. Cannot use google stt.")
        return None
    except Exception as e:
        print(f"warn: google transcribing failed: {e}")
        return None


def main():
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Listen for Thai voice commands (print to terminal, optional MQTT)"
    )
    parser.add_argument(
        "--enable-mqtt",
        action="store_true",
        help="Enable MQTT publish (default: print only)",
    )
    parser.add_argument("--broker", default=os.getenv("MQTT_BROKER", DEFAULT_BROKER), help="MQTT broker host")
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", str(DEFAULT_PORT))), help="MQTT broker port")
    parser.add_argument("--topic-relay1", default=os.getenv("MQTT_TOPIC_RELAY1", DEFAULT_TOPIC_RELAY1), help="MQTT topic for relay 1")
    parser.add_argument("--topic-relay2", default=os.getenv("MQTT_TOPIC_RELAY2", DEFAULT_TOPIC_RELAY2), help="MQTT topic for relay 2")
    parser.add_argument("--payload-on", default=os.getenv("MQTT_PAYLOAD_ON", DEFAULT_PAYLOAD_ON), help="MQTT payload for turn on")
    parser.add_argument("--payload-off", default=os.getenv("MQTT_PAYLOAD_OFF", DEFAULT_PAYLOAD_OFF), help="MQTT payload for turn off")
    parser.add_argument("--client-id", default=os.getenv("MQTT_CLIENT_ID_VOICE", ""), help="MQTT client id (optional)")
    parser.add_argument("--device", default=os.getenv("MIC_DEVICE", DEFAULT_DEVICE), help="ALSA capture device")
    parser.add_argument(
        "--model",
        default=os.getenv("VOSK_MODEL", DEFAULT_MODEL),
        help="Path to Vosk model directory",
    )
    parser.add_argument(
        "--min-rms",
        type=float,
        default=float(os.getenv("VOICE_MIN_RMS", "0.003")),
        help="RMS threshold to ignore background noise (default: 0.003)",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=1.8,
        help="Minimum seconds between repeated publishes",
    )
    parser.add_argument("--username", default=os.getenv("MQTT_USER", DEFAULT_USER), help="MQTT username (optional)")
    parser.add_argument("--password", default=os.getenv("MQTT_PASS", DEFAULT_PASS), help="MQTT password (optional)")
    parser.add_argument(
        "--tls",
        action="store_true",
        help="Use TLS for MQTT (required for HiveMQ Cloud port 8883)",
    )
    parser.add_argument(
        "--intent-backend",
        default=os.getenv("VOICE_INTENT_BACKEND", "chatgpt"),
        choices=["rule", "chatgpt", "auto"],
        help="Command understanding backend (default: chatgpt): rule/chatgpt/auto",
    )
    parser.add_argument(
        "--stt-backend",
        default=os.getenv("VOICE_STT_BACKEND", "google"),
        choices=["whisper", "google", "vosk"],
        help="Speech-to-text AI backend to use after Vosk VAD (default: google)",
    )
    parser.add_argument(
        "--openai-api-key",
        default=os.getenv("OPENAI_API_KEY", ""),
        help="OpenAI API key for chatgpt intent backend",
    )
    parser.add_argument(
        "--openai-intent-model",
        default=os.getenv("OPENAI_INTENT_MODEL", "gpt-4o-mini,gpt-4o"),
        help="OpenAI model for chatgpt intent backend",
    )
    parser.add_argument(
        "--openai-base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI API base URL",
    )
    parser.add_argument(
        "--intent-timeout",
        type=float,
        default=float(os.getenv("OPENAI_INTENT_TIMEOUT", "6.0")),
        help="OpenAI intent request timeout (seconds)",
    )
    parser.add_argument(
        "--enable-qa",
        action="store_true",
        default=env_bool("VOICE_QA_ENABLE", True),
        help="Enable general Q&A when sentence is not a light command",
    )
    parser.add_argument(
        "--disable-qa",
        action="store_false",
        dest="enable_qa",
        help="Disable general Q&A mode",
    )
    parser.add_argument(
        "--qa-trigger",
        choices=["question", "all"],
        default=os.getenv("VOICE_QA_TRIGGER", "question"),
        help="When to send text to Q&A model",
    )
    parser.add_argument(
        "--qa-wake-word",
        default=os.getenv("VOICE_QA_WAKE_WORD", ""),
        help="Optional wake word required before Q&A",
    )
    parser.add_argument(
        "--qa-cooldown",
        type=float,
        default=float(os.getenv("VOICE_QA_COOLDOWN", "1.2")),
        help="Minimum seconds between Q&A requests",
    )
    parser.add_argument(
        "--qa-model",
        default=os.getenv("OPENAI_QA_MODEL", "gpt-4o-mini,gpt-4o"),
        help="OpenAI model list for Q&A (comma-separated)",
    )
    parser.add_argument(
        "--qa-timeout",
        type=float,
        default=float(os.getenv("OPENAI_QA_TIMEOUT", "10.0")),
        help="OpenAI Q&A request timeout (seconds)",
    )
    parser.add_argument(
        "--qa-system-prompt",
        default=os.getenv("OPENAI_QA_SYSTEM", "คุณคือผู้ช่วยภาษาไทย ตอบสั้น กระชับ เข้าใจง่าย"),
        help="System prompt for Q&A model",
    )
    parser.add_argument(
        "--qa-max-tokens",
        type=int,
        default=int(os.getenv("OPENAI_QA_MAX_TOKENS", "220")),
        help="Max tokens for Q&A response",
    )
    parser.add_argument(
        "--qa-speak",
        action="store_true",
        default=env_bool("VOICE_QA_SPEAK", True),
        help="Speak Q&A answer out loud using local TTS engine",
    )
    parser.add_argument(
        "--no-qa-speak",
        action="store_false",
        dest="qa_speak",
        help="Do not speak Q&A answer",
    )
    parser.add_argument(
        "--qa-voice-lang",
        default=os.getenv("VOICE_QA_LANG", "th"),
        help="Language for local TTS engine",
    )
    parser.add_argument(
        "--qa-voice-rate",
        type=int,
        default=int(os.getenv("VOICE_QA_RATE", "155")),
        help="Speech rate for local TTS engine",
    )
    parser.add_argument(
        "--qa-voice-cmd",
        default=os.getenv("VOICE_QA_CMD", ""),
        help="Custom TTS command; use {text} as placeholder",
    )
    args = parser.parse_args()

    try:
        from vosk import KaldiRecognizer, Model
    except Exception:
        print("missing dependency: vosk")
        print("install: pip install vosk")
        return 2

    mqtt = None
    client = None
    if args.enable_mqtt:
        try:
            import paho.mqtt.client as mqtt
        except Exception:
            print("missing dependency: paho-mqtt")
            print("install: pip install paho-mqtt")
            return 2

    try:
        bad_model_files = validate_vosk_model(args.model)
        if bad_model_files:
            print(f"error: invalid or incomplete Vosk model at {args.model}")
            for p, reason in bad_model_files:
                print(f" - {reason}: {p}")
            print("tip: re-download and extract a complete model (example: vosk-model-small-th-0.22)")
            print("tip: run setup_vosk_model.bat or set MODEL_PATH to a valid model folder")
            return 2

        model = Model(args.model)
    except Exception as exc:
        print(f"error: cannot load Vosk model at {args.model}: {exc}")
        return 2

    if args.enable_mqtt:
        client_id = (args.client_id or "").strip() or build_default_client_id()
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        if args.username:
            client.username_pw_set(args.username, args.password or None)
        if args.tls:
            client.tls_set()
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        try:
            client.connect(args.broker, args.port, keepalive=30)
        except Exception as exc:
            print(f"error: cannot connect MQTT broker {args.broker}:{args.port}: {exc}")
            return 2
        client.loop_start()

    try:
        audio_source, in_channels, active_device, audio_backend = open_audio_input(args.device)
    except Exception as exc:
        print(f"error: audio input init failed: {exc}")
        if "sounddevice" in str(exc).lower():
            print("tip: install sounddevice -> pip install sounddevice")
        return 2

    mode = "mqtt+print" if args.enable_mqtt else "print-only"
    print(
        f"Listening... backend={audio_backend}, device={active_device}, "
        f"input_channels={in_channels}, mode={mode}"
    )
    if args.enable_mqtt:
        print(
            f"MQTT target: {args.broker}:{args.port} "
            f"relay1={args.topic_relay1} relay2={args.topic_relay2}"
        )
    safe_print("คำสั่งที่รองรับ:")
    safe_print("- เปิดไฟดวงที่ 1 / ปิดไฟดวงที่ 1")
    safe_print("- เปิดไฟดวงที่ 2 / ปิดไฟดวงที่ 2")

    recognizer = KaldiRecognizer(model, 16000)
    intent_resolver = ChatGPTIntentResolver(
        api_key=args.openai_api_key,
        model=args.openai_intent_model,
        base_url=args.openai_base_url,
        timeout=args.intent_timeout,
    )
    qa_resolver = ChatGPTQA(
        api_key=args.openai_api_key,
        model=args.qa_model,
        base_url=args.openai_base_url,
        timeout=args.qa_timeout,
        system_prompt=args.qa_system_prompt,
        max_tokens=args.qa_max_tokens,
    )
    
    speaker_obj = None
    if args.enable_qa and args.qa_speak:
        try:
            from voice_status_cm5 import ThaiStatusSpeaker
            tts_backend = os.getenv("PETBOX_VOICE_BACKEND", "auto")
            speaker_obj = ThaiStatusSpeaker(
                alsa_device=args.device,
                backend=tts_backend,
                openai_api_key=args.openai_api_key,
                openai_model=os.getenv("OPENAI_TTS_MODEL", "tts-1"),
                openai_voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
                openai_base_url=args.openai_base_url,
                edge_voice=os.getenv("EDGE_TTS_VOICE", "th-TH-PremwadeeNeural")
            )
        except Exception as exc:
            print(f"warn: ThaiStatusSpeaker init failed: {exc}, fallback to original speak_answer")

    if args.intent_backend == "chatgpt" and not intent_resolver.enabled():
        print("warn: --intent-backend chatgpt set but OPENAI_API_KEY is missing; fallback to rules")
    if args.enable_qa and not qa_resolver.enabled():
        print("warn: Q&A enabled but OPENAI_API_KEY is missing; Q&A will be skipped")
    last_pub_ts = 0.0
    last_action = None
    last_light_id = None
    last_qa_ts = 0.0
    
    pcm_buffer = bytearray()
    current_utterance_max_rms = 0.0
    MAX_PCM_LEN = 15 * 16000 * 2  # Max 15 seconds

    try:
        while True:
            chunk = audio_source.read(4000)
            if not chunk:
                if audio_source.poll() is not None:
                    err = audio_source.get_stderr_text().strip()
                    print(f"audio source stopped: {err or 'unknown error'}")
                    break
                continue

            mono = maybe_downmix_to_mono(chunk, in_channels)
            if not mono:
                continue
                
            pcm_buffer.extend(mono)
            if len(pcm_buffer) > MAX_PCM_LEN:
                pcm_buffer = bytearray(pcm_buffer[-MAX_PCM_LEN:])
                
            rms = calculate_rms(mono)
            if rms > current_utterance_max_rms:
                current_utterance_max_rms = rms

            if recognizer.AcceptWaveform(mono):
                result_str = recognizer.Result() or "{}"
                if current_utterance_max_rms < args.min_rms:
                    current_utterance_max_rms = 0.0
                    pcm_buffer.clear()
                    continue

                current_utterance_max_rms = 0.0
                result = json.loads(result_str)
                vosk_text = result.get("text", "").strip()
                text = vosk_text
                
                if vosk_text:
                    safe_print(f"[VAD Triggered] Vosk heard: {vosk_text}")
                    if args.stt_backend == "whisper" and intent_resolver.enabled():
                        whisper_result = transcribe_whisper_openai(
                            pcm_buffer,
                            api_key=intent_resolver.api_key,
                            base_url=intent_resolver.base_url
                        )
                        if whisper_result:
                            text = whisper_result
                    elif args.stt_backend == "google":
                        google_result = transcribe_google_free(pcm_buffer)
                        if google_result:
                            # Use Google's result which is very accurate for Thai
                            text = google_result
                        
                pcm_buffer.clear()
                
                if not text:
                    continue
                safe_print(f"heard: {text}")

                cmd, src = resolve_light_command(text, args, intent_resolver)
                if cmd is None:
                    if not args.enable_qa:
                        continue
                    if args.qa_trigger == "question" and not is_question_like(text):
                        continue

                    ask_text = text.strip()
                    wake_word = (args.qa_wake_word or "").strip()
                    if wake_word:
                        if wake_word not in ask_text:
                            continue
                        ask_text = ask_text.replace(wake_word, "", 1).strip()
                        if not ask_text:
                            continue

                    now = time.time()
                    if now - last_qa_ts < args.qa_cooldown:
                        continue
                        
                    answer = None
                    ask_text_collapsed = ask_text.replace(" ", "")
                    # Match many possible STT mistakes for 201, 2-201, สองศูนย์หนึ่ง, สองศูนย์นิ่ง, สองร้อยหนึ่ง
                    if any(kw in ask_text_collapsed for kw in ["201", "2-201", "สองศูนย์", "สองร้อย"]):
                        answer = "จุดหมายคือห้อง 2-201 ซึ่งอยู่ที่ชั้น 2 ห้อง 201 ของตึก Drawing คณะวิศวกรรมศาสตร์ มหาวิทยาลัยเชียงใหม่ กรุณาไปที่ตึก Drawing ก่อน จากนั้นขึ้นไปชั้น 2 และมองหาป้ายห้อง 201"
                    else:
                        answer = qa_resolver.ask(ask_text)
                        
                    if not answer:
                        continue
                    safe_print(f"qa: {answer}")
                    speak_answer(answer, args, speaker_obj=speaker_obj)
                    
                    time.sleep(0.1)
                    if hasattr(audio_source, "_q"):
                        try:
                            with audio_source._q.mutex:
                                audio_source._q.queue.clear()
                        except Exception:
                            pass
                            
                    last_qa_ts = time.time()
                    continue
                action, light_id = cmd
                action_th = "เปิด" if action == "on" else "ปิด"
                payload_value = args.payload_on if action == "on" else args.payload_off
                target_topic = args.topic_relay1 if light_id == 1 else args.topic_relay2

                safe_print(f"match: {action_th}ไฟดวงที่ {light_id} (via {src})")
                now = time.time()
                if (
                    now - last_pub_ts < args.cooldown
                    and light_id == last_light_id
                    and action == last_action
                ):
                    continue

                payload = {
                    "command": "turn_on" if action == "on" else "turn_off",
                    "light": light_id,
                    "text": text,
                    "ts": int(now),
                }
                if args.enable_mqtt:
                    info = client.publish(
                        target_topic, payload_value, qos=1, retain=False
                    )
                    info.wait_for_publish(timeout=2.0)
                    print(
                        f"MQTT published: topic={target_topic} payload={payload_value} "
                        f"meta={payload}"
                    )
                else:
                    print(
                        f"dry-run mqtt -> topic={target_topic} payload={payload_value} "
                        f"meta={payload}"
                    )

                last_pub_ts = now
                last_action = action
                last_light_id = light_id
    except KeyboardInterrupt:
        print("\nstop")
    finally:
        try:
            if hasattr(audio_source, "terminate"):
                audio_source.terminate()
            if hasattr(audio_source, "wait"):
                audio_source.wait(timeout=1.0)
            if hasattr(audio_source, "close"):
                audio_source.close()
        except Exception:
            pass
        if client is not None:
            client.disconnect()
            client.loop_stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
