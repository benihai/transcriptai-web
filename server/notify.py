"""
שליחת סיכום פגישה למייל בסוף pipeline.

הגדרה בקובץ .env על השרת:
    NOTIFY_EMAIL=bnim4444@gmail.com          # לאן לשלוח
    NOTIFY_SMTP_USER=bot@gmail.com           # ממי (חשבון Gmail ששולח)
    NOTIFY_SMTP_PASS=xxxx xxxx xxxx xxxx    # App Password של ה-Gmail השולח
                                             # (לא סיסמת החשבון הרגילה!)
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _build_html(data: dict) -> str:
    summary = data.get("summary", {})
    title = summary.get("title") or data.get("id", "פגישה")
    text = summary.get("summary") or ""
    topics: list = summary.get("topics") or []
    key_points: list = summary.get("key_points") or []
    decisions: list = summary.get("decisions") or []
    action_items: list = summary.get("action_items") or []
    project = data.get("project") or ""

    def ul(items):
        return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>" if items else ""

    topics_html = ""
    for t in topics:
        pts = "".join(f"<li>{p}</li>" for p in (t.get("points") or []))
        topics_html += f"<h3 style='color:#2563EB;margin:16px 0 4px'>{t.get('title','')}</h3><ul>{pts}</ul>"

    tasks_html = ""
    for a in action_items:
        task = a.get("task", "")
        owner = f" — {a['owner']}" if a.get("owner") else ""
        due = f" ({a['due']})" if a.get("due") else ""
        tasks_html += f"<li>{task}{owner}{due}</li>"

    return f"""
<div dir="rtl" style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;color:#1f2937">
  <div style="background:#2563EB;padding:16px 24px;border-radius:8px 8px 0 0">
    <span style="color:white;font-size:20px;font-weight:bold">TranscriptAI ✦</span>
    <span style="color:#93c5fd;margin-right:12px;font-size:13px">סיכום פגישה אוטומטי</span>
  </div>
  <div style="background:#fff;border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 8px 8px">
    <h2 style="margin:0 0 4px;color:#111827">{title}</h2>
    <p style="color:#6b7280;font-size:13px;margin:0 0 20px">פרויקט: {project}</p>

    <h3 style="color:#374151;border-bottom:2px solid #e5e7eb;padding-bottom:6px">תקציר</h3>
    <p style="line-height:1.7">{text}</p>

    {"<h3 style='color:#374151;border-bottom:2px solid #e5e7eb;padding-bottom:6px'>נקודות עיקריות</h3>" + ul(key_points) if key_points else ""}
    {"<h3 style='color:#374151;border-bottom:2px solid #e5e7eb;padding-bottom:6px'>נושאים שנדונו</h3>" + topics_html if topics_html else ""}
    {"<h3 style='color:#374151;border-bottom:2px solid #e5e7eb;padding-bottom:6px'>החלטות</h3>" + ul(decisions) if decisions else ""}
    {"<h3 style='color:#2563EB;border-bottom:2px solid #e5e7eb;padding-bottom:6px'>משימות לביצוע</h3><ul>" + tasks_html + "</ul>" if tasks_html else ""}
  </div>
  <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:12px">נשלח אוטומטית על ידי TranscriptAI</p>
</div>
"""


def send_summary_email(data: dict):
    to_addr = os.getenv("NOTIFY_EMAIL", "").strip()
    smtp_user = os.getenv("NOTIFY_SMTP_USER", "").strip()
    smtp_pass = os.getenv("NOTIFY_SMTP_PASS", "").strip()

    if not all([to_addr, smtp_user, smtp_pass]):
        raise ValueError(
            "חסרות הגדרות מייל ב-.env: NOTIFY_EMAIL, NOTIFY_SMTP_USER, NOTIFY_SMTP_PASS"
        )

    summary = data.get("summary", {})
    title = summary.get("title") or "סיכום פגישה"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"סיכום פגישה: {title}"
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(_build_html(data), "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, [to_addr], msg.as_bytes())
