#!/usr/bin/env bash
set -euo pipefail

# Quick-Piper-Endpoint docker launcher
# Override any variable by exporting it before running this script.

: "${CONTAINER_NAME:=quick-piper-endpoint}"
: "${IMAGE_NAME:=quick-piper-endpoint:latest}"
: "${NETWORK_NAME:=tts-net}"
: "${HOST_PORT:=8092}"
: "${CONTAINER_PORT:=8092}"

# Preferred runtime env consumed by piper_shim_server.py
: "${PIPER_HTTP_URL:=http://wyoming-piper:10200/api/tts}"
: "${PIPER_MODEL:=en_GB-cori-medium}"
: "${PIPER_CORI_VOICE:=en_GB-cori-medium}"
: "${PIPER_SPEAKER:=}"
: "${PIPER_HTTP_TIMEOUT_SECONDS:=120}"
: "${QWEN_PLAY_Q_MAX:=100}"

# Backward-compatible aliases (optional)
: "${PIPER_HOST:=wyoming-piper}"
: "${PIPER_PORT:=10200}"
: "${PIPER_VOICE:=cori}"
: "${PIPER_LENGTH_SCALE:=1.0}"
: "${PIPER_NOISE_SCALE:=0.667}"
: "${PIPER_NOISE_W:=0.8}"
: "${LOG_LEVEL:=info}"

# If caller only set host/port, derive URL unless explicitly provided
if [[ -z "${PIPER_HTTP_URL:-}" || "${PIPER_HTTP_URL}" == "http://wyoming-piper:10200/api/tts" ]]; then
  PIPER_HTTP_URL="http://${PIPER_HOST}:${PIPER_PORT}/api/tts"
fi

# If caller only set legacy voice, map to model unless explicitly provided
if [[ -n "${PIPER_VOICE:-}" && "${PIPER_MODEL}" == "en_GB-cori-medium" ]]; then
  case "${PIPER_VOICE}" in
    cori)
      PIPER_MODEL="${PIPER_CORI_VOICE}"
      ;;
    *)
      PIPER_MODEL="${PIPER_VOICE}"
      ;;
  esac
fi

if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  container_pid="$(docker inspect -f '{{.State.Pid}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  if [[ "$container_pid" =~ ^[0-9]+$ ]] && (( container_pid > 0 )); then
    echo "Killing existing container PID: $container_pid"
    kill -9 "$container_pid" 2>/dev/null || true
  fi

  echo "Removing existing container: $CONTAINER_NAME"
  docker rm -f "$CONTAINER_NAME" >/dev/null
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Docker network '$NETWORK_NAME' not found. Creating it..."
  docker network create "$NETWORK_NAME" >/dev/null
fi

echo "Starting $CONTAINER_NAME"

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --network "$NETWORK_NAME" \
  -p "$HOST_PORT:$CONTAINER_PORT" \
  -e PIPER_HTTP_URL="$PIPER_HTTP_URL" \
  -e PIPER_MODEL="$PIPER_MODEL" \
  -e PIPER_CORI_VOICE="$PIPER_CORI_VOICE" \
  -e PIPER_SPEAKER="$PIPER_SPEAKER" \
  -e PIPER_HTTP_TIMEOUT_SECONDS="$PIPER_HTTP_TIMEOUT_SECONDS" \
  -e QWEN_PLAY_Q_MAX="$QWEN_PLAY_Q_MAX" \
  -e LOG_LEVEL="$LOG_LEVEL" \
  "$IMAGE_NAME"

echo "Done."
echo "Container: $CONTAINER_NAME"
echo "Image:     $IMAGE_NAME"
echo "Model:     $PIPER_MODEL"
echo "Backend:   $PIPER_HTTP_URL"
echo "Health:    http://localhost:${HOST_PORT}/health"
