import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
FAL_KEY = os.getenv("FAL_KEY", "").strip()
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.6").strip()

FAL_IMAGE_MODEL = os.getenv("FAL_IMAGE_MODEL", "fal-ai/nano-banana-2/edit").strip()
FAL_VIDEO_MODEL = os.getenv(
    "FAL_VIDEO_MODEL",
    "fal-ai/kling-video/v3/pro/image-to-video",
).strip()

MAX_PHOTOS = 5
MIN_PHOTOS = 1
ALBUM_WAIT_SEC = 1.4

ASPECT_RATIOS = {"auto", "1:1", "4:5", "9:16", "16:9", "3:2", "4:3"}


def require_env() -> None:
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("XAI_API_KEY", XAI_API_KEY),
            ("FAL_KEY", FAL_KEY),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing env: {', '.join(missing)}. Copy .env.example to .env")
