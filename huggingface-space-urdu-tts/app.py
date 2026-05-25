from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from transformers import (
    VitsModel,
    AutoTokenizer,
    MBartForConditionalGeneration,
    MBart50TokenizerFast,
)
import torch
import scipy.io.wavfile
import base64
import io
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bilingual TTS API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("Loading English TTS model...")
english_tts_model = VitsModel.from_pretrained("facebook/mms-tts-eng")
english_tts_tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-eng")
english_tts_model.eval()
logger.info("English TTS model loaded")

logger.info("Loading Urdu TTS model...")
urdu_tts_model = VitsModel.from_pretrained("facebook/mms-tts-urd-script_arabic")
urdu_tts_tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-urd-script_arabic")
urdu_tts_model.eval()
logger.info("Urdu TTS model loaded")

logger.info("Loading Translation model (English → Urdu)...")
TRANSLATE_MODEL = "abdulwaheed1/english-to-urdu-translation-mbart"
translate_tokenizer = MBart50TokenizerFast.from_pretrained(
    TRANSLATE_MODEL, src_lang="en_XX", tgt_lang="ur_PK"
)
translate_model = MBartForConditionalGeneration.from_pretrained(TRANSLATE_MODEL)
translate_model.eval()
logger.info("Translation model loaded")


def generate_audio_base64(text: str, model, tokenizer) -> str:
    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        waveform = model(**inputs).waveform

    waveform_np = waveform.squeeze().numpy()
    sample_rate = model.config.sampling_rate

    buffer = io.BytesIO()
    scipy.io.wavfile.write(buffer, rate=sample_rate, data=waveform_np)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


def translate_to_urdu(english_text: str) -> str:
    inputs = translate_tokenizer(english_text, return_tensors="pt")
    with torch.no_grad():
        tokens = translate_model.generate(
            **inputs,
            forced_bos_token_id=translate_tokenizer.lang_code_to_id["ur_PK"],
            max_length=512,
        )
    return translate_tokenizer.decode(tokens[0], skip_special_tokens=True)


class TTSRequest(BaseModel):
    text: str


@app.get("/")
def root():
    return {"status": "Bilingual TTS API is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tts/english")
def english_tts(request: TTSRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        logger.info("English TTS: %s", request.text[:50])
        audio_b64 = generate_audio_base64(
            request.text, english_tts_model, english_tts_tokenizer
        )
        return {
            "audio": audio_b64,
            "language": "english",
            "text": request.text,
        }
    except Exception as e:
        logger.error("English TTS error: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/tts/english-to-urdu")
def english_to_urdu_tts(request: TTSRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        logger.info("Translating: %s", request.text[:50])
        urdu_text = translate_to_urdu(request.text)
        logger.info("Translated: %s", urdu_text[:50])
        audio_b64 = generate_audio_base64(
            urdu_text, urdu_tts_model, urdu_tts_tokenizer
        )
        return {
            "audio": audio_b64,
            "language": "urdu",
            "original_text": request.text,
            "urdu_text": urdu_text,
        }
    except Exception as e:
        logger.error("English→Urdu TTS error: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
