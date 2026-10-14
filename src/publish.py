import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path
from typing import Dict, Any, Optional, List

import config
from src.db import update_clip_status

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False


YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


def get_authenticated_service(
    client_secrets_file: Optional[str] = None,
    token_file: Optional[str] = None,
):
    """
    Handles YouTube OAuth 2.0 flow with token persistence and auto-refresh.
    """
    if not GOOGLE_API_AVAILABLE:
        raise RuntimeError("google-api-python-client or google-auth-oauthlib not installed.")

    secrets_file = client_secrets_file or config.YOUTUBE_CLIENT_SECRETS_FILE
    tok_file = token_file or config.YOUTUBE_TOKEN_FILE

    creds = None
    if os.path.exists(tok_file):
        creds = Credentials.from_authorized_user_file(tok_file, YOUTUBE_UPLOAD_SCOPE)
    elif hasattr(config, "SHEETS_TOKEN_FILE") and os.path.exists(config.SHEETS_TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(config.SHEETS_TOKEN_FILE, YOUTUBE_UPLOAD_SCOPE)
            if creds and creds.valid:
                print(f"[Publish] Reusing OAuth token from {config.SHEETS_TOKEN_FILE}")
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[Publish] Refreshing expired YouTube OAuth token...")
            creds.refresh(Request())
        else:
            if not os.path.exists(secrets_file):
                raise FileNotFoundError(
                    f"YouTube client_secrets.json not found at '{secrets_file}'. "
                    "Download it from Google Cloud Console > APIs & Services > Credentials."
                )
            print("[Publish] Starting local OAuth flow for YouTube...")
            flow = InstalledAppFlow.from_client_secrets_file(secrets_file, YOUTUBE_UPLOAD_SCOPE)
            creds = flow.run_local_server(port=0)

        # Save credentials for next run
        Path(tok_file).parent.mkdir(parents=True, exist_ok=True)
        with open(tok_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def generate_viral_metadata(
    clip_title: str = "",
    clip_hook: str = "",
    clip_transcript: str = "",
    original_video_title: str = "",
) -> Dict[str, Any]:
    """
    Uses Gemini to generate an ultra-catchy viral YouTube Shorts title, 
    optimized tags (10-15 high SEO tags), and an engaging description with relevant hashtags.
    """
    try:
        from src.ai_client import get_ai_completion
        import json

        prompt = (
            "You are a top-tier YouTube Shorts growth strategist and copywriter.\n"
            "Generate viral YouTube metadata for this Short.\n\n"
            f"Original Video Title: {original_video_title}\n"
            f"Clip Draft Title: {clip_title}\n"
            f"Clip Hook: {clip_hook}\n"
            f"Clip Transcript: {clip_transcript[:500]}\n\n"
            "Requirements:\n"
            "1. 'title': Ultra-catchy, emotional or curiosity-inducing title under 60 characters, with 1-2 relevant emojis and ending with '#Shorts'. "
            "Examples: 'When Phone Calls Cost MORE Than The Moon! 🤯 #Shorts', 'The Impossible Glass That Built The Internet 🌐 #Shorts'\n"
            "2. 'tags': A list of 10 to 15 high-ranking YouTube search tags relevant to the topic (e.g., ['shorts', 'technology', 'history', 'science', 'mindblown', 'facts', 'engineering', 'viral']).\n"
            "3. 'description': An engaging 2-3 sentence description with key context, a call to subscribe for daily shorts, and 4-5 relevant hashtags.\n\n"
            "Return a valid JSON object with keys 'title', 'tags', and 'description'."
        )

        content = get_ai_completion(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        data = json.loads(content)
        title = data.get("title", clip_title or "Mindblowing Fact #Shorts").strip()
        if "#Shorts" not in title and "#shorts" not in title:
            title = f"{title[:50]} #Shorts"
        tags = data.get("tags", ["shorts", "viral", "facts"])
        if "shorts" not in [t.lower() for t in tags]:
            tags.append("shorts")
        description = data.get("description", f"{clip_hook}\n\n#Shorts\nSubscribe for daily insights.")
        return {
            "title": title,
            "tags": tags,
            "description": description,
        }
    except Exception as e:
        print(f"[Publish] Warning: AI metadata generation failed ({e}), using default format.")
        clean_title = clip_title.strip() or "Amazing Discovery"
        if "#Shorts" not in clean_title and "#shorts" not in clean_title:
            clean_title = f"{clean_title[:50]} #Shorts"
        return {
            "title": clean_title,
            "tags": ["shorts", "viral", "facts", "trending"],
            "description": f"{clip_hook}\n\n#Shorts\nSubscribe for daily insights.",
        }


def upload_short_to_youtube(
    video_path: str,
    title: str,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    privacy_status: str = "public",
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Uploads a short to YouTube using resumable upload chunks.
    Ensures #Shorts tag is in title and description.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found at: {video_path}")

    youtube = get_authenticated_service()

    clean_title = title.strip()
    if "#Shorts" not in clean_title and "#shorts" not in clean_title:
        clean_title = f"{clean_title[:50]} #Shorts"

    tag_list = tags or ["shorts"]
    if "shorts" not in [t.lower() for t in tag_list]:
        tag_list.append("shorts")

    desc = description or f"#Shorts\nSubscribe for more!"

    body = {
        "snippet": {
            "title": clean_title,
            "description": desc,
            "tags": tag_list,
            "categoryId": "28",  # Science & Technology
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        chunksize=1024 * 1024 * 2,  # 2MB chunks
        resumable=True,
        mimetype="video/mp4",
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    print(f"\n[Publish] Uploading to YouTube:")
    print(f"  Title  : {clean_title}")
    print(f"  Tags   : {', '.join(tag_list[:8])}...")
    print(f"  Privacy: {privacy_status}")

    response = None
    retry_count = 0

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"[Publish] Upload progress: {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504] and retry_count < max_retries:
                retry_count += 1
                wait_sec = 2.0 ** retry_count
                print(f"[Publish] HttpError {e.resp.status}. Retrying in {wait_sec}s...")
                time.sleep(wait_sec)
            else:
                raise

    video_id = response.get("id")
    shorts_url = f"https://youtube.com/shorts/{video_id}"
    print(f"[Publish] Successfully published YouTube Short: {shorts_url}\n")

    return {
        "youtube_video_id": video_id,
        "url": shorts_url,
        "title": clean_title,
    }


def publish_approved_clip(
    clip: Dict[str, Any],
    privacy_status: Optional[str] = None,
    original_video_title: str = "",
) -> Dict[str, Any]:
    """
    Generates viral metadata (catchy title, tags, description) and publishes clip to YouTube.
    """
    clip_id = clip.get("id")
    video_path = clip.get("final_clip_path") or clip.get("subtitled_clip_path") or clip.get("raw_clip_path")

    # Generate viral title, high SEO tags, and engaging description
    metadata = generate_viral_metadata(
        clip_title=clip.get("title", ""),
        clip_hook=clip.get("hook", ""),
        clip_transcript=clip.get("transcript", ""),
        original_video_title=original_video_title,
    )

    chosen_privacy = privacy_status or getattr(config, "YOUTUBE_PRIVACY_STATUS", "public")

    result = upload_short_to_youtube(
        video_path=video_path,
        title=metadata["title"],
        description=metadata["description"],
        tags=metadata["tags"],
        privacy_status=chosen_privacy,
    )

    if clip_id:
        update_clip_status(clip_id, "published")

    return result


if __name__ == "__main__":
    print("Testing YouTube publisher authentication...")
    try:
        service = get_authenticated_service()
        print("YouTube authentication successful! Service created.")
    except Exception as e:
        print(f"Auth check result: {e}")
