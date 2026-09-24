from __future__ import annotations
import hashlib
import os
import pathlib
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
MODEL = ROOT / "model"
TTS = MODEL / "tts"
ASR = MODEL / "asr"
TTS.mkdir(parents=True, exist_ok=True)
ASR.mkdir(parents=True, exist_ok=True)

TTS_BASE = "https://huggingface.co/csukuangfj/vits-cantonese-hf-xiaomaiiwn/resolve/main"
TTS_FILES = {
    "vits-cantonese-hf-xiaomaiiwn.onnx": (110_000_000, "7d8d4f5550b607999417a99131b034c8cc2b8dca69f9e9fdefeff6655643c139"),
    "lexicon.txt": (250_000, None),
    "tokens.txt": (300, None),
    "rule.fst": (50_000, None),
}

def sha256(path: pathlib.Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b=f.read(1024*1024)
            if not b: break
            h.update(b)
    return h.hexdigest()

def download(url: str, dst: pathlib.Path):
    tmp=dst.with_suffix(dst.suffix+".part")
    req=urllib.request.Request(url,headers={"User-Agent":"cantonese-song-coach/1.0"})
    with urllib.request.urlopen(req,timeout=180) as r,tmp.open("wb") as out:
        total=0
        while True:
            b=r.read(1024*1024)
            if not b: break
            out.write(b); total+=len(b)
            if total and total%(20*1024*1024)<1024*1024:
                print(f"[download] {dst.name}: {total/1024/1024:.1f} MB",flush=True)
    os.replace(tmp,dst)

for name,(min_size,digest) in TTS_FILES.items():
    dst=TTS/name
    valid=dst.exists() and dst.stat().st_size>=min_size and (not digest or sha256(dst)==digest)
    if not valid:
        print("[tts-model] downloading",name,flush=True)
        download(f"{TTS_BASE}/{name}?download=true",dst)
    if dst.stat().st_size<min_size: raise RuntimeError(f"TTS asset too small: {name}")
    if digest and sha256(dst)!=digest: raise RuntimeError(f"TTS sha mismatch: {name}")
    print(f"[tts-model] ready {name} {dst.stat().st_size:,}",flush=True)

ASR_BASE="https://huggingface.co/csukuangfj/sherpa-onnx-wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10/resolve/main"
ASR_FILES={
    "model.int8.onnx": (134_000_000, "201bfd9e12ec4ac9ee3b23c5e071d9fa2381a8b21df317e2e08a170d6f1f55d3"),
    "tokens.txt": (80_000, None),
}
for name,(min_size,digest) in ASR_FILES.items():
    p=ASR/name
    valid=p.exists() and p.stat().st_size>=min_size and (not digest or sha256(p)==digest)
    if not valid:
        print(f"[asr-model] downloading {name}",flush=True)
        download(f"{ASR_BASE}/{name}?download=true",p)
    if not p.exists() or p.stat().st_size<min_size:
        raise RuntimeError(f"ASR asset invalid: {name}")
    if digest and sha256(p)!=digest:
        raise RuntimeError(f"ASR sha mismatch: {name}")
    print(f"[asr-model] ready {name} {p.stat().st_size:,}",flush=True)
print("[models] all assets ready",flush=True)
