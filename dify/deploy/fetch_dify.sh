#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dify
curl -L "https://gh-proxy.com/https://github.com/langgenius/dify/archive/refs/tags/1.19.0.tar.gz" -o dify-1.19.0.tar.gz
tar -xzf dify-1.19.0.tar.gz -C dify --strip-components=2 dify-1.19.0/docker
echo "Dify 1.19.0 docker files extracted to: $(pwd)/dify"
