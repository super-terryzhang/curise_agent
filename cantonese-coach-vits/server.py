from __future__ import annotations
import io
import json
import os
import pathlib
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import numpy as np
import sherpa_onnx
from opencc import OpenCC

ROOT = pathlib.Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "10000"))
MODEL_DIR = ROOT / "model"

MODEL = MODEL_DIR / "vits-cantonese-hf-xiaomaiiwn.onnx"
LEXICON = MODEL_DIR / "lexicon.txt"
TOKENS = MODEL_DIR / "tokens.txt"
RULE_FST = MODEL_DIR / "rule.fst"

for p in (MODEL, LEXICON, TOKENS, RULE_FST):
    if not p.is_file():
        raise RuntimeError(f"Missing model asset: {p}")

print("[boot] configuring sherpa-onnx Cantonese VITS", flush=True)
config = sherpa_onnx.OfflineTtsConfig(
    model=sherpa_onnx.OfflineTtsModelConfig(
        vits=sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(MODEL),
            lexicon=str(LEXICON),
            tokens=str(TOKENS),
            length_scale=1.0,
        ),
        provider="cpu",
        debug=False,
        num_threads=1,
    ),
    rule_fsts=str(RULE_FST),
    rule_fars="",
    max_num_sentences=1,
)

if not config.validate():
    raise RuntimeError("sherpa-onnx Cantonese VITS config validation failed")

tts = sherpa_onnx.OfflineTts(config)
tts_lock = threading.Lock()
t2s = OpenCC("t2s")

def generate_wav(text: str, speed: float = 1.0) -> tuple[bytes, int, float]:
    text = text.strip()
    if not text:
        raise ValueError("Text is empty")
    if len(text) > 120:
        raise ValueError("Text is too long for demo mode")
    speed = max(0.65, min(1.35, float(speed)))

    normalized = t2s.convert(text)
    started = time.perf_counter()
    with tts_lock:
        audio = tts.generate(text=normalized, sid=0, speed=speed)
    elapsed = time.perf_counter() - started

    samples = np.asarray(audio.samples, dtype=np.float32)
    if samples.size == 0 or int(audio.sample_rate) <= 0:
        raise RuntimeError("TTS generated empty audio")

    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(audio.sample_rate))
        w.writeframes(pcm.tobytes())
    return out.getvalue(), int(audio.sample_rate), elapsed

print("[selftest] generating 你好嗎 -> " + t2s.convert("你好嗎"), flush=True)
test_wav, test_sr, test_elapsed = generate_wav("你好嗎", 1.0)
if len(test_wav) < 1000:
    raise RuntimeError("Self-test WAV is unexpectedly small")
print(f"[selftest] PASS bytes={len(test_wav)} sample_rate={test_sr} elapsed={test_elapsed:.2f}s", flush=True)

STATIC = {
    "/": (ROOT / "index.html", "text/html; charset=utf-8"),
    "/app.js": (ROOT / "app.js", "application/javascript; charset=utf-8"),
    "/vendor/cantojpmin_data.js": (ROOT.parent / "cantonese-coach-mvp" / "vendor" / "cantojpmin_data.js", "application/javascript; charset=utf-8"),
    "/vendor/cantojpmin_functions.js": (ROOT.parent / "cantonese-coach-mvp" / "vendor" / "cantojpmin_functions.js", "application/javascript; charset=utf-8"),
}

class Handler(BaseHTTPRequestHandler):
    server_version = "CantoneseCoachVITS/1.0"

    def log_message(self, fmt, *args):
        print("[http] " + (fmt % args), flush=True)

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json(200, {
                "ok": True,
                "engine": "sherpa-onnx",
                "model": "vits-cantonese-hf-xiaomaiiwn",
                "model_bytes": MODEL.stat().st_size,
                "external_tts_api": False,
                "sample_rate": test_sr,
                "selftest": True,
                "traditional_to_simplified": True,
            })

        item = STATIC.get(path)
        if not item:
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        file_path, content_type = item
        if not file_path.is_file():
            return self._send(500, f"Missing static file: {file_path}".encode(), "text/plain; charset=utf-8")
        return self._send(200, file_path.read_bytes(), content_type)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/tts":
            return self._json(404, {"error": "Not found"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            text = str(data.get("text", ""))
            speed = float(data.get("speed", 1.0))
            print(f"[tts] request chars={len(text)} speed={speed}", flush=True)
            wav_bytes, sr, elapsed = generate_wav(text, speed)
            print(f"[tts] success bytes={len(wav_bytes)} sr={sr} elapsed={elapsed:.2f}s", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-TTS-Engine", "sherpa-onnx-vits-cantonese")
            self.send_header("X-TTS-Time", f"{elapsed:.3f}")
            self.end_headers()
            self.wfile.write(wav_bytes)
        except Exception as e:
            print(f"[tts] error {type(e).__name__}: {e}", flush=True)
            return self._json(400, {"error": str(e)})

print(f"[boot] listening on 0.0.0.0:{PORT}", flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
