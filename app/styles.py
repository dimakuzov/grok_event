from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

STYLES = {
    "delo": {
        "button": "Business",
        "title": "Business (tie in the backpack)",
        "text": (
            "Strict and short, like a real person at a conference, not a press office. "
            "Where you were, what you did, one idea. No pomp, no gratitude essays."
        ),
        "image": (
            "Strict, clean conference still. Natural light, modest color, no gloss, no cinematic grade, "
            "no film grain, no glow. No text overlays. Keep real people and venue."
        ),
        "video": (
            "Camera turns VERY slowly, almost locked off. Tiny handheld micro shake ok. "
            "No fly-through, no dolly, no orbit, no slow motion, no bloom, no film look, no extra effects. "
            "People may blink and shift slightly."
        ),
    },
    "startup": {
        "button": "Startup",
        "title": "Startup (pitch in a hoodie)",
        "text": (
            "Like a team-chat note after demo day: upbeat, simple, not corporate. "
            "What you shipped, who you were with, what landed."
        ),
        "image": (
            "Startup energy, laptops, people. Phone snapshot grade. "
            "MUST add a short overlay caption on each photo: 2 to 5 words in the user's language, "
            "bold readable type, not covering faces. Example vibe: shipped it, demo day, we built this. "
            "Do not invent fake logos."
        ),
        "video": (
            "Handheld phone pan, moderate speed. No drone, no slow-mo. Natural room motion."
        ),
    },
    "modnik": {
        "button": "Fashion",
        "title": "Fashion (street with a badge)",
        "text": (
            "Youthful and simple. Lowercase is fine except proper names. "
            "Short on the vibe of the place. No essays."
        ),
        "image": (
            "Street style crop as for stories, fashion color, slightly more cinematic contrast and soft bloom. "
            "Still a phone photo, not a magazine cover. No big text overlays."
        ),
        "video": (
            "Weak slow fly-through or gentle push-in, plus a small pan. Light cinematic grade, soft bloom, "
            "mild film. Not Hollywood, not drone, not orbit, not slow motion."
        ),
    },
    "rock": {
        "button": "Rock star",
        "title": "Rock star (hackathon headliner)",
        "text": (
            "Bold and short, like you are the one in the frame. A joke is fine. No long thank-yous."
        ),
        "image": (
            "You as the main subject, bolder crop, punchier light. "
            "MUST add overlay text on each photo: 2 to 5 words, loud type, in the user's language. "
            "Not covering faces. Vibe like headliner, main character, in the room."
        ),
        "video": (
            "ALWAYS a clear camera pan or turn. Never a locked static shot. Phone handheld, confident sweep. "
            "No slow motion, no drone, no orbit."
        ),
    },
}


def style_brief(style_id: str, custom: str = "", platform: str = "") -> str:
    plat = platform or "instagram"
    if style_id == "custom":
        return (
            f"Target platform: {plat}\n"
            f"User style (highest priority):\n{custom}\n"
            "Apply this to caption, photo edits, overlays, and video motion."
        )
    spec = STYLES[style_id]
    extra = ""
    if custom:
        extra = (
            "\n\nUSER PREFERENCES (highest priority, override the base style on conflict):\n"
            f"{custom}\n"
            "If they ask for polaroid, overlays, italic on-screen text, a handle, camera move, "
            "or film look, put that into edit_prompts and video_prompt exactly. "
            "Keep real people and the real venue."
        )
    return (
        f"Target platform: {plat}\n"
        f"Base style: {spec['title']}\n"
        f"Caption rules: {spec['text']}\n"
        f"Photo edit rules: {spec['image']}\n"
        f"Video motion rules: {spec['video']}"
        f"{extra}"
    )


def style_keyboard(exclude: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key, spec in STYLES.items():
        if key == exclude:
            continue
        row.append(InlineKeyboardButton(text=spec["button"], callback_data=f"style:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Custom style", callback_data="style:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prefs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="No extra notes", callback_data="skip_prefs")]]
    )


def platform_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Instagram", callback_data="platform:instagram"),
                InlineKeyboardButton(text="LinkedIn", callback_data="platform:linkedin"),
            ]
        ]
    )
