import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
from src.publish import publish_approved_clip
from src.db import get_clips_for_video, upsert_clip

def upload_all(video_id: str = "NiCgcUTL9_Q"):
    clips = get_clips_for_video(video_id)
    if not clips:
        print(f"No clips found for video {video_id}")
        return

    print(f"Starting upload for {len(clips)} shorts to YouTube...")
    uploaded = []

    for c in clips:
        idx = c["clip_index"]
        print(f"\n==========================================")
        print(f"  Uploading Short #{idx}")
        print(f"==========================================")
        try:
            res = publish_approved_clip(c, original_video_title="The Crazy Engineering of Fiber Optics")
            uploaded.append(res)
            upsert_clip(
                video_id=video_id,
                clip_index=idx,
                youtube_video_id=res["youtube_video_id"],
                status="published",
            )
            print(f"[Upload] SUCCESS! Short #{idx} live at: {res['url']}")
            time.sleep(3)
        except Exception as e:
            print(f"[Upload] Error uploading Short #{idx}: {e}")

    print("\n==========================================")
    print("  SUMMARY OF PUBLISHED SHORTS")
    print("==========================================")
    for r in uploaded:
        print(f"* {r['title']} -> {r['url']}")

if __name__ == "__main__":
    vid = sys.argv[1] if len(sys.argv) > 1 else "NiCgcUTL9_Q"
    upload_all(vid)
