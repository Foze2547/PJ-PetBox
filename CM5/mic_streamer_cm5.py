#!/usr/bin/env python3
import argparse
import socket
import subprocess
import shutil
import time
import os

def build_parser():
    p = argparse.ArgumentParser(description="CM5 Microphone TCP Streamer")
    p.add_argument("--host", required=True, help="IP address of the PC running voice_to_mqtt.py")
    p.add_argument("--port", type=int, default=5000, help="TCP port (default: 5000)")
    p.add_argument("--device", default=os.getenv("MIC_DEVICE", "dsnoop:CARD=PetboxAudio,DEV=0"), help="ALSA capture device")
    p.add_argument("--rate", type=int, default=16000, help="Sample rate")
    p.add_argument("--channels", type=int, default=1, help="Channels (1=mono)")
    p.add_argument("--reconnect-delay", type=float, default=2.0, help="Seconds to wait before reconnecting")
    return p

def main():
    args = build_parser().parse_args()

    cmd = None
    if shutil.which("arecord"):
        fmt = "S16_LE"
        cmd = [
            "arecord",
            "-q",
            "-D", args.device,
            "-f", fmt,
            "-r", str(args.rate),
            "-c", str(args.channels),
            "-t", "raw"
        ]
    else:
        print("warn: 'arecord' not found. Ensure it is installed on the CM5.")
        return 1

    while True:
        print(f"Connecting to {args.host}:{args.port}...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            sock.connect((args.host, args.port))
            sock.settimeout(None)  # Blocking mode for streaming
            print("Connected! Starting audio capture...")
            
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            try:
                while True:
                    # Read chunk from arecord
                    data = proc.stdout.read(4000)
                    if not data:
                        err = proc.stderr.read().decode('utf-8', errors='ignore')
                        print(f"arecord stopped: {err}")
                        break
                        
                    # Send chunk over TCP
                    sock.sendall(data)
            except (ConnectionResetError, BrokenPipeError, socket.error) as e:
                print(f"TCP connection lost: {e}")
            finally:
                proc.terminate()
                proc.wait(timeout=1.0)
                
        except socket.error as e:
            print(f"Connection failed: {e}")
            
        finally:
            sock.close()
            
        print(f"Reconnecting in {args.reconnect_delay} seconds...")
        time.sleep(args.reconnect_delay)

if __name__ == "__main__":
    raise SystemExit(main())
