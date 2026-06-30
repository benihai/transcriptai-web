"""תמלול אודיו לטקסט באמצעות faster-whisper (מקומי, תומך עברית)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

from . import config

config.ensure_ffmpeg_on_path()

# המודל נטען פעם אחת ונשמר בזיכרון (טעינה ראשונה מורידה את המשקלים)
_model = None


@dataclass
class Segment:
    """קטע מתומלל בודד עם חותמות זמן."""
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    """תוצאת תמלול מלאה."""
    text: str
    language: str
    segments: List[Segment] = field(default_factory=list)


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        # int8 = מהיר וחסכוני בזיכרון, מתאים ל-CPU
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
            cpu_threads=config.WHISPER_CPU_THREADS,   # ניצול כל הליבות
        )
    return _model


def transcribe(audio_path: str, language: str | None = config.WHISPER_LANGUAGE,
               on_progress=None) -> TranscriptResult:
    """מתמלל קובץ אודיו ומחזיר טקסט מלא + קטעים עם זמנים.

    on_progress: פונקציה אופציונלית שתיקרא עם כל קטע (להצגת התקדמות).
    """
    model = _get_model()
    segments_iter, info = model.transcribe(
        audio_path,
        language=language,
        vad_filter=True,                          # מדלג על שתיקות - מזרז ומשפר דיוק
        beam_size=config.WHISPER_BEAM_SIZE,        # 1 = מהיר
    )

    segments: List[Segment] = []
    parts: List[str] = []
    for seg in segments_iter:
        s = Segment(start=seg.start, end=seg.end, text=seg.text.strip())
        segments.append(s)
        parts.append(s.text)
        if on_progress:
            on_progress(s)

    return TranscriptResult(
        text=" ".join(parts).strip(),
        language=info.language,
        segments=segments,
    )
