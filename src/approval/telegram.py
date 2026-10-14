import json
import time
from pathlib import Path
from typing import Dict, Any, Optional

import requests
import config
from src.approval.base import ApprovalNotifier
from src.db import update_clip_status


class TelegramNotifier(ApprovalNotifier):
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.last_update_id = 0

    def _post(self, method: str, data: Optional[Dict] = None, files: Optional[Dict] = None):
        if not self.bot_token or not self.chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        url = f"{self.base_url}/{method}"
        resp = requests.post(url, data=data, files=files, timeout=45)
        resp.raise_for_status()
        return resp.json()

    def send_short_for_approval(self, clip: Dict[str, Any], video_path: str) -> str:
        idx = clip.get("clip_index", 1)
        title = clip.get("title", f"Short #{idx}")
        hook = clip.get("hook", "")
        tags = clip.get("tags", [])
        if isinstance(tags, str):
            tags_str = tags
        else:
            tags_str = " ".join([f"#{t.replace('#', '')}" for t in tags])

        caption = (
            f"🎬 Short Approval Request (Clip #{idx})\n\n"
            f"📌 Title: {title}\n"
            f"🪝 Hook: {hook}\n"
            f"🏷️ Tags: {tags_str}\n\n"
            f"Reply with 'approve {idx}' or 'reject {idx}', or tap below:"
        )

        # Inline buttons for instant 1-tap approval
        inline_keyboard = {
            "inline_keyboard": [
                [
                    {"text": f"✅ Approve #{idx}", "callback_data": f"approve_{idx}"},
                    {"text": f"❌ Reject #{idx}", "callback_data": f"reject_{idx}"},
                ]
            ]
        }

        print(f"[Approval:Telegram] Uploading {video_path} to Telegram chat {self.chat_id}...")
        with open(video_path, "rb") as video_file:
            res = self._post(
                "sendVideo",
                data={
                    "chat_id": self.chat_id,
                    "caption": caption[:1024],
                    "reply_markup": json.dumps(inline_keyboard),
                    "supports_streaming": True,
                },
                files={"video": (Path(video_path).name, video_file, "video/mp4")},
            )

        message_id = str(res.get("result", {}).get("message_id", ""))
        update_clip_status(clip.get("id", idx), "pending_approval")
        return message_id

    def check_approval_status(self, clip_id: int, timeout_seconds: int = 30) -> Optional[bool]:
        """
        Polls getUpdates for a response matching this clip index.
        Recognizes callback button clicks or text messages 'approve <clip_id>' / 'reject <clip_id>'.
        """
        start_time = time.time()
        print(f"[Approval:Telegram] Waiting for approval response for clip #{clip_id} (timeout={timeout_seconds}s)...")

        while (time.time() - start_time) < timeout_seconds:
            try:
                params = {"timeout": 10}
                if self.last_update_id:
                    params["offset"] = self.last_update_id + 1

                res = requests.get(f"{self.base_url}/getUpdates", params=params, timeout=15).json()
                updates = res.get("result", [])

                for u in updates:
                    self.last_update_id = u["update_id"]

                    # 1. Check callback query (inline button press)
                    if "callback_query" in u:
                        data = u["callback_query"].get("data", "")
                        cq_id = u["callback_query"].get("id")
                        # Acknowledge callback query
                        try:
                            requests.post(f"{self.base_url}/answerCallbackQuery", json={"callback_query_id": cq_id})
                        except Exception:
                            pass

                        if data == f"approve_{clip_id}":
                            print(f"[Approval:Telegram] Clip #{clip_id} APPROVED via button.")
                            return True
                        elif data == f"reject_{clip_id}":
                            print(f"[Approval:Telegram] Clip #{clip_id} REJECTED via button.")
                            return False

                    # 2. Check text reply
                    if "message" in u and "text" in u["message"]:
                        text = u["message"]["text"].strip().lower()
                        if text in [f"approve {clip_id}", f"yes {clip_id}", f"approve #{clip_id}"]:
                            print(f"[Approval:Telegram] Clip #{clip_id} APPROVED via message.")
                            return True
                        elif text in [f"reject {clip_id}", f"no {clip_id}", f"reject #{clip_id}"]:
                            print(f"[Approval:Telegram] Clip #{clip_id} REJECTED via message.")
                            return False

            except Exception as e:
                print(f"[Approval:Telegram] Poll error: {e}")

            time.sleep(3)

        return None

    def notify_result(self, clip: Dict[str, Any], message: str) -> None:
        idx = clip.get("clip_index", 1)
        text = f"📢 Clip #{idx} Update:\n{message}"
        try:
            self._post("sendMessage", data={"chat_id": self.chat_id, "text": text})
        except Exception as e:
            print(f"[Approval:Telegram] Failed to send notification: {e}")
