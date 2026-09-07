FROM ubuntu:24.04 AS ffmpeg

ENV DEBIAN_FRONTEND=noninteractive
# BestSource in the blur tarball links libavutil.so.60 (FFmpeg 8). Ubuntu 24.04 only has 6.1.
RUN apt-get update && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
    && curl -4fL --retry 5 --retry-all-errors --connect-timeout 20 -o /tmp/ff.tar.xz \
        https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-shared-8.1.tar.xz \
    && mkdir -p /opt/ffmpeg \
    && tar -xJf /tmp/ff.tar.xz -C /opt/ffmpeg --strip-components=1 \
    && rm /tmp/ff.tar.xz \
    && test -x /opt/ffmpeg/bin/ffmpeg \
    && test -f /opt/ffmpeg/lib/libavutil.so.60


FROM ubuntu:24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/usr/local/lib/python3/dist-packages:/usr/local/lib/python3.12/dist-packages:/usr/local/lib/python3.12/site-packages \
    PKG_CONFIG_PATH=/usr/local/lib/pkgconfig \
    LD_LIBRARY_PATH=/usr/local/lib

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev python3-setuptools \
        build-essential pkg-config meson ninja-build \
        libzimg-dev wget ca-certificates tar \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --break-system-packages --no-cache-dir 'cython>=3.1'

WORKDIR /tmp/vs
RUN wget -qO vs.tgz https://github.com/vapoursynth/vapoursynth/archive/refs/tags/R73.tar.gz \
    && tar xzf vs.tgz --strip-components=1 \
    && meson setup build --prefix=/usr/local -Dbuildtype=release -Dpython.bytecompile=-1 \
    && meson compile -C build \
    && meson install -C build \
    && ldconfig \
    && pip3 install --break-system-packages --no-cache-dir . \
    && python3 -c "import vapoursynth; print(vapoursynth.__version__)" \
    && vspipe --version

WORKDIR /opt/blur
RUN wget -qO /tmp/blur.tar.gz https://github.com/f0e/blur/releases/download/v2.45/blur-Linux-Release-x64.tar.gz \
    && tar -xzf /tmp/blur.tar.gz \
    && CLI="$(find /opt/blur -maxdepth 1 -type f -name 'blur-cli*' | head -1)" \
    && ln -sf "$(basename "$CLI")" /opt/blur/blur-cli \
    && chmod +x "$CLI" /opt/blur/blur-cli \
    && rm /tmp/blur.tar.gz


FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BLUR_BIN=/opt/blur/blur-cli \
    DATA_DIR=/data \
    PATH="/opt/blur:/opt/ffmpeg/bin:/usr/local/bin:${PATH}" \
    LD_LIBRARY_PATH=/opt/ffmpeg/lib:/usr/local/lib \
    PYTHONPATH=/usr/local/lib/python3/dist-packages:/usr/local/lib/python3.12/dist-packages:/usr/local/lib/python3.12/site-packages

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip libpython3.12t64 \
        libvulkan1 libzimg2 libfftw3-single3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local /usr/local
COPY --from=build /opt/blur /opt/blur
COPY --from=ffmpeg /opt/ffmpeg /opt/ffmpeg
# R73 autoloads $libdir/vapoursynth only; VS_PLUGIN_PATH is ignored. BestSource is not LoadPlugin'd by blur.
RUN mkdir -p /usr/local/lib/vapoursynth \
    && ln -sf /opt/blur/vapoursynth-plugins/*.so /usr/local/lib/vapoursynth/ \
    && echo /usr/local/lib > /etc/ld.so.conf.d/vapoursynth.conf \
    && echo /opt/ffmpeg/lib > /etc/ld.so.conf.d/ffmpeg.conf \
    && ldconfig \
    && python3 -c "import vapoursynth as vs; c=vs.core; print([p.namespace for p in c.plugins()]); assert hasattr(c,'bs')" \
    && vspipe --version \
    && ffmpeg -version \
    && test -x /opt/blur/blur-cli

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static

VOLUME /data
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
