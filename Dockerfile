FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEATHERSTREAM_CONFIG=/config \
    WEATHERSTREAM_MUSIC=/music \
    WEATHERSTREAM_LIVE=/tmp/weatherstream/live \
    WEATHERSTREAM_PREVIEW=/tmp/weatherstream/preview.jpg

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core curl intel-media-va-driver vainfo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/weatherstream
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

RUN mkdir -p /config /music /tmp/weatherstream/live

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8787/health/live >/dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1"]
