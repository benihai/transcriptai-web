"""אפליקציית ביניז בשורת פקודה: הקלטה חיה -> תמלול -> סיכום.

שימוש:
    python -m src.app_cli              # מיקרופון בלבד
    python -m src.app_cli --system     # מיקרופון + קול מערכת (לפגישות Zoom/Meet)

זרימה: לוחצים Enter כדי להתחיל, מדברים, לוחצים Enter שוב כדי לעצור.
"""
import sys
import time
import threading

sys.stdout.reconfigure(encoding="utf-8")

from . import record, transcribe, summarize


def main():
    capture_system = "--system" in sys.argv
    sources = "מיקרופון" + (" + קול מערכת" if capture_system else "")

    print("=" * 60)
    print("🎬  מערכת סיכום פגישות — מצב הקלטה חיה")
    print("=" * 60)
    print(f"מקורות: {sources}")
    input("\n▶️  לחץ Enter כדי להתחיל להקליט...")

    r = record.Recorder(capture_mic=True, capture_system=capture_system)
    r.start()

    # מציג טיימר חי עד שלוחצים Enter
    stop_flag = threading.Event()

    def wait_enter():
        input()
        stop_flag.set()

    threading.Thread(target=wait_enter, daemon=True).start()
    print("🔴 מקליט... (לחץ Enter כדי לעצור)")
    while not stop_flag.is_set():
        print(f"\r   ⏱️  {r.elapsed():5.1f} שניות", end="", flush=True)
        time.sleep(0.2)

    print("\n⏹️  עוצר ושומר...")
    path = r.stop()
    print(f"💾 נשמר: {path}\n")

    print("📝 מתמלל (עשוי לקחת זמן על CPU)...\n")
    result = transcribe.transcribe(path, on_progress=lambda s: print(f"  [{s.start:6.1f}s] {s.text}"))
    print(f"\n✅ תמלול הושלם.\n")

    if not result.text.strip():
        print("⚠️  לא זוהה דיבור בהקלטה. בדוק שהמיקרופון עובד ושדיברת.")
        return

    print("🤖 מסכם עם Gemini...\n")
    try:
        s = summarize.summarize(result.text)
    except RuntimeError as e:
        print(f"❌ הסיכום נכשל: {e}\n\n📝 התמלול:\n{result.text}")
        return

    print("=" * 60)
    print(f"📋 {s.title}")
    print("=" * 60)
    print(f"\n{s.summary}\n")
    if s.key_points:
        print("🔹 נקודות עיקריות:")
        for p in s.key_points:
            print(f"   • {p}")
        print()
    if s.decisions:
        print("✔️  החלטות:")
        for d in s.decisions:
            print(f"   • {d}")
        print()
    if s.action_items:
        print("📌 משימות לביצוע:")
        for a in s.action_items:
            extra = []
            if a.owner:
                extra.append(f"אחראי: {a.owner}")
            if a.due:
                extra.append(f"יעד: {a.due}")
            suffix = f"  ({', '.join(extra)})" if extra else ""
            print(f"   ☐ {a.task}{suffix}")
        print()


if __name__ == "__main__":
    main()
