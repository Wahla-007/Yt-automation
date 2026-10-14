import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

import config
from src.db import upsert_video

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False


def extract_video_id(url: str) -> str:
    """Extracts YouTube 11-character video ID from various YouTube URL formats."""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"shorts\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # Fallback to sanitized url if no match
    return re.sub(r"[^0-9A-Za-z_-]", "_", url)[:20]


def download_youtube_video(
    url: str,
    output_dir: Optional[Path] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Downloads YouTube video at maximum 720p using yt-dlp.
    Reuses existing cached download if present.
    """
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp is not installed. Please install requirements.txt.")

    out_dir = output_dir or config.DOWNLOADS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    video_id = extract_video_id(url)
    expected_video_path = out_dir / f"{video_id}.mp4"

    # Disk cache check
    if expected_video_path.exists() and expected_video_path.stat().st_size > 1000:
        print(f"[Download] Found cached video for ID {video_id} at {expected_video_path}")
        info = {
            "id": video_id,
            "title": video_id,
            "video_path": str(expected_video_path),
            "cached": True,
        }
        upsert_video(
            video_id=video_id,
            url=url,
            status="downloaded",
            video_path=str(expected_video_path),
        )
        return info

    ydl_opts = {
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
        "outtmpl": str(out_dir / f"{video_id}.%(ext)s"),
        "merge_output_format": "mp4",
        "ffmpeg_location": config.FFMPEG_BINARY,
        "quiet": False,
        "no_warnings": True,
        "retries": 3,
        "socket_timeout": 30,
    }

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[Download] Fetching video info & downloading (Attempt {attempt}/{max_retries}): {url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info_dict)

                # Ensure filename matches expected mp4
                if not Path(downloaded_file).exists():
                    # Check for mp4 container
                    candidate = Path(downloaded_file).with_suffix(".mp4")
                    if candidate.exists():
                        downloaded_file = str(candidate)

                video_title = info_dict.get("title", video_id)
                duration = info_dict.get("duration", 0)

                upsert_video(
                    video_id=video_id,
                    url=url,
                    title=video_title,
                    status="downloaded",
                    video_path=downloaded_file,
                )

                return {
                    "id": video_id,
                    "title": video_title,
                    "duration": duration,
                    "video_path": downloaded_file,
                    "cached": False,
                }
        except Exception as e:
            last_error = e
            wait = 2.0 ** attempt
            print(f"[Download] Attempt {attempt} failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)

    upsert_video(
        video_id=video_id,
        url=url,
        status="failed",
        error_message=f"Download failed: {last_error}",
    )
    raise RuntimeError(f"Failed to download video {url} after {max_retries} attempts: {last_error}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
    else:
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    print(f"Testing download module with URL: {test_url}")
    res = download_youtube_video(test_url)
    print(f"Result: {res}")
