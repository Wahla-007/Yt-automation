import time
from typing import Dict, Any, Optional

import config
from src.approval.base import ApprovalNotifier
from src.db import update_clip_status

try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


class WhatsAppNotifier(ApprovalNotifier):
    def __init__(self):
        self.account_sid = config.TWILIO_ACCOUNT_SID
        self.auth_token = config.TWILIO_AUTH_TOKEN
        self.from_num = config.TWILIO_WHATSAPP_FROM
        self.to_num = config.WHATSAPP_TO
        self._client = None

    def _get_client(self):
        if not TWILIO_AVAILABLE:
            raise RuntimeError("twilio package not installed. Install requirements.txt.")
        if not self.account_sid or not self.auth_token:
            raise ValueError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured in .env")
        if self._client is None:
            self._client = TwilioClient(self.account_sid, self.auth_token)
        return self._client

    def send_short_for_approval(self, clip: Dict[str, Any], video_path: str) -> str:
        client = self._get_client()
        idx = clip.get("clip_index", 1)
        title = clip.get("title", f"Short #{idx}")
        hook = clip.get("hook", "")
        tags = clip.get("tags", [])
        tags_str = " ".join([f"#{t.replace('#', '')}" for t in tags]) if isinstance(tags, list) else tags

        body = (
            f"🎬 YouTube Short Ready For Approval (Clip #{idx})\n\n"
            f"📌 Title: {title}\n"
            f"🪝 Hook: {hook}\n"
            f"🏷️ Tags: {tags_str}\n\n"
            f"Reply 'approve {idx}' or 'reject {idx}'"
        )

        print(f"[Approval:WhatsApp] Sending approval request to {self.to_num}...")
        msg = client.messages.create(
            from_=self.from_num,
            to=self.to_num,
            body=body,
        )
        update_clip_status(clip.get("id", idx), "pending_approval")
        return msg.sid

    def check_approval_status(self, clip_id: int, timeout_seconds: int = 30) -> Optional[bool]:
        """
        Checks incoming Twilio messages for 'approve <id>' or 'reject <id>'.
        """
        client = self._get_client()
        start_time = time.time()
        print(f"[Approval:WhatsApp] Checking messages for response (timeout={timeout_seconds}s)...")

        while (time.time() - start_time) < timeout_seconds:
            try:
                # Check recent inbound messages
                messages = client.messages.list(to=self.from_num, limit=5)
                for m in messages:
                    text = (m.body or "").strip().lower()
                    if text in [f"approve {clip_id}", f"yes {clip_id}", f"approve"]:
                        print(f"[Approval:WhatsApp] Clip #{clip_id} APPROVED via WhatsApp.")
                        return True
                    elif text in [f"reject {clip_id}", f"no {clip_id}", f"reject"]:
                        print(f"[Approval:WhatsApp] Clip #{clip_id} REJECTED via WhatsApp.")
                        return False
            except Exception as e:
                print(f"[Approval:WhatsApp] Error checking incoming messages: {e}")

            time.sleep(5)

        return None

    def notify_result(self, clip: Dict[str, Any], message: str) -> None:
        client = self._get_client()
        idx = clip.get("clip_index", 1)
        body = f"📢 Short #{idx} Update: {message}"
        try:
            client.messages.create(
                from_=self.from_num,
                to=self.to_num,
                body=body,
            )
        except Exception as e:
            print(f"[Approval:WhatsApp] Failed to send notification: {e}")
