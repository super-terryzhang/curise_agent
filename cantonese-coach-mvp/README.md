# Cantonese Coach MVP

Personal Cantonese pronunciation practice app powered by cantonese.ai.

This branch is isolated from main and exists only for the MVP deployment.

## Runtime
The application source is packaged in `app.tgz.b64`. Render decodes it at runtime and starts the Node 20 zero-dependency server.

## Features
- Cantonese text -> Jyutping
- cantonese.ai v6 reference TTS
- In-browser WAV recording
- cantonese.ai pronunciation scoring
- expected vs transcribed Jyutping
- syllable and tone mismatch feedback
- experimental local F0 pitch contour

No database. The cantonese.ai API key is entered in the browser and kept only in sessionStorage.
