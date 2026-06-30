"""סיכום תמלול באמצעות Gemini - מפיק תקציר, נקודות עיקריות, החלטות ומשימות."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import List

from . import config


def _log(msg: str) -> None:
    """כתיבת לוג בטוחה - לעולם לא זורקת חריגה (קונסולת Windows/cp1255 או pythonw ללא stdout)."""
    try:
        print(msg)
    except Exception:
        pass


def _nd(s):
    """ממיר מקפים ארוכים (em/en dash) למקף רגיל קצר - לטקסט אחיד בסיכומים ובפורמט."""
    text = str(s or "")
    for ch in ("—", "–", "―", "‒", "−"):
        text = text.replace(ch, "-")
    return text


@dataclass
class ActionItem:
    task: str
    owner: str = ""      # מי אחראי (אם נאמר)
    due: str = ""        # מועד יעד (אם נאמר)


@dataclass
class Topic:
    """נושא שנדון בפגישה, עם כל הנקודות והפרטים שעלו בו."""
    title: str
    points: List[str] = field(default_factory=list)


@dataclass
class MeetingSummary:
    title: str
    summary: str
    topics: List[Topic] = field(default_factory=list)
    key_points: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    action_items: List[ActionItem] = field(default_factory=list)


# הסכמה שאנו דורשים מ-Gemini להחזיר (JSON אמין).
# מבנה מפורט: סיכום מקיף + פירוק נושא-אחר-נושא, כדי לא לאבד פרטים מהתמלול.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "כותרת קצרה לפגישה"},
        "summary": {
            "type": "string",
            "description": "סיכום מקיף ומפורט בכמה פסקאות המתאר את מהלך הפגישה וכל הנושאים שנדונו",
        },
        "topics": {
            "type": "array",
            "description": "פירוק נושא-אחר-נושא של כל מה שנדון בפגישה. נושא לכל תחום/סעיף שעלה.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "כותרת הנושא"},
                    "points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "כל הנקודות, הפרטים, המספרים, השמות, הסכומים והדעות שנאמרו בנושא זה",
                    },
                },
                "required": ["title", "points"],
            },
        },
        "key_points": {"type": "array", "items": {"type": "string"},
                       "description": "הנקודות החשובות ביותר מהפגישה"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["task"],
            },
        },
    },
    "required": ["title", "summary", "topics", "key_points", "decisions", "action_items"],
}

_SYSTEM_INSTRUCTION = (
    "אתה עוזר מקצועי לסיכום פגישות. קיבלת תמלול של פגישה (ייתכן בעברית). "
    "המטרה: להפיק סיכום מקיף, מדויק וענייני שאינו מפספס פרטים מהותיים, אך נמנע לחלוטין מחזרות וכפילויות.\n\n"
    "מבנה הסיכום הנדרש:\n"
    "1. תקציר מנהלים (summary): פסקה אחת או שתיים המזקקות את מטרת הפגישה, התמונה הגדולה והשורה התחתונה.\n"
    "2. נושאים מרכזיים (topics): חלק את הפגישה לנושאים הנידונים. עבור כל נושא, רכז וסנתז את כל הנקודות שעלו "
    "(שמות, מספרים, תאריכים, מפרטים ופרטים טכניים, דרישות, בעיות, סיכונים וחילוקי דעות). "
    "חובה לאחד מידע שחזר על עצמו במהלך הפגישה לאותה נקודה - אל תכתוב את אותו פרט פעמיים.\n"
    "3. החלטות (decisions): רשימה ממוקדת ונפרדת של כל ההחלטות שהתקבלו.\n"
    "4. משימות לביצוע (action_items): רשימת משימות ברורה הכוללת מהות, מי אחראי ומועד יעד (אם הוזכרו).\n\n"
    "הנחיות קריטיות לביצוע:\n"
    "- שמור על כל הפרטים הטכניים והמספריים שעלו (כגון מידות, דגמים, תקציבים), אך נסח אותם בצורה תמציתית וישירה.\n"
    "- השתמש במקף רגיל קצר (-) בלבד, לעולם לא במקף ארוך (—).\n"
    "- התבסס אך ורק על הנאמר בפגישה. אם פרט מסוים לא נאמר, אל תמציא אותו והשאר ריק."
)


# ---------- חילוץ שדות לפי פורמט DIT ----------
_FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {"type": "string", "description": "שם הפרויקט"},
        "topic": {"type": "string", "description": "נושא הפגישה"},
        "date": {"type": "string", "description": "תאריך בפורמט DD/MM/YYYY"},
        "location": {"type": "string", "description": "מקום הפגישה"},
        "participants": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "role": {"type": "string"}, "company": {"type": "string"}},
                "required": ["name"]},
        },
        "findings": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "description": {"type": "string"},
                "responsible": {"type": "string"},
                "due": {"type": "string"},
                "note": {"type": "string"}},
                "required": ["description"]},
        },
    },
    "required": ["project_name", "topic", "date", "location", "participants", "findings"],
}

_FORMAT_INSTRUCTION = (
    "אתה עוזר לכתיבת סיכום פגישה מקצועי (סגנון פיקוח/הנדסה). מתוך תמלול הפגישה "
    "חלץ את השדות: שם פרויקט, נושא הפגישה, תאריך (DD/MM/YYYY), מקום, משתתפים "
    "(שם, תפקיד, חברה), ורשימת ממצאים/נקודות. לכל ממצא: תיאור, אחראי, מועד יעד, והערה. "
    "אם פרט לא נאמר בפגישה - השאר ריק, אל תמציא."
)


def extract_format_fields(transcript: str, summary: dict | None = None, on_progress=None) -> dict:
    """מחלץ שדות מובנים לפי פורמט DIT מתוך תמלול (ובמידת האפשר גם מהסיכום הקיים).

    on_progress: פונקציה אופציונלית שמקבלת אחוז (0-100) להצגת התקדמות בזמן יצירה.
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError("חסר מפתח GEMINI_API_KEY.")
    from google import genai
    from google.genai import types, errors

    content = f"להלן תמלול הפגישה:\n\n{transcript}"
    # אם כבר יש סיכום מובנה - נותנים אותו ל-AI כדי שהמיפוי לפורמט יהיה שלם ומדויק
    if summary:
        extra = []
        if summary.get("decisions"):
            extra.append("החלטות: " + " | ".join(summary["decisions"]))
        if summary.get("key_points"):
            extra.append("נקודות מפתח: " + " | ".join(summary["key_points"]))
        if summary.get("action_items"):
            ai = "; ".join(
                f"{a.get('task','')} (אחראי: {a.get('owner','')}, מועד: {a.get('due','')})"
                for a in summary["action_items"])
            extra.append("משימות: " + ai)
        if extra:
            content += "\n\nסיכום קיים שכבר הופק (השתמש בו כדי שהממצאים יהיו שלמים):\n" + "\n".join(extra)

    client = genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT * 1000),
    )
    gen_config = types.GenerateContentConfig(
        system_instruction=_FORMAT_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=_FORMAT_SCHEMA,
        temperature=0.2,
        max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
    )
    estimated = max(1000, int(len(transcript) * 0.3))
    try:
        parts, got = [], 0
        stream = client.models.generate_content_stream(
            model=config.GEMINI_MODEL, contents=content, config=gen_config)
        for chunk in stream:
            t = chunk.text or ""
            parts.append(t); got += len(t)
            if on_progress:
                on_progress(min(95, int(got / estimated * 100)))
        raw = "".join(parts)
    except Exception as e:
        raise RuntimeError(f"שגיאת Gemini: {e}") from e
    if on_progress:
        on_progress(100)
    return json.loads(raw)


# ---------- צ'אט על הפגישה (Q&A מעוגן בתמלול ובסיכום) ----------
_CHAT_INSTRUCTION = (
    "אתה עוזר חכם שעובד על סיכום פגישה ספציפי, ומבוסס אך ורק על התמלול והסיכום שיינתנו לך. ענה בעברית. "
    "החלט בין שתי פעולות:\n"
    "• action='answer' - אם המשתמש שואל שאלה או מחפש מידע. החזר את התשובה ב-reply בלבד.\n"
    "• action='edit' - אם המשתמש מבקש לשנות, להוסיף, להסיר, לתקן או לנסח מחדש משהו בסיכום. "
    "במקרה זה החזר ב-summary את הסיכום *המלא והמעודכן* (כל השדות: title, summary, topics, key_points, "
    "decisions, action_items), כשהשינוי המבוקש מוטמע בו, ושאר התוכן נשמר כפי שהיה. ב-reply כתוב אישור קצר. "
    "אל תיצור סיכום חדש מאפס - ערוך את הסיכום הקיים. "
    "אם מידע לא קיים בתמלול - אמור זאת. השתמש במקף קצר (-) בלבד."
)

_CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["answer", "edit"]},
        "reply": {"type": "string", "description": "תשובה/אישור קצר למשתמש"},
        "summary": _RESPONSE_SCHEMA,
    },
    "required": ["action", "reply"],
}


def chat(transcript: str, summary: dict | None, history: list | None, message: str) -> dict:
    """צ'אט על פגישה. מחזיר dict: {action:'answer'|'edit', reply:str, summary:dict|None}.
    כאשר action='edit' - summary מכיל את הסיכום המלא המעודכן (לשמירה על המקור)."""
    if not config.GEMINI_API_KEY:
        raise RuntimeError("חסר מפתח GEMINI_API_KEY.")
    from google import genai
    from google.genai import types

    summary = summary or {}
    context = ("הסיכום הנוכחי (JSON, לעריכה במידת הצורך):\n"
               + json.dumps(summary, ensure_ascii=False)
               + "\n\nתמלול הפגישה המלא:\n" + (transcript or ""))

    client = genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT * 1000),
    )
    contents = [types.Content(role="user", parts=[types.Part(text=context)]),
                types.Content(role="model", parts=[types.Part(text="קראתי את הפגישה והסיכום. כיצד אפשר לעזור?")])]
    for turn in (history or [])[-8:]:
        role = "model" if turn.get("role") == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=str(turn.get("text", "")))]))
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

    try:
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL, contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_CHAT_INSTRUCTION, temperature=0.4,
                response_mime_type="application/json", response_schema=_CHAT_SCHEMA,
                max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS),
        )
    except Exception as e:
        raise RuntimeError(f"שגיאת Gemini: {e}") from e

    data = json.loads(resp.text)
    out = {"action": data.get("action", "answer"), "reply": _nd(data.get("reply", "")), "summary": None}
    if out["action"] == "edit" and isinstance(data.get("summary"), dict):
        s = data["summary"]
        out["summary"] = {
            "title": _nd(s.get("title", "") or summary.get("title", "")),
            "summary": _nd(s.get("summary", "")),
            "topics": [{"title": _nd(t.get("title", "")), "points": [_nd(p) for p in (t.get("points") or [])]}
                       for t in s.get("topics", []) or []],
            "key_points": [_nd(p) for p in s.get("key_points", [])],
            "decisions": [_nd(d) for d in s.get("decisions", [])],
            "action_items": [{"task": _nd(a.get("task", "")), "owner": _nd(a.get("owner", "")), "due": _nd(a.get("due", ""))}
                             for a in s.get("action_items", [])],
        }
    return out


def summarize(transcript: str, on_progress=None,
              participants: list | None = None) -> MeetingSummary:
    """מקבל טקסט תמלול ומחזיר סיכום מובנה.

    on_progress: פונקציה אופציונלית שמקבלת אחוז (0-100) להצגת התקדמות.
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "חסר מפתח GEMINI_API_KEY. צור קובץ .env והוסף את המפתח "
            "(מתקבל בחינם מ-https://aistudio.google.com/apikey)."
        )

    from google import genai
    from google.genai import types, errors

    # timeout (במילישניות) על קריאות ה-HTTP - כך שחיבור סטרימינג שנפל בשקט
    # יזרוק שגיאה אחרי GEMINI_TIMEOUT שניות במקום להיתקע לנצח.
    client = genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT * 1000),
    )
    # הוסף הקשר משתתפים לפרומפט אם קיים
    system_instruction = _SYSTEM_INSTRUCTION
    if participants:
        names = ", ".join(participants)
        system_instruction += (
            f"\n\nהמשתתפים בפגישה: {names}. "
            "כשאנשים מדברים בגוף ראשון (אני, אנחנו, נעשה וכו'), "
            "נסה להסיק מהקשר מי דיבר ושלב את שמם בסיכום ובמשימות. "
            "לדוגמה: אם 'בני' אמר 'אני אביא את החומר' — כתוב במשימות: 'הבאת החומר — אחראי: בני'."
        )

    gen_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=_RESPONSE_SCHEMA,
        temperature=0.3,
        max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
    )

    # הערכת אורך תשובה צפוי כדי לחשב אחוז התקדמות בזמן streaming
    # (סיכום מפורט -> פלט ארוך; מעריכים גבוה יותר כדי שהאחוז יזחל בהדרגה)
    estimated = max(1500, int(len(transcript) * 0.5))

    def _stream_once():
        parts, got = [], 0
        stream = client.models.generate_content_stream(
            model=config.GEMINI_MODEL,
            contents=f"להלן תמלול הפגישה:\n\n{transcript}",
            config=gen_config,
        )
        for chunk in stream:
            t = chunk.text or ""
            parts.append(t)
            got += len(t)
            if on_progress:
                on_progress(min(95, int(got / estimated * 100)))
        return "".join(parts)

    # ניסיונות חוזרים על שגיאות זמניות: עומס שרת (503/429) וגם timeout/נפילת חיבור.
    last_err = None
    raw = None
    for attempt in range(4):
        try:
            raw = _stream_once()
            break
        except errors.ClientError as e:
            # שגיאת בקשה אמיתית (מפתח/קלט) - אין טעם לנסות שוב
            raise RuntimeError(f"שגיאת Gemini (ככל הנראה במפתח או בבקשה): {e}") from e
        except Exception as e:
            # ServerError, timeout (httpx.ReadTimeout), נפילת חיבור וכו' - מנסים שוב
            last_err = e
            if attempt < 3:
                wait = 3 * (attempt + 1)
                _log(f"   [warn] Gemini did not respond (attempt {attempt + 1}/4): {type(e).__name__}. retrying in {wait}s...")
                time.sleep(wait)
    else:
        raise RuntimeError(
            f"Gemini לא הגיב אחרי 4 ניסיונות (כנראה תקלת רשת או עומס). "
            f"התמלול נשמר — אפשר להפיק את הסיכום שוב מתפריט 'תמלולים'. ({type(last_err).__name__})"
        )

    if on_progress:
        on_progress(100)
    data = json.loads(raw)
    return MeetingSummary(
        title=_nd(data.get("title", "")),
        summary=_nd(data.get("summary", "")),
        topics=[
            Topic(title=_nd(t.get("title", "")), points=[_nd(p) for p in (t.get("points", []) or [])])
            for t in data.get("topics", []) or []
        ],
        key_points=[_nd(p) for p in data.get("key_points", [])],
        decisions=[_nd(d) for d in data.get("decisions", [])],
        action_items=[
            ActionItem(task=_nd(a.get("task", "")), owner=_nd(a.get("owner", "")), due=_nd(a.get("due", "")))
            for a in data.get("action_items", [])
        ],
    )
