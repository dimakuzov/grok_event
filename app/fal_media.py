from __future__ import annotations

import os
import tempfile
from typing import Any, Callable

import fal_client
import httpx

from app.config import FAL_IMAGE_MODEL, FAL_VIDEO_MODEL

LogFn = Callable[[str], None]


def upload(data: bytes, filename: str = "photo.jpg") -> str:
    suffix = os.path.splitext(filename)[1] or ".jpg"
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return fal_client.upload_file(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


def _on_log(log: LogFn):
    def _inner(status: Any) -> None:
        logs = getattr(status, "logs", None) or []
        for item in logs:
            msg = item.get("message") if isinstance(item, dict) else getattr(item, "message", None)
            if msg:
                log(str(msg))

    return _inner


def _video_url(result: dict[str, Any]) -> str:
    video = result.get("video") or {}
    url = video.get("url")
    if not url:
        raise RuntimeError(f"Fal video has no url: {result}")
    return url


_FLUX_SIZE = {
    "1:1": "square_hd",
    "4:5": "auto",
    "3:4": "portrait_4_3",
    "9:16": "portrait_16_9",
    "16:9": "landscape_16_9",
    "3:2": "landscape_4_3",
    "4:3": "landscape_4_3",
}


def _edit_args(image_urls: list[str], prompt: str, aspect_ratio: str) -> dict[str, Any]:
    model = FAL_IMAGE_MODEL
    if "nano-banana" in model:
        return {
            "prompt": prompt,
            "image_urls": image_urls,
            "num_images": 1,
            "aspect_ratio": aspect_ratio,
            "output_format": "jpeg",
            "resolution": "1K",
        }
    return {
        "prompt": prompt,
        "image_urls": image_urls,
        "output_format": "jpeg",
        "image_size": _FLUX_SIZE.get(aspect_ratio, "auto"),
    }


async def edit_image(
    image_url: str,
    prompt: str,
    aspect_ratio: str,
    extra_urls: list[str],
    log: LogFn,
) -> str:
    image_urls = [image_url, *extra_urls]
    result = await fal_client.subscribe_async(
        FAL_IMAGE_MODEL,
        arguments=_edit_args(image_urls, prompt, aspect_ratio),
        with_logs=True,
        on_queue_update=_on_log(log),
    )
    return result["images"][0]["url"]


def _video_args(image_url: str, prompt: str) -> dict[str, Any]:
    model = FAL_VIDEO_MODEL
    if "h3-max" in model:
        return {
            "prompt": prompt,
            "image_url": image_url,
            "duration": 5,
            "resolution": "768P",
            "prompt_expansion_mode": "balanced",
        }
    if "kling-video" in model:
        return {
            "start_image_url": image_url,
            "prompt": prompt,
            "duration": "5",
            "generate_audio": False,
            "negative_prompt": "morphing faces, extra people, extra logos, text artifacts, warp",
        }
    if "veo" in model:
        return {
            "image_url": image_url,
            "prompt": prompt,
            "duration": "4s",
            "generate_audio": False,
            "resolution": "720p",
        }
    return {
        "image_url": image_url,
        "prompt": prompt,
        "duration": "6",
        "prompt_optimizer": True,
        "resolution": "768P",
    }


async def animate_image(image_url: str, prompt: str, log: LogFn) -> str:
    result = await fal_client.subscribe_async(
        FAL_VIDEO_MODEL,
        arguments=_video_args(image_url, prompt),
        with_logs=True,
        on_queue_update=_on_log(log),
    )
    return _video_url(result)
