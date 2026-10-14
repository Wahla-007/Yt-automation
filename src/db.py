import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import config

def get_connection() -> sqlite3.Connection:
    Path(config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                video_path TEXT,
                words_path TEXT,
                clips_path TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                clip_index INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                duration REAL NOT NULL,
                title TEXT,
                hook TEXT,
                tags TEXT,
                commentary_text TEXT,
                raw_clip_path TEXT,
                subtitled_clip_path TEXT,
                final_clip_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                youtube_video_id TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
                UNIQUE(video_id, clip_index)
            );
        """)
    conn.close()

def upsert_video(video_id: str, url: str, **kwargs) -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    fields = ["id", "url", "updated_at"]
    values = [video_id, url, now]
    updates = ["updated_at = excluded.updated_at"]

    for k, v in kwargs.items():
        fields.append(k)
        values.append(v)
        updates.append(f"{k} = excluded.{k}")

    sql = f"""
        INSERT INTO videos ({', '.join(fields)})
        VALUES ({', '.join(['?'] * len(values))})
        ON CONFLICT(id) DO UPDATE SET {', '.join(updates)}
    """
    with conn:
        conn.execute(sql, values)
    conn.close()

def get_video(video_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_video_by_url(url: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM videos WHERE url = ?", (url,)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_clip(video_id: str, clip_index: int, **kwargs) -> int:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id FROM clips WHERE video_id = ? AND clip_index = ?",
        (video_id, clip_index)
    ).fetchone()

    if existing:
        clip_id = existing["id"]
        set_clauses = ["updated_at = ?"]
        values = [now]
        for k, v in kwargs.items():
            set_clauses.append(f"{k} = ?")
            values.append(v)
        values.append(clip_id)
        with conn:
            conn.execute(f"UPDATE clips SET {', '.join(set_clauses)} WHERE id = ?", values)
    else:
        fields = ["video_id", "clip_index", "updated_at"]
        values = [video_id, clip_index, now]
        for k, v in kwargs.items():
            fields.append(k)
            values.append(v)
        with conn:
            cursor = conn.execute(
                f"INSERT INTO clips ({', '.join(fields)}) VALUES ({', '.join(['?'] * len(values))})",
                values
            )
            clip_id = cursor.lastrowid
    conn.close()
    return clip_id

def get_clips_for_video(video_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM clips WHERE video_id = ? ORDER BY clip_index ASC", (video_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_clip_by_id(clip_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_clip_status(clip_id: int, status: str, error_message: Optional[str] = None) -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "UPDATE clips SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, error_message, now, clip_id),
        )
    conn.close()

init_db()
