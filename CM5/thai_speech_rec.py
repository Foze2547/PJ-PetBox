import speech_recognition as sr
from openai import OpenAI
import os
import time
import datetime
import threading
import subprocess
import ssl
import socket
import hashlib
import logging
from io import BytesIO
from gtts import gTTS
import pygame
import paho.mqtt.client as mqtt
import re

# --- Initial Environment Setup ---
os.environ["SDL_AUDIODRIVER"] = "alsa"
os.environ["AUDIODEV"] = "plughw:CARD=PetboxAudio,DEV=1"

# --- Logging Configuration ---
# Use a format that works well with systemd journal
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger("Petbox")

# --- Constants & Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "tts_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ป้องกันโปรแกรมค้างจากจุดที่รอโหลด Network นานเกินไป
socket.setdefaulttimeout(15.0)

# --- Hardware Setup (Speaker Amp) ---
def setup_hardware():
    try:
        # GPIO 45: ON_OFF_AUDIO (Speaker Enable)
        subprocess.run(["pinctrl", "set", "45", "op", "dh"], check=True)
        logger.info("🔈 Speaker amplifier enabled (GPIO 45: ON)")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Could not configure GPIO 45: {e}")
        return False

amp_enabled = setup_hardware()

# --- Configuration ---
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "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")
MQTT_BROKER = os.getenv("MQTT_BROKER", "058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER = os.getenv("MQTT_USER", "pikub")
MQTT_PASS = os.getenv("MQTT_PASS", "Password123!")

if not OPENAI_KEY:
    logger.error("❌ OPENAI_API_KEY not found in environment!")

client_ai = OpenAI(api_key=OPENAI_KEY, timeout=15.0)

# --- Audio Initialization (Pygame) ---
try:
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
except pygame.error as e:
    logger.warning(f"Failed to init pygame.mixer with specific settings: {e}")
    pygame.mixer.init()

speak_lock = threading.Lock()

# --- MQTT & Device State ---
device_states = {
    "relay1": "unknown",
    "relay2": "unknown",
    "motor": "unknown",
    "steer": "unknown",
    "status_text": "no data"
}

mqtt_client = mqtt.Client()

def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("✅ Connected to MQTT Broker. Subscribing to topics...")
        client.subscribe("mechcode/#")
        client.subscribe("robot/#")
    else:
        logger.error(f"❌ MQTT connection failed (RC: {rc})")

def on_mqtt_disconnect(client, userdata, rc):
    logger.warning(f"⚠️ Disconnected from MQTT (RC: {rc})")

def on_mqtt_message(client, userdata, msg):
    topic = msg.topic.strip()
    try:
        payload = msg.payload.decode("utf-8", errors="ignore").strip().lower()
    except:
        payload = str(msg.payload).strip().lower()
        
    updated = False
    
    # Mapping topics to state and announcement
    if topic == "robot/control/motor":
        if device_states["motor"] != payload:
            device_states["motor"] = payload
            updated = True
            logger.info(f"📡 Motor: {payload}")
            if payload == "forward": announce("กำลังเดินหน้า")
            elif payload == "backward": announce("กำลังถอยหลัง")
            elif payload in {"stop", "soft_stop", "hard_stop"}: announce("หยุดการเคลื่อนที่")

    elif topic == "robot/control/steer":
        if device_states["steer"] != payload:
            device_states["steer"] = payload
            updated = True
            logger.info(f"📡 Steer: {payload}")
            if payload == "left": announce("เลี้ยวซ้าย")
            elif payload == "right": announce("เลี้ยวขวา")
            elif payload == "reset": announce("ปรับทิศทางตรง")

    elif topic == "mechcode/relay1/state":
        if device_states["relay1"] != payload:
            device_states["relay1"] = payload
            updated = True
            logger.info(f"📡 Relay 1: {payload}")
            if payload in {"on", "1", "true"}: announce("เปิดไฟดวงที่หนึ่ง")
            else: announce("ปิดไฟดวงที่หนึ่ง")

    elif topic == "mechcode/relay2/state":
        if device_states["relay2"] != payload:
            device_states["relay2"] = payload
            updated = True
            logger.info(f"📡 Relay 2: {payload}")
            if payload in {"on", "1", "true"}: announce("เปิดไฟดวงที่สอง")
            else: announce("ปิดไฟดวงที่สอง")

def announce(text):
    threading.Thread(target=speak, args=(text,), daemon=True).start()

mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_disconnect = on_mqtt_disconnect
mqtt_client.on_message = on_mqtt_message

def start_mqtt():
    try:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        mqtt_client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start() 
    except Exception as e:
        logger.error(f"❌ Failed to start MQTT: {e}")

# --- TTS with Caching ---
def get_cache_path(text):
    """Generate a consistent filename for a given text string."""
    hash_val = hashlib.md5(text.encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, f"{hash_val}.mp3")

def speak(text):
    """Play speech with local caching to reduce latency/API usage."""
    if not text: return
    
    with speak_lock:
        try:
            cache_path = get_cache_path(text)
            
            if not os.path.exists(cache_path):
                logger.debug(f"Generating TTS for: {text}")
                tts = gTTS(text=text, lang='th')
                tts.save(cache_path)
            
            logger.info(f"🔊 Assistant: {text}")
            pygame.mixer.music.load(cache_path)
            pygame.mixer.music.play()
            
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        except Exception as e:
            logger.error(f"❌ Playback error: {e}")

# --- ChatGPT Integration ---
def ask_chatgpt(text):
    if not text: return None
    
    # Hardcoded local response example
    # Hardcoded local response example
    # Match "201" or "ห้อง 201" (digits or Thai words for numbers)
    # 2=สอง, 0=ศูนย์, 1=หนึ่ง
    room_pattern_2_201 = r"(ห้อง\s*)?(2|สอง)\s*(2|สอง)\s*(0|ศูนย์)\s*(1|หนึ่ง)"
    if re.search(room_pattern_2_201, text):
        return "จุดหมายคือห้อง 2-201 ซึ่งอยู่ที่ชั้น 2 ของตึก Drawing คณะวิศวกรรมศาสตร์ มหาวิทยาลัยเชียงใหม่ กรุณาไปที่ตึก Drawing ก่อน จากนั้นขึ้นไปชั้น 2 และมองหาป้ายห้อง 2-201"

    room_pattern_201 = r"(ห้อง\s*)?(2|สอง)\s*(0|ศูนย์)\s*(1|หนึ่ง)"
    if re.search(room_pattern_201, text):
        return "จุดหมายคือห้อง 201 ตึก 30 ปี กรุณาไปที่ตึก 30 ปี จากนั้นขึ้นไปชั้น 2 แล้วมองหาป้ายห้อง 201"

    # Quick command mapping
    if any(w in text for w in ["เดินหน้า", "เดิน", "ไปข้างหน้า"]): 
        mqtt_client.publish("robot/control/motor", "forward")
        return "กำลังเดินหน้าค่ะ"
    if any(w in text for w in ["ถอยหลัง", "เดินถอย", "ถอย"]):
        mqtt_client.publish("robot/control/motor", "backward")
        return "กำลังถอยหลังค่ะ"
    if any(w in text for w in ["หยุด", "จอด", "เบรก"]):
        mqtt_client.publish("robot/control/motor", "stop")
        return "หยุดทำงานแล้วค่ะ"

    # Relay Control (Lights)
    is_on = "เปิด" in text or "on" in text
    is_off = "ปิด" in text or "off" in text
    is_light = "ไฟ" in text or "relay" in text or "รีเลย์" in text

    if is_light and (is_on or is_off):
        action = "on" if is_on else "off"
        action_th = "เปิด" if is_on else "ปิด"
        
        target1 = "1" in text or "หนึ่ง" in text or "แรก" in text
        target2 = "2" in text or "สอง" in text
        target_all = "ทุก" in text or "หมด" in text or "ทั้งคู่" in text or "ทั้งสอง" in text

        if target_all or (not target1 and not target2):
            # If no specific target, but says "light", default to both
            mqtt_client.publish("mechcode/relay1/set", action)
            mqtt_client.publish("mechcode/relay2/set", action)
            if target_all:
                return f"กำลัง{action_th}ไฟทั้งหมดให้ค่ะ"
            else:
                return f"กำลัง{action_th}ไฟให้ทั้งสองดวงค่ะ"
        
        if target1:
            mqtt_client.publish("mechcode/relay1/set", action)
            if not target2: return f"กำลัง{action_th}ไฟดวงที่หนึ่งให้ค่ะ"
        if target2:
            mqtt_client.publish("mechcode/relay2/set", action)
            return f"กำลัง{action_th}ไฟดวงที่สองให้ค่ะ" if not target1 else f"กำลัง{action_th}ไฟทั้งสองดวงให้ค่ะ"

    try:
        logger.info("🤖 Asking ChatGPT...")
        now = datetime.datetime.now()
        thai_months = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
        current_date_th = f"{now.day} {thai_months[now.month-1]} {now.year + 543}"
        
        system_context = f"""คุณคือ Petbox ผู้ช่วยส่วนตัวแสนเป็นมิตร
วันที่: {current_date_th}
สถานะ: มอเตอร์={device_states['motor']}, Relay1={device_states['relay1']}, Relay2={device_states['relay2']}
ตอบเป็นภาษาไทย สั้น กระชับ เป็นกันเอง"""
        
        response = client_ai.chat.completions.create(
            model="gpt-4o-mini", # Optimized model
            messages=[
                {"role": "system", "content": system_context},
                {"role": "user", "content": text}
            ]
        )
        answer = response.choices[0].message.content
        logger.info(f"🤖 ChatGPT: {answer}")
        return answer
    except Exception as e:
        logger.error(f"❌ ChatGPT Error: {e}")
        return "ขออภัยค่ะ ฉันไม่สามารถติดต่อสมองกลได้ในขณะนี้"

# --- Main Recognition Loop ---
def wait_for_wake_word(r, source):
    wake_words = ["petbox", "pet box", "เพทบ็อก", "เพชรบ็อก", "สวัสดี", "สวัสสดี"]
    logger.info("🎧 Waiting for wake word ('Petbox' หรือ 'สวัสดี')...")
    
    while True:
        try:
            audio = r.listen(source, timeout=1, phrase_time_limit=3)
            text = r.recognize_google(audio, language="th-TH").lower()
            if any(word in text for word in wake_words):
                logger.info("✅ Wake word detected!")
                return True
        except (sr.WaitTimeoutError, sr.UnknownValueError):
            continue
        except Exception as e:
            logger.warning(f"Ambient listener: {e}")
            time.sleep(0.5)

def get_command(r, source):
    logger.info("🎙️ Listening for command...")
    try:
        audio = r.listen(source, timeout=5, phrase_time_limit=8)
        text = r.recognize_google(audio, language="th-TH")
        logger.info(f"🗣️ User: {text}")
        return text
    except (sr.WaitTimeoutError, sr.UnknownValueError):
        logger.info("⏰ Timeout or unrecognized, back to standby.")
    except Exception as e:
        logger.error(f"Command listener: {e}")
    return None

def main():
    logger.info("🚀 Starting Petbox Optimized Voice Assistant")
    start_mqtt()
    
    r = sr.Recognizer()
    r.pause_threshold = 0.5
    r.non_speaking_duration = 0.4

    # Determine Mic Index
    mic_idx = os.getenv("MIC_INDEX")
    if mic_idx:
        mic_idx = int(mic_idx)
    else:
        mics = sr.Microphone.list_microphone_names()
        for i, m in enumerate(mics):
            if "dmic" in m.lower() or "petbox" in m.lower():
                mic_idx = i
                break
    
    logger.info(f"🎤 Using Microphone Index: {mic_idx}")

    # Set up capture device environment for subprocesses if any (like whisper)
    # This ensures consistency even if we are not using the default card.
    os.environ["MICROPHONE_DEVICE"] = f"hw:{mic_idx}" if mic_idx is not None else "default"

    try:
        with sr.Microphone(device_index=mic_idx) as source:
            logger.info("Calibrating background noise...")
            r.adjust_for_ambient_noise(source, duration=2)
            logger.info("System Ready!")
            
            while True:
                if wait_for_wake_word(r, source):
                    speak("มีอะไรให้ฉันช่วยไหมคะ")
                    cmd = get_command(r, source)
                    if cmd:
                        resp = ask_chatgpt(cmd)
                        speak(resp)
                    time.sleep(0.5)
    except Exception as e:
        logger.critical(f"FATAL: Main loop crashed: {e}")
    finally:
        if amp_enabled:
            subprocess.run(["pinctrl", "set", "45", "op", "dl"], check=False)

if __name__ == "__main__":
    main()

