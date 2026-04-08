# Camera AI Streaming Example

ตัวอย่างนี้ออกแบบโดยยึดหลักว่า Raspberry Pi ต้องเบาที่สุด และย้ายงานหนักทั้งหมดไปที่คอมพิวเตอร์ AI server

## Architecture

```text
Pi Camera
  -> libcamera-vid (H.264 hardware encode)
  -> UDP MPEG-TS / H.264 stream
  -> Computer AI Server
       -> OpenCV/FFmpeg decode
       -> Object Detection
       -> FastAPI backend
       -> WebSocket to browser
       -> HTML5 Canvas overlay
```

## Why this design

- Raspberry Pi ทำแค่จับภาพ + hardware encode + ส่ง stream
- ไม่มี Python loop สำหรับ MJPEG บน Pi
- งาน decode, AI inference, metadata, web rendering อยู่ฝั่งคอมพิวเตอร์ทั้งหมด
- Dashboard วาด bounding box จาก metadata แยกจากภาพวิดีโอ จึงต่อยอด tracking, zones, alerts ได้ง่าย

## Project structure

```text
camera_ai_streaming/
├── README.md
├── pi_sender/
│   ├── config.env.example
│   └── start_h264_udp.sh
└── server/
    ├── requirements.txt
    └── app/
        ├── __init__.py
        ├── config.py
        ├── models.py
        ├── inference.py
        ├── stream_receiver.py
        ├── pipeline.py
        ├── main.py
        └── static/
            ├── index.html
            ├── app.js
            └── styles.css
```

## 1. Raspberry Pi sender

Pi ใช้ `libcamera-vid` เพื่อดึงภาพจาก CSI camera แล้ว encode เป็น H.264 ผ่าน hardware encoder โดยตรง

### Example run

1. คัดลอกไฟล์ config

```bash
cd camera_ai_streaming/pi_sender
cp config.env.example .env
```

2. แก้ `SERVER_HOST` ให้เป็น IP ของคอมพิวเตอร์ AI server

3. รันสตรีม

```bash
bash start_h264_udp.sh
```

สิ่งสำคัญ:
- ใช้ `--inline` เพื่อแนบ SPS/PPS เป็นระยะ ทำให้ฝั่ง receiver ต่อ stream ได้ง่ายขึ้น
- ใช้ `--nopreview` เพื่อลด overhead
- ใช้ H.264 ผ่าน hardware encoder ของ Pi แทน MJPEG ใน Python

## 2. Computer AI server

ฝั่งคอมพิวเตอร์มี 4 หน้าที่:

- รับ stream จาก Pi
- decode frame
- ทำ object detection
- ส่ง frame + metadata ไปยัง web dashboard ผ่าน FastAPI WebSocket

### Install

```bash
cd camera_ai_streaming/server
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
cd camera_ai_streaming/server
. .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

เปิดหน้า dashboard ที่ `http://SERVER_IP:8000`

## Stream URL notes

ค่า default ของ receiver ใช้:

```text
udp://0.0.0.0:5000?overrun_nonfatal=1&fifo_size=5000000
```

ถ้าระบบเครือข่ายมี packet loss มาก ให้พิจารณา:
- เปลี่ยนเป็น SRT/RTSP เมื่ออยากได้ความทนทานสูงขึ้น
- ลด resolution/fps ที่ฝั่ง Pi
- เพิ่ม buffer ที่ฝั่ง decode

## AI model notes

ตัวอย่างนี้มี detector 2 แบบ:

- `YoloDetector`: ใช้ `ultralytics` ถ้ามีโมเดลจริง
- `DummyDetector`: fallback สำหรับทดสอบ pipeline โดยยังไม่ติดตั้งโมเดล

เปลี่ยน detector ด้วย env vars เช่น:

```bash
export DETECTOR_BACKEND=yolo
export YOLO_MODEL_PATH=yolov8n.pt
```

## How each part works

### `pi_sender/start_h264_udp.sh`
สคริปต์ส่ง H.264 จาก Pi ไปยัง server ผ่าน UDP โดยใช้ `libcamera-vid`

### `app/stream_receiver.py`
เปิด stream ด้วย OpenCV/FFmpeg แล้วดึง frame ล่าสุดต่อเนื่อง

### `app/inference.py`
มี interface สำหรับ detector และแปลงผลลัพธ์ให้อยู่ในรูป normalized bounding boxes

### `app/pipeline.py`
รัน loop หลัก: รับ frame -> inference -> encode JPEG สำหรับ browser -> เก็บ latest packet ให้ websocket ส่งต่อ

### `app/main.py`
FastAPI backend สำหรับ:
- เสิร์ฟหน้าเว็บ dashboard
- เปิด WebSocket `/ws/live`
- ตอบ API `/api/health` และ `/api/detections/latest`

### `static/app.js`
ฝั่ง browser รับภาพและ metadata ผ่าน WebSocket แล้ววาดลง canvas พร้อม bounding boxes, label, confidence

## Production extension ideas

1. เปลี่ยน transport จาก UDP เป็น RTSP หรือ SRT หากต้องข้ามหลาย network segment
2. เพิ่ม tracker เช่น ByteTrack เพื่อคง ID ของวัตถุ
3. แยก process decode และ inference ถ้าต้องการ scale สูงขึ้น
4. ส่ง metadata ไป message broker หรือ database เพื่อทำ analytics ย้อนหลัง
5. เพิ่ม auth ที่ FastAPI ก่อนเปิด dashboard ให้ผู้ใช้จริง

## Python Numeric Transport (Pi -> PC)

โหมดนี้ใช้ Python ทั้งสองฝั่ง และส่ง frame เป็นชุดตัวเลขไบต์ (JPEG bytes + header) ไปยัง PC แล้วค่อย decode เป็นภาพที่ PC

### PC (receiver)

```bash
cd /home/pj/ws/Petbox/camera_ai_streaming/pc_receiver
python3 receive_numeric_tcp.py --bind-host 0.0.0.0 --bind-port 9000 --show
```

### Pi (sender)

```bash
cd /home/pj/ws/Petbox/camera_ai_streaming/pi_sender
python3 send_numeric_tcp.py --server-host 100.110.201.13 --server-port 9000 --width 960 --height 540 --fps 12 --jpeg-quality 70
```

ไฟล์ที่เกี่ยวข้อง:
- `pi_sender/send_numeric_tcp.py`
- `pc_receiver/receive_numeric_tcp.py`

หมายเหตุ: รูปแบบข้อมูลเป็น custom frame protocol (`PBX1` header + payload) ไม่ใช่ MJPEG HTTP stream
