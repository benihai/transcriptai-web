"""הגדרות מרכזיות של המערכת + טעינת משתני סביבה."""
import os
import glob
from pathlib import Path
from dotenv import load_dotenv

# שורש הפרויקט (תיקייה אחת מעל src/)
ROOT = Path(__file__).resolve().parent.parent

# טעינת קובץ .env אם קיים
load_dotenv(ROOT / ".env")

# --- הגדרות תמלול ---
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "he") or None
# beam_size: 1 = מהיר (greedy), 5 = מדויק יותר אך איטי
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "1"))
# מספר ליבות CPU לתמלול (ברירת מחדל: כמספר הליבות הפיזיות ~ חצי מהלוגיות)
WHISPER_CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", str(max(4, (os.cpu_count() or 8) // 2))))

# --- Groq API (תמלול מהיר) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

# --- הגדרות Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# timeout (שניות) לקריאות Gemini. אם לא מגיע מידע בתוך הזמן הזה - הקריאה נכשלת
# במקום להיתקע לנצח (למשל כשחיבור הסטרימינג נופל בשקט).
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "120"))
# תקרת אורך הפלט (טוקנים) לסיכום. גבוה = סיכום מפורט יותר בלי קיטוע.
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "16384"))

# --- תיקיות עבודה ---
RECORDINGS_DIR = ROOT / "recordings"
DATA_DIR = ROOT / "data"
RECORDINGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# --- פרויקטים ---
PROJECTS_DIR = ROOT / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)
DEFAULT_PROJECT = "כללי"   # פרויקט ברירת מחדל - משתמש בתיקיות הישנות


def safe_name(name: str) -> str:
    """שם תיקיה חוקי (מסיר תווים אסורים ב-Windows)."""
    return "".join(c for c in (name or "") if c not in '\\/:*?"<>|').strip()


def project_dirs(project: str | None):
    """מחזיר (תיקיית הקלטות, תיקיית סיכומים) עבור פרויקט.
    פרויקט 'כללי' או ריק -> התיקיות הישנות (לשמירת תאימות)."""
    if not project or project == DEFAULT_PROJECT:
        return RECORDINGS_DIR, DATA_DIR
    base = PROJECTS_DIR / safe_name(project)
    rec, summ = base / "recordings", base / "summaries"
    rec.mkdir(parents=True, exist_ok=True)
    summ.mkdir(parents=True, exist_ok=True)
    return rec, summ


# סיומות תמונה נתמכות ללוגו לקוח
LOGO_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def project_base(project: str | None):
    """תיקיית הבסיס של הפרויקט (גם עבור 'כללי' - נוצרת תחת projects/)."""
    base = PROJECTS_DIR / safe_name(project or DEFAULT_PROJECT)
    base.mkdir(parents=True, exist_ok=True)
    return base


def project_logo_path(project: str | None):
    """נתיב קובץ לוגו הלקוח של הפרויקט אם קיים, אחרת None.
    הלוגו נשמר בתיקיית הפרויקט בשם client_logo.<ext>."""
    base = PROJECTS_DIR / safe_name(project or DEFAULT_PROJECT)
    for ext in LOGO_EXTS:
        p = base / f"client_logo{ext}"
        if p.exists():
            return p
    return None


def ensure_ffmpeg_on_path() -> None:
    """ffmpeg הותקן דרך winget אך לא תמיד נמצא ב-PATH של התהליך הנוכחי.
    נאתר אותו ונוסיף אותו ל-PATH כדי שספריות אודיו ימצאו אותו.
    על Linux/Mac: ffmpeg מותקן דרך apt/brew ונמצא כבר ב-PATH — הפונקציה מסתיימת מיד."""
    # בדיקה גם עבור ffmpeg (Linux/Mac) וגם ffmpeg.exe (Windows)
    ffmpeg_names = ("ffmpeg.exe", "ffmpeg")
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p and any(os.path.exists(os.path.join(p, name)) for name in ffmpeg_names):
            return
    # חיפוש בתיקיות ההתקנה של winget
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "**", "ffmpeg.exe"),
    ]
    for pattern in patterns:
        for found in glob.glob(pattern, recursive=True):
            os.environ["PATH"] = os.path.dirname(found) + os.pathsep + os.environ.get("PATH", "")
            return
