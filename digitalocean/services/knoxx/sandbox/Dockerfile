# SPDX-License-Identifier: GPL-3.0-or-later
# Temurin retired its Debian variants; `21-jdk-bookworm` now 404s on pull.
# jammy rather than noble because only the JDK moves from here into the
# bookworm base below, and a JDK built against an older glibc runs on a newer
# one — jammy is 2.35, bookworm is 2.36. noble (2.39) is the wrong direction.
FROM eclipse-temurin:21-jdk-jammy AS jdk

FROM nikolaik/python-nodejs:python3.12-nodejs22-bookworm

USER 0:0

COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:${PATH}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash \
      build-essential \
      ca-certificates \
      clang \
      clojure \
      cmake \
      curl \
      fd-find \
      ffmpeg \
      file \
      gdb \
      git \
      git-lfs \
      graphviz \
      imagemagick \
      jq \
      less \
      leiningen \
      lsof \
      make \
      ninja-build \
      openssh-client \
      pandoc \
      pkg-config \
      postgresql-client \
      procps \
      redis-tools \
      ripgrep \
      rsync \
      shellcheck \
      sqlite3 \
      strace \
      tree \
      unzip \
      wget \
      xz-utils \
      zip \
 && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
 && corepack enable \
 && corepack prepare pnpm@10.14.0 --activate \
 && git lfs install --system \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /workspace /workspace/.cache \
 && chown -R 1000:1000 /workspace

COPY --chmod=0755 doctor.sh /usr/local/bin/sandbox-doctor

USER 1000:1000
ENV HOME=/workspace
ENV XDG_CACHE_HOME=/workspace/.cache
WORKDIR /workspace

CMD ["sh", "-lc", "trap 'exit 0' TERM INT; while true; do sleep 3600; done"]
