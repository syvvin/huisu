"""Optional local ASR fallback.

YouTube auto-captions are the fast path.  If a recording has no caption
track, faster-whisper can provide word timings from the downloaded audio.
The fallback is intentionally lazy: it only imports/downloads a model when
it is actually needed.  If the CUDA runtime is incomplete (a common Windows
problem with CTranslate2), it retries on CPU instead of losing the Part.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .asr import AsrError, Word

log = logging.getLogger(__name__)

_model = None
_model_key: tuple[str, str, str] | None = None


def _load_model(model_name: str, *, force_cpu: bool = False):
    global _model, _model_key
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise AsrError(
            "No caption track was available and faster-whisper is not installed."
        ) from exc

    attempts = (
        [("cpu", "int8")]
        if force_cpu
        else [("cuda", "float16"), ("cpu", "int8")]
    )
    last_error: Exception | None = None
    for device, compute_type in attempts:
        key = (model_name, device, compute_type)
        if _model is not None and _model_key == key:
            return _model
        try:
            log.info("Loading faster-whisper %s on %s (%s)", model_name, device, compute_type)
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _model = model
            _model_key = key
            return model
        except Exception as exc:
            last_error = exc
            log.warning("Whisper on %s failed: %s", device, exc)
    raise AsrError(f"Could not load faster-whisper: {last_error}")


def _run_model(model, audio_path: Path, offset: float) -> list[Word]:
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        word_timestamps=True,
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
    )
    words: list[Word] = []
    for segment in segments:
        for item in segment.words or []:
            text = (item.word or "").strip()
            if not text:
                continue
            start = offset + float(item.start)
            end = offset + float(item.end)
            words.append(Word(text=text, start=start, end=max(end, start + 0.08)))
    log.info("Whisper produced %s words for %.1fs of audio", len(words), info.duration)
    return words


def transcribe_segment(
    audio_path: Path,
    offset: float,
    *,
    model_name: str = "small",
) -> list[Word]:
    model = _load_model(model_name)
    try:
        words = _run_model(model, audio_path, offset)
    except Exception as exc:
        global _model, _model_key
        if _model_key and _model_key[1] == "cuda":
            log.warning("CUDA transcription failed (%s); retrying faster-whisper on CPU.", exc)
            _model = None
            _model_key = None
            model = _load_model(model_name, force_cpu=True)
            try:
                words = _run_model(model, audio_path, offset)
            except Exception as cpu_exc:
                raise AsrError(f"Whisper transcription failed: {cpu_exc}") from cpu_exc
        else:
            raise AsrError(f"Whisper transcription failed: {exc}") from exc
    if len(words) < 5:
        raise AsrError("Whisper returned too few words.")
    return words
