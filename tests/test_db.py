import unittest
from pathlib import Path
import config
from src.db import (
    init_db,
    upsert_video,
    get_video,
    upsert_clip,
    get_clips_for_video,
    update_clip_status,
)


class TestDB(unittest.TestCase):
    def setUp(self):
        config.DATABASE_PATH = "data/test_pipeline.db"
        Path(config.DATABASE_PATH).unlink(missing_ok=True)
        init_db()

    def tearDown(self):
        Path(config.DATABASE_PATH).unlink(missing_ok=True)

    def test_video_and_clip_lifecycle(self):
        upsert_video("v123", "https://youtube.com/watch?v=v123", title="Test Title", status="pending")
        v = get_video("v123")
        self.assertIsNotNone(v)
        self.assertEqual(v["title"], "Test Title")
        self.assertEqual(v["status"], "pending")

        clip_id = upsert_clip(
            video_id="v123",
            clip_index=1,
            start_time=10.0,
            end_time=35.0,
            duration=25.0,
            title="Hooky Title",
            hook="Wait for it...",
            tags="shorts,viral",
            status="cut",
        )
        self.assertGreater(clip_id, 0)

        clips = get_clips_for_video("v123")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["clip_index"], 1)
        self.assertEqual(clips[0]["status"], "cut")

        update_clip_status(clip_id, "approved")
        clips_updated = get_clips_for_video("v123")
        self.assertEqual(clips_updated[0]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
