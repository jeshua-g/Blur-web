FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BLUR_BIN=/opt/blur/blur-cli \
    DATA_DIR=/data \
    PATH="/opt/blur:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev python3-setuptools \
        build-essential pkg-config \
        ffmpeg libvulkan1 libzimg-dev \
        wget ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu has no vapoursynth package; wheels compile against libzimg
RUN pip3 install --no-cache-dir cython vapoursynth

RUN mkdir -p /tmp/extract /opt/blur \
    && wget -qO /tmp/blur.tar.gz https://github.com/f0e/blur/releases/download/v2.45/blur-Linux-Release-x64.tar.gz \
    && tar -xzf /tmp/blur.tar.gz -C /tmp/extract \
    && cp -a /tmp/extract/. /opt/blur/ \
    && CLI="$(find /opt/blur -maxdepth 1 -type f -name 'blur-cli*' | head -1)" \
    && ln -sf "$(basename "$CLI")" /opt/blur/blur-cli \
    && chmod +x "$CLI" /opt/blur/blur-cli \
    && rm -rf /tmp/extract /tmp/blur.tar.gz

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static

VOLUME /data
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
