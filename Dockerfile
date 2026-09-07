FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BLUR_BIN=/opt/blur/blur-cli \
    DATA_DIR=/data \
    PATH="/opt/blur:/opt/blur/ffmpeg:/opt/blur/vapoursynth:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip \
        ffmpeg vapoursynth python3-vapoursynth \
        libvulkan1 wget ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/extract /opt/blur \
    && wget -qO /tmp/blur.tar.gz https://github.com/f0e/blur/releases/download/v2.45/blur-Linux-Release-x64.tar.gz \
    && tar -xzf /tmp/blur.tar.gz -C /tmp/extract \
    && BIN="$(find /tmp/extract -name blur-cli -type f | head -1)" \
    && cp -a "$(dirname "$BIN")/." /opt/blur/ \
    && chmod +x /opt/blur/blur-cli \
    && rm -rf /tmp/extract /tmp/blur.tar.gz

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static

VOLUME /data
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
