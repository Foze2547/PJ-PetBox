#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Stop and disable the unified daemon if it exists
sudo systemctl stop petbox-daemon.service || true
sudo systemctl disable petbox-daemon.service || true

# Copy and enable the 3 old services
sudo cp "$SCRIPT_DIR/petbox-camera-sender.service" /etc/systemd/system/
sudo cp "$SCRIPT_DIR/petbox-eyes.service" /etc/systemd/system/
sudo cp "$SCRIPT_DIR/petbox-voice.service" /etc/systemd/system/

sudo systemctl daemon-reload

sudo systemctl enable petbox-camera-sender.service
sudo systemctl enable petbox-eyes.service
sudo systemctl enable petbox-voice.service

sudo systemctl restart petbox-camera-sender.service
sudo systemctl restart petbox-eyes.service
sudo systemctl restart petbox-voice.service

sudo systemctl --no-pager --full status petbox-camera-sender.service || true
sudo systemctl --no-pager --full status petbox-eyes.service || true
sudo systemctl --no-pager --full status petbox-voice.service || true
