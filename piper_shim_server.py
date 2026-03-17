#!/usr/bin/env python3
import asyncio
import base64
import json
import io
import os
import queue
import re
import subprocess
import threading
import urllib.parse
import wave
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeStopped, SynthesizeVoice

PIPER_HTTP_URL = os.environ.get("PIPER_HTTP_URL", "http://wyoming-piper:10200/api/tts").strip()

def _resolve_voice_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return "en_US-lessac-medium"
    if n.lower() == "cori":
        return os.environ.get("PIPER_CORI_VOICE", "en_GB-cori-medium").strip()
    return n

PIPER_MODEL_RAW = os.environ.get("PIPER_MODEL", os.environ.get("PIPER_VOICE", "en_GB-cori-medium")).strip()
PIPER_MODEL = _resolve_voice_name(PIPER_MODEL_RAW)
PIPER_SPEAKER_RAW = os.environ.get("PIPER_SPEAKER", "").strip()
PIPER_SPEAKER = int(PIPER_SPEAKER_RAW) if PIPER_SPEAKER_RAW.isdigit() else None
PIPER_HTTP_TIMEOUT_SECONDS = float(os.environ.get("PIPER_HTTP_TIMEOUT_SECONDS", "120"))
PLAY_Q_MAX = max(1, int(os.environ.get("QWEN_PLAY_Q_MAX", "100")))


def _wyoming_host_port(configured: str) -> tuple[str, int]:
    raw = (configured or "").strip()
    if not raw:
        return "wyoming-piper", 10200
    if "://" not in raw:
        raw = "http://" + raw
    p = urllib.parse.urlparse(raw)
    host = p.hostname or "wyoming-piper"
    port = p.port or 10200
    return host, port


WYOMING_HOST, WYOMING_PORT = _wyoming_host_port(PIPER_HTTP_URL)

app = FastAPI(title="Quick Piper Endpoint")
play_q: "queue.Queue[bytes]" = queue.Queue(maxsize=PLAY_Q_MAX)


class SpeakBody(BaseModel):
    text: Optional[str] = None
    prompt: Optional[str] = None
    speaker: Optional[str] = None
    voice: Optional[str] = None
    language: Optional[str] = None


def _split_text(text: str, max_chars: int = 240) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    rough = re.split(r"(?<=[.!?])\s+", text)
    out, cur = [], ""
    for part in rough:
        if not part:
            continue
        if len(cur) + len(part) + 1 <= max_chars:
            cur = (cur + " " + part).strip()
        else:
            if cur:
                out.append(cur)
            if len(part) <= max_chars:
                cur = part
            else:
                for i in range(0, len(part), max_chars):
                    out.append(part[i:i + max_chars])
                cur = ""
    if cur:
        out.append(cur)
    return out


def _pcm_to_wav_bytes(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def _synth_one_async(text: str, requested_speaker: Optional[str] = None) -> bytes:
    timeout = max(1.0, PIPER_HTTP_TIMEOUT_SECONDS)
    client = AsyncTcpClient(WYOMING_HOST, WYOMING_PORT)
    await client.connect()

    try:
        voice_name = PIPER_MODEL
        voice_speaker = str(PIPER_SPEAKER) if PIPER_SPEAKER is not None else None

        requested = (requested_speaker or "").strip()
        if requested:
            if requested.isdigit():
                voice_speaker = requested
            else:
                resolved = _resolve_voice_name(requested)
                if resolved.startswith("en_") and ("-" in resolved):
                    # Accept explicit Piper model names and friendly aliases like "cori".
                    voice_name = resolved

        voice = SynthesizeVoice(name=voice_name)
        if voice_speaker is not None:
            voice.speaker = voice_speaker

        await client.write_event(Synthesize(text=text, voice=voice).event())

        pcm_chunks: list[bytes] = []
        rate = width = channels = None

        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=timeout)
            if event is None:
                break

            if AudioStart.is_type(event.type):
                astart = AudioStart.from_event(event)
                rate, width, channels = astart.rate, astart.width, astart.channels
                continue

            if AudioChunk.is_type(event.type):
                achunk = AudioChunk.from_event(event)
                if rate is None:
                    rate, width, channels = achunk.rate, achunk.width, achunk.channels
                pcm_chunks.append(achunk.audio)
                continue

            if AudioStop.is_type(event.type) or SynthesizeStopped.is_type(event.type):
                break

        if not pcm_chunks or rate is None or width is None or channels is None:
            raise HTTPException(status_code=502, detail="Wyoming Piper returned no audio")

        return _pcm_to_wav_bytes(b"".join(pcm_chunks), rate, width, channels)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=502, detail=f"Wyoming Piper timeout {WYOMING_HOST}:{WYOMING_PORT}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Wyoming Piper failed: {exc}")
    finally:
        await client.disconnect()


def _synth_one(text: str, requested_speaker: Optional[str] = None) -> bytes:
    return asyncio.run(_synth_one_async(text, requested_speaker=requested_speaker))


def _player_loop() -> None:
    while True:
        wav = play_q.get()
        try:
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"],
                input=wav,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        finally:
            play_q.task_done()


threading.Thread(target=_player_loop, daemon=True).start()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "backend": "wyoming-piper",
        "wyoming_host": WYOMING_HOST,
        "wyoming_port": WYOMING_PORT,
        "piper_model": PIPER_MODEL,
    }


@app.get("/speakers")
def speakers():
    return {"speakers": ["custom"], "default": "custom", "note": "shim forwards to Wyoming Piper; configure PIPER_SPEAKER env for numeric speaker id"}


@app.get("/languages")
def languages():
    return {"languages": ["english"], "default": "english"}


def _parse_flag(name: str, value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        if value in (0, 1):
            return value
        raise HTTPException(status_code=422, detail=f"{name} must be 0/1/true/false")
    sval = str(value).strip().lower()
    if sval in {"1", "true", "t", "yes", "y", "on"}:
        return 1
    if sval in {"0", "false", "f", "no", "n", "off"}:
        return 0
    raise HTTPException(status_code=422, detail=f"{name} must be 0/1/true/false")


def _ndjson_line(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


@app.post("/speak")
def speak(
    body: Optional[SpeakBody] = None,
    text: Optional[str] = Query(None),
    prompt: Optional[str] = Query(None),
    speaker: Optional[str] = Query(None),
    voice: Optional[str] = Query(None),
    play: str = Query("1"),
    return_audio: str = Query("0"),
    chunk: str = Query("1"),
    stream_audio_chunks: str = Query("0"),
    paragraph_chunking: str = Query("1"),
    max_chars: int = Query(240, ge=20, le=1000),
):
    body = body or SpeakBody()
    text_val = (body.text or body.prompt or text or prompt or "").strip()
    if not text_val:
        raise HTTPException(status_code=400, detail="text is required (body.text/body.prompt or query text/prompt)")
    speaker_val = (body.speaker or body.voice or speaker or voice)

    play = _parse_flag("play", play)
    return_audio = _parse_flag("return_audio", return_audio)
    chunk = _parse_flag("chunk", chunk)
    stream_audio_chunks = _parse_flag("stream_audio_chunks", stream_audio_chunks)

    chunks = _split_text(text_val, max_chars=max_chars) if chunk else [text_val]
    if not chunks:
        raise HTTPException(status_code=400, detail="text is empty")

    if stream_audio_chunks:
        def gen():
            yielded = 0
            try:
                for idx, part in enumerate(chunks):
                    wav = _synth_one(part, requested_speaker=speaker_val)
                    if play:
                        try:
                            play_q.put_nowait(wav)
                        except queue.Full:
                            pass
                    yielded += 1
                    yield _ndjson_line({
                        "type": "audio_chunk",
                        "index": idx,
                        "audio_b64_wav": base64.b64encode(wav).decode("ascii"),
                    })
                yield _ndjson_line({"type": "done", "chunks": yielded})
            except HTTPException as exc:
                yield _ndjson_line({"type": "error", "detail": str(exc.detail)})
            except Exception as exc:
                yield _ndjson_line({"type": "error", "detail": str(exc)})

        return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    rendered = []
    for part in chunks:
        rendered.append(_synth_one(part, requested_speaker=speaker_val))

    wav_all = rendered[0] if len(rendered) == 1 else b"".join(rendered)

    if play:
        try:
            play_q.put_nowait(wav_all)
        except queue.Full:
            raise HTTPException(status_code=429, detail="playback queue full")

    if return_audio:
        return Response(content=wav_all, media_type="audio/wav")

    return JSONResponse({
        "ok": True,
        "queued": bool(play),
        "chunks": len(chunks),
        "bytes": len(wav_all),
        "backend": "wyoming-piper",
    })
