#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import wave


DEFAULT_CONFIG_PATH = "/home/pj/ws/Petbox/speaker_cm5_config.json"
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
            "sample_rate": int(audio.get("sample_rate", 48000)),
            "channels": int(audio.get("channels", 2)),
            "tone_freq_hz": float(audio.get("tone_freq_hz", 1000.0)),
            "tone_duration_sec": float(audio.get("tone_duration_sec", 2.0)),
        },
    }


def run_cmd(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def list_playback_devices():
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

    # Prefer non-HDMI output for speaker tests.
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


def print_pin_status(pins):
    print("== CM5 Audio Pin Status ==")
    for name, pin in pins.items():
        code, out, err = run_cmd(["pinctrl", "get", str(pin)])
        if code == 0:
            print(f"{name:12s} GPIO{pin:>2}: {out}")
        else:
            print(f"{name:12s} GPIO{pin:>2}: read failed ({err or 'unknown error'})")


def set_audio_enable(pins, high):
    mode = "dh" if high else "dl"
    code, _, err = run_cmd(["pinctrl", "set", str(pins["ON_OFF_AUDIO"]), "op", mode])
    if code != 0:
        raise RuntimeError(f"set ON_OFF_AUDIO failed: {err or 'unknown error'}")


def generate_tone_wav(
    path, freq_hz=1000.0, duration_sec=2.0, sample_rate=48000, channels=2
):
    n_samples = int(duration_sec * sample_rate)
    amp = 0.35
    with wave.open(path, "w") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n_samples):
            sample = amp * math.sin(2.0 * math.pi * freq_hz * (i / sample_rate))
            s16 = int(sample * 32767)
            if channels == 1:
                wf.writeframesraw(struct.pack("<h", s16))
            else:
                # Send same tone to L/R to avoid silence on single-channel amps.
                wf.writeframesraw(struct.pack("<hh", s16, s16))


def play_tone(path, alsa_device=None):
    cmd = ["aplay", "-q"]
    if alsa_device:
        cmd += ["-D", alsa_device]
    cmd.append(path)
    code, _, err = run_cmd(cmd)
    if code != 0:
        raise RuntimeError(f"aplay failed: {err or 'unknown error'}")


def main():
    parser = argparse.ArgumentParser(description="CM5 speaker test using configurable audio pins")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to speaker config JSON")
    parser.add_argument("--freq", type=float, default=None, help="Tone frequency in Hz")
    parser.add_argument("--duration", type=float, default=None, help="Tone duration in seconds")
    parser.add_argument("--device", default=None, help="Optional ALSA device, e.g. hw:0,0")
    parser.add_argument("--channels", type=int, default=None, choices=[1, 2], help="WAV channels")
    parser.add_argument("--sample-rate", type=int, default=None, help="WAV sample rate")
    parser.add_argument(
        "--enable-active-low",
        action="store_true",
        help="Treat ON/OFF_AUDIO as active-low instead of active-high",
    )
    parser.add_argument("--keep-on", action="store_true", help="Keep ON_OFF_AUDIO high after test")
    parser.add_argument("--no-play", action="store_true", help="Only check/toggle pins without playing sound")
    args = parser.parse_args()

    cfg = load_config(args.config)
    pins = cfg["pins"]
    audio = cfg["audio"]

    freq = args.freq if args.freq is not None else audio["tone_freq_hz"]
    duration = args.duration if args.duration is not None else audio["tone_duration_sec"]
    channels = args.channels if args.channels is not None else audio["channels"]
    sample_rate = args.sample_rate if args.sample_rate is not None else audio["sample_rate"]
    device = args.device if args.device is not None else audio["alsa_device"]
    resolved_device = resolve_device(device)

    print(f"Config: {args.config}")
    print(f"ALSA device: {resolved_device or 'default'}")
    print_pin_status(pins)

    enable_on = not args.enable_active_low
    enable_off = not enable_on
    print(
        "\nEnable speaker power path "
        f"(ON/OFF_AUDIO -> {'LOW' if args.enable_active_low else 'HIGH'})"
    )
    set_audio_enable(pins, enable_on)
    print_pin_status(pins)

    if not args.no_play:
        with tempfile.NamedTemporaryFile(prefix="cm5_speaker_", suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            generate_tone_wav(
                wav_path,
                freq_hz=freq,
                duration_sec=duration,
                sample_rate=sample_rate,
                channels=channels,
            )
            print(
                f"\nPlay {freq:.1f}Hz tone for {duration:.1f}s ({channels}ch, {sample_rate}Hz)"
            )
            play_tone(wav_path, alsa_device=resolved_device or None)
            print("Playback done")
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)
    else:
        print("\nSkip playback (--no-play)")

    if args.keep_on:
        print("Leave ON/OFF_AUDIO HIGH (--keep-on)")
    else:
        print(
            "\nDisable speaker power path "
            f"(ON/OFF_AUDIO -> {'HIGH' if args.enable_active_low else 'LOW'})"
        )
        set_audio_enable(pins, enable_off)
        print_pin_status(pins)


if __name__ == "__main__":
    main()
