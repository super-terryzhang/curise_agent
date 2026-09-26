from __future__ import annotations
import hashlib
import os
import pathlib
import sys
import urllib.request

BASE = "https://huggingface.co/csukuangfj/vits-cantonese-hf-xiaomaiiwn/resolve/main"
ROOT = pathlib.Path(__file__).resolve().parent
MODEL_DIR = ROOT / "model"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FILES = {
    "vits-cantonese-hf-xiaomaiiwn.onnx": {
        "min_size": 110_000_000,
        "sha256": "7d8d4f5550b607999417a99131b034c8cc2b8dca69f9e9fdefeff6655643c139",
    },
    "lexicon.txt": {"min_size": 250_000, "sha256": None},
    "tokens.txt": {"min_size": 300, "sha256": None},
    "rule.fst": {"min_size": 50_000, "sha256": None},
}

def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def download(name: str, spec: dict) -> None:
    dst = MODEL_DIR / name
    if dst.exists() and dst.stat().st_size >= spec["min_size"]:
        if spec["sha256"] is None or sha256(dst) == spec["sha256"]:
            print(f"[model] reuse {name} ({dst.stat().st_size:,} bytes)")
            return

    tmp = dst.with_suffix(dst.suffix + ".part")
    url = f"{BASE}/{name}?download=true"
    print(f"[model] downloading {name} from Hugging Face")
    req = urllib.request.Request(url, headers={"User-Agent": "cantonese-coach-demo/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as out:
            total = 0
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
                if total % (20 * 1024 * 1024) < 1024 * 1024:
                    print(f"[model] {name}: {total / 1024 / 1024:.1f} MB")
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise

    if tmp.stat().st_size < spec["min_size"]:
        raise RuntimeError(f"{name} is too small: {tmp.stat().st_size} bytes")

    if spec["sha256"]:
        actual = sha256(tmp)
        if actual != spec["sha256"]:
            raise RuntimeError(f"{name} sha256 mismatch: {actual}")

    os.replace(tmp, dst)
    print(f"[model] ready {name} ({dst.stat().st_size:,} bytes)")

for filename, spec in FILES.items():
    download(filename, spec)

print("[model] all Cantonese VITS assets ready")
