import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

import config

try:
    import gspread
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


# We request both Sheets and YouTube in one token so you only log in once.
# If you already have a combined token from publish.py, reuse that file.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/youtube.upload",
]

# Where the user token is cached between runs
SHEETS_TOKEN_FILE = os.getenv(
    "SHEETS_TOKEN_FILE",
    str(Path("credentials") / "sheets_token.json"),
)


def _get_oauth_credentials(
    client_secrets_file: str,
    token_file: str,
) -> "Credentials":
    """
    Returns valid OAuth credentials for the signed-in Google account.
    Opens a browser on first run so the user can log in.
    Refreshes silently on every subsequent run.
    """
    creds = None

    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[Sheet] Refreshing Google OAuth token...")
            creds.refresh(Request())
        else:
            if not Path(client_secrets_file).exists():
                raise FileNotFoundError(
                    f"\n  client_secrets.json not found at: {client_secrets_file}\n\n"
                    "  Steps to fix:\n"
                    "  1. Go to console.cloud.google.com\n"
                    "  2. Enable 'Google Sheets API' (same project as YouTube)\n"
                    "  3. APIs & Services > Credentials > Create Credentials > OAuth client ID\n"
                    "  4. Application type: Desktop app\n"
                    "  5. Download JSON and save it as credentials/client_secrets.json\n"
                )
            print("[Sheet] No token found. Opening browser for one-time Google login...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            # port=0 picks any free port automatically — works on any machine
            creds = flow.run_local_server(port=0)

        Path(token_file).parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"[Sheet] Token saved to {token_file}")

    return creds


class GoogleSheetManager:
    def __init__(
        self,
        sheet_name: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        client_secrets_file: Optional[str] = None,
        token_file: Optional[str] = None,
        sheet_id: Optional[str] = None,
    ):
        self.sheet_name = sheet_name or config.GOOGLE_SHEET_NAME
        self.sheet_id = sheet_id or getattr(config, "GOOGLE_SHEET_ID", "")
        self.worksheet_name = worksheet_name or config.GOOGLE_SHEET_WORKSHEET
        self.client_secrets_file = client_secrets_file or config.YOUTUBE_CLIENT_SECRETS_FILE
        self.token_file = token_file or SHEETS_TOKEN_FILE
        self._client = None
        self._worksheet = None

    def _get_client(self):
        if not GSPREAD_AVAILABLE:
            raise RuntimeError(
                "gspread or google-auth-oauthlib is not installed. "
                "Run: pip install -r requirements.txt"
            )
        creds = _get_oauth_credentials(self.client_secrets_file, self.token_file)
        return gspread.authorize(creds)

    def _get_worksheet(self, max_retries: int = 3, backoff: float = 2.0):
        if self._worksheet is not None:
            return self._worksheet

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                if self._client is None:
                    self._client = self._get_client()

                if self.sheet_id:
                    spreadsheet = self._client.open_by_key(self.sheet_id)
                elif self.sheet_name.startswith("http"):
                    spreadsheet = self._client.open_by_url(self.sheet_name)
                else:
                    try:
                        spreadsheet = self._client.open(self.sheet_name)
                    except Exception:
                        spreadsheet = self._client.open_by_key(self.sheet_name)

                self._worksheet = spreadsheet.worksheet(self.worksheet_name)
                return self._worksheet
            except Exception as e:
                last_error = e
                self._client = None  # force re-auth on next attempt
                wait = backoff ** attempt
                print(f"[Sheet] Attempt {attempt} failed: {e}. Retrying in {wait:.0f}s...")
                time.sleep(wait)
        raise RuntimeError(
            f"Could not open sheet '{self.sheet_name}' after {max_retries} attempts: {last_error}"
        )

    def _retry(self, func, max_retries: int = 3):
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except Exception as e:
                last_error = e
                self._worksheet = None
                wait = 2.0 ** attempt
                print(f"[Sheet] Operation failed (attempt {attempt}): {e}. Retrying in {wait:.0f}s...")
                time.sleep(wait)
        raise RuntimeError(f"Sheet operation failed after {max_retries} attempts: {last_error}")

    def _get_column_map(self) -> Dict[str, int]:
        """Returns 1-indexed column positions for known header names."""
        ws = self._get_worksheet()
        headers = [h.strip().lower() for h in ws.row_values(1)]
        col_map = {}
        for i, h in enumerate(headers, start=1):
            if h in ("url", "link", "video_url"):
                col_map["url"] = i
            elif h in ("status", "state"):
                col_map["status"] = i
            elif h in ("processed_at", "completed_at", "processed_time"):
                col_map["processed_at"] = i

        if "url" not in col_map or "status" not in col_map:
            raise ValueError(
                f"Sheet must have 'url' and 'status' columns. Found: {headers}"
            )
        return col_map

    def get_next_pending_video(self) -> Optional[Dict[str, Any]]:
        """
        Returns the first row where status is 'pending' or blank.
        Dict keys: row_index (1-based), url.
        Returns None when the queue is empty.
        """
        def _fetch():
            ws = self._get_worksheet()
            col_map = self._get_column_map()
            all_rows = ws.get_all_values()

            for row_idx, row in enumerate(all_rows[1:], start=2):
                url = row[col_map["url"] - 1].strip() if len(row) >= col_map["url"] else ""
                status = (
                    row[col_map["status"] - 1].strip().lower()
                    if len(row) >= col_map["status"]
                    else ""
                )
                if url and status in ("pending", ""):
                    return {"row_index": row_idx, "url": url}
            return None

        return self._retry(_fetch)

    def mark_processed(self, row_index: int) -> None:
        def _update():
            ws = self._get_worksheet()
            col_map = self._get_column_map()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            ws.update_cell(row_index, col_map["status"], "processed")
            if "processed_at" in col_map:
                ws.update_cell(row_index, col_map["processed_at"], now)
            print(f"[Sheet] Row {row_index} marked as processed at {now}.")

        self._retry(_update)

    def mark_failed(self, row_index: int, reason: str) -> None:
        def _update():
            ws = self._get_worksheet()
            col_map = self._get_column_map()
            short = reason.replace("\n", " ")[:60]
            ws.update_cell(row_index, col_map["status"], f"failed: {short}")
            print(f"[Sheet] Row {row_index} marked as failed: {short}")

        self._retry(_update)


if __name__ == "__main__":
    print("Testing Google Sheet connection (OAuth)...")
    mgr = GoogleSheetManager()
    print(f"Sheet name : {mgr.sheet_name}")
    print(f"Token file : {mgr.token_file}")
    print(f"Secrets    : {mgr.client_secrets_file}")

    if not Path(mgr.client_secrets_file).exists():
        print(
            "\n[Notice] credentials/client_secrets.json not found.\n"
            "Download it from Google Cloud Console (OAuth Desktop App) and try again."
        )
        sys.exit(0)

    try:
        pending = mgr.get_next_pending_video()
        if pending:
            print(f"\nNext pending video: Row {pending['row_index']} -> {pending['url']}")
        else:
            print("\nNo pending videos in the sheet.")
    except Exception as e:
        print(f"Error: {e}")
