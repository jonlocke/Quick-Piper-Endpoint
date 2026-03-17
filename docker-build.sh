#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
IMAGE_NAME=${IMAGE_NAME:-quick-piper-endpoint}
IMAGE_TAG=${IMAGE_TAG:-latest}

docker build --no-cache -t "${IMAGE_NAME}:${IMAGE_TAG}" .
