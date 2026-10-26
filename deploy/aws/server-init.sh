#!/bin/bash
# EC2 user data (Amazon Linux 2023, x86_64 or arm64), runs once at first boot: Docker, Compose, Buildx, 2 GB swap.
# The application itself is installed and updated by deploy.sh (through SSM, no SSH).
set -euxo pipefail
dnf install -y docker
systemctl enable --now docker

plugins=/usr/local/lib/docker/cli-plugins
mkdir -p "$plugins"
fetch() {  # url, sha256, destination
  curl -fsSL -o "$3" "$1"
  echo "$2  $3" | sha256sum -c -
  chmod +x "$3"
}
if [ "$(uname -m)" = aarch64 ]; then
  fetch https://github.com/docker/compose/releases/download/v5.6.1/docker-compose-linux-aarch64 \
    42cfa75f51ad0ccf290bad37899e0038a8c10a0069e85233ecc1d53e3ff1347d "$plugins/docker-compose"
  fetch https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.linux-arm64 \
    3b83b3731916ba433feccaaa689fe5bcdc81c641d92c8e6c2ea865dfa141c97a "$plugins/docker-buildx"
else
  fetch https://github.com/docker/compose/releases/download/v5.6.1/docker-compose-linux-x86_64 \
    47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0 "$plugins/docker-compose"
  fetch https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.linux-amd64 \
    e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4 "$plugins/docker-buildx"
fi

# 4 GB RAM: app + ClamAV (~1.5 GB) + search model; swap absorbs peaks (signature reloads, image builds)
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
touch /var/lib/oncm-init-done
