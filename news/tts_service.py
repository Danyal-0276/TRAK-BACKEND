"""Proxy to Hugging Face bilingual TTS space (English + English→Urdu)."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

TTS_MAX_CHARS = 40_000
# Smaller chunks = faster per request; short first chunk = playback starts sooner.
TTS_FIRST_CHUNK_CHARS = 100
TTS_CHUNK_CHARS = 240
TTS_BATCH_MAX = 4


def _wav_base64_for_browsers(audio_b64: str) -> str:
    """
    HF Vits outputs float32 WAV; many browsers only play PCM int16 in <audio>.
    Re-encode to int16 when numpy/scipy are available.
    """
    try:
        import numpy as np
        import scipy.io.wavfile as wavfile
    except ImportError:
        return audio_b64

    try:
        raw = base64.b64decode(audio_b64)
        rate, data = wavfile.read(io.BytesIO(raw))
        if np.issubdtype(data.dtype, np.floating):
            data = np.clip(data, -1.0, 1.0)
            if data.ndim > 1:
                data = data.mean(axis=1)
            data = (data * 32767).astype(np.int16)
        elif data.dtype != np.int16:
            data = data.astype(np.int16)
        out = io.BytesIO()
        wavfile.write(out, rate, data)
        return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception as e:
        logger.warning("WAV normalize skipped: %s", e)
        return audio_b64


def _chunk_text(text: str, chunk_size: int = TTS_CHUNK_CHARS) -> list[str]:
    """Split long articles into TTS-safe chunks (sentence/paragraph aware)."""
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    parts: list[str] = []
    for para in re.split(r"\n{2,}|\.\s+", cleaned):
        bit = para.strip()
        if not bit:
            continue
        if not bit.endswith("."):
            bit = bit + "."
        if len(bit) <= chunk_size:
            parts.append(bit)
            continue
        words = bit.split()
        buf: list[str] = []
        length = 0
        for word in words:
            add = len(word) + (1 if buf else 0)
            if length + add > chunk_size and buf:
                parts.append(" ".join(buf))
                buf = [word]
                length = len(word)
            else:
                buf.append(word)
                length += add
        if buf:
            parts.append(" ".join(buf))

    merged: list[str] = []
    carry = ""
    for p in parts:
        if not carry:
            carry = p
        elif len(carry) + 1 + len(p) <= chunk_size:
            carry = f"{carry} {p}"
        else:
            merged.append(carry)
            carry = p
    if carry:
        merged.append(carry)
    return merged or [cleaned[:chunk_size]]


def _merge_wav_base64_parts(parts: list[str]) -> str:
    import numpy as np
    import scipy.io.wavfile as wavfile

    if not parts:
        raise RuntimeError("No audio to merge")
    if len(parts) == 1:
        return parts[0]

    arrays: list[np.ndarray] = []
    rate: int | None = None
    pause = None

    for b64 in parts:
        raw = base64.b64decode(b64)
        r, data = wavfile.read(io.BytesIO(raw))
        if rate is None:
            rate = int(r)
            pause = np.zeros(int(rate * 0.35), dtype=np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if np.issubdtype(data.dtype, np.integer):
            data = data.astype(np.float32) / 32767.0
        else:
            data = data.astype(np.float32)
        arrays.append(data)
        arrays.append(pause)

    combined = np.clip(np.concatenate(arrays[:-1]), -1.0, 1.0)
    out = io.BytesIO()
    wavfile.write(out, rate or 22050, (combined * 32767).astype(np.int16))
    return base64.b64encode(out.getvalue()).decode("ascii")


def _base_url() -> str:
    return (getattr(settings, "TTS_API_BASE_URL", None) or "https://abd8433-urdu-tts-api.hf.space").rstrip("/")


def _prefer_local() -> bool:
    return str(getattr(settings, "TTS_PREFER_LOCAL", "") or "").lower() in (
        "1",
        "true",
        "yes",
    )


def _remote_synthesize_one(text: str, language: str) -> dict:
    """Single-chunk Hugging Face Space call."""
    lang = str(language or "english").lower().strip()
    path = "/tts/english" if lang == "english" else "/tts/english-to-urdu"
    url = f"{_base_url()}{path}"
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    per_chunk = max(60, int(getattr(settings, "TTS_API_TIMEOUT_SEC", 120) or 120))
    try:
        with urllib.request.urlopen(req, timeout=per_chunk) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning("TTS HTTP %s: %s", e.code, detail)
        raise RuntimeError(f"TTS service error ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        logger.warning("TTS unreachable: %s", e)
        raise RuntimeError("TTS service is unavailable. Try again in a moment.") from e

    if not payload.get("audio"):
        raise RuntimeError("TTS service returned no audio")
    return payload


def _remote_synthesize(text: str, language: str) -> dict:
    chunks = _chunk_text(text)
    logger.info("TTS remote: %s chunk(s), %s chars", len(chunks), len(text))
    audio_parts: list[str] = []
    urdu_parts: list[str] = []
    for i, ch in enumerate(chunks):
        logger.info("TTS remote chunk %s/%s (%s chars)", i + 1, len(chunks), len(ch))
        payload = _remote_synthesize_one(ch, language)
        audio_parts.append(str(payload["audio"]))
        if payload.get("urdu_text"):
            urdu_parts.append(str(payload["urdu_text"]))

    merged = _merge_wav_base64_parts(audio_parts)
    out: dict = {
        "audio": _wav_base64_for_browsers(merged),
        "language": language,
        "chunks": len(chunks),
        "source": "remote",
    }
    if urdu_parts:
        out["urdu_text"] = " ".join(urdu_parts)
    return out


def _local_synthesize(text: str, language: str) -> dict:
    from news.tts_local import synthesize_local_tts

    chunks = _chunk_text(text)
    logger.info("TTS local: %s chunk(s), %s chars", len(chunks), len(text))
    audio_parts: list[str] = []
    urdu_parts: list[str] = []
    for i, ch in enumerate(chunks):
        logger.info("TTS local chunk %s/%s (%s chars)", i + 1, len(chunks), len(ch))
        payload = synthesize_local_tts(ch, language=language)
        audio_parts.append(str(payload["audio"]))
        if payload.get("urdu_text"):
            urdu_parts.append(str(payload["urdu_text"]))

    merged = _merge_wav_base64_parts(audio_parts)
    out: dict = {
        "audio": _wav_base64_for_browsers(merged),
        "language": language,
        "chunks": len(chunks),
        "source": "local",
    }
    if urdu_parts:
        out["urdu_text"] = " ".join(urdu_parts)
    return out


def _normalize_tts_input(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise ValueError("Text cannot be empty")
    if len(cleaned) > TTS_MAX_CHARS:
        cleaned = cleaned[:TTS_MAX_CHARS].rsplit(" ", 1)[0] + " ..."
    return cleaned


def _chunk_text_progressive(text: str, first_size: int, rest_size: int) -> list[str]:
    """First segment is small for fast time-to-first-audio; later segments use rest_size."""
    if len(text) <= first_size:
        return [text]
    segments = _chunk_text(text, rest_size)
    if not segments:
        return [text[:first_size]]
    if len(segments[0]) <= first_size:
        return segments
    head = _chunk_text(segments[0], first_size)
    return head + segments[1:]


def plan_article_tts_segments(text: str) -> list[str]:
    """Split article for progressive playback (small first chunk, then ~320 chars)."""
    cleaned = _normalize_tts_input(text)
    return _chunk_text_progressive(cleaned, TTS_FIRST_CHUNK_CHARS, TTS_CHUNK_CHARS)


def _finalize_segment_payload(payload: dict) -> dict:
    if str(payload.get("format") or "").lower() == "mp3":
        return payload
    payload["format"] = "wav"
    payload["audio"] = _wav_base64_for_browsers(str(payload["audio"]))
    return payload


def _synthesize_segment_once(text: str, language: str) -> dict:
    """Synthesize one segment (no merge)."""
    chunk = " ".join(str(text or "").split())
    if not chunk:
        raise ValueError("Text cannot be empty")

    from news.tts_edge import edge_tts_enabled, synthesize_edge_tts

    if edge_tts_enabled():
        try:
            return _finalize_segment_payload(synthesize_edge_tts(chunk, language=language))
        except Exception as edge_err:
            logger.warning("Edge TTS failed, falling back: %s", edge_err)

    if _prefer_local():
        from news.tts_local import synthesize_local_tts

        payload = synthesize_local_tts(chunk, language=language)
        payload["source"] = "local"
    else:
        try:
            payload = _remote_synthesize_one(chunk, language)
            payload["source"] = "remote"
        except RuntimeError as remote_err:
            logger.info("Remote TTS segment failed, local fallback: %s", remote_err)
            from news.tts_local import synthesize_local_tts

            payload = synthesize_local_tts(chunk, language=language)
            payload["source"] = "local"

    return _finalize_segment_payload(payload)


def synthesize_article_tts_segments_batch(
    segments: list[str], language: str = "english"
) -> list[dict]:
    """Synthesize up to TTS_BATCH_MAX segments in parallel (fewer round-trips)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    texts = [" ".join(str(s or "").split()) for s in segments[:TTS_BATCH_MAX]]
    texts = [t for t in texts if t]
    if not texts:
        raise ValueError("No segments to synthesize")

    results: list[dict | None] = [None] * len(texts)
    workers = min(TTS_BATCH_MAX, len(texts))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(synthesize_article_tts_segment, t, language): i
            for i, t in enumerate(texts)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()
    return [r for r in results if r is not None]


def synthesize_article_tts_segment(text: str, language: str = "english") -> dict:
    """Public API: one chunk of audio for streaming playback."""
    return _synthesize_segment_once(text, language)


def synthesize_article_tts(text: str, language: str = "english") -> dict:
    """Merge all segments (legacy / single-shot). Prefer plan + segment for streaming."""
    cleaned = _normalize_tts_input(text)

    if _prefer_local():
        try:
            return _local_synthesize(cleaned, language)
        except Exception as e:
            logger.exception("Local TTS failed")
            raise RuntimeError(f"Local TTS failed: {e}") from e

    try:
        return _remote_synthesize(cleaned, language)
    except RuntimeError as remote_err:
        logger.info("Remote TTS failed, trying local fallback: %s", remote_err)
        try:
            return _local_synthesize(cleaned, language)
        except Exception as local_err:
            logger.exception("Local TTS fallback failed")
            msg = str(remote_err)
            if "500" in msg and "Numpy" in msg:
                msg += (
                    " The Hugging Face Space is missing numpy in requirements.txt — "
                    "redeploy from huggingface-space-urdu-tts/ in this repo."
                )
            raise RuntimeError(msg) from local_err
