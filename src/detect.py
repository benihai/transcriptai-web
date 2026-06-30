"""זיהוי אוטומטי של פגישה פעילה ב-Windows.

השיטה: Windows רושם איזו אפליקציה משתמשת במיקרופון *כרגע* (LastUsedTimeStop == 0).
אנו מצליבים זאת עם שמות אפליקציות פגישה מוכרות (Zoom/Teams/Webex/דפדפן עם Meet).

בדיקה ידנית:
    python -m src.detect          # מדפיס מצב כל 2 שניות
"""
from __future__ import annotations
import time
import winreg

# מיקום הרישום של Windows לשימוש במיקרופון
_MIC_BASE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

# מילות מפתח בשם האפליקציה -> שם תצוגה ידידותי
_MEETING_HINTS = {
    "zoom": "Zoom",
    "cpthost": "Zoom",
    "ms-teams": "Microsoft Teams",
    "msteams": "Microsoft Teams",
    "teams": "Microsoft Teams",
    "webex": "Cisco Webex",
    "ciscocollab": "Cisco Webex",
    "atmgr": "Cisco Webex",
    "chrome": "Google Chrome (אולי Meet)",
    "msedge": "Microsoft Edge (אולי Meet)",
    "firefox": "Firefox (אולי Meet)",
}


def _iter_consent_keys():
    """מחזיר (base_path, subkey_name) לכל אפליקציה שרשומה כמשתמשת מיקרופון."""
    for base in (_MIC_BASE + r"\NonPackaged", _MIC_BASE):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
        except FileNotFoundError:
            continue
        idx = 0
        while True:
            try:
                name = winreg.EnumKey(root, idx)
                idx += 1
            except OSError:
                break
            if name == "NonPackaged":   # מטופל בנפרד
                continue
            yield base, name


def _is_in_use(base: str, name: str) -> bool:
    """True אם האפליקציה משתמשת במיקרופון כרגע (LastUsedTimeStop == 0)."""
    try:
        sk = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\" + name)
        stop, _ = winreg.QueryValueEx(sk, "LastUsedTimeStop")
        return stop == 0
    except OSError:
        return False


def detect_meeting() -> str | None:
    """מחזיר שם תצוגה של אפליקציית פגישה פעילה, או None אם אין."""
    for base, name in _iter_consent_keys():
        if not _is_in_use(base, name):
            continue
        low = name.lower()
        for hint, label in _MEETING_HINTS.items():
            if hint in low:
                return label
    return None


def _debug():
    """מדפיס את כל רשומות המיקרופון עם הערכים הגולמיים - להרצה בזמן פגישה."""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("=== מצב מיקרופון גולמי (הרץ בזמן שאתה בפגישה) ===\n")
    for base, name in _iter_consent_keys():
        try:
            sk = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\" + name)
            start, _ = winreg.QueryValueEx(sk, "LastUsedTimeStart")
        except OSError:
            start = "?"
        try:
            stop, _ = winreg.QueryValueEx(sk, "LastUsedTimeStop")
        except OSError:
            stop = "?"
        flag = "🟢 בשימוש כעת" if stop == 0 else ""
        short = name.split("#")[-1] if "#" in name else name
        print(f"  {flag:14} stop={stop!s:<22} {short}")
    print(f"\ndetect_meeting() => {detect_meeting()}")


def _cli():
    import sys
    if "--debug" in sys.argv:
        _debug()
        return
    sys.stdout.reconfigure(encoding="utf-8")
    print("👀 עוקב אחר פגישות... (Ctrl+C לעצירה)")
    print("   התחל/הצטרף לפגישת Zoom/Teams/Meet/Webex כדי לבדוק.\n")
    last = None
    try:
        while True:
            app = detect_meeting()
            if app != last:
                if app:
                    print(f"🟢 זוהתה פגישה פעילה: {app}")
                else:
                    print("⚪ אין פגישה פעילה")
                last = app
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nעצירה.")


if __name__ == "__main__":
    _cli()
