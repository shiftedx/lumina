# Base tags must match .tool-versions (the single toolchain pin; test_v1_packaging enforces it).
FROM node:22.17.1-bookworm-slim@sha256:2fa754a9ba4d7adbd2a51d182eaabbe355c82b673624035a38c0d42b08724854 AS frontend-build

WORKDIR /build/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ffmpeg/ffprobe are jellyfin-ffmpeg (GPL; QSV/VAAPI, OpenCL tone-mapping, chromaprint): one .deb per
# architecture, pinned by sha256. amd64 also gets Intel's OpenCL runtime for tonemap_opencl; it is not
# in Debian trixie, so it comes from Intel's releases at the versions Jellyfin's trixie image uses.
# Provenance and upgrade steps: docker/README.md.
FROM scratch AS media-debs-amd64
ADD --checksum=sha256:30d9957e80051dd45740d4d4509941fdf2babf5a29c79bee109310b31dae0f6f https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.2-5/jellyfin-ffmpeg8_8.1.2-5-trixie_amd64.deb /debs/
ADD --checksum=sha256:6031a63d6e8a12ce61c14efc15f2c8e727061286e3820b8594e6d00615e04d54 https://github.com/intel/compute-runtime/releases/download/26.31.39395.13/libigdgmm12_22.10.0_amd64.deb /debs/
ADD --checksum=sha256:ebd795e9fddf303a9b24b7f04545d8ddd9ad1f85b3d0cb1166476fab24da6d44 https://github.com/intel/intel-graphics-compiler/releases/download/v2.40.13/intel-igc-core-2_2.40.13+22418_amd64.deb /debs/
ADD --checksum=sha256:4f990874efc11c3f6091a663b08aef576c4af592dcd8f12e116f8c2fc92d34d9 https://github.com/intel/intel-graphics-compiler/releases/download/v2.40.13/intel-igc-opencl-2_2.40.13+22418_amd64.deb /debs/
ADD --checksum=sha256:5a9c9e8fdca8a2f9e22754b1a4618c7babf21d7c3ab3503c680005007c7a8c44 https://github.com/intel/compute-runtime/releases/download/26.31.39395.13/intel-opencl-icd_26.31.39395.13-0_amd64.deb /debs/

FROM scratch AS media-debs-arm64
ADD --checksum=sha256:f3af50c1fdd76197867b9cd812fe8a3a1c671829a3f626a0ddc58a8e48654740 https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.2-5/jellyfin-ffmpeg8_8.1.2-5-trixie_arm64.deb /debs/

FROM media-debs-${TARGETARCH} AS media-debs


# On-device model runtimes. llama-server is built from a pinned llama.cpp
# commit on this image's own base, so it links against the runtime's C library, in a portable CPU configuration:
# never GGML_NATIVE (a natively tuned ggml build crashed with SIGILL on the reference Intel CPU). Every CPU
# variant is built and the best one is chosen at run time from the libggml-cpu-*.so files beside the binary.
# Upgrade steps: docker/README.md "On-device models".
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS llama-build
ARG LLAMA_CPP_COMMIT=f805c57a2d0b7cc171e599303ce2040f6e1bfe15
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git init -q /src \
    && git -C /src fetch -q --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_CPP_COMMIT" \
    && git -C /src checkout -q --detach FETCH_HEAD \
    && test "$(git -C /src rev-parse HEAD)" = "$LLAMA_CPP_COMMIT" \
    && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release \
         -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DBUILD_SHARED_LIBS=ON \
         -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
         -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH='$ORIGIN' \
    && cmake --build /build -j"$(nproc)" --target llama-server \
    && mkdir -p /opt/lumina-llama/bin \
    && cp /build/bin/llama-server /opt/lumina-llama/bin/ \
    && cp /src/LICENSE /opt/lumina-llama/LICENSE \
    && find /build \( -name 'lib*.so' -o -name 'lib*.so.*' \) -exec cp -a {} /opt/lumina-llama/bin/ \; \
    && ls /opt/lumina-llama/bin/libggml-cpu-*.so >/dev/null


# The speech server's own virtual environment: wheels only, every one hash-pinned (docker/lumina-asr/requirements.lock).
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS asr-venv
COPY docker/lumina-asr/requirements.lock /tmp/lumina-asr.lock
RUN python -m venv /opt/lumina-asr \
    && /opt/lumina-asr/bin/pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/lumina-asr.lock \
    && /opt/lumina-asr/bin/python -m pip uninstall --yes pip


FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ARG LUMINA_SOURCE_REVISION=unrecorded

LABEL org.opencontainers.image.title="Lumina" \
      org.opencontainers.image.source="https://github.com/shiftedx/lumina" \
      org.opencontainers.image.revision="$LUMINA_SOURCE_REVISION" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/lumina-home \
    PYTHONPATH=/app/backend \
    LUMINA_DATA_DIR=/app/backend/.data

WORKDIR /app

# setpriv comes from util-linux (essential in Debian); local .debs resolve their Debian dependencies.
RUN --mount=type=bind,from=media-debs,source=/debs,target=/tmp/media-debs \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 tzdata /tmp/media-debs/*.deb \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
    && ln -s /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe

COPY --from=llama-build /opt/lumina-llama /opt/lumina-llama
COPY --from=asr-venv /opt/lumina-asr /opt/lumina-asr
COPY docker/lumina-asr/server.py /opt/lumina-asr/server.py
COPY backend/ /app/backend/
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-build /usr/local/LICENSE /usr/local/share/doc/node/LICENSE
COPY docker/entrypoint.sh /usr/local/bin/lumina-entrypoint.sh

RUN chmod +x /usr/local/bin/lumina-entrypoint.sh \
    && mkdir -p /app/backend/.data \
    && pip install --require-hashes -r /app/backend/requirements.runtime.lock \
    && /opt/lumina-llama/bin/llama-server --version \
    && /opt/lumina-asr/bin/python -c "import faster_whisper, ctranslate2"

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=12 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2)"]

ENTRYPOINT ["/usr/local/bin/lumina-entrypoint.sh"]
