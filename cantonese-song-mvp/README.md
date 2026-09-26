# Cantonese Song Lesson Builder

A browser-based Cantonese song-learning prototype that turns pasted lyrics into line-by-line pronunciation lessons.

**Live demo:** https://terry-cantonese-songlesson-v2.onrender.com

## What it does

Paste Cantonese lyrics into the page. Each non-empty line becomes one lesson.

For every line, the app currently supports:

- automatic character-level Jyutping generation
- clickable character pronunciation
- sentence pronunciation at three speeds:
  - 0.60× teaching speed
  - 0.75× clear speed
  - 0.88× natural/reference speed
- microphone recording for the whole line
- per-syllable pronunciation assessment
- separate diagnostics for:
  - initial
  - final
  - lexical tone
  - duration
- acoustic fallback when ASR itself is uncertain
- neutral "unrated" state when the system cannot make a reliable judgment

The scorer is designed to avoid treating system uncertainty as learner error.

## Current architecture

```text
Lyrics pasted in browser
        |
        v
Line parser + local Jyutping dictionary
        |
        +--> sentence / character playback
        |
        +--> user records one full line
                  |
                  v
         Cantonese WeNet CTC ASR
                  |
         constrained Jyutping alignment
                  |
        +---------+---------+
        |                   |
initial/final           F0 tone contour
acoustic check          comparison
        |                   |
        +---------+---------+
                  |
                  v
        per-syllable feedback
```

### TTS

The current Cantonese TTS model is:

- `csukuangfj/vits-cantonese-hf-xiaomaiiwn`
- Sherpa-ONNX runtime
- no external API key required

For the two original demo lines, reference WAVs are generated at build time.

For arbitrary pasted lyrics, the Render Free instance uses an **engine-switching strategy** because the 512 MB instance cannot keep both ASR and VITS resident at the same time:

1. ASR is normally loaded.
2. For a new uncached lyric line, ASR is released.
3. VITS is loaded locally.
4. 0.60× / 0.75× / 0.88× WAVs are generated and cached.
5. VITS is released.
6. ASR is loaded again.

This avoids the earlier dependency on sleeping external TTS services.

## Pronunciation assessment

The current evaluator is intentionally not a single black-box score.

For each target syllable it combines:

- Cantonese ASR alignment
- Jyutping syllable matching
- initial/final comparison
- reference-audio spectral similarity
- F0 tone-contour comparison
- syllable duration

If ASR is unreliable at a position, the system tries the reference-audio acoustic comparison instead. If evidence is still insufficient, the syllable is marked **unrated** and excluded from the total score.

This is still an experimental pronunciation coach, not a standardized language-proficiency score.

## Dynamic lyrics

The current dynamic input limits are:

- up to 60 non-empty lyric lines
- up to 120 characters per line
- punctuation is displayed but excluded from pronunciation scoring
- unsupported characters are surfaced to the UI instead of silently scored

### Important limitation: polyphonic characters

Jyutping currently comes from the bundled CantoJpMin-style character dictionary and defaults to the first available reading.

That means context-sensitive/polyphonic characters can still be wrong. A future version should support contextual G2P and/or manual Jyutping correction per character.

## Validation performed

The deployed build has been tested with both the original demo lines and a line that was not hard-coded in the application.

Dynamic end-to-end test:

```text
今天我想學粵語
gam1 tin1 ngo5 soeng2 hok6 jyut6 jyu5
```

Observed deployment self-test:

- dynamic parse succeeded
- 7 / 7 character audio segments generated
- pronunciation reference generated locally
- assessment coverage: 100%
- reference-audio self-score: 99
- ASR -> VITS -> ASR engine switching completed without OOM on Render Free

The first generation of a completely new line is slower because the service has to swap ASR and VITS models. In the measured self-test, generation of all three TTS variants plus ASR restoration took about 38 seconds. Cached lines are much faster afterward.

## Run locally

Requirements:

- Python 3.13 tested in the current Render deployment
- enough disk space for the Cantonese ASR and VITS ONNX models
- a modern browser with microphone permission

Install and download models:

```bash
pip install -r cantonese-song-mvp/requirements.txt
python cantonese-song-mvp/download_models.py
```

Start:

```bash
python cantonese-song-mvp/server.py
```

Then open:

```text
http://localhost:10000
```

The server uses `PORT` when provided by the environment.

## Main files

- `index.html` — Song Lesson Builder UI
- `app.js` — lyrics builder, recording, playback, result rendering
- `server.py` — dynamic lyric parsing, TTS/ASR engine switching, scoring
- `download_models.py` — model download and build-time reference generation
- `requirements.txt` — Python dependencies

The project also reuses the bundled Jyutping dictionary under `cantonese-coach-mvp/vendor/`.

## Stable rollback branches

The development work intentionally kept rollback points:

- `cantonese-song-charclick-stable-20260924` — fixed two-line version with clickable character audio
- `cantonese-song-eval-stable-20260924` — stable per-syllable evaluation version
- `cantonese-coach-vits-stable-20260924` — stable VITS-only baseline

## Deployment

Current Render service:

- service: `terry-cantonese-songlesson-v2`
- URL: https://terry-cantonese-songlesson-v2.onrender.com
- runtime: Python
- region: Singapore
- current demo plan: Render Free

## Scope

This is currently a learning/research prototype. The main next technical improvements are:

1. contextual Jyutping / polyphonic-character handling
2. lower-latency generation for completely new lyric lines
3. stronger phone-level pronunciation scoring
4. persistence for songs, progress, and cached lesson assets
