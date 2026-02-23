FROM python:3.12-slim

WORKDIR /app

# Install ffmpeg for video processing (yt-dlp dependency)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/

RUN mkdir -p data

CMD ["python", "-m", "src.main"]
