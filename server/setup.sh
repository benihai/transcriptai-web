#!/bin/bash
# TranscriptAI — סקריפט התקנה לשרת Ubuntu 22.04
# הרצה פעם אחת בלבד אחרי קנייה של השרת:
#   bash /opt/transcriptai/server/setup.sh

set -e

PROJECT_DIR=/opt/transcriptai
BOT_OUTPUT_DIR=/home/ubuntu/meetingbot-output
VENV=$PROJECT_DIR/.venv

echo "================================================"
echo "  TranscriptAI Server Setup"
echo "================================================"

# 1. חבילות מערכת
echo ""
echo "[1/6] מתקין חבילות מערכת..."
apt-get update -qq
apt-get install -y -qq \
    python3.12 python3.12-venv python3-pip \
    ffmpeg git curl wget

# 2. Docker
echo ""
echo "[2/6] מתקין Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
else
    echo "  Docker כבר מותקן — דולג"
fi

# Docker Compose plugin
if ! docker compose version &>/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-plugin
fi

# 3. Python venv
echo ""
echo "[3/6] מגדיר סביבת Python..."
cd $PROJECT_DIR
python3.12 -m venv $VENV
$VENV/bin/pip install --quiet --upgrade pip
$VENV/bin/pip install --quiet \
    faster-whisper \
    "google-genai>=1.21.0" \
    python-dotenv \
    requests \
    av

echo "  חבילות Python הותקנו"

# 4. הורדת מודל Whisper (1.6GB, פעם אחת)
echo ""
echo "[4/6] מוריד מודל תמלול עברית (ivrit-ai, ~1.6GB)..."
echo "  זה ייקח כמה דקות — בהתאם למהירות החיבור"
$VENV/bin/python3 -c "
from faster_whisper import WhisperModel
print('  מוריד ומאמת...')
WhisperModel('ivrit-ai/whisper-large-v3-turbo-ct2', device='cpu', compute_type='int8')
print('  המודל מוכן!')
" || echo "  אזהרה: הורדת המודל נכשלה — תנסה שוב ידנית"

# 5. תיקיות
echo ""
echo "[5/6] יוצר תיקיות..."
mkdir -p $BOT_OUTPUT_DIR
mkdir -p $PROJECT_DIR/data
mkdir -p $PROJECT_DIR/recordings
mkdir -p $PROJECT_DIR/projects
echo "  תיקיות נוצרו"

# 6. systemd service
echo ""
echo "[6/6] מתקין systemd service..."
cp $PROJECT_DIR/server/transcriptai.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable transcriptai
echo "  Service מותקן ויופעל בכל אתחול"

# סיום
echo ""
echo "================================================"
echo "  ההתקנה הושלמה בהצלחה!"
echo "================================================"
echo ""
echo "שלבים הבאים שצריך לבצע:"
echo ""
echo "  1. ערוך את קובץ .env:"
echo "     nano $PROJECT_DIR/.env"
echo "     הוסף:"
echo "       GEMINI_API_KEY=your_key"
echo "       NOTIFY_EMAIL=bnim4444@gmail.com"
echo "       NOTIFY_SMTP_USER=bot_gmail@gmail.com"
echo "       NOTIFY_SMTP_PASS=xxxx xxxx xxxx xxxx"
echo "       WHISPER_MODEL=ivrit-ai/whisper-large-v3-turbo-ct2"
echo ""
echo "  2. ערוך calendar_config.json:"
echo "     nano $PROJECT_DIR/calendar_config.json"
echo "     שנה:"
echo "       enabled: true"
echo "       email: (ג'ימייל הבוט)"
echo "       app_password: (App Password)"
echo "       bot_output_dir: $BOT_OUTPUT_DIR"
echo ""
echo "  3. הפעל בוט Docker:"
echo "     cd $PROJECT_DIR/external/meeting-bot"
echo "     docker compose -f ../../server/docker-compose.server.yml up -d --build"
echo ""
echo "  4. הפעל watcher:"
echo "     systemctl start transcriptai"
echo ""
echo "  5. בדוק לוגים:"
echo "     journalctl -u transcriptai -f"
echo "     tail -f $PROJECT_DIR/data/calendar.log"
echo ""
