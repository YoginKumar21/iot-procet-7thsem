# WebUI ESP32 Firebase 4Relay - Backend Dockerization

This repository contains a Flask-based backend for a Mech assistant. Below are quick steps to build and run it in Docker.

Prerequisites:
- Docker installed

Build the image:

```bash
docker build -t mech-backend:latest .
```

Run the container (recommended: bind `firebase_key.json` and any `.env` variables)

```bash
docker run -d \
  -p 5000:5000 \
  -v $(pwd)/firebase_key.json:/app/firebase_key.json:ro \
  --name mech-backend \
  mech-backend:latest
```

Notes:
- The `yolov8n.pt` model file is copied into the image if present in the repo. To reduce image size, consider mounting it as a volume or using a remote model store.
- For security, prefer using Docker secrets (or bind-mount `firebase_key.json`) instead of baking credentials into the image.
- The Dockerfile installs CPU PyTorch wheels. If you need GPU support, update the Dockerfile accordingly.

If you want, I can build and run the image locally and verify that the Flask server comes up.