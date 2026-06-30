"""תהליך רקע שמזהה פגישות ומקפיץ חלון "התחל הקלטה".

הרצה:
    python -m src.watcher

כשמזוהה פגישה (Zoom/Teams/Meet/Webex) קופץ חלון קטן בפינה.
לחיצה על "התחל הקלטה" מתחילה הקלטה; "סיים וסכם" מעבד ומציג בחלון הראשי.
"""
import os
import sys
import json
import time
import shutil
import threading
import subprocess
import datetime as dt
from pathlib import Path
import tkinter as tk

import pystray
from PIL import Image, ImageDraw

from . import config, record, detect, storage, calendar_sync, meeting_bot, bot_dispatch

# ----- צבעים בסגנון מערכת TranscriptAI (בהיר) -----
BG = "#FFFFFF"        # כרטיס לבן
PAPER = "#F9FAFB"     # רקע משני
FG = "#121C2A"        # טקסט ראשי
MUTED = "#6B7280"     # טקסט משני
ACCENT = "#2563EB"    # כחול ראשי
RED = "#DC2626"       # הקלטה
GREEN = "#10B981"     # סיום/הצלחה
BORDER = "#E5E7EB"    # גבול
FONT = "Segoe UI"

POLL_MS = 2000  # בדיקת פגישה כל 2 שניות


class Watcher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()  # החלון הראשי מוסתר; נשתמש רק ב-popup

        self.popup = None
        self.recorder = None
        self.recording = False
        self.rec_start = 0.0
        self.handled_app = None   # פגישה שכבר טיפלנו בה (לא להציק שוב)
        self.current_app = None
        self.tray = None

        # --- סנכרון יומן (הצטרפות אוטומטית) ---
        self.cal_events = []          # פגישות קרובות שנשלפו מהיומן
        self.cal_recording = False    # האם ההקלטה הנוכחית הופעלה ע"י היומן
        self.cal_ev = None            # האירוע הנוכחי
        self.cal_end_ts = 0.0         # זמן יעד לעצירה (epoch)
        self._cal_next_fetch = 0.0    # מתי לשלוף שוב מה-IMAP
        self.bot = None               # בוט הדפדפן (אם פעיל)

        self.root.after(500, self._poll)

    # ---------- לולאת זיהוי ----------
    def _poll(self):
        # עוטפים בכל מקרה כדי ששגיאה בודדת לא תשתק את הלולאה לצמיתות
        try:
            self._poll_body()
        except Exception as e:
            calendar_sync.log(f"poll error: {type(e).__name__}: {e}")
        finally:
            self.root.after(POLL_MS, self._poll)

    def _poll_body(self):
        now = time.time()
        # שליפת יומן מרוחקת (ב-thread). מהיר יותר כשפגישה מתקרבת כדי להצטרף בזמן.
        if now >= self._cal_next_fetch:
            interval = 12 if self._meeting_soon() else 30
            self._cal_next_fetch = now + interval
            threading.Thread(target=self._cal_fetch, daemon=True).start()

        # קליטת הקלטות שהבוט ב-Docker סיים והניח בתיקיית הפלט
        self._ingest_bot_outputs()

        if self.recording:
            # אם זו הקלטת יומן - עוצרים אוטומטית בזמן הסיום
            if self.cal_recording and now >= self.cal_end_ts:
                self._cal_stop()
        else:
            # קודם בודקים אם יש פגישה ביומן שצריך להצטרף אליה עכשיו
            if not self._cal_try_join():
                # אחרת - זיהוי ידני (המשתמש פתח פגישה בעצמו)
                app = detect.detect_meeting()
                if app and app != self.handled_app:
                    self.current_app = app
                    self.handled_app = app
                    self._show_prompt(app)
                elif not app:
                    self.handled_app = None

    def _meeting_soon(self) -> bool:
        """האם יש פגישה שמתחילה בתוך 6 הדקות הקרובות (או שכבר בחלון) -> לשלוף מהר."""
        nowdt = dt.datetime.now()
        for ev in self.cal_events:
            try:
                start = dt.datetime.fromisoformat(ev["start"])
            except Exception:
                continue
            secs = (start - nowdt).total_seconds()
            if -300 <= secs <= 360:
                return True
        return False

    # ---------- סנכרון יומן ----------
    def _cal_fetch(self):
        try:
            cfg = calendar_sync.load_config()
            if not cfg.get("enabled"):
                self.cal_events = []
                return
            self.cal_events = calendar_sync.fetch_upcoming(cfg)
            calendar_sync.log(f"fetch ok: {len(self.cal_events)} events: "
                              + "; ".join(f"{e['title']}@{e['start']}{'[link]' if e.get('url') else '[no-link]'}"
                                          for e in self.cal_events))
        except Exception as e:
            self.cal_events = []
            calendar_sync.log(f"fetch error: {type(e).__name__}: {e}")

    # ---------- בוט Docker: מעקב פגישות שנשלחו + קליטת הקלטות ----------
    def _bot_pending_path(self):
        return config.DATA_DIR / "bot_pending.json"

    def _bot_load_pending(self) -> dict:
        f = self._bot_pending_path()
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _bot_save_pending(self, d: dict):
        try:
            self._bot_pending_path().write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _bot_add_pending(self, key: str, title, project):
        d = self._bot_load_pending()
        d[key] = {"title": title or "", "project": project or config.DEFAULT_PROJECT, "at": time.time()}
        self._bot_save_pending(d)

    def _ingest_bot_outputs(self):
        """קולט קובצי webm שהבוט ב-Docker סיים, משייך לפגישה, ומעביר לפייפליין שלנו."""
        cfg = calendar_sync.load_config()
        if not cfg.get("use_docker_bot", True):
            return
        out_dir = Path(cfg.get("bot_output_dir") or r"C:\meetingbot-output")
        if not out_dir.exists():
            return
        for f in list(out_dir.glob("*.webm")):
            if f.name.startswith(".partial-"):
                continue
            try:
                st = f.stat()
                if st.st_size == 0 or (time.time() - st.st_mtime) < 3:
                    continue   # קובץ שעדיין נכתב
            except Exception:
                continue
            name = f.name
            key = name.split("__", 1)[0] if "__" in name else ""
            pending = self._bot_load_pending()
            meta = pending.get(key) or {}
            title = meta.get("title")
            project = meta.get("project") or config.DEFAULT_PROJECT
            try:
                rec_dir, _ = config.project_dirs(project)
                stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                dst = rec_dir / f"meeting_{stamp}.webm"
                shutil.move(str(f), str(dst))
            except Exception as e:
                calendar_sync.log(f"ingest move failed for {name}: {e}")
                continue
            # ניקוי sidecar
            side = out_dir / f"{name}.json"
            try:
                if side.exists():
                    side.unlink()
            except Exception:
                pass
            if key in pending:
                pending.pop(key, None)
                self._bot_save_pending(pending)
            calendar_sync.log(f"INGEST '{title}' -> {dst} (project={project})")
            # עיבוד דרך המערכת שלנו (תמלול + סיכום), עם שם הפגישה מהיומן
            self._launch_gui(str(dst), project, title)

    def _cal_try_join(self) -> bool:
        """אם יש פגישה ביומן שזמנה הגיע - מצטרף ומקליט. מחזיר True אם הצטרף."""
        cfg = calendar_sync.load_config()
        if not cfg.get("enabled") or not self.cal_events:
            return False
        now = dt.datetime.now()
        lead = dt.timedelta(seconds=cfg.get("lead_seconds", 60))
        handled = calendar_sync.load_handled()
        for ev in self.cal_events:
            if handled.get(ev["uid"]) == ev["start"]:
                continue
            try:
                start = dt.datetime.fromisoformat(ev["start"])
                end = dt.datetime.fromisoformat(ev["end"])
            except Exception:
                continue
            # חלון הצטרפות: מ-(התחלה פחות lead) ועד 5 דקות אחרי ההתחלה
            if (start - lead) <= now <= (start + dt.timedelta(minutes=5)) and now < end:
                calendar_sync.log(f"JOIN window hit -> '{ev['title']}' start={ev['start']} url={'yes' if ev.get('url') else 'NO'}")
                self._cal_join(ev, cfg, end)
                return True
            else:
                secs = (start - now).total_seconds()
                if 0 < secs < 600:   # פגישה בתוך 10 דקות - מתעדים כדי לראות שהבוט "רואה" אותה
                    calendar_sync.log(f"upcoming '{ev['title']}' in {int(secs)}s (start={ev['start']}, lead={lead.total_seconds():.0f}s)")
        return False

    def _cal_join(self, ev, cfg, end):
        project = cfg.get("project") or config.DEFAULT_PROJECT
        url = ev.get("url") or ""
        platform = meeting_bot.detect_platform(url)

        # --- מצב מועדף: בוט Docker (משתתף נפרד, כל הפלטפורמות כולל Teams) ---
        if cfg.get("use_docker_bot", True) and url and platform:
            stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
            key = bot_dispatch.make_key(ev["uid"], stamp)
            r = bot_dispatch.dispatch(
                cfg.get("bot_api_url", "http://localhost:3000"), platform, url,
                cfg.get("bot_name") or "Synthia Notetaker", key,
                cfg.get("bot_timezone", "Asia/Jerusalem"))
            if r.get("ok"):
                # מסמנים כטופל רק בהצלחה - דחייה (בוט תפוס/שגיאה) תנסה שוב בפול הבא
                calendar_sync.mark_handled(ev["uid"], ev.get("start", ""))
                self._bot_add_pending(key, ev.get("title"), project)
                calendar_sync.log(f"DOCKER dispatch OK '{ev['title']}' platform={platform} key={key}")
            else:
                calendar_sync.log(f"DOCKER dispatch FAILED '{ev['title']}': {r.get('error')} (יינסה שוב)")
            return

        use_bot = cfg.get("use_browser_bot", True) and url and platform
        calendar_sync.log(f"JOINING '{ev['title']}' -> {'BOT' if use_bot else 'open-app'} | url={url or '(none)'} | project={project}")
        calendar_sync.mark_handled(ev["uid"], ev.get("start", ""))   # מסמנים מיד כדי לא להצטרף פעמיים
        if use_bot:
            # בוט נפרד בדפדפן מצטרף כמשתתף עצמאי בשם הבוט
            try:
                self.bot = meeting_bot.MeetingBot(url, cfg.get("bot_name") or "Synthia Notetaker",
                                                  log=calendar_sync.log)
                self.bot.start()
            except Exception as e:
                calendar_sync.log(f"bot start failed: {e}")
                self.bot = None
        elif url:
            # נפילה: פתיחת הקישור באפליקציה/דפדפן ברירת המחדל
            try:
                os.startfile(url)
            except Exception:
                try:
                    import webbrowser; webbrowser.open(url)
                except Exception:
                    pass
        try:
            self.recorder = record.Recorder(capture_mic=True, capture_system=True)
            self.recorder.start()
        except Exception as e:
            self._show_error(str(e))
            return
        self.recording = True
        self.cal_recording = True
        self.cal_ev = ev
        self.sel_project = project
        self.rec_start = time.time()
        max_ts = time.time() + cfg.get("max_minutes", 180) * 60
        end_ts = end.timestamp()
        self.cal_end_ts = min(end_ts, max_ts) if end_ts > time.time() else max_ts
        self._show_recorder_bar(title=ev.get("title"), stop_cb=self._cal_stop)

    def _cal_stop(self):
        self.recording = False
        self.cal_recording = False
        # עוזבים את הפגישה (סוגרים את בוט הדפדפן)
        if self.bot:
            try:
                self.bot.stop()
            except Exception:
                pass
            self.bot = None
        project = getattr(self, "sel_project", config.DEFAULT_PROJECT)
        try:
            rec_dir, _ = config.project_dirs(project)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            out = str(rec_dir / f"meeting_{stamp}.wav")
            wav = self.recorder.stop(out)
        except Exception as e:
            self._show_error(str(e))
            return
        title = (self.cal_ev or {}).get("title")
        self._launch_gui(wav, project, title)
        self.cal_ev = None
        self._dismiss()

    # ---------- חלון קופץ ----------
    def _new_popup(self, w=300, h=120):
        if self.popup:
            self.popup.destroy()
        p = tk.Toplevel(self.root)
        p.overrideredirect(True)          # ללא מסגרת
        p.attributes("-topmost", True)    # תמיד עליון
        p.configure(bg=BG)
        # מיקום בפינה ימנית-עליונה
        sw = p.winfo_screenwidth()
        p.geometry(f"{w}x{h}+{sw - w - 24}+28")
        # מסגרת דקה
        p.configure(highlightbackground=ACCENT, highlightthickness=1)
        self.popup = p
        return p

    def _opt_menu(self, parent, var, values):
        om = tk.OptionMenu(parent, var, *values)
        om.config(bg=PAPER, fg=FG, font=(FONT, 9), highlightthickness=1,
                  highlightbackground=BORDER, relief="flat", borderwidth=0,
                  activebackground=ACCENT, activeforeground="white")
        om["menu"].config(bg=BG, fg=FG, font=(FONT, 9),
                          activebackground=ACCENT, activeforeground="white")
        om.pack(fill="x", padx=22)
        return om

    def _brand(self, p):
        bar = tk.Frame(p, bg=ACCENT, height=4); bar.pack(fill="x")
        top = tk.Frame(p, bg=BG); top.pack(fill="x", pady=(10, 0), padx=18)
        tk.Label(top, text="TranscriptAI", bg=BG, fg=FG,
                 font=(FONT, 11, "bold")).pack(side="right")

    def _show_prompt(self, app):
        p = self._new_popup(w=330, h=300)
        self._brand(p)
        tk.Label(p, text="🎙  זוהתה פגישה", bg=BG, fg=ACCENT,
                 font=(FONT, 13, "bold")).pack(pady=(8, 1))
        tk.Label(p, text=app, bg=BG, fg=MUTED, font=(FONT, 9)).pack()

        mics = record.list_input_devices() or ["ברירת מחדל"]
        self.mic_var = tk.StringVar(value=mics[0])
        tk.Label(p, text="מיקרופון", bg=BG, fg=MUTED, font=(FONT, 8)).pack(pady=(9, 2), anchor="e", padx=24)
        self._opt_menu(p, self.mic_var, mics)

        projs = storage.list_projects()
        self.proj_var = tk.StringVar(value=projs[0])
        tk.Label(p, text="פרויקט", bg=BG, fg=MUTED, font=(FONT, 8)).pack(pady=(7, 2), anchor="e", padx=24)
        self._opt_menu(p, self.proj_var, projs)

        self.mute_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="השתק מיקרופון (רק דוברים אחרים)", variable=self.mute_var,
                       bg=BG, fg=MUTED, selectcolor=PAPER, activebackground=BG,
                       activeforeground=FG, font=(FONT, 8), borderwidth=0,
                       highlightthickness=0).pack(pady=(9, 2))

        row = tk.Frame(p, bg=BG); row.pack(pady=10)
        tk.Button(row, text="● התחל הקלטה", command=self._start_recording,
                  bg=RED, fg="white", font=(FONT, 10, "bold"),
                  relief="flat", borderwidth=0, padx=14, pady=7, cursor="hand2").pack(side="left", padx=4)
        tk.Button(row, text="התעלם", command=self._dismiss,
                  bg=PAPER, fg=FG, font=(FONT, 10),
                  relief="flat", borderwidth=0, padx=12, pady=7, cursor="hand2").pack(side="left", padx=4)

    def _dismiss(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

    # ---------- הקלטה ----------
    def _start_recording(self):
        mic = self.mic_var.get() if hasattr(self, "mic_var") else None
        mute = self.mute_var.get() if hasattr(self, "mute_var") else False
        self.sel_project = self.proj_var.get() if hasattr(self, "proj_var") else config.DEFAULT_PROJECT
        try:
            self.recorder = record.Recorder(
                capture_mic=not mute, capture_system=True, mic_name=mic)
            self.recorder.start()
        except Exception as e:
            self._show_error(str(e))
            return
        self.recording = True
        self.rec_start = time.time()
        self._show_recorder_bar()

    def _show_recorder_bar(self, title=None, stop_cb=None):
        p = self._new_popup(w=300, h=170 if title else 150)
        self._brand(p)
        tk.Label(p, text="● מקליט פגישה...", bg=BG, fg=RED,
                 font=(FONT, 12, "bold")).pack(pady=(8, 2))
        if title:
            tk.Label(p, text=title[:38], bg=BG, fg=MUTED, font=(FONT, 9),
                     wraplength=270).pack()
        self.timer_lbl = tk.Label(p, text="00:00", bg=BG, fg=FG,
                                  font=(FONT, 18, "bold"))
        self.timer_lbl.pack()
        tk.Button(p, text="■ סיים וסכם", command=(stop_cb or self._stop_recording),
                  bg=GREEN, fg="white", font=(FONT, 10, "bold"),
                  relief="flat", borderwidth=0, padx=16, pady=7, cursor="hand2").pack(pady=12)
        self._tick()

    def _tick(self):
        if self.recording and self.popup:
            el = int(time.time() - self.rec_start)
            self.timer_lbl.config(text=f"{el // 60:02d}:{el % 60:02d}")
            self.root.after(500, self._tick)

    def _stop_recording(self):
        self.recording = False
        project = getattr(self, "sel_project", config.DEFAULT_PROJECT)
        try:
            rec_dir, _ = config.project_dirs(project)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            out = str(rec_dir / f"meeting_{stamp}.wav")
            wav = self.recorder.stop(out)
        except Exception as e:
            self._show_error(str(e))
            return
        # מעבר לחלון הראשי לעיבוד והצגה (בפרויקט הנבחר)
        self._launch_gui(wav, project)
        self._dismiss()

    def _launch_gui(self, wav, project=None, title=None):
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        cmd = [pyw, "-m", "src.gui", "--process", wav]
        if project:
            cmd += ["--project", project]
        if title:
            cmd += ["--title", title]
        subprocess.Popen(cmd, cwd=str(config.ROOT))

    def _show_error(self, msg):
        p = self._new_popup(w=320, h=110)
        tk.Label(p, text="⚠️ שגיאה", bg=BG, fg=RED, font=("Segoe UI", 12, "bold")).pack(pady=(14, 2))
        tk.Label(p, text=msg[:120], bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=290).pack()
        tk.Button(p, text="סגור", command=self._dismiss, bg=BORDER, fg=FG,
                  relief="flat", padx=10, pady=4).pack(pady=8)
        self.recording = False

    # ---------- אייקון מגש ----------
    def _make_icon_image(self):
        """יוצר אייקון עגול סגול עם נקודת מיקרופון."""
        img = Image.new("RGB", (64, 64), (26, 29, 39))
        d = ImageDraw.Draw(img)
        d.ellipse((10, 10, 54, 54), fill=(108, 92, 231))   # עיגול סגול
        d.ellipse((26, 20, 38, 38), fill=(255, 255, 255))  # גוף מיקרופון
        d.rectangle((30, 36, 34, 46), fill=(255, 255, 255))  # רגל
        return img

    def _open_main_app(self, *_):
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        subprocess.Popen([pyw, "-m", "src.gui"], cwd=str(config.ROOT))

    def _open_data_folder(self, *_):
        os.startfile(str(config.DATA_DIR))

    def _quit(self, *_):
        if self.tray:
            self.tray.stop()
        self.root.after(0, self.root.quit)

    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("🎙️ מערכת סיכום פגישות", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("הקלטה ידנית (פתח אפליקציה)", self._open_main_app),
            pystray.MenuItem("תיקיית הסיכומים", self._open_data_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("יציאה", self._quit),
        )
        self.tray = pystray.Icon("meeting_summary", self._make_icon_image(),
                                 "מערכת סיכום פגישות — פעיל", menu)
        self.tray.run_detached()   # רץ ב-thread נפרד, לא חוסם את tkinter

    def run(self):
        self._start_tray()
        self.root.mainloop()


def _ensure_single_instance():
    """מונע ריצה כפולה: תופס פורט מקומי. אם תפוס - כבר רץ, יוצאים."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 50573))  # פורט שרירותי ייחודי למערכת
    except OSError:
        sys.exit(0)   # כבר רץ מופע אחר
    return s  # שומרים reference כדי שהפורט יישאר תפוס


def main():
    _lock = _ensure_single_instance()  # noqa: F841 - חייב להישאר בחיים
    Watcher().run()


if __name__ == "__main__":
    main()
