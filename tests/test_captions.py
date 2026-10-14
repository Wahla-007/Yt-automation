import os
import unittest
from pathlib import Path
from src.captions import (
    format_ass_time,
    group_words_into_chunks,
    generate_ass_subtitles,
    create_ass_header,
)


class TestCaptions(unittest.TestCase):
    def test_format_ass_time(self):
        self.assertEqual(format_ass_time(0.0), "0:00:00.00")
        self.assertEqual(format_ass_time(65.45), "0:01:05.45")
        self.assertEqual(format_ass_time(3661.05), "1:01:01.05")

    def test_group_words_into_chunks(self):
        words = [
            {"word": "Hello", "start": 10.0, "end": 10.5},
            {"word": "world", "start": 10.6, "end": 11.0},
            {"word": "this", "start": 11.1, "end": 11.3},
            {"word": "is", "start": 11.4, "end": 11.5},
            {"word": "testing.", "start": 11.6, "end": 12.0},
        ]
        # Clip from 10.0 to 15.0
        chunks = group_words_into_chunks(words, clip_start=10.0, clip_end=15.0, max_words_per_chunk=3)
        self.assertTrue(len(chunks) >= 2)
        # Check relative times
        self.assertAlmostEqual(chunks[0]["start"], 0.0, places=2)
        self.assertTrue("HELLO" in chunks[0]["text"])

    def test_generate_ass_file(self):
        words = [
            {"word": "Step", "start": 1.0, "end": 1.5},
            {"word": "one", "start": 1.6, "end": 2.0},
        ]
        out_file = Path("data/output/unittest_captions.ass")
        generate_ass_subtitles(words, clip_start=0.0, clip_end=5.0, output_ass_path=str(out_file))
        self.assertTrue(out_file.exists())
        content = out_file.read_text(encoding="utf-8")
        self.assertIn("[Script Info]", content)
        self.assertIn("STEP ONE", content)
        out_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
