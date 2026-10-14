import argparse
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
from typing import Optional

import config
from src.db import (
    init_db,
    upsert_video,
    get_video,
    get_clips_for_video,
    update_clip_status,
)
from src.sheet import GoogleSheetManager
from src.download import download_youtube_video, extract_video_id
from src.transcribe import transcribe_video
from src.select_clips import select_clips_for_video
from src.cut import cut_all_clips
from src.captions import generate_ass_subtitles, burn_captions
from src.voiceover import process_clip_voiceover
from src.approval import get_notifier
from src.publish import publish_approved_clip


def run_daily_pipeline(
    manual_url: str = None,
    auto_approve: Optional[bool] = None,
    cleanup_source: bool = True,
    dry_run: bool = False,
):
    """
    Executes the full end-to-end YouTube Shorts generation pipeline:
    1. Sheet -> 2. Download -> 3. Transcribe -> 4. Select -> 5. Cut ->
    6. Captions -> 7. Voiceover -> 8. Approval -> 9. Publish -> 10. Cleanup
    """
    if auto_approve is None:
        auto_approve = getattr(config, "AUTO_APPROVE", True)

    init_db()
    sheet_mgr = GoogleSheetManager()
    sheet_row_index = None

    # Step 1: Input from Sheet or CLI
    if manual_url:
        video_url = manual_url
        print(f"[Pipeline] Running in manual mode with URL: {video_url}")
    else:
        print("[Pipeline] Checking Google Sheet for next pending video...")
        pending_item = sheet_mgr.get_next_pending_video()
        if not pending_item:
            print("[Pipeline] No pending videos found in Google Sheet. Daily run complete.")
            return
        video_url = pending_item["url"]
        sheet_row_index = pending_item["row_index"]
        print(f"[Pipeline] Picked video from Sheet row {sheet_row_index}: {video_url}")

    video_id = extract_video_id(video_url)
    upsert_video(video_id=video_id, url=video_url, status="processing")

    try:
        # Step 2: Download (max 720p)
        print("\n--- STAGE 2: DOWNLOAD ---")
        dl_info = download_youtube_video(video_url)
        video_path = dl_info["video_path"]
        video_title = dl_info.get("title", video_id)

        # Step 3: Transcribe (Captions first -> Whisper fallback)
        print("\n--- STAGE 3: TRANSCRIBE ---")
        words = transcribe_video(video_id=video_id, video_path=video_path)
        if not words:
            raise RuntimeError(f"No transcription words could be obtained for {video_id}.")

        # Step 4: Clip Selection (Sentence grouping + 1 LLM call)
        print("\n--- STAGE 4: CLIP SELECTION ---")
        clips = select_clips_for_video(video_id=video_id, words=words, video_title=video_title)
        if not clips:
            raise RuntimeError("Clip selection returned 0 clips.")

        output_dir = config.OUTPUT_DIR / video_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Check if all clips are already finished and ready on disk
        all_finished = len(clips) > 0 and all(
            (output_dir / f"short_{c['clip_index']:02d}.mp4").exists() and 
            (output_dir / f"short_{c['clip_index']:02d}.mp4").stat().st_size > 1000
            for c in clips
        )

        if all_finished:
            print(f"\n[Pipeline] Found {len(clips)} existing finished shorts at {output_dir}. Skipping re-cutting.")
            for c in clips:
                c["final_clip_path"] = str(output_dir / f"short_{c['clip_index']:02d}.mp4")
        else:
            # Step 5: Cut (FFmpeg 9:16 center-crop + audio fade)
            print("\n--- STAGE 5: CUT & 9:16 CROP ---")
            clips = cut_all_clips(video_id=video_id, source_video_path=video_path, clips=clips)

            # Step 6: Captions (ASS generation & burn-in)
            print("\n--- STAGE 6: CAPTIONS ---")
            for c in clips:
                idx = c["clip_index"]
                subtitled_path = output_dir / f"short_{idx:02d}_subtitled.mp4"
                if subtitled_path.exists() and subtitled_path.stat().st_size > 1000:
                    c["subtitled_clip_path"] = str(subtitled_path)
                    continue

                ass_path = output_dir / f"short_{idx:02d}.ass"
                generate_ass_subtitles(
                    words=words,
                    clip_start=c["start_time"],
                    clip_end=c["end_time"],
                    output_ass_path=str(ass_path),
                )
                print(f"[Captions] Burning ASS subtitles into clip #{idx}...")
                burn_captions(
                    video_path=c["raw_clip_path"],
                    ass_path=str(ass_path),
                    output_video_path=str(subtitled_path),
                )
                c["subtitled_clip_path"] = str(subtitled_path)

            # Step 7: Commentary & Voiceover (edge-tts + audio ducking)
            print("\n--- STAGE 7: COMMENTARY & VOICEOVER ---")
            for c in clips:
                idx = c["clip_index"]
                final_path = output_dir / f"short_{idx:02d}_final.mp4"
                process_clip_voiceover(
                    video_id=video_id,
                    clip=c,
                    subtitled_video_path=c["subtitled_clip_path"],
                    output_final_path=str(final_path),
                    video_title=video_title,
                )

        if dry_run:
            print("\n[Pipeline] Dry-run enabled. Skipping approval and publishing.")
            return

        # Step 8: Clip Approval (Auto-approve by default, no Telegram/WhatsApp needed)
        print("\n--- STAGE 8: APPROVAL ---")
        approved_clips = []
        notifier = None

        if auto_approve:
            print(f"[Approval] Auto-approve active. Approving all {len(clips)} generated clips.")
            for c in clips:
                idx = c["clip_index"]
                c["status"] = "approved"
                update_clip_status(c["id"] if "id" in c else idx, "approved")
                approved_clips.append(c)
        else:
            try:
                notifier = get_notifier()
            except Exception as e:
                print(f"[Approval] Could not initialize notifier ({e}). Falling back to auto-approve.")
                notifier = None

            for c in clips:
                idx = c["clip_index"]
                final_video = c["final_clip_path"]

                if not notifier:
                    c["status"] = "approved"
                    update_clip_status(c["id"] if "id" in c else idx, "approved")
                    approved_clips.append(c)
                    continue

                # Dispatch for approval via Telegram / WhatsApp
                try:
                    notifier.send_short_for_approval(clip=c, video_path=final_video)
                    decision = notifier.check_approval_status(clip_id=idx, timeout_seconds=90)
                    if decision is True:
                        c["status"] = "approved"
                        update_clip_status(c["id"] if "id" in c else idx, "approved")
                        approved_clips.append(c)
                    else:
                        c["status"] = "rejected"
                        update_clip_status(c["id"] if "id" in c else idx, "rejected")
                        print(f"[Approval] Clip #{idx} was not approved (decision={decision}).")
                except Exception as e:
                    print(f"[Approval] Notification error for clip #{idx}: {e}")

        # Step 9: Save Clips Locally and Publish to YouTube
        print("\n--- STAGE 9: PUBLISHING TO YOUTUBE & LOCAL SAVE ---")
        published_count = 0
        for c in approved_clips:
            final_path = c.get("final_clip_path") or c.get("subtitled_clip_path") or c.get("raw_clip_path")
            print(f"[Saved] Short #{c['clip_index']} ready at: {final_path}")

            if getattr(config, "UPLOAD_TO_YOUTUBE", True):
                try:
                    res = publish_approved_clip(c, original_video_title=video_title)
                    published_count += 1
                    c["youtube_url"] = res.get("url", "")
                    c["youtube_video_id"] = res.get("youtube_video_id", "")
                    if notifier:
                        notifier.notify_result(c, f"Successfully published to YouTube: {res['url']}")
                    print(f"[Publish] Clip #{c['clip_index']} uploaded successfully: {res.get('url', '')}")
                    time.sleep(2)
                except Exception as e:
                    print(f"[Publish] Failed to publish clip #{c['clip_index']}: {e}")
                    if notifier:
                        notifier.notify_result(c, f"Publishing failed: {e}")

        # Step 10: Mark Sheet Processed & Clean up
        print("\n--- STAGE 10: COMPLETION & CLEANUP ---")
        if sheet_row_index:
            sheet_mgr.mark_processed(sheet_row_index)

        upsert_video(video_id=video_id, url=video_url, status="processed")

        if cleanup_source:
            if os.path.exists(video_path):
                print(f"[Cleanup] Deleting source video {video_path} to conserve disk space...")
                os.remove(video_path)

        print(f"\n[Pipeline] Run finished successfully! {len(approved_clips)} shorts saved in: {output_dir}")

    except Exception as e:
        print(f"\n[Pipeline Error] Run failed: {e}")
        upsert_video(video_id=video_id, url=video_url, status="failed", error_message=str(e))
        if sheet_row_index:
            try:
                sheet_mgr.mark_failed(sheet_row_index, str(e))
            except Exception:
                pass
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube Long-to-Shorts Automation Pipeline")
    parser.add_argument("--url", type=str, help="Process a specific YouTube URL instead of reading Google Sheet")
    parser.add_argument("--auto-approve", action="store_true", default=None, help="Auto-approve all clips (default: enabled)")
    parser.add_argument("--require-approval", action="store_true", help="Require manual Telegram/WhatsApp approval before publishing")
    parser.add_argument("--dry-run", action="store_true", help="Run cutting and voiceover without publishing")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep source downloaded video on disk")
    args = parser.parse_args()

    auto_approve_flag = None
    if args.require_approval:
        auto_approve_flag = False
    elif args.auto_approve:
        auto_approve_flag = True

    run_daily_pipeline(
        manual_url=args.url,
        auto_approve=auto_approve_flag,
        cleanup_source=not args.no_cleanup,
        dry_run=args.dry_run,
    )
