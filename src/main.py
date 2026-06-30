"""סקריפט בדיקה לקו הליבה: אודיו -> תמלול -> סיכום.

שימוש:
    python -m src.main <נתיב לקובץ אודיו>
    python -m src.main --transcribe-only <נתיב>   # רק תמלול, בלי Gemini
"""
import sys
import argparse

# הכרחת פלט UTF-8 בקונסול של Windows (כדי לתמוך בעברית ובאימוג'ים)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from . import transcribe, summarize


def main():
    parser = argparse.ArgumentParser(description="תמלול וסיכום פגישה")
    parser.add_argument("audio", help="נתיב לקובץ אודיו (wav/mp3/m4a/...)")
    parser.add_argument("--transcribe-only", action="store_true",
                        help="רק תמלול, ללא סיכום Gemini")
    args = parser.parse_args()

    print(f"\n🎙️  מתמלל: {args.audio}")
    print("    (טעינת המודל בפעם הראשונה עשויה לקחת זמן - מוריד משקלים)\n")

    def show(seg):
        print(f"  [{seg.start:6.1f}s] {seg.text}")

    result = transcribe.transcribe(args.audio, on_progress=show)

    print("\n" + "=" * 60)
    print(f"✅ תמלול הושלם (שפה שזוהתה: {result.language})")
    print("=" * 60)

    if args.transcribe_only:
        return

    print("\n🤖 מסכם עם Gemini...\n")
    try:
        s = summarize.summarize(result.text)
    except RuntimeError as e:
        print(f"❌ הסיכום נכשל: {e}")
        print("\n📝 התמלול המלא נשמר בכל מקרה:\n")
        print(result.text)
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
