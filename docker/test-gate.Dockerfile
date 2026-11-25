# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183
# Keep Python and Node aligned with .tool-versions and the Playwright image
# aligned with frontend/package-lock.json.
FROM mcr.microsoft.com/playwright:v1.55.1-noble@sha256:2f29369043d81d6d69a815ceb80760f55e85f5020371ad06a4d996f18503ad1c AS playwright
FROM node:22.23.4-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS node

FROM scratch AS tools-amd64
ADD --checksum=sha256:6d7521138fd33afddc1ccd08abb007d9a2829e38117c2576f9efdc0ec73ce772 https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.12.15%2B20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz /python.tar.gz
ADD --checksum=sha256:188fa8b31f7e0ded9dadd1d11d1cd05482a7f03cec4e5c0af80aae21fde44ce4 https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.3-1/jellyfin-ffmpeg8_8.1.3-1-noble_amd64.deb /jellyfin-ffmpeg.deb

FROM scratch AS tools-arm64
ADD --checksum=sha256:b4567aa5a11f51b540c4dfd9e082ebf23e554f8ac17b85caa6cea23e83693394 https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.12.15%2B20261021-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz /python.tar.gz
ADD --checksum=sha256:3bd155208103bf8e81545df2585911a6e67da5a0c97e7433fb95290f7e217ab4 https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.3-1/jellyfin-ffmpeg8_8.1.3-1-noble_arm64.deb /jellyfin-ffmpeg.deb

FROM tools-${TARGETARCH} AS tools

# Build these against Noble's ABI. The parser starts /usr/bin/setpriv directly.
FROM playwright AS parser-tools
ADD --checksum=sha256:83b6085232d1588c379dc9b9cae47bb37407cf262e6e74993c61ba72d2a784dc https://github.com/seccomp/libseccomp/releases/download/v2.6.0/libseccomp-2.6.0.tar.gz /tmp/libseccomp.tar.gz
ADD --checksum=sha256:6062a1d89b571a61932e6fc0211f36060c4183568b81ee866cf363bce9f6583e https://www.kernel.org/pub/linux/utils/util-linux/v2.41/util-linux-2.41.2.tar.xz /tmp/util-linux.tar.xz
ADD --checksum=sha256:56cad765459d5ee25a3b761f90c47d99379d28297fbfb72d908880d6b875fb96 https://github.com/libarchive/libarchive/releases/download/v3.8.10/libarchive-3.8.10.tar.xz /tmp/libarchive.tar.xz
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake gperf pkg-config libcap-ng-dev libssl-dev zlib1g-dev libbz2-dev liblzma-dev libzstd-dev liblz4-dev \
    && mkdir -p /build/seccomp /build/util-linux /build/archive /out/lib \
    && tar -xzf /tmp/libseccomp.tar.gz -C /build/seccomp --strip-components=1 \
    && cd /build/seccomp \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make DESTDIR=/out install \
    && tar -xJf /tmp/util-linux.tar.xz -C /build/util-linux --strip-components=1 \
    && cd /build/util-linux \
    && ./configure --disable-all-programs --enable-setpriv \
    && make -j"$(nproc)" setpriv \
    && ./setpriv --help | grep -q -- --seccomp-filter \
    && ./setpriv --help | grep -q -- --landlock-rule \
    && cp setpriv /out/setpriv \
    && tar -xJf /tmp/libarchive.tar.xz -C /build/archive --strip-components=1 \
    && cmake -S /build/archive -B /build/archive-out -DCMAKE_BUILD_TYPE=Release \
         -DENABLE_TAR=OFF -DENABLE_CPIO=OFF -DENABLE_CAT=OFF -DENABLE_UNZIP=OFF \
         -DENABLE_LIBXML2=OFF -DENABLE_EXPAT=OFF -DENABLE_BSDXML=OFF \
    && cmake --build /build/archive-out -j"$(nproc)" --target archive \
    && cp -a /build/archive-out/libarchive/libarchive.so* /out/lib/

FROM playwright
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=parser-tools /out/usr/local/lib/libseccomp.so.2.6.0 /usr/local/lib/
COPY --from=parser-tools /out/lib/ /usr/local/lib/
RUN --mount=type=bind,from=tools,source=/,target=/tool-archives \
    apt-get update \
    && apt-get install -y --no-install-recommends git make libcap-ng0 libbz2-1.0 liblzma5 libzstd1 liblz4-1 /tool-archives/jellyfin-ffmpeg.deb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /opt/python /work \
    && tar -xzf /tool-archives/python.tar.gz -C /opt/python --strip-components=1 \
    && ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
    && ln -sf /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe \
    && printf '%s\n' /usr/local/lib > /etc/ld.so.conf.d/00-lumina-test-gate.conf \
    && ldconfig \
    && chown pwuser:pwuser /work

# Install after APT so a package update cannot replace the qualified setpriv.
COPY --from=parser-tools /out/setpriv /usr/bin/setpriv

# The stripped Python archive omits bytecode, and pwuser cannot cache it.
RUN /opt/python/bin/python3 -m compileall -q /opt/python/lib/python3.12

ENV PATH="/opt/python/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
USER pwuser
