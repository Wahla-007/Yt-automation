import unittest
from unittest.mock import MagicMock
from src.sheet import GoogleSheetManager


class TestGoogleSheetManager(unittest.TestCase):
    def setUp(self):
        self.mgr = GoogleSheetManager(
            sheet_name="TestSheet",
            worksheet_name="Sheet1",
        )
        # Inject a mock worksheet so no real OAuth happens during tests
        self._inject_mock_ws(["URL", "Status", "Processed_At"])

    def _inject_mock_ws(self, headers):
        mock_ws = MagicMock()
        mock_ws.row_values.return_value = headers
        self.mgr._worksheet = mock_ws
        self.mock_ws = mock_ws

    def test_column_indices_mapping(self):
        self.mock_ws.row_values.return_value = ["ID", "URL", "Status", "Notes", "Processed_At"]
        indices = self.mgr._get_column_map()
        self.assertEqual(indices["url"], 2)
        self.assertEqual(indices["status"], 3)
        self.assertEqual(indices["processed_at"], 5)

    def test_get_next_pending_video_finds_first_pending(self):
        self.mock_ws.row_values.return_value = ["URL", "Status", "Processed_At"]
        self.mock_ws.get_all_values.return_value = [
            ["URL", "Status", "Processed_At"],
            ["https://youtu.be/video1", "processed", "2026-10-14 10:00:00 UTC"],
            ["https://youtu.be/video2", "pending", ""],
            ["https://youtu.be/video3", "pending", ""],
        ]

        pending = self.mgr.get_next_pending_video()
        self.assertIsNotNone(pending)
        self.assertEqual(pending["row_index"], 3)
        self.assertEqual(pending["url"], "https://youtu.be/video2")

    def test_get_next_pending_video_returns_none_when_empty(self):
        self.mock_ws.row_values.return_value = ["URL", "Status"]
        self.mock_ws.get_all_values.return_value = [
            ["URL", "Status"],
            ["https://youtu.be/video1", "processed"],
        ]

        pending = self.mgr.get_next_pending_video()
        self.assertIsNone(pending)

    def test_mark_processed(self):
        self.mock_ws.row_values.return_value = ["URL", "Status", "Processed_At"]
        self.mgr.mark_processed(row_index=3)

        calls = self.mock_ws.update_cell.call_args_list
        # First call sets status = "processed" in column 2
        self.assertEqual(calls[0][0][0], 3)
        self.assertEqual(calls[0][0][1], 2)
        self.assertEqual(calls[0][0][2], "processed")


if __name__ == "__main__":
    unittest.main()
