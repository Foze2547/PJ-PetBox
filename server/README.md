# Camera AI Streaming Server

FastAPI server for live video streaming and object detection (YOLO) with optional GPU acceleration.

## 1. Requirements

- Windows 10/11
- Python 3.10+
- NVIDIA GPU + up-to-date driver (for GPU mode)

## 2. Install Dependencies

From project root:

```bat
cd C:\Users\User\Downloads\server
py -m venv .venv
.\.venv\Scripts\activate
```

Install base dependencies:

```bat
pip install -r requirements.txt
```

## 3. Install PyTorch for GPU (CUDA)

`ultralytics` uses PyTorch underneath. To run on GPU, install CUDA-enabled PyTorch.

Example (CUDA 12.1):

```bat
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

If you do not have CUDA or GPU, install CPU build instead:

```bat
pip install torch torchvision torchaudio
```

## 4. Runtime Configuration

Set environment variables in Command Prompt before running.

### Minimum settings for YOLO + GPU

```bat
set DETECTOR_BACKEND=yolo
set YOLO_DEVICE=auto
set YOLO_MODEL_PATH=yolov8n.pt
set YOLO_CONFIDENCE=0.35
set SWAP_RB=1
```

`YOLO_DEVICE` options:

- `auto` : use `cuda:0` when available, otherwise CPU
- `cuda:0` : force first GPU
- `cpu` : force CPU

`SWAP_RB` options:

- `1` : swap red/blue channels (use this when people look blue)
- `0` : keep original channel order

### Stream source

Default:

- `udp://0.0.0.0:5000?overrun_nonfatal=1&fifo_size=5000000`

Example RTSP camera:

```bat
set STREAM_URL=rtsp://user:pass@camera-ip:554/stream
```

### Numeric TCP source (Pi Python sender)

Use this mode when Pi runs `pi_sender/send_numeric_tcp.py`.

```bat
set SOURCE_MODE=numeric_tcp
set NUMERIC_BIND_HOST=0.0.0.0
set NUMERIC_BIND_PORT=9000
```

Pi side example:

```bash
python3 send_numeric_tcp.py --server-host <PC_IP> --server-port 9000
```

## 5. Run Server

```bat
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open:

- UI: `http://localhost:8000`
- Health API: `http://localhost:8000/api/health`
- Detection API: `http://localhost:8000/api/detections/latest`

## 6. Verify GPU Is Used

1. Ensure health endpoint shows YOLO enabled:

```json
{
  "detector_backend": "yolo",
  "yolo_device": "auto"
}
```

2. Check terminal logs and GPU usage (`nvidia-smi`) while inference is running.

## 7. Useful Tuning

- `INFERENCE_EVERY_N_FRAMES` (default `2`): larger value lowers GPU load
- `MAX_OUTPUT_FPS` (default `12`): cap outgoing stream frame rate
- `JPEG_QUALITY` (default `80`): lower value saves bandwidth

## 8. Troubleshooting

- Black/no video: verify `STREAM_URL` source is valid and accessible.
- No detections: ensure `DETECTOR_BACKEND=yolo` and model file exists.
- GPU not used: install CUDA-enabled PyTorch and verify with `nvidia-smi`.
- Crash on start with YOLO: check model path in `YOLO_MODEL_PATH`.
