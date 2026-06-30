"""הקלטת אודיו: מיקרופון ו/או קול מערכת (loopback), מיזוג לקובץ WAV אחד.

שימוש כבדיקה:
    python -m src.record 5                # מקליט 5 שניות מהמיקרופון
    python -m src.record 5 --system       # מיקרופון + קול מערכת
    python -m src.record 5 --system-only   # רק קול מערכת
"""
from __future__ import annotations
import os
import sys
import time
import tempfile
import threading
import subprocess
import datetime as dt
from pathlib import Path

import numpy as np
import soundcard as sc

from . import config

# דגל ליצירת תהליך ffmpeg ללא חלון קונסול (Windows)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SAMPLE_RATE = 16000   # 16kHz מספיק לדיבור ומתאים ל-Whisper
CHANNELS = 1          # מונו


class Recorder:
    """מקליט מיקרופון ו/או קול מערכת בו-זמנית, וממזג לקובץ WAV.

    שימוש:
        r = Recorder(capture_mic=True, capture_system=True)
        r.start()
        ... (הפגישה מתנהלת) ...
        path = r.stop()   # מחזיר נתיב לקובץ WAV
    """

    def __init__(self, capture_mic: bool = True, capture_system: bool = False,
                 mic_name: str | None = None, capture_video: bool = False):
        self.capture_mic = capture_mic
        self.capture_system = capture_system
        self.mic_name = mic_name      # שם התקן מיקרופון ספציפי (None = ברירת מחדל)
        self.capture_video = capture_video   # הקלטת מסך (וידאו) במקביל לאודיו
        self._running = False
        self._paused = False
        self._threads: list[threading.Thread] = []
        self._mic_chunks: list[np.ndarray] = []
        self._sys_chunks: list[np.ndarray] = []
        self._start_time: float | None = None
        self._paused_total = 0.0      # סך זמן ההשהיות (שניות)
        self._pause_started: float | None = None
        self._video_proc: subprocess.Popen | None = None
        self._video_tmp: str | None = None

    def output_ext(self) -> str:
        """סיומת קובץ הפלט: mp4 אם מקליטים וידאו, אחרת wav."""
        return ".mp4" if self.capture_video else ".wav"

    def _record_source(self, mic_obj, sink: list):
        """לולאת הקלטה ממקור בודד עד שעוצרים."""
        # אתחול COM ל-thread הנוכחי (נדרש ל-WASAPI כשמריצים מתוך GUI)
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        except Exception:
            pass
        with mic_obj.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS) as rec:
            while self._running:
                data = rec.record(numframes=SAMPLE_RATE // 10)  # מקטעי 0.1 שניות
                # בזמן השהיה ממשיכים לקרוא מהמכשיר (כדי לא להציף buffer) אך לא שומרים
                if not self._paused:
                    sink.append(data.copy())

    def start(self):
        if self._running:
            return
        self._running = True
        self._start_time = time.time()

        if self.capture_mic:
            mic = _find_microphone(self.mic_name)
            t = threading.Thread(target=self._record_source, args=(mic, self._mic_chunks), daemon=True)
            t.start()
            self._threads.append(t)

        if self.capture_system:
            # לכידת מה שיוצא לרמקול (loopback) = קול המשתתפים האחרים
            loopback = sc.get_microphone(sc.default_speaker().name, include_loopback=True)
            t = threading.Thread(target=self._record_source, args=(loopback, self._sys_chunks), daemon=True)
            t.start()
            self._threads.append(t)

        if self.capture_video:
            self._start_video()

    def _start_video(self):
        """מתחיל הקלטת מסך (וידאו בלבד) לקובץ זמני באמצעות ffmpeg gdigrab."""
        config.ensure_ffmpeg_on_path()
        self._video_tmp = str(Path(tempfile.gettempdir()) / f"synthia_screen_{int(time.time())}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "gdigrab", "-framerate", "10", "-i", "desktop",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            self._video_tmp,
        ]
        try:
            self._video_proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
        except Exception as e:
            print(f"⚠️  הקלטת מסך נכשלה: {e}")
            self._video_proc = None
            self.capture_video = False

    def _stop_video(self) -> str | None:
        """עוצר את הקלטת המסך בעדינות (שולח 'q' ל-ffmpeg) ומחזיר נתיב הקובץ הזמני."""
        p = self._video_proc
        if not p:
            return None
        try:
            if p.stdin:
                p.stdin.write(b"q")
                p.stdin.flush()
        except Exception:
            pass
        try:
            p.wait(timeout=8)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
        self._video_proc = None
        return self._video_tmp

    def pause(self):
        if self._running and not self._paused:
            self._paused = True
            self._pause_started = time.time()

    def resume(self):
        if self._running and self._paused:
            self._paused = False
            if self._pause_started:
                self._paused_total += time.time() - self._pause_started
                self._pause_started = None

    @property
    def is_paused(self) -> bool:
        return self._paused

    def elapsed(self) -> float:
        """זמן הקלטה נטו (ללא זמן ההשהיות)."""
        if not self._start_time:
            return 0.0
        total = time.time() - self._start_time - self._paused_total
        if self._paused and self._pause_started:
            total -= time.time() - self._pause_started
        return max(0.0, total)

    def stop(self, out_path: str | None = None) -> str:
        """עוצר את ההקלטה, ממזג את המקורות ושומר. מחזיר נתיב.

        ללא וידאו -> שומר WAV. עם וידאו -> ממזג את וידאו המסך עם האודיו לקובץ MP4 אחד.
        """
        self._running = False
        for t in self._threads:
            t.join(timeout=2)

        video_file = self._stop_video() if self.capture_video else None

        tracks = []
        if self.capture_mic and self._mic_chunks:
            tracks.append(np.concatenate(self._mic_chunks, axis=0).flatten())
        if self.capture_system and self._sys_chunks:
            tracks.append(np.concatenate(self._sys_chunks, axis=0).flatten())

        if out_path is None:
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = str(config.RECORDINGS_DIR / f"meeting_{stamp}{self.output_ext()}")

        # אם אין אודיו אך יש וידאו - שומרים את הווידאו לבדו
        if not tracks:
            if video_file and Path(video_file).exists():
                import shutil
                shutil.move(video_file, out_path)
                return out_path
            raise RuntimeError("לא נקלט אודיו. בדוק הרשאות מיקרופון / מקור קול.")

        # מיזוג: חיתוך לאורך המשותף הקצר וסכימה
        min_len = min(len(t) for t in tracks)
        mixed = np.zeros(min_len, dtype=np.float32)
        for t in tracks:
            mixed += t[:min_len]
        mixed /= len(tracks)  # ממוצע כדי למנוע עיוות (clipping)

        # נרמול עדין למניעת שקט/רוויה
        peak = np.max(np.abs(mixed)) or 1.0
        mixed = (mixed / peak) * 0.95
        audio_i16 = (mixed * 32767).astype(np.int16)

        # עם וידאו: כותבים WAV זמני וממזגים אותו עם הווידאו ל-MP4
        if video_file and Path(video_file).exists():
            tmp_wav = video_file + ".wav"
            _write_wav(tmp_wav, audio_i16, SAMPLE_RATE)
            ok = self._mux(video_file, tmp_wav, out_path)
            for f in (video_file, tmp_wav):
                try:
                    os.remove(f)
                except Exception:
                    pass
            if ok and Path(out_path).exists():
                return out_path
            # מיזוג נכשל - נופלים חזרה לשמירת WAV בלבד
            out_path = str(Path(out_path).with_suffix(".wav"))

        _write_wav(out_path, audio_i16, SAMPLE_RATE)
        return out_path

    def _mux(self, video_path: str, audio_path: str, out_path: str) -> bool:
        """ממזג וידאו (ללא קול) עם קובץ אודיו ל-MP4 אחד באמצעות ffmpeg."""
        config.ensure_ffmpeg_on_path()
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path, "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            out_path,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW, timeout=120)
            return True
        except Exception as e:
            print(f"⚠️  מיזוג וידאו+אודיו נכשל: {e}")
            return False


def _find_microphone(mic_name: str | None):
    """מחזיר התקן מיקרופון לפי שם, או ברירת המחדל אם לא נמצא/לא צוין."""
    if mic_name:
        for m in sc.all_microphones(include_loopback=False):
            if m.name == mic_name:
                return m
    return sc.default_microphone()


def list_input_devices() -> list[str]:
    """רשימת שמות המיקרופונים הזמינים."""
    try:
        return [m.name for m in sc.all_microphones(include_loopback=False)]
    except Exception:
        return []


def _write_wav(path: str, data: np.ndarray, samplerate: int):
    """כתיבת WAV באמצעות מודול wave הסטנדרטי (ללא תלות חיצונית)."""
    import wave
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)        # 16-bit
        wf.setframerate(samplerate)
        wf.writeframes(data.tobytes())


def _cli():
    sys.stdout.reconfigure(encoding="utf-8")
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    capture_system = "--system" in sys.argv or "--system-only" in sys.argv
    capture_mic = "--system-only" not in sys.argv

    src = []
    if capture_mic:
        src.append("מיקרופון")
    if capture_system:
        src.append("קול מערכת")
    print(f"🔴 מקליט {seconds:.0f} שניות ({' + '.join(src)})... דבר/הפעל קול עכשיו!")

    r = Recorder(capture_mic=capture_mic, capture_system=capture_system)
    r.start()
    time.sleep(seconds)
    path = r.stop()
    print(f"✅ נשמר: {path}")


if __name__ == "__main__":
    _cli()
