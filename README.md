# YouTube Long-to-Shorts Automation Pipeline

A lightweight, CPU-friendly automation system that turns long-form YouTube videos into YouTube Shorts with a human-in-the-loop approval step before publishing.

Designed to run on a small VPS (1 vCPU / 1GB RAM) or GitHub Actions cron at **under $0.05/day**.

---

## Architecture & Flow

```
[Google Sheet Queue]
         │ (Picks first pending row)
         ▼
    [1. yt-dlp] (Downloads video, 720p max)
         │
         ▼
  [2. Transcribe] (YouTube captions first -> faster-whisper int8 CPU fallback)
         │
         ▼
 [3. Select Clips] (Sentence grouping -> 1 gpt-4o-mini call -> 3-5 clips of 20-30s)
         │
         ▼
    [4. Cut & 9:16] (FFmpeg center crop 1080x1920 + smooth audio fades)
         │
         ▼
   [5. Captions] (Dynamic ASS subtitle generation + bold lower-third burn-in)
         │
         ▼
  [6. Commentary] (LLM context hook + free edge-tts + sidechain audio ducking)
         │
         ▼
   [7. Approval] (Telegram Bot with 1-tap inline buttons or WhatsApp via Twilio)
         │
         ├──── Approved ────► [8. YouTube API v3] (Resumable Short upload with #Shorts)
         │
         └──── Rejected ────► Log status in SQLite
         │
         ▼
  [9. Mark Sheet & Disk Cleanup] (Marks row processed, removes raw source video)
```

---

## Directory Structure

```text
├── config.py                 # Central configuration and environment settings
├── main.py                   # Daily orchestrator & CLI
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variables template
├── credentials/              # Service accounts & OAuth tokens (gitignored)
├── data/
│   ├── pipeline.db           # SQLite state store (resumable pipeline)
│   ├── downloads/            # Cached source videos and transcripts
│   └── output/               # Rendered shorts and subtitles
├── src/
│   ├── sheet.py              # Stage 1: Google Sheets reader & status updater
│   ├── download.py           # Stage 2: yt-dlp 720p downloader
│   ├── transcribe.py         # Stage 3: Captions / Whisper CPU transcription
│   ├── select_clips.py       # Stage 4: Sentence grouping & GPT-4o-mini clip selection
│   ├── cut.py                # Stage 5: FFmpeg 9:16 cut & audio fade
│   ├── captions.py           # Stage 6: ASS subtitle generation & burn-in
│   ├── voiceover.py          # Stage 7: LLM commentary + edge-tts + audio ducking
│   ├── approval/             # Stage 8: Human approval interface
│   │   ├── base.py           # Abstract messaging interface
│   │   ├── telegram.py       # Telegram Bot provider (Free fallback)
│   │   └── whatsapp.py       # WhatsApp provider (Twilio)
│   └── publish.py            # Stage 9: YouTube Data API v3 publisher
├── tests/                    # Unit tests for each pipeline stage
└── .github/workflows/
    └── daily_shorts.yml      # Daily cron workflow for GitHub Actions
```

---

## Prerequisites

1. **Python 3.10+**
2. **FFmpeg**:
   - Ubuntu / Debian: `sudo apt-get install ffmpeg`
   - macOS: `brew install ffmpeg`
   - Windows: `winget install Gyan.FFmpeg` or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)

---

## Setup Guide

### 1. Installation

```bash
git clone https://github.com/Wahla-007/Yt-automation.git
cd Yt-automation
python -m venv venv

# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

### 2. Google Sheets Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable both **Google Sheets API** and **Google Drive API**.
3. Create a **Service Account** (under *Credentials* > *Create Credentials* > *Service Account*).
4. Create a JSON key and download it to `credentials/service_account.json`.
5. Create a Google Sheet with columns:
   - `url` (e.g. `https://www.youtube.com/watch?v=...`)
   - `status` (leave blank or enter `pending`)
   - `processed_at` (will be updated automatically)
6. Click **Share** on your Google Sheet and share it with the service account email (with *Editor* access).
7. Set the sheet name in `.env`: `GOOGLE_SHEET_NAME="YourSheetName"`.

### 3. YouTube Data API v3 Setup (Publishing)

1. In Google Cloud Console, enable **YouTube Data API v3**.
2. Under *Credentials*, click *Create Credentials* > *OAuth client ID* (Application type: *Desktop App*).
3. Download the client secrets file to `credentials/client_secrets.json`.
4. Run the one-time authentication check:
   ```bash
   python -m src.publish
   ```
   A browser window will open asking you to grant permission. Once authorized, `credentials/youtube_token.json` is generated and saved automatically. Future runs will auto-refresh this token without opening a browser.

### 4. Human Approval Setup

#### Option A: Telegram Bot (Recommended, 100% Free)
1. Message `@BotFather` on Telegram and create a bot with `/newbot`.
2. Copy the API Token to `.env` as `TELEGRAM_BOT_TOKEN`.
3. Start a conversation with your bot, then get your numeric user ID from `@userinfobot`.
4. Add it to `.env` as `TELEGRAM_CHAT_ID`.
5. In `.env`, set `APPROVAL_PROVIDER=telegram`.

#### Option B: WhatsApp (Twilio)
1. Sign up on [Twilio](https://www.twilio.com/) and enable the WhatsApp Sandbox.
2. Add your `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` to `.env`.
3. Set `APPROVAL_PROVIDER=whatsapp`.

---

## Running the Pipeline

### Full Daily Run
```bash
python main.py
```

### Process a Single Video Directly (Bypass Sheet)
```bash
python main.py --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Dry Run (Render Clips Without Publishing)
```bash
python main.py --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --dry-run
```

### Auto-Approve (For Unattended Testing)
```bash
python main.py --auto-approve
```

---

## Standalone Stage Testing

Each stage is completely modular and can be tested independently:

```bash
# Test unit suite
python -m unittest discover tests

# Test Google Sheet connection
python -m src.sheet

# Test ASS subtitle generation
python -m src.captions

# Test sentence grouping & LLM selection
python -m src.select_clips

# Test edge-tts voice synthesis
python -m src.voiceover

# Test YouTube OAuth connection
python -m src.publish
```

---

## Automated Daily Scheduling

### GitHub Actions (No Server Needed)
Add repository secrets in GitHub (*Settings* > *Secrets and variables* > *Actions*):
- `OPENAI_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON` (contents of `credentials/service_account.json`)
- `YOUTUBE_CLIENT_SECRETS_JSON` (contents of `credentials/client_secrets.json`)
- `YOUTUBE_TOKEN_JSON` (contents of `credentials/youtube_token.json`)
- `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID`

The workflow in `.github/workflows/daily_shorts.yml` triggers every day at 08:00 UTC automatically.

### Linux VPS Cron
Add to crontab (`crontab -e`):
```cron
0 8 * * * cd /path/to/Yt-automation && /path/to/venv/bin/python main.py >> /var/log/yt_shorts.log 2>&1
```
