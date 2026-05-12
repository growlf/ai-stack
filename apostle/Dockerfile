FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends pciutils \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
WORKDIR /app
ENV APOSTLE_PORT=40116
# scripts/apostle.py and scripts/models.yaml are mounted via docker-compose volume
CMD ["python", "scripts/apostle.py", "serve"]
