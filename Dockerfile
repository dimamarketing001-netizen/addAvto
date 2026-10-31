FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# With network_mode: host (compose.yaml), 0.0.0.0 here would mean uvicorn
# listens on ALL of the host's interfaces, not just loopback — a real
# exposure regression from the previous bridge+port-mapping setup, which
# only published 127.0.0.1:8000. Bind to loopback explicitly to keep the
# same "nginx-only, nothing else reaches it directly" posture.
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
