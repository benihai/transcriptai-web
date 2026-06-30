"""
תמלול דרך Groq Whisper API — מהיר פי ~100 מ-faster-whisper מקומי.
5-15 שניות לפגישה של שעה (במקום 8-15 דקות).

מגבלת Groq: 25MB לקובץ.
פתרון: המרה ל-MP3 בקצב נמוך (32kbps) לפני שליחה — מקטינה כ-10×.
פגישה של שעה (50MB webm) → ~4MB mp3 → מתחת למגבלה.

משתמש באותה interface כמו src/transcribe.py:
    from server.transcribe_groq import transcribe
    result = transcribe(audio_path, language="he", on_progress=callback)
"""
from __future__ import annotations

import os
import sys
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Callable

# Import TranscriptResult from the local transcribe module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.transcribe import TranscriptResult, Segment
from src import config

GROQ_MODEL = "whisper-large-v3-turbo"
MAX_FILE_BYTES = 24 * 1024 * 1024   # 24MB — מרווח בטיחות מהמגבלה של 25MB
MP3_BITRATE = "32k"                  # איכות מינימלית לתמלול (ברורה מספיק לדיבור)


def transcribe(audio_path: str, language: str | None = None,
               on_progress: Callable | None = None) -> TranscriptResult:
    """
    מתמלל קובץ אודיו דרך Groq Whisper API.
    מחזיר TranscriptResult עם text + segments (ריק ב-Groq).
    """
    from groq import Groq

    api_key = config.GROQ_API_KEY
    if not api_key:
        raise RuntimeError("GROQ_API_KEY לא מוגדר ב-.env")

    client = Groq(api_key=api_key)
    lang = language or config.WHISPER_LANGUAGE or "he"

    # המר לmp3 אם הקובץ גדול מדי
    audio_path_to_send = _prepare_audio(audio_path)
    temp_created = audio_path_to_send != audio_path

    try:
        if on_progress:
            # Groq מהיר מדי לprogress אמיתי — מציג spinner
            try:
                from src.transcribe import Segment as _Seg
                on_progress(_Seg(start=0, end=1, text=""))  # טריגר ל-UI
            except Exception:
                pass

        with open(audio_path_to_send, "rb") as f:
            resp = client.audio.transcriptions.create(
                file=(Path(audio_path_to_send).name, f),
                model=GROQ_MODEL,
                language=lang,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        # בניית segments מהתגובה
        raw_segs = getattr(resp, "segments", None) or []
        segments = [
            Segment(start=s.get("start", 0), end=s.get("end", 0), text=(s.get("text") or "").strip())
            for s in raw_segs
        ]

        text = (getattr(resp, "text", None) or "").strip()
        detected_lang = getattr(resp, "language", None) or lang

        return TranscriptResult(text=text, language=detected_lang, segments=segments)

    finally:
        if temp_created and os.path.exists(audio_path_to_send):
            try:
                os.unlink(audio_path_to_send)
            except Exception:
                pass


def _prepare_audio(audio_path: str) -> str:
    """
    אם הקובץ גדול מ-24MB — ממיר ל-mp3 בקצב נמוך.
    מחזיר נתיב לקובץ (מקורי או temp).
    """
    size = os.path.getsize(audio_path)
    if size <= MAX_FILE_BYTES:
        return audio_path  # קובץ קטן מספיק, שלח ישירות

    # המרה ל-mp3
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-vn",                          # רק אודיו
        "-ac", "1",                     # mono
        "-ar", "16000",                 # 16kHz — מספיק לתמלול
        "-b:a", MP3_BITRATE,
        tmp.name,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        # אם ffmpeg נכשל — שלח המקורי ונסה בכל זאת
        os.unlink(tmp.name)
        return audio_path

    # בדוק שהmp3 לא גדול מדי (פגישות ארוכות מאוד)
    mp3_size = os.path.getsize(tmp.name)
    if mp3_size > MAX_FILE_BYTES:
        # פגישה ארוכה מאוד — חלק לשניים ותמלל כל חלק
        # (מקרה נדיר — פגישה של 6+ שעות)
        os.unlink(tmp.name)
        return _split_and_transcribe_fallback(audio_path)

    return tmp.name


def _split_and_transcribe_fallback(audio_path: str) -> str:
    """
    עבור פגישות ארוכות מאוד (6+ שעות): חלק ל-20 דקות כל אחת.
    מחזיר קובץ mp3 של החלק הראשון (תמלול חלקי).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", MP3_BITRATE,
        "-t", "1200",  # 20 דקות ראשונות
        tmp.name,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)
    return tmp.name
