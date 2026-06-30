"""שליחת בקשת הצטרפות לבוט הפגישות שרץ ב-Docker (screenappai/meeting-bot).

הבוט מצטרף לפגישה (Meet/Zoom/Teams) כמשתתף נפרד, מקליט, ובמצב מקומי שומר את
קובץ ה-webm לתיקיית הפלט (C:\\meetingbot-output) עם שם שמתחיל ב-<userId>__ .
האפליקציה שלנו קולטת את הקובץ ומריצה תמלול+סיכום במערכת שלנו.
"""
from __future__ import annotations
import re
import json
import urllib.request
import urllib.error

# מיפוי פלטפורמה -> נתיב ה-API בבוט
_PLATFORM_PATH = {"meet": "google", "zoom": "zoom", "teams": "microsoft"}


def make_key(uid: str, stamp: str) -> str:
    """מזהה בטוח (alnum) לפגישה - מוטמע בשם קובץ ההקלטה לצורך שיוך חזרה."""
    base = re.sub(r"[^A-Za-z0-9]", "", uid or "")[:28]
    return f"m{base}{stamp}"


def dispatch(api_url: str, platform: str, url: str, name: str, key: str,
             timezone: str = "Asia/Jerusalem") -> dict:
    """שולח POST ל-{api_url}/{google|zoom|microsoft}/join. מחזיר {ok, ...}."""
    path = _PLATFORM_PATH.get(platform)
    if not path:
        return {"ok": False, "error": f"פלטפורמה לא נתמכת: {platform}"}
    body = {
        "bearerToken": "local",      # לא בשימוש במצב אחסון מקומי
        "url": url,
        "name": name or "Synthia Notetaker",
        "teamId": "local",
        "timezone": timezone,
        "userId": key,               # מוטמע בשם קובץ ההקלטה
        "botId": key,
        "eventId": key,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/{path}/join", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return {"ok": True, "status": r.status, "body": r.read().decode("utf-8", "ignore")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','ignore')[:300]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def health(api_url: str) -> dict:
    """בדיקת זמינות בסיסית של ה-API."""
    try:
        with urllib.request.urlopen(api_url.rstrip("/") + "/", timeout=8) as r:
            return {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        return {"ok": True, "status": e.code}   # מגיב = חי
    except Exception as e:
        return {"ok": False, "error": str(e)}
