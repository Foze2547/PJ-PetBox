const canvas = document.getElementById("videoCanvas");
const ctx = canvas.getContext("2d");
const connState = document.getElementById("connState");
const frameIdEl = document.getElementById("frameId");
const objectCountEl = document.getElementById("objectCount");
const frameImage = new Image();

function connect() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${window.location.host}/ws/live`);

  ws.onopen = () => {
    connState.textContent = "Live";
  };

  ws.onclose = () => {
    connState.textContent = "Reconnecting";
    window.setTimeout(connect, 1000);
  };

  ws.onerror = () => {
    ws.close();
  };

  ws.onmessage = (event) => {
    const packet = JSON.parse(event.data);
    frameIdEl.textContent = packet.frame_id;
    objectCountEl.textContent = packet.detections.length;

    frameImage.onload = () => {
      if (canvas.width !== packet.width || canvas.height !== packet.height) {
        canvas.width = packet.width;
        canvas.height = packet.height;
      }

      ctx.drawImage(frameImage, 0, 0, canvas.width, canvas.height);
      drawDetections(packet.detections, canvas.width, canvas.height);
    };
    frameImage.src = `data:image/jpeg;base64,${packet.jpeg_b64}`;
  };
}

function drawDetections(detections, width, height) {
  ctx.lineWidth = 3;
  ctx.font = "600 18px 'IBM Plex Sans', sans-serif";
  ctx.textBaseline = "top";

  detections.forEach((det) => {
    const x = det.x1 * width;
    const y = det.y1 * height;
    const w = (det.x2 - det.x1) * width;
    const h = (det.y2 - det.y1) * height;
    const label = `${det.label} ${(det.confidence * 100).toFixed(1)}%`;

    ctx.strokeStyle = "#7ef9a9";
    ctx.fillStyle = "rgba(7, 20, 16, 0.82)";
    ctx.strokeRect(x, y, w, h);

    const textWidth = ctx.measureText(label).width;
    ctx.fillRect(x, Math.max(0, y - 28), textWidth + 18, 28);
    ctx.fillStyle = "#dffff0";
    ctx.fillText(label, x + 9, Math.max(0, y - 24));
  });
}

connect();
