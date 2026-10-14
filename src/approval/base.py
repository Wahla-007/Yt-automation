from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class ApprovalNotifier(ABC):
    """
    Abstract interface for human-in-the-loop approval of generated YouTube Shorts.
    """

    @abstractmethod
    def send_short_for_approval(
        self,
        clip: Dict[str, Any],
        video_path: str,
    ) -> str:
        """
        Sends the finished short to the operator along with title, hook, and tags.
        Returns a message/dispatch identifier.
        """
        pass

    @abstractmethod
    def check_approval_status(
        self,
        clip_id: int,
        timeout_seconds: int = 30,
    ) -> Optional[bool]:
        """
        Polls or checks if operator replied with 'approve' (True) or 'reject' (False).
        Returns None if pending / no response within timeout.
        """
        pass

    @abstractmethod
    def notify_result(
        self,
        clip: Dict[str, Any],
        message: str,
    ) -> None:
        """Sends a confirmation or alert after publishing or failure."""
        pass
