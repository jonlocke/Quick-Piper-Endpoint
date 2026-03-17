# Quick-Piper-Endpoint

Quick-Piper-Endpoint is an HTTP shim that makes Wyoming Piper look like a simple Quick-TTS style API.

It lets apps post text to /speak and get back either JSON status, WAV audio, or streamed base64 WAV chunks.

## How the shim works

1. Client sends text to POST /speak.
2. Shim parses flags like play, return_audio, chunk, and stream_audio_chunks.
3. Shim optionally chunks long text.
4. For each chunk, shim connects to Wyoming Piper over TCP and sends a synth event.
5. Shim receives PCM audio events, wraps them into WAV bytes, and:
   - returns WAV directly, or
   - streams NDJSON audio_chunk messages, and/or
   - queues playback with ffplay.

So this service is the translation layer between an HTTP client and a Wyoming Piper backend.

## What it is used for

- Drop-in TTS backend for apps expecting a Quick-TTS-like /speak endpoint
- Holly/holly5005 integrations that need streamed chunk output
- Centralized voice configuration with env vars
- Running Piper as a shared network service while keeping client integration simple

## Repo layout

- piper_shim_server.py main FastAPI shim
- Dockerfile container image definition
- docker-build.sh helper script to build image
- docker-run.sh helper script to run container with env defaults
- requirements.txt python dependencies
- run.sh local non-docker start

## Build the Docker container

From Dads laptop in this folder:

cd ~/Quick-Piper-Endpoint

Build using helper script:

./docker-build.sh

Equivalent manual build:

docker build --no-cache -t quick-piper-endpoint:latest .

## Run the container

Run with helper script:

./docker-run.sh

The script will:
- stop and remove any existing container with the same name
- ensure docker network exists tts-net by default
- start container detached
- publish host port to container port default 8092:8092

## Where ENV is

For this project, env settings are defined in docker-run.sh via shell defaults.

Container/runtime env passed by docker-run.sh:
- CONTAINER_NAME default quick-piper-endpoint
- IMAGE_NAME default quick-piper-endpoint:latest
- NETWORK_NAME default tts-net
- HOST_PORT default 8092
- CONTAINER_PORT default 8092
- PIPER_HTTP_URL default http://wyoming-piper:10200/api/tts
- PIPER_MODEL default en_GB-cori-medium
- PIPER_CORI_VOICE default en_GB-cori-medium
- PIPER_SPEAKER optional numeric speaker id
- PIPER_HTTP_TIMEOUT_SECONDS default 120
- QWEN_PLAY_Q_MAX default 100
- LOG_LEVEL default info

Backward-compat aliases also supported by docker-run.sh:
- PIPER_HOST
- PIPER_PORT
- PIPER_VOICE

## Overriding env values

One-shot override:

PIPER_MODEL=en_GB-cori-medium HOST_PORT=8093 ./docker-run.sh

Or export before running:

export IMAGE_NAME=quick-piper-endpoint:latest
export NETWORK_NAME=tts-net
export PIPER_HTTP_URL=http://wyoming-piper:10200/api/tts
export PIPER_MODEL=en_GB-cori-medium
./docker-run.sh

## API

### POST /speak

Body accepts:
- text or prompt
- optional speaker or voice

Query flags:
- play=0 or 1 default 1
- return_audio=0 or 1 default 0
- chunk=0 or 1 default 1
- stream_audio_chunks=0 or 1 default 0
- max_chars default 240

### GET /health
Returns backend details and current Piper model target.

### GET /speakers
Shim metadata endpoint.

### GET /languages
Shim metadata endpoint.

## Usage examples

Health:

curl -s http://127.0.0.1:8092/health

Basic speak, JSON output:

curl -s -X POST 'http://127.0.0.1:8092/speak?play=0' \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello from Quick Piper shim","speaker":"cori"}'

Expected JSON shape:

{
  "ok": true,
  "queued": false,
  "chunks": 1,
  "bytes": 63020,
  "backend": "wyoming-piper"
}

Return WAV audio bytes to file:

curl -s -X POST 'http://127.0.0.1:8092/speak?play=0&return_audio=1' \
  -H 'Content-Type: application/json' \
  -d '{"text":"Return raw wav audio","speaker":"cori"}' \
  --output out.wav

Inspect resulting audio:

file out.wav

Stream base64 WAV chunks as NDJSON:

curl -N -X POST 'http://127.0.0.1:8092/speak?stream_audio_chunks=1&play=0' \
  -H 'Content-Type: application/json' \
  -d '{"text":"Stream this in chunks","speaker":"cori"}'

Expected streamed lines:

{"type":"audio_chunk","index":0,"audio_b64_wav":"UklGR..."}
{"type":"audio_chunk","index":1,"audio_b64_wav":"UklGR..."}
{"type":"done","chunks":2}

Notes on stream format:
- media type is application/x-ndjson
- each line is one JSON object
- audio_b64_wav is base64-encoded WAV bytes for that chunk
- decode a chunk with: echo 'UklGR...' | base64 -d > chunk.wav
- on failure, shim emits: {"type":"error","detail":"..."}

## Integration note for Holly

Typical app-side setting:
- QWEN_TTS_API_BASE=http://172.17.0.1:8092

That points the app at this shim, which then forwards synthesis to Wyoming Piper.
