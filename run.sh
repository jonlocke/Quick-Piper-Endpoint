#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec uvicorn piper_shim_server:app --host 0.0.0.0 --port 8092
