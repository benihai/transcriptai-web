"""
TranscriptAI Server Watcher — רץ על שרת Linux (VPS).

מה הוא עושה:
  - קורא יומן מג'ימייל דרך IMAP (calendar_sync)
  - שולח בוט Docker להצטרף לפגישות (bot_dispatch)
  - קולט קבצי webm שהבוט סיים להקליט (_ingest_bot_outputs)
  - מריץ pipeline: תמלול + סיכום + שמירה + מייל

מה הוא לא עושה (רק בגרסת Windows):
  - אין tray icon (pystray)
  - אין חלון tkinter
  - אין זיהוי ידני (Registry / detect.py)
  - אין הקלטה ידנית (soundcard/sounddevice)

הרצה:
    python -m server.watcher_server

כ-systemd service:
    systemctl start transcriptai
"""
import json
import time
import shutil
import threading
import datetime as dt
from pathlib import Path

from src import config, calendar_sync, bot_dispatch, meeting_bot

POLL_INTERVAL = 2   # שניות בין כל בדיקה


class ServerWatcher:
    def __init__(self):
        self.cal_events = []
        self._cal_next_fetch = 0.0
        # cooldown per-UID: אחרי 409 (בוט עסוק) לא ננסה שוב לפחות 90 שניות
        self._dispatch_cooldown: dict[str, float] = {}

    # ---------- לוג ----------

    def _log(self, msg: str):
        calendar_sync.log(msg)

    # ---------- לולאה ראשית ----------

    def run(self):
        self._log("TranscriptAI Server Watcher started")
        while True:
            try:
                self._poll_body()
            except Exception as e:
                self._log(f"poll error: {type(e).__name__}: {e}")
            time.sleep(POLL_INTERVAL)

    def _poll_body(self):
        now = time.time()
        # שליפת יומן כל 12-30 שניות (מהיר יותר כשפגישה מתקרבת)
        if now >= self._cal_next_fetch:
            interval = 12 if self._meeting_soon() else 30
            self._cal_next_fetch = now + interval
            threading.Thread(target=self._cal_fetch, daemon=True).start()

        self._ingest_bot_outputs()
        self._cal_try_join()
        self._manual_try_join()

    # ---------- סנכרון יומן ----------

    def _meeting_soon(self) -> bool:
        nowdt = dt.datetime.now()
        for ev in self.cal_events:
            try:
                start = dt.datetime.fromisoformat(ev["start"])
            except Exception:
                continue
            if -300 <= (start - nowdt).total_seconds() <= 360:
                return True
        return False

    def _cal_fetch(self):
        try:
            cfg = calendar_sync.load_config()
            if not cfg.get("enabled"):
                self.cal_events = []
                return
            self.cal_events = calendar_sync.fetch_upcoming(cfg)
            self._log(f"fetch ok: {len(self.cal_events)} events: "
                      + "; ".join(f"{e['title']}@{e['start']}{'[link]' if e.get('url') else '[no-link]'}"
                                  for e in self.cal_events))
        except Exception as e:
            self.cal_events = []
            self._log(f"fetch error: {type(e).__name__}: {e}")

    # ---------- bot_pending ----------

    def _bot_pending_path(self) -> Path:
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
            # ניקוי אוטומטי — מחק רשומות ישנות מ-6 שעות+
            cutoff = time.time() - 6 * 3600
            d = {k: v for k, v in d.items() if v.get("at", 0) > cutoff}
            self._bot_pending_path().write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _bot_add_pending(self, key: str, title, project):
        d = self._bot_load_pending()
        d[key] = {"title": title or "", "project": project or config.DEFAULT_PROJECT, "at": time.time()}
        self._bot_save_pending(d)

    # ---------- קליטת הקלטות מהבוט ----------

    def _ingest_bot_outputs(self):
        cfg = calendar_sync.load_config()
        if not cfg.get("use_docker_bot", True):
            return
        out_dir = Path(cfg.get("bot_output_dir") or "/home/ubuntu/meetingbot-output")
        if not out_dir.exists():
            return
        for f in list(out_dir.glob("*.webm")):
            if f.name.startswith(".partial-"):
                continue
            try:
                st = f.stat()
                if st.st_size == 0 or (time.time() - st.st_mtime) < 3:
                    continue
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
                self._log(f"ingest move failed for {name}: {e}")
                continue

            # מחיקת sidecar JSON של הבוט
            try:
                side = out_dir / f"{name}.json"
                if side.exists():
                    side.unlink()
            except Exception:
                pass

            if key in pending:
                pending.pop(key, None)
                self._bot_save_pending(pending)

            participants = meta.get("participants") or []
            self._log(f"INGEST '{title}' -> {dst} (project={project}, participants={participants})")
            # pipeline רץ ב-thread נפרד כדי שלא לחסום את לולאת הבדיקה
            threading.Thread(
                target=self._run_pipeline, args=(str(dst), project, title, participants), daemon=False
            ).start()

    def _run_pipeline(self, wav_path: str, project: str, title: str | None,
                      participants: list | None = None):
        from server.pipeline import run_pipeline
        run_pipeline(wav_path, project, title, participants=participants, log=self._log)

    # ---------- הצטרפות אוטומטית לפגישה ----------

    def _cal_try_join(self) -> bool:
        cfg = calendar_sync.load_config()
        if not cfg.get("enabled") or not self.cal_events:
            return False
        now = dt.datetime.now()
        lead = dt.timedelta(seconds=cfg.get("lead_seconds", 120))
        handled = calendar_sync.load_handled()
        for ev in self.cal_events:
            if handled.get(ev["uid"]) == ev["start"]:
                continue
            try:
                start = dt.datetime.fromisoformat(ev["start"])
                end = dt.datetime.fromisoformat(ev["end"])
            except Exception:
                continue
            if (start - lead) <= now <= (start + dt.timedelta(minutes=5)) and now < end:
                self._log(f"JOIN window hit -> '{ev['title']}' start={ev['start']} url={'yes' if ev.get('url') else 'NO'}")
                self._cal_join(ev, cfg, end)
                return True
            secs = (start - now).total_seconds()
            if 0 < secs < 600:
                self._log(f"upcoming '{ev['title']}' in {int(secs)}s")
        return False

    def _cal_join(self, ev, cfg, end):
        project = cfg.get("project") or config.DEFAULT_PROJECT
        url = ev.get("url") or ""
        platform = meeting_bot.detect_platform(url)

        if cfg.get("use_docker_bot", True) and url and platform:
            uid = ev["uid"]

            # cooldown: אחרי 409 (בוט עסוק) מחכים 90 שניות לפני ניסיון נוסף
            cooldown_until = self._dispatch_cooldown.get(uid, 0)
            if time.time() < cooldown_until:
                return  # בשקט — לא מציפים את הלוג

            stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
            key = bot_dispatch.make_key(uid, stamp)
            r = bot_dispatch.dispatch(
                cfg.get("bot_api_url", "http://localhost:3000"), platform, url,
                cfg.get("bot_name") or "TranscriptAI Bot", key,
                cfg.get("bot_timezone", "Asia/Jerusalem"))
            if r.get("ok"):
                calendar_sync.mark_handled(uid, ev.get("start", ""))
                self._bot_add_pending(key, ev.get("title"), project)
                self._log(f"DOCKER dispatch OK '{ev['title']}' platform={platform} key={key}")
                self._dispatch_cooldown.pop(uid, None)
            else:
                err = r.get("error", "")
                is_409 = "409" in str(err) or "being processed" in str(err).lower()
                if is_409:
                    self._dispatch_cooldown[uid] = time.time() + 90
                    self._log(f"DOCKER dispatch 409 '{ev['title']}' — בוט עסוק, ניסיון בעוד 90s")
                else:
                    self._log(f"DOCKER dispatch FAILED '{ev['title']}': {err}")
                    self._save_alert(ev.get("title","פגישה"), platform, str(err))
            return

        # על שרת: אין דפדפן/Playwright — רק Docker bot נתמך
        self._log(f"SKIP join '{ev['title']}' — Docker bot disabled or no URL/platform (url={url!r})")
        calendar_sync.mark_handled(ev["uid"], ev.get("start", ""))

    def _save_alert(self, title: str, platform: str, error: str):
        """שומר התראת כישלון בוט לקובץ שהאתר מציג."""
        try:
            alerts_file = config.DATA_DIR / "bot_alerts.json"
            alerts = json.loads(alerts_file.read_text(encoding="utf-8")) if alerts_file.exists() else []
            alerts.insert(0, {
                "title": title, "platform": platform, "error": error,
                "time": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "type": "error"
            })
            alerts_file.write_text(json.dumps(alerts[:50], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---------- פגישות ידניות ----------

    def _manual_meetings_file(self) -> Path:
        return config.DATA_DIR / "manual_meetings.json"

    def _load_manual_meetings(self) -> list:
        f = self._manual_meetings_file()
        if not f.exists(): return []
        try: return json.loads(f.read_text(encoding="utf-8"))
        except: return []

    def _save_manual_meetings(self, meetings: list):
        self._manual_meetings_file().write_text(
            json.dumps(meetings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _manual_try_join(self):
        meetings = self._load_manual_meetings()
        if not meetings: return
        now = dt.datetime.now()
        cfg = calendar_sync.load_config()
        lead = dt.timedelta(seconds=cfg.get("lead_seconds", 120))
        updated = []
        for m in meetings:
            if m.get("handled"):
                updated.append(m)
                continue
            try:
                start = dt.datetime.fromisoformat(m["start"])
                end_str = m.get("end")
                end = dt.datetime.fromisoformat(end_str) if end_str else start + dt.timedelta(hours=1)
            except Exception:
                updated.append(m)
                continue
            if (start - lead) <= now <= (start + dt.timedelta(minutes=5)) and now < end:
                url = m.get("url", "")
                platform = meeting_bot.detect_platform(url)
                self._log(f"MANUAL JOIN '{m.get('title','ללא שם')}' platform={platform or 'unknown'} url={url}")
                if url and platform:
                    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
                    key = bot_dispatch.make_key(m["id"], stamp)
                    r = bot_dispatch.dispatch(
                        cfg.get("bot_api_url", "http://localhost:3000"), platform, url,
                        cfg.get("bot_name") or "TranscriptAI Bot", key,
                        cfg.get("bot_timezone", "Asia/Jerusalem"))
                    if r.get("ok"):
                        self._bot_add_pending(key, m.get("title"), m.get("project", config.DEFAULT_PROJECT))
                        self._log(f"MANUAL dispatch OK key={key}")
                    else:
                        self._log(f"MANUAL dispatch FAILED: {r.get('error')}")
                else:
                    self._log(f"MANUAL SKIP — פלטפורמה לא נתמכת ({url}). השתמש בהקלטה ידנית מהאתר.")
                m["handled"] = True
            updated.append(m)
        self._save_manual_meetings(updated)


def main():
    ServerWatcher().run()


if __name__ == "__main__":
    main()
