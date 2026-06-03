"""
Fast TTS via Microsoft Edge neural voices (edge-tts) + translation for Urdu.
Typical segment latency: ~1–4s vs 30s+ for local Vits/Hugging Face.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

EN_VOICE = "en-US-JennyNeural"
# Female voices only — no male fallback per segment
UR_VOICES_FEMALE = (
    "ur-PK-UzmaNeural",
    "ur-IN-GulNeural",
)
UR_VOICES = UR_VOICES_FEMALE


def _setting(name: str, default: str) -> str:
    return str(getattr(settings, name, default) or default).strip()


def edge_tts_enabled() -> bool:
    if _setting("TTS_PREFER_LOCAL", "").lower() in ("1", "true", "yes"):
        return False
    return _setting("TTS_USE_EDGE", "true").lower() not in ("0", "false", "no", "off")


def _edge_rate() -> str:
    return _setting("TTS_EDGE_RATE", "+12%") or "+12%"


def _sanitize_for_tts(text: str) -> str:
    """Strip characters that break Edge TTS or translators."""
    cleaned = " ".join(str(text or "").split())
    cleaned = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", cleaned)
    return cleaned.strip()


async def _stream_edge_audio(text: str, voice: str) -> bytes:
    import edge_tts

    speak = _sanitize_for_tts(text)
    if not speak:
        raise RuntimeError("No speakable text after sanitization")

    communicate = edge_tts.Communicate(speak, voice, rate=_edge_rate())
    parts: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            parts.append(chunk["data"])
    if not parts:
        raise RuntimeError(f"Edge TTS returned no audio for voice {voice}")
    return b"".join(parts)


async def _stream_edge_audio_any_voice(text: str, voices: tuple[str, ...]) -> bytes:
    errors: list[str] = []
    for voice in voices:
        try:
            return await _stream_edge_audio(text, voice)
        except Exception as e:
            errors.append(f"{voice}: {e}")
            logger.warning("Edge voice %s failed: %s", voice, e)
    raise RuntimeError("Edge Urdu TTS failed. " + "; ".join(errors[-3:]))


def _translate_en_to_ur(text: str) -> str:
    cleaned = _sanitize_for_tts(text)
    if not cleaned:
        raise ValueError("Text cannot be empty")
    if len(cleaned) > 4500:
        cleaned = cleaned[:4500].rsplit(" ", 1)[0]

    errors: list[str] = []

    try:
        from deep_translator import GoogleTranslator

        out = GoogleTranslator(source="en", target="ur").translate(cleaned)
        if out and str(out).strip():
            return str(out).strip()
    except Exception as e:
        errors.append(f"Google: {e}")
        logger.warning("Google translate failed: %s", e)

    try:
        from deep_translator import MyMemoryTranslator

        out = MyMemoryTranslator(source="en-GB", target="ur-PK").translate(cleaned)
        if out and str(out).strip():
            return str(out).strip()
    except Exception as e:
        errors.append(f"MyMemory: {e}")
        logger.warning("MyMemory translate failed: %s", e)

    raise RuntimeError(
        "Could not translate to Urdu. "
        + (errors[-1] if errors else "No translation service available.")
    )


def _remote_urdu_fallback(english_text: str) -> dict:
    """HF Space english-to-urdu when Edge + translate path fails."""
    from news.tts_service import _finalize_segment_payload, _remote_synthesize_one

    payload = _remote_synthesize_one(english_text, "urdu")
    payload["source"] = "remote"
    return _finalize_segment_payload(payload)


def pick_session_voice(language: str, voice: str | None = None) -> str:
    """Resolve Edge voice for a listen session (female-only)."""
    lang = str(language or "english").lower().strip()
    if lang == "english":
        return EN_VOICE
    if voice and voice in UR_VOICES_FEMALE:
        return voice
    return UR_VOICES_FEMALE[0]


async def _synthesize_edge_async(text: str, language: str, voice: str | None = None) -> dict:
    cleaned = _sanitize_for_tts(text)
    if not cleaned:
        raise ValueError("Text cannot be empty")
    lang = str(language or "english").lower().strip()
    resolved = pick_session_voice(lang, voice)

    if lang == "english":
        audio = await _stream_edge_audio(cleaned, resolved)
        return {
            "audio": base64.b64encode(audio).decode("ascii"),
            "language": "english",
            "format": "mp3",
            "source": "edge",
            "voice_id": resolved,
        }

    urdu_text = await asyncio.to_thread(_translate_en_to_ur, cleaned)
    try:
        audio = await _stream_edge_audio(urdu_text, resolved)
    except Exception:
        if resolved != UR_VOICES_FEMALE[0]:
            resolved = UR_VOICES_FEMALE[0]
            audio = await _stream_edge_audio(urdu_text, resolved)
        else:
            for alt in UR_VOICES_FEMALE[1:]:
                try:
                    audio = await _stream_edge_audio(urdu_text, alt)
                    resolved = alt
                    break
                except Exception:
                    continue
            else:
                raise
    return {
        "audio": base64.b64encode(audio).decode("ascii"),
        "language": "urdu",
        "format": "mp3",
        "source": "edge",
        "voice_id": resolved,
        "original_text": cleaned,
        "urdu_text": urdu_text,
    }


def synthesize_edge_tts(text: str, language: str = "english", voice: str | None = None) -> dict:
    try:
        import edge_tts  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "edge-tts is not installed. Run: pip install edge-tts deep-translator"
        ) from e

    cleaned = _sanitize_for_tts(text)
    if not cleaned:
        raise ValueError("Text cannot be empty")

    async def _run() -> dict:
        return await _synthesize_edge_async(cleaned, language, voice=voice)

    return asyncio.run(_run())
