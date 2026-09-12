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
    state: str = "idle"
    photos: list[Photo] = field(default_factory=list)
    caption: str = ""
    clarify: str = ""
    style_id: str = ""
    style_text: str = ""
    platform: str = ""
    photo_urls: list[str] = field(default_factory=list)
    media_group_id: str | None = None
    collect_task: asyncio.Task | None = None
    clarify_task: asyncio.Task | None = None
    pack_id: int = 0
    awaiting_clarify: bool = False
    pending_outlier: dict | None = None
    skip_indices: set[int] = field(default_factory=set)
    upload_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    status_chat_id: int | None = None
    status_message_id: int | None = None
    status_base: str = ""
    status_dots: int = 0
    status_animate: bool = False
    status_anim_task: asyncio.Task | None = None
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        line = f"[{_now()}] {message}"
        self.logs.append(line)
        if len(self.logs) > 80:
            self.logs = self.logs[-80:]

    def reset_job(self) -> None:
        if self.collect_task and not self.collect_task.done():
            self.collect_task.cancel()
        if self.clarify_task and not self.clarify_task.done():
            self.clarify_task.cancel()
        if self.status_anim_task and not self.status_anim_task.done():
            self.status_anim_task.cancel()
        self.state = "idle"
        self.photos = []
        self.caption = ""
        self.clarify = ""
        self.style_id = ""
        self.style_text = ""
        self.platform = ""
        self.photo_urls = []
        self.media_group_id = None
        self.collect_task = None
        self.clarify_task = None
        self.status_anim_task = None
        self.status_base = ""
        self.status_dots = 0
        self.status_animate = False
        self.awaiting_clarify = False
        self.pending_outlier = None
        self.skip_indices = set()
        self.status_chat_id = None
        self.status_message_id = None


sessions: dict[int, Session] = {}


def get_session(user_id: int) -> Session:
    if user_id not in sessions:
        sessions[user_id] = Session()
    return sessions[user_id]
