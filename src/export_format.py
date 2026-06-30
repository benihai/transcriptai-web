"""בניית סיכום פגישה בפורמט DIT (HTML/CSS) וייצוא ל-PDF דרך Chromium (Playwright).

הרינדור ב-HTML נותן התאמה מדויקת לעיצוב המקורי + תמיכת RTL מלאה.
"""
from __future__ import annotations
import sys
import base64
import html as _html
import threading
import subprocess
from pathlib import Path

from . import config

LOGO_PATH = config.ROOT / "assets" / "logo.png"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _install_chromium() -> None:
    """מוריד את דפדפן Chromium של Playwright (כולל ה-headless shell). פעולה חד-פעמית."""
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW, timeout=600,
    )


def _launch_chromium(p):
    """מפעיל Chromium ל-PDF. אם הדפדפן חסר (אחרי עדכון Playwright) - מתקין אוטומטית
    ומנסה שוב; כמוצא אחרון נופל ל-Edge המובנה של Windows (ללא הורדה)."""
    try:
        return p.chromium.launch()
    except Exception as e:
        msg = str(e)
        if "Executable doesn" in msg or "playwright install" in msg or "download new browsers" in msg:
            try:
                _install_chromium()
                return p.chromium.launch()
            except Exception:
                pass
            # מוצא אחרון: Microsoft Edge (קיים בכל Windows 11, לא דורש הורדה)
            try:
                return p.chromium.launch(channel="msedge")
            except Exception:
                raise RuntimeError(
                    "יצירת ה-PDF נכשלה: דפדפן ה-PDF (Chromium) חסר ולא הצלחתי להתקין אותו אוטומטית. "
                    "פתח שורת פקודה בתיקיית הפרויקט והרץ: .venv\\Scripts\\playwright install chromium"
                ) from e
        raise


def normalize_dashes(s) -> str:
    """ממיר מקפים ארוכים (em/en dash) ותווי מקף מיוחדים למקף רגיל קצר."""
    text = str(s or "")
    for ch in ("—", "–", "―", "‒", "−"):  # — – ― ‒ −
        text = text.replace(ch, "-")
    return text


def _esc(s) -> str:
    return _html.escape(normalize_dashes(s))


def _img_data_uri(path) -> str:
    """מחזיר data-URI לתמונה (PNG/JPG/WEBP) או מחרוזת ריקה אם אין."""
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    ext = p.suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/webp" if ext == ".webp" else "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _logo_data_uri() -> str:
    return _img_data_uri(LOGO_PATH)


def _render_html(data: dict, client_logo_uri: str = "") -> str:
    logo = _logo_data_uri()
    logo_html = (f'<img class="logo" src="{logo}">' if logo
                 else '<div class="logo-txt">DESIGN IT RIGHT<br><b>DIT</b></div>')

    # לוגו הלקוח: אם הועלה לפרויקט - מוצג; אחרת נשארת מסגרת מקווקוות "לוגו הלקוח"
    client_html = (f'<img class="client-logo-img" src="{client_logo_uri}">' if client_logo_uri
                   else '<div class="client-logo">לוגו הלקוח</div>')

    # משתתפים
    parts = data.get("participants", []) or []
    part_items = "".join(
        f"<li>{_esc(' — '.join(x for x in [p.get('name',''), p.get('role',''), p.get('company','')] if x))}</li>"
        for p in parts) or "<li>—</li>"

    # ממצאים
    findings_html = ""
    for i, f in enumerate(data.get("findings", []) or [], 1):
        boxes = ""
        if f.get("responsible"):
            boxes += f'<span class="lbl">אחראי</span><span class="box">{_esc(f["responsible"])}</span>'
        if f.get("due"):
            boxes += f'<span class="lbl">מועד</span><span class="box">{_esc(f["due"])}</span>'
        note = (f'<div class="note"><span class="note-lbl">הערה</span>{_esc(f.get("note"))}</div>'
                if f.get("note") else "")
        findings_html += f"""
        <div class="finding">
          <div class="f-head">
            <span class="badge">{i:02d}</span>
            <span class="f-desc">{_esc(f.get('description'))}</span>
          </div>
          {f'<div class="f-meta">{boxes}</div>' if boxes else ''}
          {note}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=David+Libre:wght@400;500;700&display=swap');
  /* השוליים נקבעים ע"י Playwright (page.pdf margin) - כך הכותרת התחתונה חוזרת נכון בכל עמוד */
  @page {{ size:A4; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  /* פונט אחיד הנהוג במסמכים רשמיים בעברית */
  body {{ font-family:"David Libre","David","Frank Ruehl CLM","Times New Roman",serif;
         color:#222; font-size:13px; line-height:1.5; }}
  .topbar {{ height:6px; border-radius:3px; margin-bottom:14px;
            background:linear-gradient(90deg,#1a1a1a 0 55%,#8BC34A 55% 100%); }}

  header {{ display:flex; justify-content:space-between; align-items:center;
           padding-bottom:16px; border-bottom:1px solid #ddd; }}
  .client-logo {{ width:210px; height:78px; border:1px dashed #bbb; border-radius:4px;
                 display:flex; align-items:center; justify-content:center; color:#aaa; font-size:12px; }}
  .client-logo-img {{ max-width:210px; max-height:78px; object-fit:contain; }}
  .logo {{ height:64px; }}
  .logo-txt {{ text-align:left; font-weight:700; font-size:20px; color:#7CB342; }}

  .title-block {{ text-align:center; margin:24px 0 20px; }}
  .eyebrow {{ color:#8BC34A; font-size:12px; font-weight:700; letter-spacing:6px; }}
  .title {{ font-size:38px; font-weight:700; margin:6px 0 0; }}
  .title-underline {{ width:64px; height:4px; background:#8BC34A; margin:12px auto 0; border-radius:2px; }}

  table.meta {{ width:100%; border-collapse:collapse; }}
  table.meta tr {{ break-inside:avoid; }}
  table.meta td {{ border:1px solid #ddd; padding:11px 14px; vertical-align:middle; }}
  td.lbl {{ background:#f6f6f3; color:#999; font-size:11px; width:14%; white-space:nowrap; }}
  td.val {{ font-size:13.5px; }}
  table.meta ul {{ list-style:none; }}
  table.meta li {{ position:relative; padding-right:16px; line-height:1.9; }}
  table.meta li::before {{ content:"▪"; color:#8BC34A; position:absolute; right:0; }}

  .section-head {{ display:flex; align-items:center; gap:10px; justify-content:flex-start;
                  margin:24px 0 16px; padding-top:18px; border-top:1px solid #ddd; break-after:avoid; }}
  .section-head .bar {{ width:5px; height:22px; background:#8BC34A; border-radius:2px; }}
  .section-head h2 {{ font-size:18px; font-weight:700; }}

  /* ממצא = יחידה אחת שלא נחתכת בין עמודים */
  .finding {{ padding:6px 0 14px; border-bottom:1px solid #eee; margin-bottom:13px;
             break-inside:avoid; page-break-inside:avoid; }}
  .f-head {{ display:flex; align-items:flex-start; gap:12px; }}
  .badge {{ flex:0 0 auto; width:34px; height:34px; background:#1a1a1a; color:#fff;
           font-weight:700; font-size:14px; border-radius:4px; display:flex;
           align-items:center; justify-content:center; }}
  .f-desc {{ font-size:14px; font-weight:500; line-height:1.5; padding-top:5px; }}
  .f-meta {{ margin:10px 46px 0 0; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .f-meta .lbl {{ color:#999; font-size:11px; }}
  .f-meta .box {{ border:1px solid #ccc; border-radius:4px; padding:4px 12px; font-size:12.5px; }}
  .note {{ margin:10px 46px 0 0; background:#f6f6f3; border-right:4px solid #8BC34A;
          border-radius:3px; padding:9px 14px; font-size:12px; color:#555; }}
  .note-lbl {{ color:#999; margin-left:8px; }}

</style></head>
<body>
  <div class="page">
    <div class="topbar"></div>
    <header>
      {logo_html}
      {client_html}
    </header>

    <div class="title-block">
      <div class="eyebrow">DESIGN IT RIGHT</div>
      <div class="title">סיכום פגישה</div>
      <div class="title-underline"></div>
    </div>

    <table class="meta">
      <tr>
        <td class="lbl">שם הפרויקט</td><td class="val">{_esc(data.get('project_name'))}</td>
        <td class="lbl">נושא הפגישה</td><td class="val">{_esc(data.get('topic'))}</td>
      </tr>
      <tr>
        <td class="lbl">תאריך</td><td class="val">{_esc(data.get('date'))}</td>
        <td class="lbl">מקום</td><td class="val">{_esc(data.get('location'))}</td>
      </tr>
      <tr>
        <td class="lbl">משתתפים</td><td class="val" colspan="3"><ul>{part_items}</ul></td>
      </tr>
    </table>

    <div class="section-head"><span class="bar"></span><h2>ממצאים ונקודות מהפגישה</h2></div>
    {findings_html}
  </div>
</body></html>"""


# תבנית כותרת תחתונה (חוזרת בכל עמוד) - כתובת DIT + מספור עמודים
_FOOTER_TEMPLATE = """
<div style="width:100%;font-family:'David Libre','David',serif;font-size:8.5px;color:#999;
            padding:0 14mm;display:flex;justify-content:space-between;direction:rtl;">
  <span>הרכבת 58, תל אביב&nbsp;&nbsp;·&nbsp;&nbsp;www.dit.co.il</span>
  <span>עמוד <span class="pageNumber"></span> מתוך <span class="totalPages"></span></span>
</div>"""


def render_preview_html(data: dict, client_logo: str | None = None) -> str:
    """מחזיר את ה-HTML המלא של הפורמט (עם לוגואים מוטמעים) לתצוגה מקדימה."""
    return _render_html(data, _img_data_uri(client_logo))


def build_pdf(data: dict, out_pdf: str, client_logo: str | None = None) -> str:
    """מרנדר את ה-HTML ל-PDF דרך Chromium. רץ ב-thread נקי (Playwright sync).

    client_logo: נתיב לקובץ לוגו הלקוח (אופציונלי) - יוטמע בפינת הפורמט.
    """
    page_html = _render_html(data, _img_data_uri(client_logo))
    err = {}

    def work():
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = _launch_chromium(p)
                page = browser.new_page()
                # networkidle -> מחכים שהפונט (David Libre) ייטען לפני יצירת ה-PDF
                page.set_content(page_html, wait_until="networkidle")
                try:
                    page.evaluate("document.fonts && document.fonts.ready")
                except Exception:
                    pass
                page.pdf(
                    path=out_pdf, format="A4", print_background=True,
                    display_header_footer=True,
                    header_template="<span></span>",
                    footer_template=_FOOTER_TEMPLATE,
                    margin={"top": "14mm", "bottom": "16mm", "left": "13mm", "right": "13mm"},
                )
                browser.close()
        except Exception as e:
            err["e"] = e

    t = threading.Thread(target=work)
    t.start()
    t.join()
    if "e" in err:
        raise err["e"]
    return out_pdf
