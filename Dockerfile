FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
WORKDIR /app/backend
EXPOSE 8000
# Hosts like Render set $PORT; fall back to 8000 locally
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
