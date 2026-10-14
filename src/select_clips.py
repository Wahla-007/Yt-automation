import json
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import config
from src.db import upsert_clip, upsert_video
from src.ai_client import get_ai_completion


def group_words_into_sentences(
    words: List[Dict[str, Any]],
    max_pause_seconds: float = 1.0,
) -> List[Dict[str, Any]]:
    """
    Groups word-level timestamps into natural sentences based on terminal punctuation
    or silence gaps greater than max_pause_seconds.
    """
    if not words:
        return []

    sentences = []
    curr_words = []
    sentence_idx = 0

    for i, w in enumerate(words):
        curr_words.append(w)
        word_text = str(w.get("word", "")).strip()
        is_terminal = any(word_text.endswith(p) for p in [".", "!", "?"])

        # Check pause gap to next word
        is_long_pause = False
        if i + 1 < len(words):
            next_start = float(words[i + 1]["start"])
            curr_end = float(w["end"])
            if (next_start - curr_end) >= max_pause_seconds:
                is_long_pause = True

        if is_terminal or is_long_pause or i == len(words) - 1:
            s_start = float(curr_words[0]["start"])
            s_end = float(curr_words[-1]["end"])
            s_text = " ".join([item.get("word", "").strip() for item in curr_words])

            sentences.append({
                "index": sentence_idx,
                "start": round(s_start, 3),
                "end": round(s_end, 3),
                "duration": round(s_end - s_start, 3),
                "text": s_text,
                "words": curr_words,
            })
            sentence_idx += 1
            curr_words = []

    return sentences


def select_clips_with_llm(
    sentences: List[Dict[str, Any]],
    video_title: str = "",
    min_duration: int = 20,
    max_duration: int = 30,
    target_count: int = 4,
) -> List[Dict[str, Any]]:
    """
    Selects 3-5 non-overlapping clip definitions snapped to sentence boundaries using AI.
    """

    # Format indexed transcript
    transcript_lines = []
    for s in sentences:
        transcript_lines.append(f"[{s['index']}] ({s['start']:.1f}s - {s['end']:.1f}s): {s['text']}")
    indexed_transcript = "\n".join(transcript_lines)

    system_prompt = (
        "You are an expert YouTube Shorts editor. Your task is to select 3 to 5 non-overlapping "
        "viral clip segments from a long-form video transcript.\n"
        f"Each clip must be between {min_duration} and {max_duration} seconds in duration.\n"
        "Rules:\n"
        "1. Select clips with high retention: a strong curiosity hook in the first 3 seconds, "
        "valuable context, and a clear punchline or takeaway.\n"
        "2. Do NOT overlap sentence ranges between clips.\n"
        "3. Snap precisely to sentence index boundaries (start_index to end_index inclusive).\n"
        "4. Return strict JSON format with a list called 'clips'."
    )

    user_prompt = f"""Video Title: {video_title}

Indexed Sentence Transcript:
{indexed_transcript}

Please select {target_count} compelling, non-overlapping clips (target duration: {min_duration}-{max_duration} seconds each).
Return a JSON object in this exact schema:
{{
  "clips": [
    {{
      "start_index": 0,
      "end_index": 3,
      "hook": "Curiosity-inducing opening sentence or phrase",
      "title": "Punchy YouTube Shorts Title (under 60 chars)",
      "tags": ["shorts", "tag1", "tag2"],
      "reason": "Why this snippet works well as a short"
    }}
  ]
}}"""

    content = get_ai_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    data = json.loads(content)
    raw_clips = data.get("clips", [])

    resolved_clips = []
    num_sentences = len(sentences)
    occupied_intervals = []

    for idx, c in enumerate(raw_clips, start=1):
        s_idx = max(0, min(int(c.get("start_index", 0)), num_sentences - 1))
        e_idx = max(s_idx, min(int(c.get("end_index", s_idx)), num_sentences - 1))

        start_time = sentences[s_idx]["start"]
        end_time = sentences[e_idx]["end"]
        duration = round(end_time - start_time, 2)

        # Check collision with previous clips
        has_overlap = any(
            not (end_time <= prev_start or start_time >= prev_end)
            for prev_start, prev_end in occupied_intervals
        )
        if has_overlap:
            print(f"[Select] Skipping clip {idx} due to overlap ({start_time}s - {end_time}s).")
            continue

        occupied_intervals.append((start_time, end_time))

        clip_sentences = sentences[s_idx : e_idx + 1]
        clip_text = " ".join([s["text"] for s in clip_sentences])

        tags = c.get("tags", [])
        if "shorts" not in [t.lower() for t in tags]:
            tags.append("shorts")

        resolved_clips.append({
            "clip_index": idx,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "start_sentence_index": s_idx,
            "end_sentence_index": e_idx,
            "title": str(c.get("title", f"Short #{idx}")).strip(),
            "hook": str(c.get("hook", "")).strip(),
            "tags": tags,
            "reason": str(c.get("reason", "")).strip(),
            "transcript": clip_text,
        })

    return resolved_clips


def select_clips_for_video(
    video_id: str,
    words: List[Dict[str, Any]],
    video_title: str = "",
    output_clips_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Main clip selection pipeline stage.
    Groups words -> sentences -> LLM selection -> stores in DB and clips.json.
    """
    clips_file = Path(
        output_clips_path or (config.DOWNLOADS_DIR / f"{video_id}_clips.json")
    )

    # Disk cache check
    if clips_file.exists() and clips_file.stat().st_size > 10:
        print(f"[Select] Using cached clips from {clips_file}")
        with open(clips_file, "r", encoding="utf-8") as f:
            clips = json.load(f)
            for c in clips:
                upsert_clip(
                    video_id=video_id,
                    clip_index=c["clip_index"],
                    start_time=c["start_time"],
                    end_time=c["end_time"],
                    duration=c["duration"],
                    title=c["title"],
                    hook=c["hook"],
                    tags=",".join(c.get("tags", [])),
                    status="selected",
                )
            return clips

    sentences = group_words_into_sentences(words)
    if not sentences:
        raise ValueError(f"No sentences could be formed for video {video_id}.")

    clips = select_clips_with_llm(
        sentences=sentences,
        video_title=video_title,
        min_duration=config.MIN_CLIP_DURATION,
        max_duration=config.MAX_CLIP_DURATION,
        target_count=config.TARGET_CLIPS_COUNT,
    )

    # Save to disk
    with open(clips_file, "w", encoding="utf-8") as f:
        json.dump(clips, f, indent=2)

    # Upsert into database
    for c in clips:
        upsert_clip(
            video_id=video_id,
            clip_index=c["clip_index"],
            start_time=c["start_time"],
            end_time=c["end_time"],
            duration=c["duration"],
            title=c["title"],
            hook=c["hook"],
            tags=",".join(c.get("tags", [])),
            status="selected",
        )

    upsert_video(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        status="clips_selected",
        clips_path=str(clips_file),
    )

    print(f"[Select] Selected {len(clips)} candidate clips for {video_id}.")
    return clips


if __name__ == "__main__":
    print("Testing select_clips sentence grouping with sample data...")
    test_words = [
        {"word": "Welcome", "start": 0.0, "end": 0.5},
        {"word": "back", "start": 0.5, "end": 0.9},
        {"word": "everyone.", "start": 0.9, "end": 1.4},
        {"word": "Today", "start": 2.0, "end": 2.3},
        {"word": "we", "start": 2.3, "end": 2.5},
        {"word": "talk", "start": 2.5, "end": 2.8},
        {"word": "about", "start": 2.8, "end": 3.0},
        {"word": "automation.", "start": 3.0, "end": 3.6},
    ]
    sents = group_words_into_sentences(test_words)
    print(f"Grouped into {len(sents)} sentences:")
    for s in sents:
        print(f" - [{s['index']}] ({s['start']}s - {s['end']}s): {s['text']}")
