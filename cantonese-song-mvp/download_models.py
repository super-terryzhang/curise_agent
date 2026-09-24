from __future__ import annotations
import hashlib
import os
import pathlib
import shutil
import urllib.request
import wave

import numpy as np
import sherpa_onnx
from opencc import OpenCC

ROOT=pathlib.Path(__file__).resolve().parent
ASR=ROOT/"model"/"asr"
REF=ROOT/"reference"
TMP_TTS=ROOT/"model"/"_tts_build"
ASR.mkdir(parents=True,exist_ok=True)
REF.mkdir(parents=True,exist_ok=True)
TMP_TTS.mkdir(parents=True,exist_ok=True)

def sha256(path:pathlib.Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b=f.read(1024*1024)
            if not b:break
            h.update(b)
    return h.hexdigest()

def download(url:str,dst:pathlib.Path):
    dst.parent.mkdir(parents=True,exist_ok=True)
    tmp=dst.with_suffix(dst.suffix+".part")
    req=urllib.request.Request(url,headers={"User-Agent":"cantonese-song-coach/1.0"})
    with urllib.request.urlopen(req,timeout=180) as r,tmp.open("wb") as out:
        total=0
        while True:
            b=r.read(1024*1024)
            if not b:break
            out.write(b);total+=len(b)
            if total and total%(20*1024*1024)<1024*1024:
                print(f"[download] {dst.name}: {total/1024/1024:.1f} MB",flush=True)
    os.replace(tmp,dst)

# ---- Cantonese ASR runtime model ----
ASR_BASE="https://huggingface.co/csukuangfj/sherpa-onnx-wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10/resolve/main"
ASR_FILES={
    "model.int8.onnx": (134_000_000,"201bfd9e12ec4ac9ee3b23c5e071d9fa2381a8b21df317e2e08a170d6f1f55d3"),
    "tokens.txt": (80_000,None),
}
for name,(min_size,digest) in ASR_FILES.items():
    p=ASR/name
    valid=p.exists() and p.stat().st_size>=min_size and (not digest or sha256(p)==digest)
    if not valid:
        print(f"[asr-model] downloading {name}",flush=True)
        download(f"{ASR_BASE}/{name}?download=true",p)
    if not p.exists() or p.stat().st_size<min_size:raise RuntimeError(f"ASR asset invalid: {name}")
    if digest and sha256(p)!=digest:raise RuntimeError(f"ASR sha mismatch: {name}")
    print(f"[asr-model] ready {name} {p.stat().st_size:,}",flush=True)

# ---- Build-only VITS: generate fixed teaching/reference audio, then delete model ----
TTS_BASE="https://huggingface.co/csukuangfj/vits-cantonese-hf-xiaomaiiwn/resolve/main"
TTS_FILES={
    "vits-cantonese-hf-xiaomaiiwn.onnx":(110_000_000,"7d8d4f5550b607999417a99131b034c8cc2b8dca69f9e9fdefeff6655643c139"),
    "lexicon.txt":(250_000,None),"tokens.txt":(300,None),"rule.fst":(50_000,None),
}
LINES=["流水像清得沒帶半顆沙","前身被擱在上游風化"]
SPEED_VARIANTS={
    "slow":0.60,
    "clear":0.75,
    "reference":0.88,
}
def audio_path(line_idx,label):
    return REF/(f"line{line_idx}.wav" if label=="reference" else f"line{line_idx}_{label}.wav")

audio_files=[audio_path(i,label) for i in range(len(LINES)) for label in SPEED_VARIANTS]
need_audio=not all(p.exists() and p.stat().st_size>3000 for p in audio_files)
if need_audio:
    for name,(min_size,digest) in TTS_FILES.items():
        p=TMP_TTS/name
        valid=p.exists() and p.stat().st_size>=min_size and (not digest or sha256(p)==digest)
        if not valid:
            print(f"[reference] downloading build-only TTS {name}",flush=True)
            download(f"{TTS_BASE}/{name}?download=true",p)
        if p.stat().st_size<min_size:raise RuntimeError(f"TTS asset invalid: {name}")
        if digest and sha256(p)!=digest:raise RuntimeError(f"TTS sha mismatch: {name}")

    cfg=sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(TMP_TTS/"vits-cantonese-hf-xiaomaiiwn.onnx"),
                lexicon=str(TMP_TTS/"lexicon.txt"),
                tokens=str(TMP_TTS/"tokens.txt"),length_scale=1.0),
            provider="cpu",debug=False,num_threads=1),
        rule_fsts=str(TMP_TTS/"rule.fst"),rule_fars="",max_num_sentences=1)
    if not cfg.validate():raise RuntimeError("Build reference TTS config invalid")
    tts=sherpa_onnx.OfflineTts(cfg);t2s=OpenCC("t2s")
    durations={}
    for i,text in enumerate(LINES):
        durations[i]={}
        for label,speed in SPEED_VARIANTS.items():
            out=audio_path(i,label)
            print(f"[reference] generating line{i+1} {label} speed={speed:.2f}",flush=True)
            audio=tts.generate(text=t2s.convert(text),sid=0,speed=speed)
            samples=np.asarray(audio.samples,dtype=np.float32)
            if samples.size<100:raise RuntimeError(f"Audio line{i+1}/{label} generated empty audio")
            pcm=(np.clip(samples,-1,1)*32767).astype("<i2")
            with wave.open(str(out),"wb") as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(int(audio.sample_rate));w.writeframes(pcm.tobytes())
            seconds=float(samples.size)/float(audio.sample_rate)
            durations[i][label]=seconds
            print(f"[reference] line{i+1} {label} ready {seconds:.2f}s bytes={out.stat().st_size}",flush=True)

        # Empirical build-time assertion: teaching slow must really be longer.
        if not (durations[i]["slow"]>durations[i]["clear"]>durations[i]["reference"]):
            raise RuntimeError(f"Unexpected TTS speed ordering for line{i+1}: {durations[i]}")
        if durations[i]["slow"] < durations[i]["reference"]*1.15:
            raise RuntimeError(f"Teaching audio not sufficiently slower for line{i+1}: {durations[i]}")
    del tts
else:
    print("[reference] reuse pre-generated teaching/reference WAVs",flush=True)

# Do not ship the 114MB TTS model in the Song Lesson runtime.
shutil.rmtree(TMP_TTS,ignore_errors=True)
print("[models] Cantonese ASR + fixed reference audio ready",flush=True)
