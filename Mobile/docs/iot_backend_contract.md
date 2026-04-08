# IoT Backend Contract (Flutter <-> Backend <-> MQTT)

## Architecture
- Mobile app calls Backend API only.
- Backend validates auth/permission.
- Backend publishes MQTT command to device topics.
- Device publishes retained state and event ack/error.
- Backend pushes realtime status to app by WebSocket.

## REST API
- `POST /api/devices/{deviceId}/control`
- Body:
```json
{
  "requestId": "req-20260315-0001",
  "channel": "out1",
  "value": 1,
  "source": "mobile-app",
  "userId": "user123",
  "ts": 1760000000
}
```

## MQTT Topics
- Command:
  - `mech/v1/device/{deviceId}/cmd/out1`
  - `mech/v1/device/{deviceId}/cmd/out2`
  - `mech/v1/device/{deviceId}/cmd/all`
- State:
  - `mech/v1/device/{deviceId}/state/out1`
  - `mech/v1/device/{deviceId}/state/out2`
  - `mech/v1/device/{deviceId}/state/online`
- Telemetry:
  - `mech/v1/device/{deviceId}/tele/info`
  - `mech/v1/device/{deviceId}/tele/rssi`
  - `mech/v1/device/{deviceId}/tele/ip`
- Event:
  - `mech/v1/device/{deviceId}/event/ack`
  - `mech/v1/device/{deviceId}/event/error`

## Example command payload (JSON)
```json
{
  "requestId": "req-20260315-0001",
  "action": "set",
  "value": 1,
  "source": "mobile-app",
  "userId": "user123",
  "ts": 1760000000
}
```

## Example state payload (retained)
```json
{
  "deviceId": "plug001",
  "channel": "out1",
  "value": 1,
  "online": true,
  "requestId": "req-20260315-0001",
  "ts": 1760000001
}
```

## Flutter config
- Backend URL: `IOT_BACKEND_URL` (default `http://10.0.2.2:3000`)
- Device ID: `IOT_DEVICE_ID` (default `plug001`)
