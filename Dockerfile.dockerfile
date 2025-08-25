FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver libnss3 libatk-bridge2.0-0 libxkbcommon0 \
    libgbm1 libgtk-3-0 libasound2 fonts-liberation curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/bin/chromedriver
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data && useradd -ms /bin/bash appuser && chown -R appuser:appuser /data /app
USER appuser

CMD ["python", "app.py"]
