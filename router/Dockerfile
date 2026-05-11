FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY smart_model_router.py .

EXPOSE 40115

CMD ["python", "smart_model_router.py"]
