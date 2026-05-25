# Deploy `daniB2112/bart-news-summarizer` on Hugging Face Spaces

This folder is a **standalone Gradio demo** for your model. TRAK Backend loads the same model via `SUMMARIZER_MODEL_ID` during `run_ai_pipeline` (no Space required for the Django app).

## Create the Space

1. Go to [huggingface.co/new-space](https://huggingface.co/new-space).
2. **Space name:** e.g. `bart-news-summarizer-demo` (your choice).
3. **License:** same as your model repo if applicable.
4. **SDK:** **Gradio**.
5. **Hardware:** **CPU basic** works (slower); use **GPU** for faster inference (~1.6 GB model).
6. Create the Space.

## Upload these files

Copy the contents of this directory into the Space repo (via the web UI “Files” tab, or git):

| File | Purpose |
|------|---------|
| `app.py` | Gradio UI + inference |
| `requirements.txt` | Python dependencies |
| `README.md` | Space card (this file; edit title/description on HF) |

You do **not** need to upload model weights — the Space downloads `daniB2112/bart-news-summarizer` from the Hub on first run.

## Optional: link model card

In the Space **README** (top of repo on HF), add:

```yaml
---
title: TRAK BART News Summarizer
emoji: 📰
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 5.12.0
app_file: app.py
pinned: false
license: mit
---
```

Adjust `sdk_version` to match your Space settings.

## Git push (alternative)

```bash
git clone https://huggingface.co/spaces/YOUR_USERNAME/YOUR_SPACE_NAME
cd YOUR_SPACE_NAME
# copy app.py and requirements.txt from huggingface-space/
git add app.py requirements.txt README.md
git commit -m "Add Gradio summarizer demo"
git push
```

The Space rebuilds automatically; open the Space URL to test.

## TRAK Backend (production pipeline)

Set in `.env` (already the project default):

```env
SUMMARIZER_MODEL_ID=daniB2112/bart-news-summarizer
SUMMARIZER_ENABLED=true
```

Then: `python manage.py run_ai_pipeline --limit 10`
