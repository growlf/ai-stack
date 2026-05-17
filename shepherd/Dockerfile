FROM python:3.12-slim

# Probe binaries: nvidia-smi comes with the host driver via NVIDIA Container Toolkit;
# we install intel_gpu_top + rocm-smi-equivalents in optional layered overlays.
# Base image stays slim — heavy probe binaries land in overlay-specific Dockerfiles when needed.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shepherd_node /app/shepherd_node
COPY shepherd_control /app/shepherd_control
COPY ui /app/ui

ENV PYTHONUNBUFFERED=1
ENV SHEPHERD_NODE_PORT=40116
ENV SHEPHERD_CONTROL_PORT=40117

# Default entrypoint runs the node sidecar; override CMD to ["python","-m","shepherd_control"]
# for the control-plane deployment.
CMD ["python", "-m", "shepherd_node"]
