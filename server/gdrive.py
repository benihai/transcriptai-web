"""
Google Drive integration — שמירת הקלטות וסיכומים ב-Drive.
משתמש ב-OAuth2 token של המשתמש (לא Service Account) כדי שהקבצים
יישמרו תחת המכסה של המשתמש.

הגדרה:
    GDRIVE_TOKEN_FILE=/opt/transcriptai/gdrive_token.json   (נוצר אחרי login חד-פעמי)
    GDRIVE_FOLDER_ID=1ABC...xyz
"""
from __future__ import annotations

import os
import json
from pathlib import Path

TOKEN_FILE  = os.getenv("GDRIVE_TOKEN_FILE",  "/opt/transcriptai/gdrive_token.json")
FOLDER_ID   = os.getenv("GDRIVE_FOLDER_ID",   "")
CLIENT_FILE = os.getenv("GDRIVE_OAUTH_CLIENT", "/opt/transcriptai/gdrive_oauth_client.json")
SCOPES      = ["https://www.googleapis.com/auth/drive.file"]


def _creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not Path(TOKEN_FILE).exists():
        raise RuntimeError("gdrive_token.json לא קיים — יש לבצע login ב-/gdrive/login")

    data = json.loads(Path(TOKEN_FILE).read_text())
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes", SCOPES),
    )
    # רענון אוטומטי אם פג תוקף
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        data["token"] = creds.token
        Path(TOKEN_FILE).write_text(json.dumps(data), encoding="utf-8")
    return creds


def _service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_creds(), cache_discovery=False)


def upload_file(local_path: str, folder_id: str | None = None, description: str = "", display_name: str = "") -> str:
    from googleapiclient.http import MediaFileUpload
    folder = folder_id or FOLDER_ID
    p = Path(local_path)
    meta = {"name": display_name or p.name, "description": description}
    if folder:
        meta["parents"] = [folder]
    media = MediaFileUpload(str(p), mimetype=_mime(p.suffix), resumable=True)
    f = _service().files().create(body=meta, media_body=media, fields="id").execute()
    return f.get("id", "")


def upload_recording(local_path: str, meeting_title: str = "") -> str:
    p = Path(local_path)
    name = f"{meeting_title}{p.suffix}" if meeting_title else p.name
    # שם נקי (ללא תווים אסורים ב-Drive)
    name = "".join(c for c in name if c not in r'\/:*?"<>|').strip() or p.name
    return upload_file(local_path, display_name=name, description=f"הקלטת פגישה: {meeting_title}")


def upload_summary(local_path: str, meeting_title: str = "") -> str:
    p = Path(local_path)
    name = f"{meeting_title}.json" if meeting_title else p.name
    name = "".join(c for c in name if c not in r'\/:*?"<>|').strip() or p.name
    return upload_file(local_path, display_name=name, description=f"סיכום פגישה: {meeting_title}")


def is_configured() -> bool:
    return bool(FOLDER_ID and Path(TOKEN_FILE).exists())


def _mime(ext: str) -> str:
    return {
        ".webm": "video/webm", ".wav": "audio/wav", ".mp4": "video/mp4",
        ".json": "application/json", ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext.lower(), "application/octet-stream")
