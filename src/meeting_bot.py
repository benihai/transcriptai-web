"""בוט הצטרפות לפגישות דרך דפדפן (Playwright) - מצטרף כמשתתף נפרד בשם בוט.

best-effort ותלוי-פלטפורמה: Google Meet נוטה לעבוד הכי טוב; Zoom web נוטה לחסום
אוטומציה (CAPTCHA/הפניה לאפליקציה); Teams משתנה. הדפדפן נפתח גלוי כדי:
  (א) שקול הפגישה יושמע דרך הרמקול וייקלט ע"י לכידת קול-המערכת,
  (ב) שתוכל להשלים ידנית שלב שנתקע (אישור מחדר המתנה, CAPTCHA וכו').

בדיקה ידנית:
    python -m src.meeting_bot "<מק meeting url>"
"""
from __future__ import annotations
import re
import sys
import time
import threading

from . import config

# דגלים: מדיה אוטומטית, ללא זיהוי אוטומציה, יציבות, וחלון מחוץ למסך (בלתי-נראה).
# הערה: לא headless - דפדפן headless לא מנגן אודיו למערכת, ואז אין מה להקליט.
# לכן החלון פתוח אך ממוקם הרחק מחוץ למסך כך שאינו נראה, והאודיו עדיין נקלט.
_CHROME_ARGS = [
    "--use-fake-ui-for-media-stream",          # מאשר הרשאות מצלמה/מיקרופון אוטומטית
    "--autoplay-policy=no-user-gesture-required",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-gpu",                           # יציבות - מונע קריסת renderer
    "--disable-software-rasterizer",
    "--disable-dev-shm-usage",
    "--window-position=-32000,-32000",         # מחוץ למסך = בלתי-נראה
    "--window-size=1280,860",
]
# סדר עדיפות דפדפנים: Chrome -> Chromium מובנה -> Edge (ל-Meet/Zoom Chrome עדיף)
_BROWSER_CHANNELS = ["chrome", None, "msedge"]


def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "zoom.us" in u:
        return "zoom"
    if "meet.google.com" in u:
        return "meet"
    if "teams.microsoft.com" in u or "teams.live.com" in u:
        return "teams"
    return ""


class MeetingBot:
    """מנהל סשן בוט בדפדפן ב-thread נפרד. start() מצטרף, stop() עוזב."""

    def __init__(self, url: str, bot_name: str = "Synthia Notetaker", log=None):
        self.url = url
        self.bot_name = bot_name or "Synthia Notetaker"
        self.platform = detect_platform(url)
        self._log = log or (lambda m: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ctx = None
        self.joined = False

    # ----- API -----
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=20)

    # ----- internal -----
    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            self._log(f"bot: Playwright לא זמין: {e}")
            return
        try:
            with sync_playwright() as p:
                browser, ctx, last_err = None, None, None
                for channel in _BROWSER_CHANNELS:
                    try:
                        lk = dict(headless=False, args=_CHROME_ARGS)
                        if channel:
                            lk["channel"] = channel
                        browser = p.chromium.launch(**lk)
                        # קונטקסט נקי (ללא פרופיל/חשבון) -> הצטרפות תמיד כאורח
                        ctx = browser.new_context(permissions=["microphone", "camera"],
                                                  viewport={"width": 1280, "height": 820})
                        self._log(f"bot: דפדפן = {channel or 'chromium'} (אורח, מוסתר)")
                        break
                    except Exception as e:
                        last_err = e
                        try:
                            if browser:
                                browser.close()
                        except Exception:
                            pass
                        browser = None
                        continue
                if ctx is None:
                    self._log(f"bot: כשל בהפעלת דפדפן: {last_err}")
                    return
                self._ctx = ctx
                # טאב-שמירה ריק: כך אם טאב הפגישה נסגר (למשל Teams שמפנה לאפליקציה)
                # הדפדפן/קונטקסט לא ייסגר לגמרי
                try:
                    guard = ctx.new_page()
                    guard.goto("about:blank", timeout=8000)
                except Exception:
                    pass
                try:
                    ctx.new_page()   # טאב העבודה לפגישה
                except Exception:
                    pass
                try:
                    if self.platform == "meet":
                        self._join_meet(ctx)
                    elif self.platform == "zoom":
                        self._join_zoom(ctx)
                    elif self.platform == "teams":
                        self._join_teams(ctx)
                    else:
                        self._page(ctx).goto(self.url, timeout=60000)
                    self.joined = True
                    self._log(f"bot: הצטרף ({self.platform}) כאורח בשם '{self.bot_name}' (חלון מוסתר).")
                except Exception as e:
                    self._log(f"bot: אוטומציה נכשלה ({self.platform}): {type(e).__name__}: {e}")
                self._snap(ctx, "after-join")
                # נשארים בפגישה עד שמבקשים לעצור (גם אם האוטומציה נכשלה)
                ticks = 0
                while not self._stop.is_set():
                    time.sleep(1)
                    ticks += 1
                    if ticks in (10, 25):   # צילומי מסך לאבחון מצב ההצטרפות
                        self._snap(ctx, f"t{ticks}")
                try:
                    ctx.close()
                except Exception:
                    pass
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
            self._log("bot: הדפדפן נסגר.")
        except Exception as e:
            self._log(f"bot: שגיאה כללית: {type(e).__name__}: {e}")

    def _snap(self, ctx, tag):
        """שומר צילום מסך של הטאב הפעיל (לאבחון). ל-data/bot_<tag>.png."""
        try:
            self._page(ctx).screenshot(path=str(config.DATA_DIR / f"bot_{tag}.png"))
            self._log(f"bot: צילום מסך נשמר bot_{tag}.png")
        except Exception as e:
            self._log(f"bot: snap warn ({tag}): {e}")

    def _page(self, ctx):
        """הטאב הפעיל האחרון (Teams/Zoom פותחים לעיתים טאב חדש ומסגרים את המקורי)."""
        try:
            pages = [pg for pg in ctx.pages if not pg.is_closed()]
            return pages[-1] if pages else ctx.new_page()
        except Exception:
            return ctx.new_page()

    def _goto(self, ctx, url):
        """ניווט עמיד: Teams לעיתים מנסה להפנות לפרוטוקול הדסקטופ וגורם ל-ERR_FAILED.
        מנסים כמה אסטרטגיות, ולבסוף ניווט דרך JS שלא זורק."""
        pg = self._page(ctx)
        for wait in ("domcontentloaded", "load"):
            try:
                pg.goto(url, wait_until=wait, timeout=45000)
                return
            except Exception as e:
                last = e
        # נפילה: ניווט דרך JS (לא מפיל את הטאב על הפניית פרוטוקול)
        try:
            pg = self._page(ctx)
            pg.goto("about:blank", timeout=10000)
            pg.evaluate("u => { window.location.href = u; }", url)
            self._log("bot: ניווט דרך JS (Teams)")
        except Exception as e:
            self._log(f"bot: goto נכשל: {last if 'last' in dir() else e}")

    def _click_any(self, ctx, labels, timeout=4000) -> bool:
        """מנסה ללחוץ על כפתור לפי כמה תוויות (אנגלית/עברית), על הטאב הפעיל."""
        for lbl in labels:
            try:
                pg = self._page(ctx)
                pg.get_by_role("button", name=re.compile(lbl, re.I)).first.click(timeout=timeout)
                self._log(f"bot: נלחץ '{lbl}'")
                return True
            except Exception:
                continue
        return False

    def _fill_name(self, ctx, selectors) -> bool:
        for sel in selectors:
            try:
                self._page(ctx).fill(sel, self.bot_name, timeout=4000)
                self._log("bot: שם הוזן")
                return True
            except Exception:
                continue
        return False

    # ---- Google Meet ----
    def _join_meet(self, ctx):
        self._goto(ctx, self.url)
        time.sleep(4)
        self._click_any(ctx, ["Turn off microphone", "כבה מיקרופון"], timeout=2500)
        self._click_any(ctx, ["Turn off camera", "כבה מצלמה"], timeout=2500)
        self._fill_name(ctx, ['input[aria-label*="name" i]', 'input[placeholder*="name" i]', 'input[type="text"]'])
        time.sleep(1)
        self._click_any(ctx, ["Ask to join", "Join now", "בקש להצטרף", "הצטרף עכשיו"], timeout=8000)

    # ---- Zoom (web client) ----
    def _join_zoom(self, ctx):
        mid = re.search(r"/j/(\d+)", self.url) or re.search(r"/wc/(\d+)", self.url)
        pwd = re.search(r"pwd=([^&\s]+)", self.url)
        if mid:
            wc = f"https://app.zoom.us/wc/{mid.group(1)}/join?fromPWA=1"
            if pwd:
                wc += f"&pwd={pwd.group(1)}"
            self._log(f"bot: פותח Zoom web client")
            self._goto(ctx, wc)
        else:
            self._goto(ctx, self.url)
        time.sleep(6)
        self._fill_name(ctx, ['#input-for-name', 'input[type="text"]', 'input#inputname'])
        time.sleep(1)
        self._click_any(ctx, ["Join", "הצטרף"], timeout=8000)

    # ---- Microsoft Teams ----
    def _join_teams(self, ctx):
        self._goto(ctx, self.url)
        time.sleep(6)   # Teams מפנה כמה פעמים ולעיתים פותח טאב חדש
        # "Continue on this browser" -> מונע פתיחת אפליקציית הדסקטופ
        self._click_any(ctx, ["Continue on this browser", "Join on the web instead",
                              "Use the web app instead", "המשך בדפדפן זה"], timeout=10000)
        time.sleep(5)
        self._fill_name(ctx, ['input[placeholder*="name" i]', 'input[type="text"]', 'input[name="username"]'])
        time.sleep(1)
        self._click_any(ctx, ["Join now", "Join", "הצטרף עכשיו"], timeout=8000)


def _cli():
    if len(sys.argv) < 2:
        print("usage: python -m src.meeting_bot <meeting_url> [bot_name]")
        return
    url = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "Synthia Notetaker"
    bot = MeetingBot(url, name, log=lambda m: print(m))
    print(f"platform: {bot.platform} | joining as '{name}'... (Ctrl+C לעצירה)")
    bot.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        bot.stop()


if __name__ == "__main__":
    _cli()
