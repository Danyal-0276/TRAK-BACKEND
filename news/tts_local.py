"""
In-process TTS fallback when the Hugging Face Space is down or misconfigured.
Mirrors abd8433/urdu-tts-api (Vits English + Urdu, MBart en→ur).
Models load lazily on first request.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import numpy as np
import scipy.io.wavfile
import torch
from transformers import (
    AutoTokenizer,
    MBartForConditionalGeneration,
    MBart50TokenizerFast,
    VitsModel,
)

logger = logging.getLogger(__name__)

_state: dict[str, Any] = {}


def _english_models():
    if "english" not in _state:
        logger.info("Loading local English TTS model…")
        model = VitsModel.from_pretrained("facebook/mms-tts-eng")
        tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-eng")
        model.eval()
        _state["english"] = (model, tokenizer)
    return _state["english"]


def _urdu_models():
    if "urdu" not in _state:
        logger.info("Loading local Urdu TTS model…")
        model = VitsModel.from_pretrained("facebook/mms-tts-urd-script_arabic")
        tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-urd-script_arabic")
        model.eval()
        _state["urdu"] = (model, tokenizer)
    return _state["urdu"]


def _translate_models():
    if "translate" not in _state:
        logger.info("Loading local English→Urdu translation model…")
        model_id = "abdulwaheed1/english-to-urdu-translation-mbart"
        tokenizer = MBart50TokenizerFast.from_pretrained(
            model_id, src_lang="en_XX", tgt_lang="ur_PK"
        )
        model = MBartForConditionalGeneration.from_pretrained(model_id)
        model.eval()
        _state["translate"] = (model, tokenizer)
    return _state["translate"]


def _generate_audio_base64(text: str, model, tokenizer) -> str:
    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        waveform = model(**inputs).waveform
    waveform_np = waveform.squeeze().numpy()
    sample_rate = model.config.sampling_rate
    buffer = io.BytesIO()
    scipy.io.wavfile.write(buffer, rate=sample_rate, data=waveform_np)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


def _translate_to_urdu(english_text: str) -> str:
    model, tokenizer = _translate_models()
    inputs = tokenizer(english_text, return_tensors="pt")
    with torch.no_grad():
        tokens = model.generate(
            **inputs,
            forced_bos_token_id=tokenizer.lang_code_to_id["ur_PK"],
            max_length=512,
        )
    return tokenizer.decode(tokens[0], skip_special_tokens=True)


def synthesize_local_tts(text: str, language: str = "english") -> dict:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise ValueError("Text cannot be empty")

    lang = str(language or "english").lower().strip()
    if lang == "english":
        model, tokenizer = _english_models()
        audio = _generate_audio_base64(cleaned, model, tokenizer)
        return {"audio": audio, "language": "english", "text": cleaned, "source": "local"}

    model, tokenizer = _urdu_models()
    urdu_text = _translate_to_urdu(cleaned)
    audio = _generate_audio_base64(urdu_text, model, tokenizer)
    return {
        "audio": audio,
        "language": "urdu",
        "original_text": cleaned,
        "urdu_text": urdu_text,
        "source": "local",
    }
