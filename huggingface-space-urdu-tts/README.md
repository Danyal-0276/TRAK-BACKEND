# Urdu TTS API (Hugging Face Space)

Deploy as a **Docker** Space. `requirements.txt` must include **numpy** and **scipy** or TTS returns `500: Numpy is not available`.

```bash
# From this folder, push to https://huggingface.co/spaces/abd8433/urdu-tts-api
```

Endpoints: `POST /tts/english`, `POST /tts/english-to-urdu` with body `{"text": "..."}`.
