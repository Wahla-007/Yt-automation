import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

import config
from src.db import upsert_clip


def cut_and_format_clip(
    source_video_path: str,
    start_time: float,
    end_time: float,
    output_path: str,
    target_width: int = 1080,
    target_height: int = 1920,
    fade_duration: float = 0.4,
) -> str:
    """
    Cuts a segment from source_video_path, center-crops to 9:16 (target_width x target_height),
    and applies smooth audio fade-in and fade-out.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    # Apply safety end-trim buffer to avoid single-frame scene transition leak from next clip
    trim_buffer = getattr(config, "SAFETY_END_TRIM", 0.12)
    trimmed_end = max(start_time + 1.0, end_time - trim_buffer)
    duration = max(0.5, round(trimmed_end - start_time, 2))

    # Calculate audio fade out point
    fade_out_start = max(0.0, duration - fade_duration)
    audio_filter = f"afade=t=in:st=0:d={fade_duration},afade=t=out:st={fade_out_start:.2f}:d={fade_duration}"

    fit_mode = getattr(config, "VIDEO_FIT_MODE", "blur").lower()
    if fit_mode == "crop":
        # Classic center crop (crops left & right edges)
        filter_complex = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},setsar=1[vout];"
            f"[0:a]{audio_filter}[aout]"
        )
    elif fit_mode == "pad":
        # Letterbox with black bars (no cropping)
        filter_complex = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vout];"
            f"[0:a]{audio_filter}[aout]"
        )
    else:
        # Default: 'blur' -> 100% of the original video frame visible in the center (0% frame loss!),
        # with matching blurred video background on top & bottom.
        filter_complex = (
            f"[0:v]scale=108:192:force_original_aspect_ratio=increase,"
            f"crop=108:192,boxblur=5:5,scale={target_width}:{target_height},eq=brightness=-0.15[bg];"
            f"[0:v]scale={target_width}:-2:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout];"
            f"[0:a]{audio_filter}[aout]"
        )

    cmd = [
        config.FFMPEG_BINARY,
        "-y",
        "-ss", str(start_time),
        "-to", str(trimmed_end),
        "-i", source_video_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return output_path
    except FileNotFoundError:
        raise RuntimeError("ffmpeg executable not found in PATH. Install ffmpeg to continue.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg cut failed for clip {start_time}-{end_time}:\n{e.stderr[-1000:]}")


def cut_all_clips(
    video_id: str,
    source_video_path: str,
    clips: List[Dict[str, Any]],
    output_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Iterates over candidate clips, cuts and formats each into short_01.mp4, short_02.mp4, etc.
    Updates SQLite state.
    """
    out_dir = output_dir or (config.OUTPUT_DIR / video_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for c in clips:
        idx = c["clip_index"]
        filename = f"short_{idx:02d}.mp4"
        out_file = out_dir / filename

        # Disk cache check
        if out_file.exists() and out_file.stat().st_size > 1000:
            print(f"[Cut] Clip #{idx} already exists at {out_file}, skipping cut.")
            c["raw_clip_path"] = str(out_file)
            results.append(c)
            continue

        print(f"[Cut] Cutting clip #{idx}: {c['start_time']}s to {c['end_time']}s ({c['duration']}s)...")
        cut_and_format_clip(
            source_video_path=source_video_path,
            start_time=c["start_time"],
            end_time=c["end_time"],
            output_path=str(out_file),
            target_width=config.TARGET_WIDTH,
            target_height=config.TARGET_HEIGHT,
        )

        c["raw_clip_path"] = str(out_file)
        upsert_clip(
            video_id=video_id,
            clip_index=idx,
            raw_clip_path=str(out_file),
            status="cut",
        )
        results.append(c)

    return results


if __name__ == "__main__":
    print("Cut module loaded. To test: python -m src.cut <source_video> <start> <end> <out.mp4>")
