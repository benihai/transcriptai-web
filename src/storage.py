"""קריאה וניהול של פרויקטים, סיכומים שמורים והקלטות."""
from __future__ import annotations
import os
import json
import wave
import uuid
import base64
import shutil
import subprocess
import datetime as dt
from pathlib import Path

from . import config

# סוגי קבצי מדיה שמוצגים ברשימת ההקלטות (אודיו + וידאו)
MEDIA_EXTS = {".wav", ".mp4", ".m4a", ".mp3", ".mov", ".mkv", ".webm", ".aac"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MY_TASKS_FILE = config.ROOT / "my_tasks.json"


def _parse_stamp(stem: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(stem.replace("meeting_", ""), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _fmt_date(d: dt.datetime | None) -> str:
    return d.strftime("%d/%m/%Y · %H:%M") if d else ""


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def media_duration(path) -> float:
    """משך קובץ מדיה בשניות (WAV / MP4 / וכו'). משתמש ב-ffprobe לקבצים שאינם WAV."""
    p = Path(path)
    if p.suffix.lower() == ".wav":
        d = _wav_duration(p)
        if d:
            return d
    config.ensure_ffmpeg_on_path()
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(p)],
            capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=30,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


# ---------- פרויקטים ----------
def list_projects() -> list[str]:
    """רשימת שמות פרויקטים: 'כללי' תמיד ראשון, השאר לפי א-ב."""
    others = sorted(p.name for p in config.PROJECTS_DIR.iterdir() if p.is_dir())
    return [config.DEFAULT_PROJECT] + others


def create_project(name: str) -> dict:
    name = config.safe_name(name)
    if not name:
        return {"ok": False, "error": "שם פרויקט לא תקין"}
    if name == config.DEFAULT_PROJECT or (config.PROJECTS_DIR / name).exists():
        return {"ok": False, "error": "פרויקט בשם זה כבר קיים"}
    config.project_dirs(name)  # יוצר את התיקיות
    return {"ok": True, "name": name}


def delete_project(name: str) -> dict:
    """מוחק פרויקט וכל תוכנו (הקלטות, סיכומים, לוגו). אסור למחוק 'כללי'."""
    if not name or name == config.DEFAULT_PROJECT:
        return {"ok": False, "error": "לא ניתן למחוק את הפרויקט הברירת מחדל"}
    base = config.PROJECTS_DIR / config.safe_name(name)
    if not base.exists():
        return {"ok": False, "error": "פרויקט לא נמצא"}
    try:
        shutil.rmtree(str(base))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def logo_datauri(project: str | None) -> str:
    """data-URI של לוגו הלקוח של הפרויקט (להצגה ב-UI), או מחרוזת ריקה."""
    p = config.project_logo_path(project)
    if not p:
        return ""
    ext = p.suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/webp" if ext == ".webp" else "image/png"
    try:
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    except Exception:
        return ""


def project_stats() -> list[dict]:
    """רשימת פרויקטים עם ספירת סיכומים והקלטות + לוגו (אם קיים)."""
    out = []
    for p in list_projects():
        rec_dir, summ_dir = config.project_dirs(p)
        out.append({
            "name": p,
            "n_meetings": len(list(summ_dir.glob("meeting_*.json"))),
            "n_recordings": len(list(rec_dir.glob("*.wav"))),
            "is_default": p == config.DEFAULT_PROJECT,
            "logo": logo_datauri(p),
        })
    return out


# ---------- העברה בין פרויקטים ----------
def move_meeting(meeting_id: str, from_project: str, to_project: str) -> dict:
    _, src_dir = config.project_dirs(from_project)
    _, dst_dir = config.project_dirs(to_project)
    f = src_dir / f"{meeting_id}.json"
    if not f.exists():
        return {"ok": False, "error": "הסיכום לא נמצא"}
    if from_project == to_project:
        return {"ok": True}
    data = json.loads(f.read_text(encoding="utf-8"))
    data["project"] = to_project
    (dst_dir / f"{meeting_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    f.unlink()
    return {"ok": True}


def move_recording(path: str, to_project: str) -> dict:
    src = Path(path)
    if not src.exists():
        return {"ok": False, "error": "ההקלטה לא נמצאה"}
    rec_dir, _ = config.project_dirs(to_project)
    target = rec_dir / src.name
    if target.resolve() == src.resolve():
        return {"ok": True, "path": str(target)}
    shutil.move(str(src), str(target))
    return {"ok": True, "path": str(target)}


def is_summarized(data: dict) -> bool:
    """האם לפגישה יש סיכום אמיתי (להבדיל מתומללה-בלבד / נכשלה / בוטלה)."""
    st = data.get("status")
    if st:
        return st == "done"
    # תאימות לאחור לקבצים ישנים ללא שדה status - מסיקים מתוכן הסיכום
    txt = ((data.get("summary") or {}).get("summary") or "").strip()
    return bool(txt) and not txt.startswith("⚠") and "בוטל" not in txt


# ---------- סיכומים ----------
def _meeting_item(f: Path, project: str) -> dict | None:
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    s = data.get("summary", {})
    when = _parse_stamp(f.stem)
    return {
        "id": f.stem,
        "project": project,
        "title": s.get("title") or "פגישה ללא כותרת",
        "date": _fmt_date(when),
        "sort": when.timestamp() if when else f.stat().st_mtime,
        "preview": (s.get("summary") or "")[:160],
        "n_points": len(s.get("key_points", [])),
        "n_tasks": len(s.get("action_items", [])),
        "has_audio": bool(data.get("audio_path") and Path(data["audio_path"]).exists()),
        "summarized": is_summarized(data),
        "status": data.get("status") or ("done" if is_summarized(data) else "pending_summary"),
        "tr_chars": len(data.get("transcript") or ""),
    }


def list_meetings(project: str | None = None) -> list[dict]:
    """סיכומים מפרויקט מסוים, או מכל הפרויקטים (project=None)."""
    projects = [project] if project else list_projects()
    items = []
    for proj in projects:
        _, summ_dir = config.project_dirs(proj)
        for f in summ_dir.glob("meeting_*.json"):
            it = _meeting_item(f, proj)
            if it:
                items.append(it)
    items.sort(key=lambda x: x["sort"], reverse=True)
    return items


def load_meeting(meeting_id: str, project: str | None = None) -> dict | None:
    projects = [project] if project else list_projects()
    for proj in projects:
        _, summ_dir = config.project_dirs(proj)
        f = summ_dir / f"{meeting_id}.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            data["id"] = meeting_id
            data["project"] = proj
            data["date"] = _fmt_date(_parse_stamp(meeting_id))
            return data
    return None


def delete_meeting(meeting_id: str, project: str | None = None) -> bool:
    projects = [project] if project else list_projects()
    for proj in projects:
        _, summ_dir = config.project_dirs(proj)
        f = summ_dir / f"{meeting_id}.json"
        if f.exists():
            f.unlink()
            return True
    return False


# ---------- הקלטות ----------
def list_recordings(project: str | None = None) -> list[dict]:
    projects = [project] if project else list_projects()
    items = []
    for proj in projects:
        rec_dir, _ = config.project_dirs(proj)
        for f in rec_dir.iterdir():
            if not f.is_file() or f.suffix.lower() not in MEDIA_EXTS:
                continue
            when = _parse_stamp(f.stem)
            dur = media_duration(f)
            items.append({
                "name": f.name,
                "path": str(f),
                "project": proj,
                "is_video": f.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"},
                "date": _fmt_date(when) if when else "",
                "sort": f.stat().st_mtime,
                "duration": f"{int(dur // 60):02d}:{int(dur % 60):02d}",
                "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            })
    items.sort(key=lambda x: x["sort"], reverse=True)
    return items


# ---------- המשימות שלי ----------
def list_my_tasks() -> list[dict]:
    if MY_TASKS_FILE.exists():
        try:
            return json.loads(MY_TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_my_tasks(tasks: list[dict]) -> None:
    MY_TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def add_my_task(task: dict) -> dict:
    """מוסיף משימה ל'המשימות שלי'. מונע כפילות של אותה משימה מאותו מקור."""
    tasks = list_my_tasks()
    text = (task.get("task") or "").strip()
    if not text:
        return {"ok": False, "error": "משימה ריקה"}
    src = task.get("source_id") or ""
    for t in tasks:
        if t.get("task") == text and t.get("source_id") == src:
            return {"ok": True, "id": t["id"], "duplicate": True}
    tid = uuid.uuid4().hex[:8]
    tasks.insert(0, {
        "id": tid,
        "task": text,
        "owner": (task.get("owner") or "").strip(),
        "due": (task.get("due") or "").strip(),
        "done": False,
        "source_id": src,
        "source_title": (task.get("source_title") or "").strip(),
        "project": (task.get("project") or "").strip(),
        "added_at": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
    })
    _save_my_tasks(tasks)
    return {"ok": True, "id": tid}


def toggle_my_task(task_id: str) -> dict:
    tasks = list_my_tasks()
    for t in tasks:
        if t.get("id") == task_id:
            t["done"] = not t.get("done")
            _save_my_tasks(tasks)
            return {"ok": True, "done": t["done"]}
    return {"ok": False, "error": "המשימה לא נמצאה"}


def delete_my_task(task_id: str) -> dict:
    tasks = list_my_tasks()
    new = [t for t in tasks if t.get("id") != task_id]
    _save_my_tasks(new)
    return {"ok": True}


# ---------- פרופיל פרויקט ----------
PROFILE_FIELDS = ("client_name", "contact", "phone", "email", "address", "notes")


def project_profile(project: str | None) -> dict:
    """מחזיר את פרטי הפרופיל של הפרויקט (מתוך profile.json), כולל האם יש לוגו."""
    base = config.project_base(project)
    data = {}
    f = base / "profile.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    out = {k: data.get(k, "") for k in PROFILE_FIELDS}
    out["name"] = project or config.DEFAULT_PROJECT
    logo = config.project_logo_path(project)
    out["has_logo"] = bool(logo)
    return out


def save_project_profile(project: str | None, profile: dict) -> dict:
    base = config.project_base(project)
    data = {k: (profile.get(k) or "").strip() for k in PROFILE_FIELDS}
    (base / "profile.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ---------- שינוי שם ----------
def rename_meeting(meeting_id: str, project: str | None, new_title: str) -> dict:
    """משנה את כותרת הסיכום (וגם custom_title כדי שלא ידרס בסיכום חוזר)."""
    title = (new_title or "").strip()
    if not title:
        return {"ok": False, "error": "שם ריק"}
    _, summ_dir = config.project_dirs(project)
    f = summ_dir / f"{meeting_id}.json"
    if not f.exists():
        return {"ok": False, "error": "הפגישה לא נמצאה"}
    data = json.loads(f.read_text(encoding="utf-8"))
    data.setdefault("summary", {})["title"] = title
    data["custom_title"] = title
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "title": title}


def _nd(s):
    text = str(s or "")
    for ch in ("—", "–", "―", "‒", "−"):
        text = text.replace(ch, "-")
    return text


def update_summary(meeting_id: str, project: str | None, summary: dict) -> dict:
    """שומר סיכום מעודכן (עריכה ידנית או עריכה ע"י הצ'אט) לתוך קובץ הפגישה."""
    _, summ_dir = config.project_dirs(project)
    f = summ_dir / f"{meeting_id}.json"
    if not f.exists():
        return {"ok": False, "error": "הפגישה לא נמצאה"}
    data = json.loads(f.read_text(encoding="utf-8"))
    s = dict(summary or {})
    # נורמליזציית מקפים בכל שדות הטקסט
    s["title"] = _nd(s.get("title", "") or data.get("summary", {}).get("title", ""))
    s["summary"] = _nd(s.get("summary", ""))
    s["topics"] = [{"title": _nd(t.get("title", "")), "points": [_nd(p) for p in (t.get("points") or [])]}
                   for t in s.get("topics", []) or []]
    s["key_points"] = [_nd(x) for x in s.get("key_points", []) or []]
    s["decisions"] = [_nd(x) for x in s.get("decisions", []) or []]
    s["action_items"] = [{"task": _nd(a.get("task", "")), "owner": _nd(a.get("owner", "")), "due": _nd(a.get("due", ""))}
                         for a in s.get("action_items", []) or []]
    data["summary"] = s
    data["status"] = "done"
    if s.get("title"):
        data["custom_title"] = s["title"]
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "summary": s}


def rename_recording(path: str, new_name: str) -> dict:
    """משנה את שם קובץ ההקלטה ומעדכן הפניות audio_path בסיכומים."""
    src = Path(path)
    if not src.exists():
        return {"ok": False, "error": "ההקלטה לא נמצאה"}
    clean = config.safe_name(new_name)
    if not clean:
        return {"ok": False, "error": "שם לא תקין"}
    # שמירה על הסיומת המקורית
    if Path(clean).suffix.lower() != src.suffix.lower():
        clean = clean + src.suffix
    dst = src.with_name(clean)
    if dst.exists():
        return {"ok": False, "error": "כבר קיים קובץ בשם זה"}
    src.rename(dst)
    # עדכון audio_path בכל סיכום שמצביע על הקובץ הישן
    for proj in list_projects():
        _, summ_dir = config.project_dirs(proj)
        for jf in summ_dir.glob("meeting_*.json"):
            try:
                d = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("audio_path") and Path(d["audio_path"]) == src:
                d["audio_path"] = str(dst)
                jf.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(dst), "name": dst.name}


def rename_project(old: str, new: str) -> dict:
    """משנה שם פרויקט: מעביר את התיקיה ומעדכן את שדה project בסיכומים."""
    if old == config.DEFAULT_PROJECT:
        return {"ok": False, "error": "לא ניתן לשנות את שם פרויקט ברירת המחדל"}
    new = config.safe_name(new)
    if not new:
        return {"ok": False, "error": "שם לא תקין"}
    if new == config.DEFAULT_PROJECT:
        return {"ok": False, "error": "שם שמור"}
    src = config.PROJECTS_DIR / config.safe_name(old)
    dst = config.PROJECTS_DIR / new
    if not src.exists():
        return {"ok": False, "error": "הפרויקט לא נמצא"}
    if dst.exists():
        return {"ok": False, "error": "פרויקט בשם זה כבר קיים"}
    shutil.move(str(src), str(dst))
    # עדכון שדה project + audio_path בכל סיכום בפרויקט החדש
    summ_dir = dst / "summaries"
    if summ_dir.exists():
        for jf in summ_dir.glob("meeting_*.json"):
            try:
                d = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            d["project"] = new
            if d.get("audio_path"):
                d["audio_path"] = d["audio_path"].replace(f"{os.sep}{config.safe_name(old)}{os.sep}",
                                                          f"{os.sep}{new}{os.sep}")
            jf.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "name": new}
