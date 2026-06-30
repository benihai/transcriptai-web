"""אפליקציית דסקטופ (pywebview) - ממשק מלא: הקלטה, סיכומים, הקלטות, ייצוא Word.

הרצה:
    python -m src.gui                     # מצב רגיל (ספרייה + הקלטה)
    python -m src.gui --process <wav>     # עבד על הקלטה קיימת והצג תוצאה
"""
import os
import json
import argparse
import threading
import datetime as dt
from pathlib import Path

import webview

from . import config, record, transcribe, summarize, storage, export_word, export_format, calendar_sync, meeting_bot

WEB_DIR = Path(__file__).resolve().parent / "web"


class _Cancelled(Exception):
    """מסומן כשהמשתמש מבטל עיבוד באמצע."""


def _ensure_format_complete(fields: dict | None, meeting: dict, summary: dict) -> dict:
    """מבטיח שאובייקט שדות הפורמט שלם. ממלא חוסרים מתוך הסיכום והמטא-דאטה.

    כך 'העברת סיכום לפורמט' תמיד עובדת - גם אם ה-AI נכשל או החזיר שדות ריקים.
    """
    f = dict(fields or {})
    f.setdefault("participants", [])
    f.setdefault("findings", [])

    # שדות כותרת - השלמה ממטא-דאטה של הפגישה אם ריקים
    if not (f.get("project_name") or "").strip():
        f["project_name"] = meeting.get("project") or ""
    if not (f.get("topic") or "").strip():
        f["topic"] = summary.get("title") or meeting.get("title") or ""
    if not (f.get("date") or "").strip():
        # data["date"] בפורמט "DD/MM/YYYY · HH:MM" -> לוקחים את חלק התאריך
        f["date"] = (meeting.get("date") or "").split("·")[0].strip()
    f.setdefault("location", f.get("location") or "")

    # אם אין ממצאים - בונים אותם ישירות מהסיכום (נושאים, משימות, החלטות, נקודות מפתח)
    if not f["findings"]:
        findings = []
        for t in summary.get("topics") or []:
            note = t.get("title", "")
            for pt in t.get("points") or []:
                findings.append({"description": pt, "responsible": "", "due": "", "note": note})
        for a in summary.get("action_items") or []:
            findings.append({
                "description": a.get("task", ""),
                "responsible": a.get("owner", ""),
                "due": a.get("due", ""),
                "note": "משימה",
            })
        for d in summary.get("decisions") or []:
            findings.append({"description": d, "responsible": "", "due": "", "note": "החלטה"})
        # נקודות מפתח רק אם אין נושאים מפורטים (כדי לא לכפול)
        if not (summary.get("topics")):
            for k in summary.get("key_points") or []:
                findings.append({"description": k, "responsible": "", "due": "", "note": ""})
        f["findings"] = findings

    return f


class Api:
    """ה-API שנחשף ל-JavaScript. רק מתודות ציבוריות (ללא קו תחתון) נחשפות."""

    def __init__(self, process_path: str | None = None, process_project: str | None = None,
                 process_title: str | None = None):
        self._window = None
        self._recorder = None
        self._process_path = process_path
        self._process_project = process_project or config.DEFAULT_PROJECT
        self._process_title = process_title
        self._project = config.DEFAULT_PROJECT
        self._cancel = False

    # --- יומן (הצטרפות אוטומטית) ---
    def get_calendar_config(self):
        return calendar_sync.load_config()

    def save_calendar_config(self, cfg):
        return calendar_sync.save_config(cfg or {})

    def test_calendar(self, cfg=None):
        return calendar_sync.test_connection(cfg or calendar_sync.load_config())

    def calendar_upcoming(self):
        """פגישות קרובות מהיומן של הבוט (להצגה בלשונית היומן)."""
        cfg = calendar_sync.load_config()
        if not cfg.get("email") or not cfg.get("app_password"):
            return {"ok": False, "error": "לא הוגדרו אימייל/סיסמה בהגדרות", "events": []}
        try:
            evs = calendar_sync.fetch_upcoming(cfg)
            handled = calendar_sync.load_handled()
            out = []
            for e in evs:
                url = e.get("url") or ""
                out.append({
                    "title": e.get("title"), "start": e.get("start"), "end": e.get("end"),
                    "url": url, "platform": meeting_bot.detect_platform(url),
                })
            return {"ok": True, "enabled": bool(cfg.get("enabled")),
                    "bot_name": cfg.get("bot_name") or "Synthia Notetaker",
                    "use_docker_bot": cfg.get("use_docker_bot", True), "events": out}
        except Exception as e:
            return {"ok": False, "error": str(e), "events": []}

    # --- פרויקטים ---
    def list_projects(self):
        return storage.list_projects()

    def create_project(self, name):
        return storage.create_project(name)

    def delete_project(self, name):
        return storage.delete_project(name)

    def project_stats(self):
        return storage.project_stats()

    def move_meeting(self, meeting_id, from_project, to_project):
        return storage.move_meeting(meeting_id, from_project, to_project)

    def move_recording(self, path, to_project):
        return storage.move_recording(path, to_project)

    # --- המשימות שלי ---
    def list_my_tasks(self):
        return storage.list_my_tasks()

    def add_my_task(self, task):
        return storage.add_my_task(task or {})

    def toggle_my_task(self, task_id):
        return storage.toggle_my_task(task_id)

    def delete_my_task(self, task_id):
        return storage.delete_my_task(task_id)

    # --- מצב הפעלה + התקנים ---
    def get_startup(self):
        return {"mode": "process" if self._process_path else "home"}

    def list_microphones(self):
        return record.list_input_devices()

    # --- הקלטה חיה ---
    def start_recording(self, project=None, mic_name=None, mute_mic=False, capture_video=False):
        try:
            self._project = project or config.DEFAULT_PROJECT
            # קול המערכת נקלט תמיד; המיקרופון נקלט אלא אם המשתמש השתיק אותו
            self._recorder = record.Recorder(
                capture_mic=not bool(mute_mic), capture_system=True,
                mic_name=mic_name or None, capture_video=bool(capture_video),
            )
            self._recorder.start()
            return {"ok": True}
        except Exception as e:
            import traceback; traceback.print_exc()
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def pause_recording(self):
        if self._recorder:
            self._recorder.pause()
        return {"ok": True, "paused": True}

    def resume_recording(self):
        if self._recorder:
            self._recorder.resume()
        return {"ok": True, "paused": False}

    def stop_recording(self):
        """עוצר את ההקלטה ומחזיר נתיב הקובץ (לפני שמבקשים שם ומעבדים)."""
        try:
            rec_dir, _ = config.project_dirs(self._project)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = self._recorder.output_ext()
            out = str(rec_dir / f"meeting_{stamp}{ext}")
            path = self._recorder.stop(out)
            return {"ok": True, "path": path, "project": self._project}
        except Exception as e:
            return {"ok": False, "error": f"שגיאת הקלטה: {e}"}

    # --- העלאת קובץ ידנית (זום / וידאו / אודיו) ---
    def upload_media(self, project=None):
        """בוחר קובץ מדיה מהמחשב, מעתיק אותו לתיקיית ההקלטות של הפרויקט ומחזיר נתיב."""
        project = project or config.DEFAULT_PROJECT
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=(
                    "קבצי וידאו ואודיו (*.mp4;*.m4a;*.mov;*.mkv;*.webm;*.wav;*.mp3;*.aac)",
                    "כל הקבצים (*.*)",
                ),
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not result:
            return {"ok": False, "canceled": True}
        src = result if isinstance(result, str) else result[0]
        try:
            import shutil
            rec_dir, _ = config.project_dirs(project)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = Path(src).suffix.lower() or ".mp4"
            dst = rec_dir / f"meeting_{stamp}{ext}"
            shutil.copy2(src, dst)
            return {"ok": True, "path": str(dst), "project": project}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- ביטול עיבוד ---
    def cancel_processing(self):
        self._cancel = True
        return {"ok": True}

    # --- עיבוד ---
    def process_file(self):
        return self._run_async(self._process_path, self._process_project, self._process_title)

    def process_recording(self, path, project=None, title=None):
        return self._run_async(path, project or config.DEFAULT_PROJECT, title)

    def _run_async(self, wav_path, project, title):
        """מריץ את העיבוד ב-thread שאינו daemon, כך שימשיך גם אם החלון נסגר."""
        box = {}
        t = threading.Thread(target=lambda: box.update(r=self._run_pipeline(wav_path, project, title)),
                             daemon=False)
        t.start()
        t.join()
        return box.get("r")

    # --- ספריית סיכומים והקלטות ---
    def list_meetings(self):
        return storage.list_meetings()

    def get_meeting(self, meeting_id, project=None):
        return storage.load_meeting(meeting_id, project)

    def delete_meeting(self, meeting_id, project=None):
        return {"ok": storage.delete_meeting(meeting_id, project)}

    def list_recordings(self):
        return storage.list_recordings()

    def delete_recording(self, path):
        try:
            p = Path(path)
            if p.exists():
                p.unlink()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def play_recording(self, path):
        try:
            os.startfile(path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- ייצוא ל-Word ---
    def export_word(self, meeting_id):
        meeting = storage.load_meeting(meeting_id)
        if not meeting:
            return {"ok": False, "error": "הסיכום לא נמצא"}
        title = meeting.get("summary", {}).get("title", "סיכום פגישה")
        safe = "".join(c for c in title if c not in '\\/:*?"<>|').strip() or "סיכום פגישה"
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=f"{safe}.docx",
            file_types=("Word Document (*.docx)",),
        )
        if not result:
            return {"ok": False, "canceled": True}
        path = result if isinstance(result, str) else result[0]
        if not path.lower().endswith(".docx"):
            path += ".docx"
        try:
            export_word.build_docx(meeting, path)
            os.startfile(path)
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- פורמט מעוצב (DIT) ---
    def extract_format(self, meeting_id):
        """ממלא את שדות הפורמט מפגישה קיימת. רץ ב-thread ומשדר אחוזי התקדמות
        (formatProgress) כך שהמשתמש רואה מד התקדמות עד שהפורמט נוצר."""
        box = {}
        t = threading.Thread(target=lambda: box.update(r=self._extract_format(meeting_id)),
                             daemon=False)
        t.start(); t.join()
        return box.get("r")

    def _extract_format(self, meeting_id):
        m = storage.load_meeting(meeting_id)
        if not m:
            return {"ok": False, "error": "הסיכום לא נמצא"}
        tr = (m.get("transcript") or "").strip()
        summary = m.get("summary") or {}

        def on_p(pct):
            self._push("formatProgress", {"percent": pct})

        on_p(3)
        fields = None
        if tr:
            try:
                fields = summarize.extract_format_fields(tr, summary, on_progress=on_p)
            except Exception as e:
                print(f"extract_format_fields failed, using summary fallback: {e}")
        # תמיד מבטיחים תוצאה שלמה ע"י השלמה/מיפוי מהסיכום והמטא-דאטה של הפגישה
        fields = _ensure_format_complete(fields, m, summary)
        on_p(100)
        return {"ok": True, "fields": fields, "project": m.get("project")}

    # --- פרופיל פרויקט + שינוי שם + צ'אט ---
    def get_project_profile(self, project=None):
        return storage.project_profile(project)

    def save_project_profile(self, project, profile):
        return storage.save_project_profile(project, profile or {})

    def rename_meeting(self, meeting_id, project, new_title):
        return storage.rename_meeting(meeting_id, project, new_title)

    def rename_recording(self, path, new_name):
        return storage.rename_recording(path, new_name)

    def rename_project(self, old, new):
        return storage.rename_project(old, new)

    def meeting_chat(self, meeting_id, project, message, history=None):
        """צ'אט מעוגן בפגישה. אם המשתמש מבקש שינוי - מעדכן את הסיכום המקורי ושומר."""
        m = storage.load_meeting(meeting_id, project)
        if not m:
            return {"ok": False, "error": "הפגישה לא נמצאה"}
        try:
            res = summarize.chat(m.get("transcript") or "", m.get("summary") or {},
                                 history or [], message or "")
        except Exception as e:
            return {"ok": False, "error": str(e)}
        out = {"ok": True, "reply": res.get("reply", "")}
        if res.get("action") == "edit" and res.get("summary"):
            storage.update_summary(meeting_id, m.get("project"), res["summary"])
            out["summary"] = res["summary"]          # ה-UI יעדכן את לשונית הסיכום
            out["edited"] = True
        return out

    def update_summary(self, meeting_id, project, summary):
        """שמירת עריכה ידנית של הסיכום מלשונית הסיכום."""
        return storage.update_summary(meeting_id, project, summary or {})

    # --- אחזור לוגו אוטומטי מהאינטרנט לפי דומיין ---
    def fetch_logo_from_domain(self, project, domain):
        import urllib.request, shutil as _sh
        d = (domain or "").strip().lower()
        for pre in ("https://", "http://", "www."):
            if d.startswith(pre):
                d = d[len(pre):]
        d = d.split("/")[0].split("@")[-1].strip()
        if not d or "." not in d:
            return {"ok": False, "error": "דומיין לא תקין (למשל: apple.com)"}
        # מקורות לוגו לפי איכות יורדת (unavatar מאחד clearbit/favicon/logo.dev וכו')
        sources = [
            f"https://unavatar.io/{d}?fallback=false",
            f"https://logo.clearbit.com/{d}?size=256",
            f"https://www.google.com/s2/favicons?domain={d}&sz=256",
        ]
        for url in sources:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    data = r.read()
                if data and len(data) > 400:
                    base = config.project_base(project)
                    for e in config.LOGO_EXTS:
                        old = base / f"client_logo{e}"
                        if old.exists():
                            old.unlink()
                    (base / "client_logo.png").write_bytes(data)
                    return {"ok": True, "exists": True}
            except Exception:
                continue
        return {"ok": False, "error": "לא נמצא לוגו לדומיין זה"}

    # --- תצוגה מקדימה של הפורמט (HTML) לפני הורדת PDF ---
    def preview_format_html(self, fields, project=None):
        try:
            logo = config.project_logo_path(project)
            html = export_format.render_preview_html(fields, str(logo) if logo else None)
            return {"ok": True, "html": html}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_format_pdf(self, fields, project=None):
        # שם הקובץ: נושא הפגישה - שם הפרויקט - תאריך
        parts = [fields.get("topic"), fields.get("project_name"), fields.get("date")]
        name = " - ".join(p.strip() for p in parts if p and p.strip()) or "סיכום פגישה"
        safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "סיכום פגישה"
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=f"{safe}.pdf",
            file_types=("PDF (*.pdf)",))
        if not result:
            return {"ok": False, "canceled": True}
        path = result if isinstance(result, str) else result[0]
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            logo = config.project_logo_path(project)
            export_format.build_pdf(fields, path, client_logo=str(logo) if logo else None)
            os.startfile(path)
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- לוגו לקוח לפי פרויקט ---
    def client_logo_status(self, project=None):
        p = config.project_logo_path(project)
        return {"exists": bool(p), "name": p.name if p else ""}

    def upload_client_logo(self, project=None):
        """בוחר תמונת לוגו ומעתיק אותה לתיקיית הפרויקט בשם client_logo.<ext>."""
        project = project or config.DEFAULT_PROJECT
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("תמונות (*.png;*.jpg;*.jpeg;*.webp)", "כל הקבצים (*.*)"))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not result:
            return {"ok": False, "canceled": True}
        src = result if isinstance(result, str) else result[0]
        ext = Path(src).suffix.lower()
        if ext not in config.LOGO_EXTS:
            return {"ok": False, "error": "פורמט תמונה לא נתמך (PNG/JPG/WEBP)"}
        try:
            import shutil
            base = config.project_base(project)
            # מסירים לוגו קודם בכל סיומת כדי שיישאר רק אחד
            for e in config.LOGO_EXTS:
                old = base / f"client_logo{e}"
                if old.exists():
                    old.unlink()
            shutil.copy2(src, base / f"client_logo{ext}")
            return {"ok": True, "exists": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def remove_client_logo(self, project=None):
        base = config.project_base(project)
        removed = False
        for e in config.LOGO_EXTS:
            old = base / f"client_logo{e}"
            if old.exists():
                old.unlink(); removed = True
        return {"ok": True, "removed": removed}

    # --- ליבה משותפת ---
    def _run_pipeline(self, wav_path, project=None, title=None):
        project = project or config.DEFAULT_PROJECT
        self._cancel = False
        duration = storage.media_duration(wav_path) or 0

        def on_seg(seg):
            if self._cancel:
                raise _Cancelled()
            self._push("addSegment", {"start": seg.start, "text": seg.text})
            if duration:
                pct = min(99, round(seg.end / duration * 100))
                self._push("transcribeProgress", {"percent": pct})

        try:
            result = transcribe.transcribe(wav_path, on_progress=on_seg)
        except _Cancelled:
            return {"ok": False, "canceled": True}
        except Exception as e:
            return {"ok": False, "error": f"שגיאת תמלול: {e}"}

        self._push("transcribeProgress", {"percent": 100})
        if not result.text.strip():
            return {"ok": False, "error": "לא זוהה דיבור בהקלטה."}

        # שם הפגישה: מה שהמשתמש נתן, אחרת ברירת מחדל זמנית עד שהסיכום ייצר כותרת
        base_title = (title or "").strip() or "פגישה (ממתין לסיכום)"
        data = {
            "ok": True, "audio_path": wav_path, "transcript": result.text,
            "project": project, "custom_title": (title or "").strip(),
            "status": "pending_summary",   # תומלל אך טרם סוכם
            "summary": {"title": base_title, "summary": "",
                        "key_points": [], "decisions": [], "action_items": []},
        }
        data["id"] = self._save(data, project)

        self._push("setProcessingStatus", "מסכם עם Gemini...")

        def on_sum(pct):
            if self._cancel:
                raise _Cancelled()
            self._push("summarizeProgress", {"percent": pct})

        try:
            s = summarize.summarize(result.text, on_progress=on_sum)
            data["summary"] = {
                # אם המשתמש נתן שם - הוא גובר על הכותרת ש-Gemini הציע
                "title": (title or "").strip() or s.title,
                "summary": s.summary,
                "topics": [{"title": t.title, "points": t.points} for t in s.topics],
                "key_points": s.key_points, "decisions": s.decisions,
                "action_items": [{"task": a.task, "owner": a.owner, "due": a.due} for a in s.action_items],
            }
            data["status"] = "done"
            self._save_existing(data)
        except _Cancelled:
            # התמלול כבר נשמר; מסמנים שהסיכום בוטל (יישאר ב'תמלולים' לסיכום חוזר)
            data["status"] = "canceled"
            data["summary"]["title"] = base_title if title else "פגישה (הסיכום בוטל)"
            data["summary"]["summary"] = "הסיכום בוטל על ידי המשתמש. התמלול נשמר."
            self._save_existing(data)
            return {"ok": False, "canceled": True, "id": data["id"], "project": project, "transcript": result.text}
        except RuntimeError as e:
            data["status"] = "failed"
            data["summary"]["title"] = base_title if title else "פגישה (הסיכום נכשל - נסה שוב)"
            data["summary"]["summary"] = f"⚠️ {e}"
            self._save_existing(data)
            data["summary_error"] = str(e)
        return data

    # --- סיכום חוזר על תמלול קיים (ללא תמלול מחדש) ---
    def summarize_meeting(self, meeting_id, project=None):
        """מריץ רק את שלב הסיכום על תמלול שכבר נשמר. לשימוש מתפריט 'תמלולים'."""
        box = {}
        t = threading.Thread(
            target=lambda: box.update(r=self._summarize_existing(meeting_id, project)),
            daemon=False)
        t.start(); t.join()
        return box.get("r")

    def _summarize_existing(self, meeting_id, project=None):
        self._cancel = False
        m = storage.load_meeting(meeting_id, project)
        if not m:
            return {"ok": False, "error": "הפגישה לא נמצאה"}
        tr = (m.get("transcript") or "").strip()
        if not tr:
            return {"ok": False, "error": "אין תמלול לפגישה זו"}
        project = m.get("project") or config.DEFAULT_PROJECT
        custom = (m.get("custom_title") or "").strip()

        self._push("setProcessingStatus", "מסכם עם Gemini...")

        def on_sum(pct):
            if self._cancel:
                raise _Cancelled()
            self._push("summarizeProgress", {"percent": pct})

        try:
            s = summarize.summarize(tr, on_progress=on_sum)
        except _Cancelled:
            return {"ok": False, "canceled": True, "id": meeting_id, "project": project}
        except RuntimeError as e:
            m["status"] = "failed"
            m["summary"]["title"] = custom or "פגישה (הסיכום נכשל - נסה שוב)"
            m["summary"]["summary"] = f"⚠️ {e}"
            self._save_existing(m)
            return {"ok": False, "error": str(e), "id": meeting_id, "project": project}

        m["summary"] = {
            "title": custom or s.title,
            "summary": s.summary,
            "topics": [{"title": t.title, "points": t.points} for t in s.topics],
            "key_points": s.key_points, "decisions": s.decisions,
            "action_items": [{"task": a.task, "owner": a.owner, "due": a.due} for a in s.action_items],
        }
        m["status"] = "done"
        self._save_existing(m)
        return m

    # --- עזר ---
    def _push(self, js_fn, payload):
        # אם החלון נסגר (עיבוד ממשיך ברקע) - מתעלמים בשקט מעדכוני ה-UI
        try:
            if self._window:
                self._window.evaluate_js(f"window.{js_fn}({json.dumps(payload, ensure_ascii=True)})")
        except Exception:
            pass

    def _save(self, data, project=None):
        project = project or data.get("project") or config.DEFAULT_PROJECT
        _, summ_dir = config.project_dirs(project)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        meeting_id = f"meeting_{stamp}"
        out = summ_dir / f"{meeting_id}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return meeting_id

    def _save_existing(self, data):
        _, summ_dir = config.project_dirs(data.get("project"))
        out = summ_dir / f"{data['id']}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process", help="נתיב לקובץ WAV לעיבוד מיידי")
    parser.add_argument("--project", help="הפרויקט שאליו לשייך את הסיכום")
    parser.add_argument("--title", help="שם הפגישה (למשל מכותרת אירוע ביומן)")
    args = parser.parse_args()

    config.ensure_ffmpeg_on_path()
    api = Api(process_path=args.process, process_project=args.project, process_title=args.title)
    window = webview.create_window(
        "מערכת סיכום פגישות",
        str(WEB_DIR / "index.html"),
        js_api=api,
        width=1180, height=800, min_size=(900, 620),
        background_color="#F3F6FB",
    )
    api._window = window
    # שים לב: אין os._exit בסגירה - כך שתמלול/סיכום שרצים ב-thread שאינו daemon
    # ממשיכים עד הסוף (ונשמרים) גם אחרי סגירת החלון.
    webview.start()


if __name__ == "__main__":
    main()
