# Cantonese Coach MVP

Personal Cantonese pronunciation practice app powered by cantonese.ai.

This code lives on an isolated branch and does not modify the repository's `main` branch.

## Live MVP

Render service: `terry-cantonese-coach-live`

## Runtime

- Node.js, zero npm dependencies
- Main entry: `server.mjs`
- No database
- No GPU
- Cantonese.ai API key is entered by the user in the browser and kept in `sessionStorage`; the app forwards it for TTS / pronunciation requests and does not persist it.

## Current features

- Chinese text → Jyutping
- cantonese.ai v6 reference TTS with Jyutping guidance
- Browser microphone recording encoded as WAV
- cantonese.ai Cantonese pronunciation score
- Expected Jyutping vs transcribed Jyutping
- Per-syllable tone mismatch feedback
- Tone hit rate

## Deployment

Render build command:

```sh
node --check cantonese-coach-mvp/server.mjs
```

Render start command:

```sh
node cantonese-coach-mvp/server.mjs
```
