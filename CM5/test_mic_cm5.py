#!/usr/bin/env python3
import argparse
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave


PINS = {
    # User requested CM5 GPIO numbers (not physical header pin numbers)
    "LRCLK": 26,
    "DOUT": 27,
    "BCLK": 49,
}

ALT_I2S0_PINS = {
    # Common Linux I2S0 pin group on Raspberry Pi
    "ALT_LRCLK": 19,
    "ALT_DIN": 20,
    "ALT_DOUT": 21,
    "ALT_BCLK": 18,
}


def run_cmd(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def require_cmd(name):
    if not shutil.which(name):
        raise RuntimeError(f"command not found: {name}")


def print_pin_status():
    print("== CM5 I2S Mic Pin Status ==")
    for name, pin in PINS.items():
        code, out, err = run_cmd(["pinctrl", "get", str(pin)])
        if code == 0:
            print(f"{name:6s} GPIO{pin:>2}: {out}")
        else:
            print(f"{name:6s} GPIO{pin:>2}: read failed ({err or 'unknown error'})")
    print("\n== ALT I2S0 Pin Status (reference) ==")
    for name, pin in ALT_I2S0_PINS.items():
        code, out, err = run_cmd(["pinctrl", "get", str(pin)])
        if code == 0:
            print(f"{name:8s} GPIO{pin:>2}: {out}")
        else:
            print(f"{name:8s} GPIO{pin:>2}: read failed ({err or 'unknown error'})")


def list_capture_devices():
    code, out, err = run_cmd(["arecord", "-l"])
    if code != 0:
        print(f"\nwarning: unable to list capture devices: {err or 'unknown error'}")
        return "", []
    print("\n== arecord -l ==")
    print(out if out else "(no capture devices reported)")
    devices = []
    for line in out.splitlines():
        m = re.search(r"card\s+(\d+):.*device\s+(\d+):", line)
        if m:
            devices.append(f"hw:{m.group(1)},{m.group(2)}")
    return out, devices


def detect_playback_only_overlay():
    for cfg in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if not os.path.exists(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        if "dtoverlay=max98357a" in text:
            return True, cfg
    return False, ""


def record_wav(path, duration, rate, channels, fmt, device):
    cmd = [
        "arecord",
        "-q",
        "-d",
        str(duration),
        "-r",
        str(rate),
        "-c",
        str(channels),
        "-f",
        fmt,
        "-t",
        "wav",
    ]
    if device:
        cmd += ["-D", device]
    cmd.append(path)
    code, _, err = run_cmd(cmd)
    if code != 0:
        raise RuntimeError(f"arecord failed: {err or 'unknown error'}")
    return channels


def _decode_samples(raw, sampwidth):
    if sampwidth == 1:
        # WAV 8-bit PCM is unsigned.
        return [b - 128 for b in raw]
    if sampwidth == 2:
        n = len(raw) // 2
        return struct.unpack("<" + "h" * n, raw)
    if sampwidth == 3:
        out = []
        for i in range(0, len(raw) - 2, 3):
            b0 = raw[i]
            b1 = raw[i + 1]
            b2 = raw[i + 2]
            v = b0 | (b1 << 8) | (b2 << 16)
            if v & 0x800000:
                v -= 1 << 24
            out.append(v)
        return out
    if sampwidth == 4:
        n = len(raw) // 4
        return struct.unpack("<" + "i" * n, raw)
    raise ValueError(f"unsupported sample width: {sampwidth}")


def analyze_wav(path):
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.getnframes()
        raw = wf.readframes(frames)

    samples = _decode_samples(raw, sampwidth)
    if not samples:
        return {
            "channels": channels,
            "sampwidth": sampwidth,
            "rate": rate,
            "frames": frames,
            "peak": 0.0,
            "rms": 0.0,
            "dbfs": float("-inf"),
        }

    max_int = float((1 << (8 * sampwidth - 1)) - 1)
    peak_abs = max(abs(v) for v in samples)
    mean_sq = sum(float(v) * float(v) for v in samples) / len(samples)
    rms_abs = math.sqrt(mean_sq)

    peak = peak_abs / max_int
    rms = rms_abs / max_int
    dbfs = 20.0 * math.log10(rms) if rms > 0.0 else float("-inf")

    # Per-channel analysis (important for 2ch captures where one channel may be mostly silent)
    channel_stats = []
    for ch in range(channels):
        ch_samples = samples[ch::channels]
        if not ch_samples:
            channel_stats.append({"peak": 0.0, "rms": 0.0, "dbfs": float("-inf")})
            continue
        ch_peak_abs = max(abs(v) for v in ch_samples)
        ch_mean_sq = sum(float(v) * float(v) for v in ch_samples) / len(ch_samples)
        ch_rms_abs = math.sqrt(ch_mean_sq)
        ch_peak = ch_peak_abs / max_int
        ch_rms = ch_rms_abs / max_int
        ch_dbfs = 20.0 * math.log10(ch_rms) if ch_rms > 0.0 else float("-inf")
        channel_stats.append({"peak": ch_peak, "rms": ch_rms, "dbfs": ch_dbfs})

    best_channel_rms = max(ch["rms"] for ch in channel_stats)

    return {
        "channels": channels,
        "sampwidth": sampwidth,
        "rate": rate,
        "frames": frames,
        "peak": peak,
        "rms": rms,
        "dbfs": dbfs,
        "per_channel": channel_stats,
        "best_channel_rms": best_channel_rms,
    }


def maybe_playback(path, device):
    cmd = ["aplay", "-q"]
    if device:
        cmd += ["-D", device]
    cmd.append(path)
    code, _, err = run_cmd(cmd)
    if code != 0:
        raise RuntimeError(f"aplay failed: {err or 'unknown error'}")


def main():
    parser = argparse.ArgumentParser(
        description="CM5 I2S microphone test (GPIO26/27/49)"
    )
    parser.add_argument("--duration", type=int, default=4, help="Record duration (seconds)")
    parser.add_argument("--rate", type=int, default=48000, help="Sample rate")
    parser.add_argument("--channels", type=int, default=1, help="Capture channels")
    parser.add_argument(
        "--format",
        default="S32_LE",
        help="arecord format (e.g. S16_LE, S24_LE, S32_LE)",
    )
    parser.add_argument("--device", default="", help="Capture ALSA device, e.g. hw:0,0")
    parser.add_argument("--playback", action="store_true", help="Play back captured file")
    parser.add_argument(
        "--play-device", default="", help="Playback ALSA device, e.g. hw:1,0"
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output WAV path (default: temp file auto removed)",
    )
    parser.add_argument(
        "--min-rms",
        type=float,
        default=0.003,
        help="Pass threshold for normalized RMS (0.0-1.0)",
    )
    args = parser.parse_args()

    try:
        require_cmd("pinctrl")
        require_cmd("arecord")
        if args.playback:
            require_cmd("aplay")
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 2

    print_pin_status()
    capture_text, capture_devices = list_capture_devices()

    selected_device = args.device
    if not selected_device and capture_devices:
        selected_device = capture_devices[0]
        print(f"\nAuto select capture device: {selected_device}")
    if not selected_device and not capture_devices:
        only_speaker, cfg_path = detect_playback_only_overlay()
        print("\nerror: ไม่พบอุปกรณ์บันทึกเสียง (capture) ใน ALSA")
        if only_speaker:
            print(
                f"hint: พบ `dtoverlay=max98357a` ใน {cfg_path} ซึ่งเป็น playback-only"
            )
        print("tip: ต้องมี overlay/codec สำหรับไมค์ก่อน แล้วค่อยระบุ --device hw:X,Y")
        return 1

    temp_path = None
    if args.output:
        wav_path = args.output
    else:
        f = tempfile.NamedTemporaryFile(prefix="cm5_mic_", suffix=".wav", delete=False)
        f.close()
        temp_path = f.name
        wav_path = f.name

    print(
        f"\nRecord {args.duration}s @ {args.rate}Hz, {args.channels}ch, fmt={args.format}"
        + f", dev={selected_device}"
    )
    try:
        used_channels = args.channels
        try:
            used_channels = record_wav(
                path=wav_path,
                duration=args.duration,
                rate=args.rate,
                channels=used_channels,
                fmt=args.format,
                device=selected_device,
            )
        except RuntimeError as exc:
            # Common on I2S DMIC: capture supports 2ch only.
            if (
                "Channels count non available" in str(exc)
                and used_channels == 1
            ):
                print("info: 1ch ใช้ไม่ได้, retry ด้วย 2ch อัตโนมัติ")
                try:
                    used_channels = record_wav(
                        path=wav_path,
                        duration=args.duration,
                        rate=args.rate,
                        channels=2,
                        fmt=args.format,
                        device=selected_device,
                    )
                except RuntimeError as exc2:
                    print(f"error: {exc2}")
                    print(
                        "tip: ลอง --channels 2 และ/หรือ --format S16_LE "
                        f"บน --device {selected_device}"
                    )
                    return 1
            else:
                print(f"error: {exc}")
                if "capture slave is not defined" in str(exc):
                    print("tip: default ALSA เป็น playback-only, ให้ระบุ --device hw:X,Y")
                else:
                    print("tip: ลองระบุ --device hw:X,Y หลังเช็กผลจาก arecord -l")
                return 1

        stats = analyze_wav(wav_path)
        print("\n== Capture Analysis ==")
        print(
            f"channels={stats['channels']} sampwidth={stats['sampwidth']}B "
            f"rate={stats['rate']} frames={stats['frames']}"
        )
        if used_channels != args.channels:
            print(f"note: requested {args.channels}ch but recorded with {used_channels}ch")
        print(
            f"peak={stats['peak']:.6f} rms={stats['rms']:.6f} "
            f"level={stats['dbfs']:.2f} dBFS"
        )
        for i, ch in enumerate(stats["per_channel"]):
            print(
                f"ch{i}: peak={ch['peak']:.6f} rms={ch['rms']:.6f} "
                f"level={ch['dbfs']:.2f} dBFS"
            )

        if stats["best_channel_rms"] >= args.min_rms:
            print(
                f"PASS: mic signal detected (best-channel rms >= {args.min_rms:.6f})"
            )
        else:
            print(
                "LOW: signal is weak/silent "
                f"(best-channel rms < {args.min_rms:.6f})"
            )
            print("tip: พูดใกล้ไมค์, เช็ก card/device, และเช็ก dtoverlay I2S")

        if args.playback:
            print("\nPlayback captured audio")
            maybe_playback(wav_path, args.play_device or "")
            print("Playback done")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
