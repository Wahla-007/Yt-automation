import json
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import config
from src.db import upsert_video

try:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
    YOUTUBE_API_AVAILABLE = True
except ImportError:
    YOUTUBE_API_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False


def _interpolate_phrase_to_words(phrase_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Splits coarse YouTube transcript phrases into individual words with estimated timestamps.
    """
    words = []
    for item in phrase_items:
        text = item.get("text", "").replace("\n", " ").strip()
        start = float(item.get("start", 0.0))
        duration = float(item.get("duration", 0.0))
        end = start + duration

        raw_tokens = text.split()
        if not raw_tokens:
            continue

        token_duration = duration / len(raw_tokens)
        current_t = start
        for token in raw_tokens:
            t_end = min(end, current_t + token_duration)
            words.append({
                "word": token,
                "start": round(current_t, 3),
                "end": round(t_end, 3),
                "probability": 1.0,
            })
            current_t = t_end

    return words


def get_youtube_captions(video_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    Tries to retrieve official or auto-generated YouTube captions using youtube-transcript-api.
    Returns word-level timestamps or None if not found/disabled.
    """
    if not YOUTUBE_API_AVAILABLE:
        print("[Transcribe] youtube-transcript-api not installed.")
        return None

    try:
        print(f"[Transcribe] Attempting to fetch existing YouTube captions for {video_id}...")
        raw_items = None

        # 1. Try instance API (youtube-transcript-api >= 1.0)
        try:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id)
            raw_items = [
                {"text": getattr(s, "text", ""), "start": getattr(s, "start", 0.0), "duration": getattr(s, "duration", 0.0)}
                for s in fetched
            ]
        except Exception:
            pass

        # 2. Try legacy static methods (youtube-transcript-api < 1.0)
        if not raw_items and hasattr(YouTubeTranscriptApi, "list_transcripts"):
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                transcript = None
                for method in ["find_manually_created_transcript", "find_generated_transcript"]:
                    try:
                        transcript = getattr(transcript_list, method)(['en', 'en-US', 'en-GB'])
                        if transcript:
                            break
                    except Exception:
                        pass
                if transcript:
                    raw_items = transcript.fetch()
            except Exception:
                pass

        if not raw_items:
            print("[Transcribe] No English transcript found on YouTube.")
            return None

        words = _interpolate_phrase_to_words(raw_items)
        if len(words) < 10:
            print("[Transcribe] YouTube captions too brief/coarse.")
            return None

        print(f"[Transcribe] Successfully fetched {len(words)} words from YouTube captions.")
        return words
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"[Transcribe] Captions unavailable for {video_id}: {e}")
        return None
    except Exception as e:
        print(f"[Transcribe] Error fetching YouTube captions: {e}")
        return None


def transcribe_with_whisper(
    audio_or_video_path: str,
    model_size: Optional[str] = None,
    compute_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Runs faster-whisper on CPU with int8 quantization and VAD filter enabled.
    Returns word-level timestamps.
    """
    if not FASTER_WHISPER_AVAILABLE:
        raise RuntimeError("faster-whisper is not installed. Install requirements.txt.")

    model_name = model_size or config.WHISPER_MODEL
    comp_type = compute_type or config.WHISPER_COMPUTE_TYPE

    print(f"[Transcribe] Running faster-whisper (model={model_name}, compute_type={comp_type}, CPU, VAD=True)...")
    model = WhisperModel(model_name, device="cpu", compute_type=comp_type)

    segments, info = model.transcribe(
        audio_or_video_path,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        word_timestamps=True,
    )

    words = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                cleaned_word = w.word.strip()
                if cleaned_word:
                    words.append({
                        "word": cleaned_word,
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "probability": round(w.probability, 3),
                    })
        else:
            # Fallback if word-level missing in segment
            raw_tokens = segment.text.strip().split()
            if raw_tokens:
                step = (segment.end - segment.start) / len(raw_tokens)
                curr = segment.start
                for t in raw_tokens:
                    words.append({
                        "word": t,
                        "start": round(curr, 3),
                        "end": round(curr + step, 3),
                        "probability": 0.9,
                    })
                    curr += step

    print(f"[Transcribe] Whisper transcription complete: {len(words)} words detected.")
    return words


def transcribe_video(
    video_id: str,
    video_path: str,
    output_words_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Main transcription entrypoint.
    Checks disk cache -> tries YouTube captions -> falls back to faster-whisper.
    Saves words to output_words_path and updates database.
    """
    words_file = Path(
        output_words_path or (config.DOWNLOADS_DIR / f"{video_id}_words.json")
    )
    words_file.parent.mkdir(parents=True, exist_ok=True)

    # Disk cache check
    if words_file.exists() and words_file.stat().st_size > 10:
        print(f"[Transcribe] Using cached words from {words_file}")
        with open(words_file, "r", encoding="utf-8") as f:
            words = json.load(f)
            upsert_video(
                video_id=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                status="transcribed",
                words_path=str(words_file),
            )
            return words

    # Step 1: Try YouTube Captions
    words = get_youtube_captions(video_id)

    # Step 2: Fallback to faster-whisper
    if not words:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found at: {video_path}")
        words = transcribe_with_whisper(video_path)

    # Save to disk
    with open(words_file, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2)

    upsert_video(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        status="transcribed",
        words_path=str(words_file),
    )

    return words


if __name__ == "__main__":
    if len(sys.argv) > 2:
        v_id = sys.argv[1]
        v_path = sys.argv[2]
        result = transcribe_video(v_id, v_path)
        print(f"Transcribed {len(result)} words. Sample: {result[:5]}")
    else:
        print("Usage: python -m src.transcribe <video_id> <video_path>")
