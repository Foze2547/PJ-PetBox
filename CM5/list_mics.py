import speech_recognition as sr
try:
    mics = sr.Microphone.list_microphone_names()
    for i, m in enumerate(mics):
        print(f"[{i}] {m}")
except Exception as e:
    print(f"Error: {e}")
