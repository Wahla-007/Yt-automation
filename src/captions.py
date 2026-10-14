import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import config


def format_ass_time(seconds: float) -> str:
    """Converts seconds (e.g. 12.345) to ASS timestamp format: H:MM:SS.cs"""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        secs += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def create_ass_header(
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    font_name: str = "Arial Black",
    font_size: int = 70,
    margin_v: int = 380,
) -> str:
    """
    Builds the ASS script header.
    Lower-third placement: Alignment=2 (Bottom-Center) with MarginV=380 keeps captions
    safely above YouTube Shorts bottom metadata and like/comment buttons.
    """
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,40,40,{margin_v},1
Style: Highlight,{font_name},{font_size},&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def group_words_into_chunks(
    words: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    max_words_per_chunk: int = 3,
    max_chunk_duration: float = 1.6,
) -> List[Dict[str, Any]]:
    """
    Filters words inside [clip_start, clip_end] and groups them into short chunks of 2-3 words.
    Adjusts timestamps so 0.0 is the start of the clip.
    """
    clip_words = []
    for w in words:
        w_start = float(w["start"])
        w_end = float(w["end"])
        word_text = str(w.get("word", "")).strip()

        if not word_text:
            continue
        # Check overlap with clip window
        if w_end > clip_start and w_start < clip_end:
            rel_start = max(0.0, w_start - clip_start)
            rel_end = max(rel_start + 0.1, min(clip_end - clip_start, w_end - clip_start))
            clip_words.append({
                "word": word_text.upper(),
                "start": rel_start,
                "end": rel_end,
            })

    if not clip_words:
        return []

    chunks = []
    current_chunk = []
    chunk_start = clip_words[0]["start"]

    for w in clip_words:
        if not current_chunk:
            chunk_start = w["start"]
            current_chunk.append(w)
            continue

        would_exceed_count = len(current_chunk) >= max_words_per_chunk
        would_exceed_time = (w["end"] - chunk_start) > max_chunk_duration
        is_sentence_ender = any(current_chunk[-1]["word"].endswith(p) for p in [".", "!", "?"])

        if would_exceed_count or would_exceed_time or is_sentence_ender:
            chunks.append({
                "start": chunk_start,
                "end": max(current_chunk[-1]["end"], chunk_start + 0.3),
                "text": " ".join([item["word"] for item in current_chunk]),
                "words": current_chunk,
            })
            current_chunk = [w]
            chunk_start = w["start"]
        else:
            current_chunk.append(w)

    if current_chunk:
        chunks.append({
            "start": chunk_start,
            "end": max(current_chunk[-1]["end"], chunk_start + 0.3),
            "text": " ".join([item["word"] for item in current_chunk]),
            "words": current_chunk,
        })

    return chunks


def generate_ass_subtitles(
    words: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    output_ass_path: str,
    highlight_active_word: bool = True,
) -> str:
    """
    Generates an ASS subtitle file from word timestamps for a specific clip range.
    Uses bold, punchy capital text with black stroke, centered in the lower third.
    """
    Path(output_ass_path).parent.mkdir(parents=True, exist_ok=True)
    chunks = group_words_into_chunks(words, clip_start, clip_end)

    events = []
    for chunk in chunks:
        c_start = format_ass_time(chunk["start"])
        c_end = format_ass_time(chunk["end"])

        # Clean punctuation for display if needed, but preserve natural reading
        clean_text = chunk["text"]

        if highlight_active_word and len(chunk["words"]) > 1:
            # Word-level karaoke / active highlight inside the chunk
            # or simple pop line
            events.append(
                f"Dialogue: 0,{c_start},{c_end},Default,,0,0,0,,{clean_text}"
            )
        else:
            events.append(
                f"Dialogue: 0,{c_start},{c_end},Default,,0,0,0,,{clean_text}"
            )

    ass_content = create_ass_header() + "\n".join(events) + "\n"
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    return output_ass_path


def escape_ffmpeg_path(path_str: str) -> str:
    r"""
    Escapes windows file paths for use in FFmpeg filters like subtitles or ass.
    Backslashes must become forward slashes and colons escaped with \:
    """
    p = os.path.abspath(path_str).replace("\\", "/")
    # Escape colon (e.g. C:/ -> C\:/)
    p = p.replace(":", "\\:")
    p = p.replace("'", "\\'")
    return p


def burn_captions(
    video_path: str,
    ass_path: str,
    output_video_path: str,
) -> str:
    """
    Burns the ASS subtitles into the video file using FFmpeg.
    Uses fast preset and CRF 18 for crisp text and sharp video.
    """
    Path(output_video_path).parent.mkdir(parents=True, exist_ok=True)
    escaped_ass = escape_ffmpeg_path(ass_path)

    cmd = [
        config.FFMPEG_BINARY,
        "-y",
        "-i", video_path,
        "-vf", f"ass='{escaped_ass}'",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        output_video_path,
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return output_video_path
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is not installed or not in system PATH.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg subtitle burn failed:\n{e.stderr[-1000:]}")


if __name__ == "__main__":
    print("Testing Captions module with synthetic word data...")
    dummy_words = [
        {"word": "The", "start": 0.2, "end": 0.5},
        {"word": "secret", "start": 0.5, "end": 0.9},
        {"word": "to", "start": 0.9, "end": 1.1},
        {"word": "scaling", "start": 1.1, "end": 1.6},
        {"word": "YouTube", "start": 1.7, "end": 2.2},
        {"word": "Shorts", "start": 2.2, "end": 2.7},
        {"word": "is", "start": 2.8, "end": 3.0},
        {"word": "consistency.", "start": 3.0, "end": 3.8},
        {"word": "You", "start": 4.1, "end": 4.3},
        {"word": "don't", "start": 4.3, "end": 4.7},
        {"word": "need", "start": 4.7, "end": 5.0},
        {"word": "fancy", "start": 5.0, "end": 5.5},
        {"word": "editing.", "start": 5.5, "end": 6.2},
    ]

    out_ass = "data/output/test_captions.ass"
    generate_ass_subtitles(
        words=dummy_words,
        clip_start=0.0,
        clip_end=7.0,
        output_ass_path=out_ass,
    )
    print(f"Generated ASS subtitle file: {out_ass}")
    with open(out_ass, "r", encoding="utf-8") as f:
        print(f.read())
