import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CREDENTIALS_DIR = BASE_DIR / "credentials"
DOWNLOADS_DIR = DATA_DIR / "downloads"
OUTPUT_DIR = DATA_DIR / "output"

for d in [DATA_DIR, CREDENTIALS_DIR, DOWNLOADS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# AI Engine Settings
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()

# Google Gemini (Supports automatic rotation and fallback across multiple keys)
_gemini_keys_env = os.getenv("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [k.strip() for k in _gemini_keys_env.split(",") if k.strip()]
if not GEMINI_API_KEYS:
    GEMINI_API_KEYS = [
        k for k in [
            os.getenv("GEMINI_API_KEY", ""),
            os.getenv("GEMINI_API_KEY_1", ""),
            os.getenv("GEMINI_API_KEY_2", ""),
            os.getenv("GEMINI_API_KEY_3", ""),
        ] if k
    ]

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# OpenAI (Optional / Alternative)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Google Sheets (uses same OAuth client_secrets.json as YouTube)
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "yt-automate")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1FF7RoUpHnTIDrMQe6ygH84pLqFf2nVh8neY8ceyDm5w")
GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "Sheet1")
SHEETS_TOKEN_FILE = os.getenv("SHEETS_TOKEN_FILE", str(CREDENTIALS_DIR / "sheets_token.json"))

# YouTube Data API
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv(
    "YOUTUBE_CLIENT_SECRETS_FILE", str(CREDENTIALS_DIR / "client_secrets.json")
)
YOUTUBE_TOKEN_FILE = os.getenv(
    "YOUTUBE_TOKEN_FILE", str(CREDENTIALS_DIR / "youtube_token.json")
)
# YouTube Upload (Set to true to publish shorts directly to YouTube)
UPLOAD_TO_YOUTUBE = os.getenv("UPLOAD_TO_YOUTUBE", "true").lower() in ("true", "1", "yes")
YOUTUBE_PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "public").lower()

# Approval / Auto-Publish (Default: True = fully automatic, no Telegram/WhatsApp needed)
AUTO_APPROVE = os.getenv("AUTO_APPROVE", "true").lower() in ("true", "1", "yes")
APPROVAL_PROVIDER = os.getenv("APPROVAL_PROVIDER", "none").lower()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "twilio").lower()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")
WHATSAPP_TO = os.getenv("WHATSAPP_TO", "")

# Video Constraints
MIN_CLIP_DURATION = int(os.getenv("MIN_CLIP_DURATION", "20"))
MAX_CLIP_DURATION = int(os.getenv("MAX_CLIP_DURATION", "30"))
TARGET_CLIPS_COUNT = int(os.getenv("TARGET_CLIPS_COUNT", "4"))
TARGET_WIDTH = int(os.getenv("TARGET_WIDTH", "1080"))
TARGET_HEIGHT = int(os.getenv("TARGET_HEIGHT", "1920"))

# Video Fit Mode ('blur' = full frame with blurred background, 'pad' = black bars, 'crop' = center crop)
VIDEO_FIT_MODE = os.getenv("VIDEO_FIT_MODE", "blur").lower()

# Cleanup intermediate clips (default: false = keep only the single final video per short)
KEEP_INTERMEDIATE_CLIPS = os.getenv("KEEP_INTERMEDIATE_CLIPS", "false").lower() in ("true", "1", "yes")

# End trim buffer in seconds to prevent single-frame next-scene leaks
SAFETY_END_TRIM = float(os.getenv("SAFETY_END_TRIM", "0.12"))

# Voiceover (edge-tts)
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
VOICEOVER_VOLUME = float(os.getenv("VOICEOVER_VOLUME", "0.03"))  # 0.03 = 3% volume (subtle background for algorithm, 100% real voice for human)

# State Database
DATABASE_PATH = os.getenv("DATABASE_PATH", str(DATA_DIR / "pipeline.db"))

# Whisper settings (CPU, low memory)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small.en")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# FFmpeg Executable Resolution
def _find_ffmpeg() -> str:
    import shutil
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

FFMPEG_BINARY = _find_ffmpeg()
if FFMPEG_BINARY and os.path.exists(FFMPEG_BINARY):
    _ffmpeg_dir = str(Path(FFMPEG_BINARY).parent)
    if _ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
