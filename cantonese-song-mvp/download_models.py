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

ASR_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-zipformer-cantonese-2024-03-13.tar.bz2"
ASR_FILES={
    "encoder-epoch-45-avg-35.int8.onnx":60_000_000,
    "decoder-epoch-45-avg-35.onnx":10_000_000,
    "joiner-epoch-45-avg-35.int8.onnx":2_000_000,
    "tokens.txt":30_000,
}
if not all((ASR/k).exists() and (ASR/k).stat().st_size>=v for k,v in ASR_FILES.items()):
    archive=MODEL/"cantonese-asr.tar.bz2"
    print("[asr-model] downloading Cantonese Zipformer archive",flush=True)
    download(ASR_URL,archive)
    wanted=set(ASR_FILES)
    with tarfile.open(archive,"r:bz2") as tf:
        members=[]
        for m in tf.getmembers():
            base=pathlib.PurePosixPath(m.name).name
            if base in wanted:
                m.name=base
                members.append(m)
        found={m.name for m in members}
        if found!=wanted: raise RuntimeError(f"Missing ASR members: {wanted-found}")
        tf.extractall(ASR,members=members)
    archive.unlink(missing_ok=True)

for name,min_size in ASR_FILES.items():
    p=ASR/name
    if not p.exists() or p.stat().st_size<min_size: raise RuntimeError(f"ASR asset invalid: {name}")
    print(f"[asr-model] ready {name} {p.stat().st_size:,}",flush=True)
print("[models] all assets ready",flush=True)
