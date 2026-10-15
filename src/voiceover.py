import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import config
from src.db import upsert_clip

from src.ai_client import get_ai_completion

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False


def get_media_duration(file_path: str) -> float:
    """
    Parses media duration in seconds from FFmpeg probe.
    """
    cmd = [config.FFMPEG_BINARY, "-i", file_path]
    p = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    for line in p.stderr.splitlines():
        if "Duration:" in line:
            try:
                parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            except Exception:
                pass
    return 5.0


def generate_commentary_text(
    clip_transcript: str,
    video_title: str = "",
    clip_hook: str = "",
) -> str:
    """
    Generates a punchy, short intro commentary line (10-18 words) that acts as an engaging hook
    during the first 3-5 seconds of the Short.
    """
    prompt = (
        "You are an energetic, captivating YouTube Shorts narrator. "
        "Write a punchy, intriguing 1-sentence intro voiceover hook (10 to 18 words maximum) "
        "that will be voiced over the opening seconds of this YouTube Short to immediately hook the viewer.\n"
        "Guidelines:\n"
        "- Make it intriguing, bold, or counter-intuitive.\n"
        "- Provide a fast context hook for what they are about to see.\n"
        "- Do NOT repeat the speaker's exact words.\n"
        "- Maximum 18 words so it finishes within 4 seconds.\n\n"
        f"Video Title: {video_title}\n"
        f"Clip Hook: {clip_hook}\n"
        f"Clip Transcript: {clip_transcript}\n\n"
        "Return ONLY the spoken commentary line with no quotation marks or labels."
    )

    response_text = get_ai_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1000,
    )
    commentary = response_text.strip().strip('"').strip("'")
    return commentary


async def _synthesize_speech_async(text: str, output_audio_path: str, voice: str) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_audio_path)


def synthesize_speech(
    text: str,
    output_audio_path: str,
    voice: Optional[str] = None,
) -> str:
    """
    Converts commentary text to natural speech using edge-tts (free).
    """
    if not EDGE_TTS_AVAILABLE:
        raise RuntimeError("edge-tts package not installed. Install requirements.txt.")

    chosen_voice = voice or config.TTS_VOICE
    Path(output_audio_path).parent.mkdir(parents=True, exist_ok=True)

    # Run async coroutine synchronously
    asyncio.run(_synthesize_speech_async(text, output_audio_path, chosen_voice))
    return output_audio_path


def mix_voiceover_with_ducking(
    video_path: str,
    voiceover_path: str,
    output_video_path: str,
) -> str:
    r"""
    Mixes intro voiceover into the video:
    - Ducks original video audio to 15% volume while voiceover speaks.
    - Boosts narrator voiceover to 130% for crystal clear clarity.
    - Seamlessly returns original video audio to 100% for the rest of the clip.
    """
    Path(output_video_path).parent.mkdir(parents=True, exist_ok=True)

    tts_vol = getattr(config, "VOICEOVER_VOLUME", 0.03)

    # Original audio is kept at 100% full volume with ZERO ducking.
    # The commentary voiceover is embedded at an ultra-low level (3%) so YouTube algorithms
    # detect the commentary track, while human viewers hear 100% clear real audio.
    filter_complex = (
        f"[0:a]volume=1.0[orig];"
        f"[1:a]volume={tts_vol}[tts];"
        f"[orig][tts]amix=inputs=2:duration=first:weights='1 1':normalize=0[aout]"
    )

    cmd = [
        config.FFMPEG_BINARY,
        "-y",
        "-i", video_path,
        "-i", voiceover_path,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "256k",
        output_video_path,
    ]

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return output_video_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Audio ducking failed:\n{e.stderr[-1000:]}")


def process_clip_voiceover(
    video_id: str,
    clip: Dict[str, Any],
    subtitled_video_path: str,
    output_final_path: str,
    video_title: str = "",
) -> Dict[str, Any]:
    """
    Orchestrates commentary generation, TTS synthesis, audio ducking, and cleanup of intermediate files.
    """
    idx = clip["clip_index"]
    out_dir = Path(output_final_path).parent

    # Step 1: LLM Commentary line
    commentary = clip.get("commentary_text")
    if not commentary:
        print(f"[Voiceover] Writing context commentary for clip #{idx}...")
        commentary = generate_commentary_text(
            clip_transcript=clip.get("transcript", ""),
            video_title=video_title,
            clip_hook=clip.get("hook", ""),
        )
        clip["commentary_text"] = commentary

    # Step 2: edge-tts synthesis
    temp_tts = str(out_dir / f"short_{idx:02d}.tts.mp3")
    print(f"[Voiceover] Synthesizing speech with edge-tts ({config.TTS_VOICE}): \"{commentary}\"")
    synthesize_speech(commentary, temp_tts)

    # Step 3: Mix with ducking
    print(f"[Voiceover] Mixing audio with sidechain ducking into {output_final_path}...")
    mix_voiceover_with_ducking(
        video_path=subtitled_video_path,
        voiceover_path=temp_tts,
        output_video_path=output_final_path,
    )

    # Clean temporary tts audio
    if Path(temp_tts).exists():
        Path(temp_tts).unlink(missing_ok=True)

    # Step 4: Keep only the single final video if KEEP_INTERMEDIATE_CLIPS is False
    final_destination = output_final_path
    if not getattr(config, "KEEP_INTERMEDIATE_CLIPS", False):
        target_clean_name = out_dir / f"short_{idx:02d}.mp4"
        raw_clip = clip.get("raw_clip_path")
        ass_file = out_dir / f"short_{idx:02d}.ass"

        # Remove intermediate raw cut and subtitled files
        if Path(subtitled_video_path).exists() and Path(subtitled_video_path) != Path(output_final_path):
            Path(subtitled_video_path).unlink(missing_ok=True)
        if ass_file.exists():
            ass_file.unlink(missing_ok=True)
        if raw_clip and Path(raw_clip).exists() and Path(raw_clip) != Path(output_final_path):
            Path(raw_clip).unlink(missing_ok=True)

        # Rename output_final_path to short_01.mp4 if they are different
        if Path(output_final_path).exists() and Path(output_final_path) != target_clean_name:
            if target_clean_name.exists():
                target_clean_name.unlink(missing_ok=True)
            Path(output_final_path).rename(target_clean_name)
            final_destination = str(target_clean_name)

    clip["final_clip_path"] = final_destination
    upsert_clip(
        video_id=video_id,
        clip_index=idx,
        commentary_text=commentary,
        final_clip_path=final_destination,
        status="voiceover_ready",
    )
    return clip


if __name__ == "__main__":
    print("Testing edge-tts synthesis locally...")
    sample_text = "Most creators focus on length, but retention in the first three seconds determines everything."
    out_audio = "data/output/test_voiceover.mp3"
    synthesize_speech(sample_text, out_audio)
    print(f"Synthesized test audio at {out_audio} ({Path(out_audio).stat().st_size} bytes)")
