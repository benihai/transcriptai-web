"""סנכרון יומן: המערכת מקבלת כתובת Gmail ייעודית. כשמזמינים אותה לפגישה ביומן,
היא קוראת את הזמנת היומן (text/calendar) דרך IMAP, מזהה את זמן הפגישה ואת הקישור,
ובזמן הפגישה ה-watcher מצטרף אוטומטית ומקליט.

הגדרה: דרך מסך ההגדרות באפליקציה (נשמר ל-calendar_config.json). דורש:
  - הפעלת IMAP בחשבון Gmail
  - יצירת App Password (דורש אימות דו-שלבי) והזנתו כאן (לא סיסמת החשבון הרגילה)
"""
from __future__ import annotations
import re
import json
import email
import imaplib
import datetime as dt
from pathlib import Path

from . import config

CONFIG_FILE = config.ROOT / "calendar_config.json"
HANDLED_FILE = config.DATA_DIR / "calendar_handled.json"
LOG_FILE = config.DATA_DIR / "calendar.log"


def log(msg: str) -> None:
    """רישום לקובץ לוג (ל-watcher שרץ ללא קונסולה) - לעולם לא זורק."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except Exception:
        pass

DEFAULT_CONFIG = {
    "enabled": False,
    "email": "",
    "app_password": "",
    "imap_host": "imap.gmail.com",
    "project": config.DEFAULT_PROJECT,
    "lead_seconds": 120,      # להצטרף X שניות לפני תחילת הפגישה (מוקדם, לפצות על זמן עליית הבוט)
    "max_minutes": 180,       # תקרת משך הקלטה אם אין שעת סיום
    "use_browser_bot": True,  # להצטרף כבוט בדפדפן (משתתף נפרד) במקום לפתוח את האפליקציה
    "bot_name": "Synthia Notetaker",   # השם שיוצג בפגישה
    # בוט Docker (screenappai/meeting-bot) - מצטרף כמשתתף לכל הפלטפורמות כולל Teams,
    # מקליט, ושומר webm מקומית. מועדף על פני הבוט המקומי כשפעיל.
    "use_docker_bot": True,
    "bot_api_url": "http://localhost:3000",
    "bot_output_dir": "C:\\meetingbot-output",
    "bot_timezone": "Asia/Jerusalem",
}

# קישורי פגישה נתמכים (Zoom / Google Meet / Teams)
_MEETING_RE = re.compile(
    r"https?://[^\s<>\"']*?(?:zoom\.us/(?:j|s|my|w)/[^\s<>\"']+"
    r"|meet\.google\.com/[a-z0-9\-]+"
    r"|teams\.microsoft\.com/l/meetup-join/[^\s<>\"']+"
    r"|teams\.microsoft\.com/meet/[^\s<>\"']+"
    r"|teams\.live\.com/meet/[^\s<>\"']+)",
    re.I,
)


# ---------- הגדרות ----------
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> dict:
    data = {**DEFAULT_CONFIG, **(cfg or {})}
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ---------- מעקב אחרי פגישות שכבר טופלו ----------
# נשמר כ-dict: {uid: start_time} — אם השעה השתנתה, נצטרף שוב
def load_handled() -> dict:
    if HANDLED_FILE.exists():
        try:
            data = json.loads(HANDLED_FILE.read_text(encoding="utf-8"))
            # תאימות לאחור: אם הפורמט הישן הוא list, ממירים ל-dict
            if isinstance(data, list):
                return {uid: "" for uid in data}
            return data
        except Exception:
            return {}
    return {}


def mark_handled(uid: str, start: str = "") -> None:
    h = load_handled()
    h[uid] = start
    # שומרים רק 500 אחרונים
    if len(h) > 500:
        h = dict(list(h.items())[-500:])
    HANDLED_FILE.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")


# ---------- ניתוח ICS ----------
def extract_meeting_url(text: str) -> str:
    if not text:
        return ""
    m = _MEETING_RE.search(text)
    return m.group(0).rstrip(",.;)>\"'") if m else ""


def _unescape(v: str) -> str:
    return (v.replace("\\,", ",").replace("\\;", ";")
            .replace("\\n", "\n").replace("\\N", "\n").replace("\\\\", "\\"))


def _unfold(ics: str) -> str:
    """שורות ICS מקופלות (CRLF ואז רווח/טאב) - מאחדים אותן."""
    return re.sub(r"\r?\n[ \t]", "", ics)


def _parse_dt(val: str, params: dict):
    """ממיר ערך תאריך/שעה של ICS לזמן מקומי (naive)."""
    val = (val or "").strip()
    try:
        if val.endswith("Z"):
            d = dt.datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            return d.astimezone().replace(tzinfo=None)
        if "T" in val:
            naive = dt.datetime.strptime(val, "%Y%m%dT%H%M%S")
            tzid = params.get("TZID")
            if tzid:
                try:
                    from zoneinfo import ZoneInfo
                    return naive.replace(tzinfo=ZoneInfo(tzid)).astimezone().replace(tzinfo=None)
                except Exception:
                    return naive   # אין מסד אזורי-זמן - מתייחסים כזמן מקומי
            return naive
        return dt.datetime.strptime(val, "%Y%m%d")
    except Exception:
        return None


def _parse_vevents(ics: str) -> list[dict]:
    ics = _unfold(ics)
    events, cur = [], None
    for line in ics.splitlines():
        s = line.strip()
        if s == "BEGIN:VEVENT":
            cur = {}
        elif s == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in s:
            key, val = s.split(":", 1)
            parts = key.split(";")
            name = parts[0].upper()
            params = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k.upper()] = v
            cur[name] = (val, params)
    return events


def parse_ics(ics: str) -> list[dict]:
    """מחזיר רשימת אירועים {uid,title,start,end,url,cancelled} מתוך מחרוזת ICS."""
    method = ""
    mm = re.search(r"^METHOD:(.+)$", _unfold(ics), re.M)
    if mm:
        method = mm.group(1).strip().upper()
    out = []
    for ev in _parse_vevents(ics):
        if "DTSTART" not in ev:
            continue
        start = _parse_dt(*ev["DTSTART"])
        if not start:
            continue
        end = _parse_dt(*ev["DTEND"]) if "DTEND" in ev else None
        uid = (ev.get("UID", ("", {}))[0]) or start.isoformat()
        title = _unescape(ev.get("SUMMARY", ("פגישה", {}))[0]) or "פגישה"
        blob = " ".join([
            ev.get("LOCATION", ("", {}))[0],
            ev.get("DESCRIPTION", ("", {}))[0],
            ev.get("URL", ("", {}))[0],
            ev.get("X-GOOGLE-CONFERENCE", ("", {}))[0],
        ])
        out.append({
            "uid": uid, "title": title,
            "start": start, "end": end,
            "url": extract_meeting_url(_unescape(blob)),
            "cancelled": method == "CANCEL" or ev.get("STATUS", ("", {}))[0].upper() == "CANCELLED",
        })
    return out


# ---------- שליפה מ-Gmail (IMAP) ----------
def fetch_upcoming(cfg: dict | None = None, days_back: int = 30, days_fwd: int = 30) -> list[dict]:
    """מתחבר ל-IMAP, קורא הזמנות יומן ומחזיר פגישות קרובות ממויינות לפי זמן.

    מחזיר רשימה של dicts: {uid, title, start(iso), end(iso), url}.
    """
    cfg = cfg or load_config()
    if not cfg.get("email") or not cfg.get("app_password"):
        return []
    host = cfg.get("imap_host") or "imap.gmail.com"
    M = imaplib.IMAP4_SSL(host)
    try:
        M.login(cfg["email"], cfg["app_password"])
        M.select("INBOX")
        # מצמצמים רק להזמנות יומן (קובץ .ics) כדי שיהיה מהיר - חיפוש Gmail ייעודי,
        # ונפילה לחיפוש IMAP רגיל אם לא Gmail.
        ids = []
        try:
            typ, data = M.search(None, "X-GM-RAW", f'"has:attachment filename:ics newer_than:{days_back}d"')
            ids = data[0].split() if data and data[0] else []
        except Exception:
            ids = []
        if not ids:
            since = (dt.date.today() - dt.timedelta(days=days_back)).strftime("%d-%b-%Y")
            typ, data = M.search(None, f'(SINCE {since})')
            ids = data[0].split() if data and data[0] else []
        now = dt.datetime.now()
        horizon = now + dt.timedelta(days=days_fwd)
        by_uid: dict[str, dict] = {}
        for num in ids[-80:]:
            typ, md = M.fetch(num, "(BODY.PEEK[])")   # PEEK = לא לסמן כנקרא
            if not md or not md[0]:
                continue
            msg = email.message_from_bytes(md[0][1])
            for part in msg.walk():
                if part.get_content_type() != "text/calendar":
                    continue
                try:
                    ics = part.get_payload(decode=True).decode("utf-8", "ignore")
                except Exception:
                    continue
                for ev in parse_ics(ics):
                    uid = ev["uid"]
                    if ev["cancelled"]:
                        by_uid.pop(uid, None)
                        continue
                    start = ev["start"]
                    # שומרים גם פגישות שהתחילו עד 10 דק' אחורה (חלון ההצטרפות 5 דק')
                    if start < now - dt.timedelta(minutes=10) or start > horizon:
                        continue
                    end = ev["end"] or (start + dt.timedelta(minutes=cfg.get("max_minutes", 180)))
                    by_uid[uid] = {
                        "uid": uid, "title": ev["title"], "url": ev["url"],
                        "start": start.isoformat(timespec="minutes"),
                        "end": end.isoformat(timespec="minutes"),
                    }
        return sorted(by_uid.values(), key=lambda e: e["start"])
    finally:
        try:
            M.logout()
        except Exception:
            pass


def test_connection(cfg: dict) -> dict:
    """בדיקת חיבור IMAP. מחזיר {ok, events|error}."""
    try:
        events = fetch_upcoming(cfg)
        return {"ok": True, "events": events}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "error": f"התחברות נכשלה (בדוק אימייל/App Password/הפעלת IMAP): {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
