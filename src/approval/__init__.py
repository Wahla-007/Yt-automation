from typing import Optional
import config
from src.approval.base import ApprovalNotifier
from src.approval.telegram import TelegramNotifier
from src.approval.whatsapp import WhatsAppNotifier


def get_notifier(provider: Optional[str] = None) -> ApprovalNotifier:
    """
    Factory returning the configured approval notifier (telegram or whatsapp).
    """
    chosen = (provider or config.APPROVAL_PROVIDER).lower()
    if chosen == "whatsapp":
        return WhatsAppNotifier()
    elif chosen == "telegram":
        return TelegramNotifier()
    else:
        print(f"[Approval] Unknown provider '{chosen}', defaulting to Telegram.")
        return TelegramNotifier()
