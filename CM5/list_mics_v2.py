import speech_recognition as sr
import pyaudio

print("--- PyAudio Check ---")
p = pyaudio.PyAudio()
count = p.get_device_count()
print(f"Device count: {count}")
for i in range(count):
    info = p.get_device_info_by_index(i)
    print(f"[{i}] {info['name']}")

print("\n--- SpeechRecognition Check ---")
try:
    mics = sr.Microphone.list_microphone_names()
    print(f"Mics list: {mics}")
except Exception as e:
    print(f"SR Error: {e}")
