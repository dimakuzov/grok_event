from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from app.config import ASPECT_RATIOS, XAI_API_KEY, XAI_MODEL

SYSTEM = """You are an SMM planner for event photos.
Look at every photo and the user's description. If the event is named or a URL is given, use web search.

Your job:
1. Write an edit prompt for each photo (Nano Banana / instruction-based editor). Crop dead space (empty wall, ceiling, random backs), tighten framing on people, optional color grade / style match. Keep real faces, clothes, venue, logos that exist in the photo. Do not invent people or brands.
2. Score each photo 0-1 for "live photo" potential (animate-ability): sharp subject, readable faces, depth, natural pose, not a screenshot/slide, not extreme blur.
3. Pick ONE hero_index — the photo that will become a short video. Prefer a human or group with room for subtle motion (blink, breath, crowd, light, camera ease). Avoid slides, posters, food-only, heavy motion blur.
4. Write video_prompt for that hero AFTER the planned edit (describe motion as if the cropped/styled frame is already done). Subtle natural motion only. No new objects, no extra logos, no talking unless a mouth is clearly visible and it fits.

If a style reference is attached or described, every edit_prompt must apply that look.
If nothing should change on a photo, set edit_prompt to "".

Return ONLY valid JSON:
{
  "event_type": "short label",
  "platform": "instagram" | "linkedin",
  "tone": "short tone",
  "aspect_ratio": "1:1" | "4:5" | "9:16" | "16:9" | "auto",
  "photos": [
    {
      "index": 0,
      "edit_prompt": "English editing instruction, or empty string",
      "animate_score": 0.0,
      "animate_notes": "why this score"
    }
  ],
  "hero_index": 0,
  "hero_reason": "why this frame should be animated",
  "video_prompt": "English motion prompt for the edited hero still",
  "post_text": "caption in the user's language, few hashtags",
  "summary": "1-2 sentences"
}
photos length MUST equal the photo count. Instagram: 4:5 or 1:1 or 9:16. LinkedIn: 16:9.
Do not invent facts that are not in the photos or verified by search.
"""


def _client() -> OpenAI:
    return OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")


def _output_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return text
    chunks: list[str] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) in ("output_text", "text"):
                chunks.append(getattr(part, "text", "") or "")
    return "".join(chunks)


def _parse_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"x.ai did not return JSON: {raw[:400]}")
    return json.loads(raw[start : end + 1])


def _normalize(plan: dict[str, Any], photo_count: int, has_style: bool) -> dict[str, Any]:
    photos = plan.get("photos")
    if not isinstance(photos, list):
        photos = []
    by_index: dict[int, dict[str, Any]] = {}
    for item in photos:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[idx] = item

    normalized: list[dict[str, Any]] = []
    for i in range(photo_count):
        item = by_index.get(i, {})
        prompt = str(item.get("edit_prompt") or "").strip()
        if has_style and not prompt:
            prompt = (
                "Match the attached style reference: color, contrast, grain. "
                "Keep the real people and venue. Crop empty walls or unused space if they hurt the frame."
            )
        try:
            score = float(item.get("animate_score"))
        except (TypeError, ValueError):
            score = 0.5
        normalized.append(
            {
                "index": i,
                "edit_prompt": prompt,
                "animate_score": max(0.0, min(1.0, score)),
                "animate_notes": str(item.get("animate_notes") or ""),
            }
        )
    plan["photos"] = normalized

    try:
        hero = int(plan.get("hero_index") or 0)
    except (TypeError, ValueError):
        hero = 0
    if not (0 <= hero < photo_count):
        hero = max(range(photo_count), key=lambda i: normalized[i]["animate_score"])
    plan["hero_index"] = hero

    if not str(plan.get("video_prompt") or "").strip():
        plan["video_prompt"] = (
            "Subtle natural motion: gentle breathing, blinking, light crowd movement, "
            "slow camera ease-in. Keep identity and scene unchanged."
        )

    ratio = str(plan.get("aspect_ratio") or "auto")
    if ratio not in ASPECT_RATIOS:
        ratio = "4:5" if plan.get("platform") == "instagram" else "16:9"
    plan["aspect_ratio"] = ratio
    if plan.get("platform") not in {"instagram", "linkedin"}:
        plan["platform"] = "instagram"
    return plan


def analyze(
    photo_urls: list[str],
    caption: str,
    style_text: str,
    style_url: str | None,
    photo_count: int,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                f"Event description from the user:\n{caption}\n\n"
                f"Photo count: {photo_count}. Photos are in order, index 0 is first.\n"
                f"Optional style instruction: {style_text or '(none)'}\n"
                "If a style reference image is attached, match its visual style.\n"
                "Score animate potential, pick one hero, write edit prompts (crop walls etc.) "
                "and a motion prompt for the hero after that edit."
            ),
        }
    ]
    for i, url in enumerate(photo_urls):
        content.append({"type": "input_text", "text": f"Event photo index {i}:"})
        content.append({"type": "input_image", "image_url": url})
    if style_url:
        content.append({"type": "input_text", "text": "Style reference:"})
        content.append({"type": "input_image", "image_url": style_url})

    resp = _client().responses.create(
        model=XAI_MODEL,
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content},
        ],
        tools=[{"type": "web_search", "enable_image_understanding": True}],
        reasoning={"effort": "low"},
    )
    plan = _parse_json(_output_text(resp))
    return _normalize(plan, photo_count, has_style=bool(style_url or style_text))
