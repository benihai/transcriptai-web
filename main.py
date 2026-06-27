"""
TranscriptAI Web Server — FastAPI
מחליף את pywebview; מגיש את ממשק ה-HTML דרך HTTP ומספק REST API.

הרצה מקומית (לפיתוח):
    uvicorn main:app --reload --port 8000

על השרת (systemd):
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import sys
import os
import json
import shutil
import asyncio
import threading
import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ---- נתיב לקוד הליבה (src/) ----
# על השרת: /opt/transcriptai
# בפיתוח מקומי: ניתן להגדיר TRANSCRIPTAI_CORE
CORE_PATH = os.getenv("TRANSCRIPTAI_CORE", "/opt/transcriptai")
sys.path.insert(0, CORE_PATH)

from src import config, storage, calendar_sync, meeting_bot, summarize, transcribe, export_format
try:
    from src import export_word as _export_word
except ImportError:
    _export_word = None

# ---- אפליקציה ----
app = FastAPI(title="TranscriptAI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---- SSE event bus ----
_subscribers: list[asyncio.Queue] = []
_event_loop: asyncio.AbstractEventLoop | None = None


def _push(fn: str, data: Any):
    """שולח event לכל הדפדפנים המחוברים דרך SSE (נקרא מ-threads)."""
    if _event_loop is None:
        return
    msg = json.dumps({"fn": fn, "data": data}, ensure_ascii=False)
    async def _send():
        for q in list(_subscribers):
            await q.put(msg)
    asyncio.run_coroutine_threadsafe(_send(), _event_loop)


@app.on_event("startup")
async def _startup():
    global _event_loop
    _event_loop = asyncio.get_event_loop()


@app.get("/api/events")
async def sse_stream():
    """Server-Sent Events — דפדפן מאזין לעדכוני עיבוד (progress, segments, וכו')."""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.append(q)
    async def _gen():
        try:
            yield "data: {\"fn\":\"ping\",\"data\":null}\n\n"
            while True:
                msg = await q.get()
                yield f"data: {msg}\n\n"
        finally:
            _subscribers.remove(q)
    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- ה-API ----
class ServerApi:
    """כל ה-methods שנחשפים ל-JS. זהה ל-gui.py:Api אך ללא pywebview."""

    def __init__(self):
        self._cancel = False

    # יומן
    def get_calendar_config(self):
        return calendar_sync.load_config()

    def save_calendar_config(self, cfg):
        return calendar_sync.save_config(cfg or {})

    def test_calendar(self, cfg=None):
        return calendar_sync.test_connection(cfg or calendar_sync.load_config())

    def calendar_upcoming(self):
        cfg = calendar_sync.load_config()
        if not cfg.get("email") or not cfg.get("app_password"):
            return {"ok": False, "error": "לא הוגדרו אימייל/סיסמה בהגדרות", "events": []}
        try:
            evs = calendar_sync.fetch_upcoming(cfg)
            out = []
            for e in evs:
                url = e.get("url") or ""
                out.append({"title": e.get("title"), "start": e.get("start"), "end": e.get("end"),
                            "url": url, "platform": meeting_bot.detect_platform(url)})
            return {"ok": True, "enabled": bool(cfg.get("enabled")),
                    "bot_name": cfg.get("bot_name") or "TranscriptAI Bot",
                    "use_docker_bot": cfg.get("use_docker_bot", True), "events": out}
        except Exception as e:
            return {"ok": False, "error": str(e), "events": []}

    # פרויקטים
    def list_projects(self):     return storage.list_projects()
    def create_project(self, name): return storage.create_project(name)
    def project_stats(self):     return storage.project_stats()
    def move_meeting(self, meeting_id, from_project, to_project):
        return storage.move_meeting(meeting_id, from_project, to_project)
    def move_recording(self, path, to_project):
        return storage.move_recording(path, to_project)

    # משימות
    def list_my_tasks(self):             return storage.list_my_tasks()
    def add_my_task(self, task):         return storage.add_my_task(task or {})
    def toggle_my_task(self, task_id):   return storage.toggle_my_task(task_id)
    def delete_my_task(self, task_id):   return storage.delete_my_task(task_id)

    # מצב אתחול — Web תמיד במצב "home"
    def get_startup(self):
        return {"mode": "home"}

    # מיקרופונים — לא רלוונטי בשרת
    def list_microphones(self):
        return []

    # הקלטה חיה — לא נתמכת בשרת (הבוט מקליט)
    def start_recording(self, *a, **kw):
        return {"ok": False, "error": "הקלטה ידנית אינה נתמכת בממשק הווב — הבוט מקליט אוטומטית"}
    def pause_recording(self):   return {"ok": False}
    def resume_recording(self):  return {"ok": False}
    def stop_recording(self):    return {"ok": False}

    # העלאת קובץ — נתמכת דרך /api/upload_file
    def upload_media(self, project=None):
        return {"ok": False, "error": "השתמש בכפתור העלאה בממשק"}

    # ביטול עיבוד
    def cancel_processing(self):
        self._cancel = True
        return {"ok": True}

    def process_file(self):
        return {"ok": False, "error": "לא רלוונטי בממשק הווב"}

    def process_recording(self, path, project=None, title=None):
        """מריץ pipeline על קובץ קיים בשרת."""
        self._cancel = False
        project = project or config.DEFAULT_PROJECT

        def on_seg(seg):
            if self._cancel: raise _Cancelled()
            _push("addSegment", {"start": seg.start, "text": seg.text})
            dur = storage.media_duration(path) or 0
            if dur:
                _push("transcribeProgress", {"percent": min(99, round(seg.end / dur * 100))})

        try:
            result = transcribe.transcribe(path, on_progress=on_seg)
        except _Cancelled:
            return {"ok": False, "canceled": True}
        except Exception as e:
            return {"ok": False, "error": f"שגיאת תמלול: {e}"}

        _push("transcribeProgress", {"percent": 100})
        if not result.text.strip():
            return {"ok": False, "error": "לא זוהה דיבור"}

        base_title = (title or "").strip() or "פגישה (ממתין לסיכום)"
        _, summ_dir = config.project_dirs(project)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        meeting_id = f"meeting_{stamp}"
        data = {
            "id": meeting_id, "ok": True, "audio_path": path, "transcript": result.text,
            "project": project, "custom_title": (title or "").strip(), "status": "pending_summary",
            "summary": {"title": base_title, "summary": "", "topics": [],
                        "key_points": [], "decisions": [], "action_items": []},
        }
        out = summ_dir / f"{meeting_id}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        _push("setProcessingStatus", "מסכם עם Gemini...")

        def on_sum(pct):
            if self._cancel: raise _Cancelled()
            _push("summarizeProgress", {"percent": pct})

        try:
            s = summarize.summarize(result.text, on_progress=on_sum)
            data["summary"] = {
                "title": (title or "").strip() or s.title, "summary": s.summary,
                "topics": [{"title": t.title, "points": t.points} for t in s.topics],
                "key_points": s.key_points, "decisions": s.decisions,
                "action_items": [{"task": a.task, "owner": a.owner, "due": a.due} for a in s.action_items],
            }
            data["status"] = "done"
        except _Cancelled:
            data["status"] = "canceled"
        except Exception as e:
            data["status"] = "failed"
            data["summary"]["summary"] = f"⚠️ {e}"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    # סיכומים והקלטות
    def list_meetings(self):     return storage.list_meetings()
    def get_meeting(self, meeting_id, project=None): return storage.load_meeting(meeting_id, project)
    def delete_meeting(self, meeting_id, project=None): return {"ok": storage.delete_meeting(meeting_id, project)}
    def list_recordings(self):   return storage.list_recordings()

    def delete_recording(self, path):
        try:
            p = Path(path)
            if p.exists(): p.unlink()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def play_recording(self, path):
        # בממשק ווב — הפעלה מתבצעת ישירות דרך /api/stream_audio
        return {"ok": True, "web_url": f"/api/stream_audio?path={path}"}

    # ייצוא Word
    def export_word(self, meeting_id):
        if _export_word is None:
            return {"ok": False, "error": "ייצוא Word לא זמין"}
        m = storage.load_meeting(meeting_id)
        if not m: return {"ok": False, "error": "פגישה לא נמצאה"}
        try:
            project = m.get("project") or config.DEFAULT_PROJECT
            _, summ_dir = config.project_dirs(project)
            out = str(summ_dir / f"{meeting_id}.docx")
            _export_word.export(m, out)
            return {"ok": True, "path": out, "download_url": f"/api/download?path={out}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # פורמט DIT
    def extract_format(self, meeting_id):
        m = storage.load_meeting(meeting_id)
        if not m: return {"ok": False, "error": "פגישה לא נמצאה"}
        tr = (m.get("transcript") or "").strip()
        summary = m.get("summary") or {}
        def on_p(pct): _push("formatProgress", {"percent": pct})
        try:
            fields = export_format.extract_format_fields(tr, summary, on_progress=on_p) if tr else {}
        except Exception:
            fields = {}
        from src.gui import _ensure_format_complete
        return {"ok": True, "fields": _ensure_format_complete(fields, m, summary)}

    def preview_format_html(self, fields, project=None):
        try:
            logo = config.project_logo_path(project)
            html = export_format.render_preview_html(fields or {}, client_logo=logo)
            return {"ok": True, "html": html}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_format_pdf(self, fields, project=None):
        try:
            logo = config.project_logo_path(project)
            _, summ_dir = config.project_dirs(project)
            summary = fields.get("topic") or "format"
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            out = str(summ_dir / f"format_{stamp}.pdf")
            export_format.build_pdf(fields or {}, out, client_logo=logo)
            return {"ok": True, "path": out, "download_url": f"/api/download?path={out}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # פרופיל פרויקט
    def get_project_profile(self, project=None): return storage.project_profile(project)
    def save_project_profile(self, project, profile): return storage.save_project_profile(project, profile or {})

    # שינוי שמות
    def rename_meeting(self, meeting_id, project, new_title):
        return storage.rename_meeting(meeting_id, project, new_title)
    def rename_recording(self, path, new_name):
        return storage.rename_recording(path, new_name)
    def rename_project(self, old, new):
        return storage.rename_project(old, new)

    # צ'אט
    def meeting_chat(self, meeting_id, project, message, history=None):
        m = storage.load_meeting(meeting_id, project)
        if not m: return {"ok": False, "error": "פגישה לא נמצאה"}
        tr = (m.get("transcript") or "").strip()
        summary = m.get("summary") or {}
        try:
            r = summarize.chat(tr, summary, history or [], message)
            if r.get("action") == "edit" and r.get("summary"):
                storage.update_summary(meeting_id, project, r["summary"])
            return {"ok": True, **r}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def update_summary(self, meeting_id, project, summary):
        return storage.update_summary(meeting_id, project, summary or {})

    def summarize_meeting(self, meeting_id, project=None):
        self._cancel = False
        m = storage.load_meeting(meeting_id, project)
        if not m: return {"ok": False, "error": "פגישה לא נמצאה"}
        tr = (m.get("transcript") or "").strip()
        if not tr: return {"ok": False, "error": "אין תמלול"}
        project = m.get("project") or config.DEFAULT_PROJECT
        custom = (m.get("custom_title") or "").strip()
        def on_sum(pct):
            _push("summarizeProgress", {"percent": pct})
        try:
            s = summarize.summarize(tr, on_progress=on_sum)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        m["summary"] = {
            "title": custom or s.title, "summary": s.summary,
            "topics": [{"title": t.title, "points": t.points} for t in s.topics],
            "key_points": s.key_points, "decisions": s.decisions,
            "action_items": [{"task": a.task, "owner": a.owner, "due": a.due} for a in s.action_items],
        }
        m["status"] = "done"
        _, summ_dir = config.project_dirs(project)
        out = summ_dir / f"{meeting_id}.json"
        out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        return m

    # לוגו
    def client_logo_status(self, project=None):
        p = config.project_logo_path(project)
        return {"has_logo": p is not None, "path": str(p) if p else None}

    def upload_client_logo(self, project=None):
        return {"ok": False, "error": "השתמש ב-/api/upload_logo"}

    def remove_client_logo(self, project=None):
        for ext in config.LOGO_EXTS:
            p = config.PROJECTS_DIR / config.safe_name(project or config.DEFAULT_PROJECT) / f"client_logo{ext}"
            if p.exists():
                p.unlink()
        return {"ok": True}

    def fetch_logo_from_domain(self, project, domain):
        import urllib.request
        project = project or config.DEFAULT_PROJECT
        base = config.project_base(project)
        for url in [
            f"https://unavatar.io/{domain}",
            f"https://logo.clearbit.com/{domain}",
            f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
        ]:
            try:
                dst = base / "client_logo.png"
                urllib.request.urlretrieve(url, str(dst))
                if dst.stat().st_size > 500:
                    return {"ok": True}
                dst.unlink()
            except Exception:
                continue
        return {"ok": False, "error": "לא נמצא לוגו"}


class _Cancelled(Exception):
    pass


# ---- instance יחיד ----
_api = ServerApi()


# ---- נתיב גנרי לכל קריאות ה-API ----
@app.post("/api/{method_name}")
async def api_call(method_name: str, request: Request):
    """
    נתיב אחד שמטפל בכל ה-API calls.
    JS שולח: POST /api/list_meetings עם body = [arg1, arg2, ...]
    """
    fn = getattr(_api, method_name, None)
    if fn is None or method_name.startswith("_"):
        raise HTTPException(404, f"method '{method_name}' not found")
    try:
        body = await request.body()
        args = json.loads(body) if body else []
        if not isinstance(args, list):
            args = [args]
    except Exception:
        args = []

    # process_recording ומשימות כבדות — רצות ב-thread נפרד
    heavy = {"process_recording", "summarize_meeting", "extract_format", "meeting_chat",
             "export_format_pdf", "export_word", "test_calendar", "fetch_logo_from_domain"}
    if method_name in heavy:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: fn(*args))
    else:
        result = fn(*args)
    return result


# ---- הורדת קבצים ----
@app.get("/api/download")
async def download_file(path: str):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), filename=p.name)


@app.get("/api/stream_audio")
async def stream_audio(path: str):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="audio/webm")


# ---- העלאת קובץ מדיה ----
from fastapi import UploadFile, File, Form

@app.post("/api/upload_file")
async def upload_file(file: UploadFile = File(...), project: str = Form(default="")):
    project = project or config.DEFAULT_PROJECT
    rec_dir, _ = config.project_dirs(project)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = Path(file.filename or "").suffix.lower() or ".webm"
    dst = rec_dir / f"meeting_{stamp}{ext}"
    with open(dst, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "path": str(dst), "project": project}


# ---- העלאת לוגו ----
@app.post("/api/upload_logo")
async def upload_logo(file: UploadFile = File(...), project: str = Form(default="")):
    project = project or config.DEFAULT_PROJECT
    base = config.project_base(project)
    ext = Path(file.filename or "").suffix.lower() or ".png"
    for old_ext in config.LOGO_EXTS:
        old = base / f"client_logo{old_ext}"
        if old.exists(): old.unlink()
    dst = base / f"client_logo{ext}"
    with open(dst, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True}


# ---- דף הבית ----
@app.get("/")
async def root():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)
