from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from app.config import ASPECT_RATIOS, XAI_API_KEY, XAI_MODEL

CAPTION_RULES = """
CAPTION RULES (all styles, no exceptions):
- Write in the language the user actually used in their description and style (if they wrote Russian, write Russian).
- Sound like a person typing a post, not a press release.
- NEVER use the em dash character or en dash. Use a period, comma, or a new sentence.
- NEVER hyphenate words (forbidden: early-career, full-day, youth-business). Rephrase: early career, whole day.
- Hyphens in URLs and hashtags like #GrokBot are ok.
- No gratitude essays. If thanks fit, one short line like "спасибо, я был очень рад!" and stop.
- If the style is youth / simple / modnik, use lowercase except proper names.
- 2-5 short sentences max, then hashtags.
"""

SYSTEM = """You are an SMM planner for event photos.
Look at every photo and the user's description. If the event is named or a URL is given, use web search.

Your job:
1. Write an edit prompt for each photo (Nano Banana 2). Crop dead space (empty wall, ceiling), tighten on people, apply the chosen style grade. Keep real faces, clothes, venue, logos. Do not invent people or brands. Photos must still look like phone shots, not studio campaigns.
2. Score each photo 0-1 for live-photo potential.
3. Pick ONE hero_index for video. Prefer a person or group. Avoid slides, posters, unrelated cutouts.
4. Write video_prompt for the hero AFTER the edit.

VIDEO RULES:
- Follow the style brief video rules exactly. They override the defaults below.
- Default if the brief is silent: lucky handheld phone video, small pan, no drone.
- Rock: camera MUST pan or turn. Never static.
- Delo: extremely slow pan only, no extra effects.
- Modnik: weak slow fly-through plus light cinematic grade is allowed.
- Do not invent people, logos, or big action.

""" + CAPTION_RULES + """

If a style brief is given, merge base style with USER PREFERENCES. User preferences win on conflict: film look, overlays, italic on-screen text, handles, camera move, palette. Keep real people and venue.
If the brief asks for text overlays, put the exact overlay words in each edit_prompt and in video_prompt when they want text in the video.
If nothing should change on a photo besides the style grade, still write a short edit_prompt for that grade.
If the user explained an odd photo (dog, meme), respect that explanation. Include it in the caption only if they want it mentioned.
The target platform is given by the user. Do not pick a different one.
Instagram aspect_ratio: 4:5. LinkedIn aspect_ratio: 16:9.

Return ONLY valid JSON:
{
  "event_type": "short label",
  "platform": "instagram" | "linkedin",
  "tone": "short tone",
  "aspect_ratio": "1:1" | "4:5" | "9:16" | "16:9" | "auto",
  "photos": [
    {
      "index": 0,
      "edit_prompt": "English editing instruction",
      "animate_score": 0.0,
      "animate_notes": "why this score"
    }
  ],
  "hero_index": 0,
  "hero_reason": "why this frame should be animated",
  "video_prompt": "English motion prompt, phone pan or locked-off only",
  "post_text": "caption following CAPTION RULES",
  "summary": "1-2 sentences"
}
photos length MUST equal the photo count. Instagram: 4:5 or 1:1 or 9:16. LinkedIn: 16:9.
Do not invent facts that are not in the photos, user text, or verified by search.
"""

CLARIFY_SYSTEM = """You look at event photos plus the user's description.
Flag a photo ONLY if it is clearly out of context: random pet cutout, meme, unrelated screenshot, something that does not belong to the event room.
Do NOT flag ordinary event photos (hall, crowd, laptops, food at the venue, speakers).
If unsure, do not flag.
At most one outlier. 2 or 3 short questions in the user's language. Practical questions (is the dog yours, does it have a name, should we mention it in the post).
Return ONLY JSON: {"outliers": [{"index": 0, "reason": "short", "questions": ["q1", "q2"]}]}
Empty outliers if nothing is strange.
"""


def _client(timeout: float = 90) -> OpenAI:
    return OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1", timeout=timeout)


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
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"x.ai did not return JSON: {raw[:400]}")
    return json.loads(raw[start : end + 1])


def clean_caption(text: str) -> str:
    text = (text or "").replace("\u2014", ", ").replace("\u2013", ", ").replace(" - ", ", ")
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _normalize(plan: dict[str, Any], photo_count: int, has_style: bool, platform: str = "") -> dict[str, Any]:
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
                "Apply the chosen look, keep real people and venue, crop empty walls. "
                "Must look like a phone photo, not cinema."
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
            "Handheld phone video, camera locked off or a tiny pan. "
            "People blink and shift naturally. No walk-in, no slow motion, no cinematic blur."
        )
    else:
        plan["video_prompt"] = (
            str(plan["video_prompt"]).strip()
            + " Camera static or slight pan only, phone handheld, no slow-mo, no dolly, no cinematic blur."
        )

    if platform in {"instagram", "linkedin"}:
        plan["platform"] = platform
    elif plan.get("platform") not in {"instagram", "linkedin"}:
        plan["platform"] = "instagram"
    if plan["platform"] == "linkedin":
        plan["aspect_ratio"] = "16:9"
    else:
        ratio = str(plan.get("aspect_ratio") or "4:5")
        if ratio not in ASPECT_RATIOS:
            ratio = "4:5"
        if ratio == "16:9":
            ratio = "4:5"
        plan["aspect_ratio"] = ratio
    plan["post_text"] = clean_caption(str(plan.get("post_text") or ""))
    return plan


def _vision_content(photo_urls: list[str], text: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    for i, url in enumerate(photo_urls):
        content.append({"type": "input_text", "text": f"Photo index {i}:"})
        content.append({"type": "input_image", "image_url": url})
    return content


def flag_outliers(photo_urls: list[str], caption: str) -> list[dict[str, Any]]:
    resp = _client(timeout=25).responses.create(
        model=XAI_MODEL,
        input=[
            {"role": "system", "content": CLARIFY_SYSTEM},
            {
                "role": "user",
                "content": _vision_content(
                    photo_urls,
                    f"User description:\n{caption}\nFlag only a clearly unrelated photo. JSON only.",
                ),
            },
        ],
        max_output_tokens=400,
    )
    data = _parse_json(_output_text(resp))
    raw = data.get("outliers") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return []
    item = raw[0]
    if not isinstance(item, dict):
        return []
    try:
        index = int(item.get("index"))
    except (TypeError, ValueError):
        return []
    if not (0 <= index < len(photo_urls)):
        return []
    questions = [str(q).strip() for q in (item.get("questions") or []) if str(q).strip()]
    questions = questions[:3]
    if len(questions) < 2:
        questions = [
            "Это как-то связано с ивентом?",
            "Упоминать это в посте?",
        ]
    return [{"index": index, "reason": str(item.get("reason") or ""), "questions": questions}]


def analyze(
    photo_urls: list[str],
    caption: str,
    style_text: str,
    photo_count: int,
    platform: str = "",
) -> dict[str, Any]:
    plat = platform if platform in {"instagram", "linkedin"} else "instagram"
    resp = _client().responses.create(
        model=XAI_MODEL,
        input=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": _vision_content(
                    photo_urls,
                    (
                        f"Event description from the user:\n{caption}\n\n"
                        f"Photo count: {photo_count}. Index 0 is first.\n"
                        f"Target platform: {plat}\n"
                        f"Style brief:\n{style_text or '(none)'}\n"
                    ),
                ),
            },
        ],
        tools=[{"type": "web_search", "enable_image_understanding": True}],
        reasoning={"effort": "low"},
    )
    plan = _parse_json(_output_text(resp))
    return _normalize(plan, photo_count, has_style=bool(style_text), platform=plat)
