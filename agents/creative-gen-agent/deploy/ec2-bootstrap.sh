#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y \
  software-properties-common \
  git \
  nginx \
  curl \
  python3.10 \
  python3.10-venv \
  python3-pip \
  certbot \
  python3-certbot-nginx

if ! command -v poetry >/dev/null 2>&1; then
  curl -sSL https://install.python-poetry.org | python3 -
fi

if ! grep -q 'PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc"; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi

echo "Bootstrap complete."
echo "Run: source ~/.bashrc"
