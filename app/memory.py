from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


@dataclass
class Photo:
    file_id: str
    data: bytes = b""


@dataclass
class Session:
    state: str = "idle"  # idle | collecting | waiting_style | busy
    photos: list[Photo] = field(default_factory=list)
    caption: str = ""
    style_text: str = ""
    style_photo: Photo | None = None
    media_group_id: str | None = None
    collect_task: asyncio.Task | None = None
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        line = f"[{_now()}] {message}"
        self.logs.append(line)
        if len(self.logs) > 80:
            self.logs = self.logs[-80:]

    def reset_job(self) -> None:
        if self.collect_task and not self.collect_task.done():
            self.collect_task.cancel()
        self.state = "idle"
        self.photos = []
        self.caption = ""
        self.style_text = ""
        self.style_photo = None
        self.media_group_id = None
        self.collect_task = None


sessions: dict[int, Session] = {}


def get_session(user_id: int) -> Session:
    if user_id not in sessions:
        sessions[user_id] = Session()
    return sessions[user_id]
