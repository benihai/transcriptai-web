"""
Pipeline של שרת: תמלול + סיכום + שמירת JSON + שליחת מייל.
נקרא מ-watcher_server.py אחרי כל הקלטה שנקלטה מהבוט.

אין תלויות ב-Windows כאן:
  - faster-whisper רץ על CPU ב-Linux
  - Gemini הוא API חיצוני
  - שמירה לתיקיות רגילות
"""
from __future__ import annotations

import json
import datetime as dt
from pathlib import Path

from src import config, summarize

# העדפת Groq אם key מוגדר — מהיר פי ~100
if config.GROQ_API_KEY:
    from server.transcribe_groq import transcribe
else:
    from src import transcribe  # type: ignore[no-redef]


def run_pipeline(wav_path: str, project: str, title: str | None = None,
                 participants: list | None = None, log=print):
    """
    מריץ את כל שלבי העיבוד על קובץ ההקלטה.

    פרמטרים:
        wav_path  - נתיב לקובץ webm/wav/mp4
        project   - שם הפרויקט
        title     - כותרת ידנית (מהיומן); אם None → Gemini יציע כותרת
        log       - פונקציית לוג
    """
    import time as _time
    from src import storage as _storage

    project = project or config.DEFAULT_PROJECT
    log(f"PIPELINE start: {wav_path} project={project} title={title!r}")

    # meeting_id מבוסס על זמן ההקלטה (מהשם), לא זמן העיבוד
    rec_stem = Path(wav_path).stem
    if rec_stem.startswith("meeting_"):
        try:
            dt.datetime.strptime(rec_stem, "meeting_%Y%m%d_%H%M%S")
            stamp = rec_stem.replace("meeting_", "")
        except ValueError:
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    _, summ_dir = config.project_dirs(project)
    meeting_id = f"meeting_{stamp}"
    base_title = (title or "").strip() or "פגישה (בעיבוד...)"

    # progress file — web server קורא ומציג למשתמש
    _prog_file = config.DATA_DIR / "pipeline_progress.json"
    _cancel_file = config.DATA_DIR / "pipeline_cancel"

    # נקה cancel file ישן מריצה קודמת
    try:
        if _cancel_file.exists(): _cancel_file.unlink()
    except Exception:
        pass

    def _is_cancelled() -> bool:
        return _cancel_file.exists()

    def _write_progress(phase: str, pct: int):
        try:
            _prog_file.write_text(json.dumps({
                "meeting_id": meeting_id, "phase": phase,
                "pct": pct, "ts": _time.time()
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _clear_progress():
        try:
            if _prog_file.exists(): _prog_file.unlink()
            if _cancel_file.exists(): _cancel_file.unlink()
        except Exception:
            pass

    # שמירה ראשונית — סטטוס "processing"
    out = summ_dir / f"{meeting_id}.json"
    _save(out, {
        "id": meeting_id, "ok": True, "audio_path": wav_path,
        "transcript": "", "project": project,
        "custom_title": (title or "").strip(), "status": "processing",
        "summary": {"title": base_title, "summary": "", "topics": [],
                    "key_points": [], "decisions": [], "action_items": []},
    })

    # ---- תמלול ----
    _write_progress("מתמלל...", 0)
    dur = _storage.media_duration(wav_path) or 0

    try:
        def on_seg(seg):
            if _is_cancelled():
                raise InterruptedError("cancelled by user")
            if dur:
                pct = min(99, round(seg.end / dur * 100))
                _write_progress("מתמלל...", pct)

        result = transcribe(wav_path, on_progress=on_seg)
    except InterruptedError:
        _clear_progress()
        log(f"PIPELINE cancelled by user during transcription")
        return
    except Exception as e:
        _clear_progress()
        log(f"PIPELINE transcribe ERROR: {e}")
        return

    if not result.text.strip():
        _clear_progress()
        log("PIPELINE: no speech detected — skipping")
        return
    log(f"PIPELINE transcribed: {len(result.text)} chars")
    base_title = (title or "").strip() or "פגישה (ממתין לסיכום)"
    data: dict = {
        "id": meeting_id,
        "ok": True,
        "audio_path": wav_path,
        "transcript": result.text,
        "project": project,
        "custom_title": (title or "").strip(),
        "status": "pending_summary",
        "summary": {
            "title": base_title,
            "summary": "",
            "topics": [],
            "key_points": [],
            "decisions": [],
            "action_items": [],
        },
    }
    out = summ_dir / f"{meeting_id}.json"
    _save(out, data)
    log(f"PIPELINE transcript saved: {out}")

    # ---- סיכום ----
    _write_progress("מסכם עם Gemini...", 0)
    try:
        if _is_cancelled():
            data["status"] = "pending_summary"
            _save(out, data)
            _clear_progress()
            log("PIPELINE cancelled by user before summarization")
            return

        def on_sum(pct):
            if _is_cancelled():
                raise InterruptedError("cancelled")
            _write_progress("מסכם עם Gemini...", pct)

        s = summarize.summarize(result.text, on_progress=on_sum, participants=participants)
        data["summary"] = {
            "title": (title or "").strip() or s.title,
            "summary": s.summary,
            "topics": [{"title": t.title, "points": t.points} for t in s.topics],
            "key_points": s.key_points,
            "decisions": s.decisions,
            "action_items": [{"task": a.task, "owner": a.owner, "due": a.due} for a in s.action_items],
        }
        data["status"] = "done"
        _save(out, data)
        log(f"PIPELINE summary done: '{data['summary']['title']}'")
    except InterruptedError:
        data["status"] = "pending_summary"
        _save(out, data)
        _clear_progress()
        log("PIPELINE cancelled by user during summarization")
        return
    except Exception as e:
        data["status"] = "failed"
        data["summary"]["summary"] = f"שגיאת סיכום: {e}"
        _save(out, data)
        log(f"PIPELINE summarize ERROR: {e}")

    # ---- מייל ----
    try:
        from server.notify import send_summary_email
        send_summary_email(data)
        log(f"PIPELINE email sent for '{data['summary']['title']}'")
    except Exception as e:
        log(f"PIPELINE email ERROR (non-fatal): {e}")

    # ---- העלאה ל-Google Drive ----
    try:
        from server.gdrive import is_configured, upload_recording, upload_summary
        if is_configured():
            meeting_title = data["summary"].get("title") or title or ""
            upload_recording(wav_path, meeting_title)
            log(f"PIPELINE Drive: recording uploaded")
            upload_summary(str(out), meeting_title)
            log(f"PIPELINE Drive: summary uploaded")
        else:
            log("PIPELINE Drive: not configured (skipping)")
    except Exception as e:
        log(f"PIPELINE Drive ERROR (non-fatal): {e}")

    # נקה progress file — העיבוד הסתיים
    _clear_progress()
    # ההקלטה נשמרת גם בשרת לצפייה מהאתר


def _save(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
