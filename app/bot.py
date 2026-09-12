from __future__ import annotations

import asyncio
import html
import logging
from io import BytesIO

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InputMediaPhoto,
    Message,
)

from app.config import ALBUM_WAIT_SEC, MAX_PHOTOS, MIN_PHOTOS, TELEGRAM_BOT_TOKEN, require_env
from app.fal_media import animate_image, download, edit_image, upload
from app.memory import Photo, Session, get_session
from app.styles import STYLES, platform_keyboard, prefs_keyboard, style_brief, style_keyboard
from app.xai_plan import analyze, flag_outliers

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("grok_event")

router = Router()

HELP = (
    "Send 1-5 photos in one message with a caption "
    "(what the event is and who it is for).\n\n"
    "Pick a platform first, then a style. After the style you can send one taste note "
    "(film, on-screen text, camera) or tap “No extra notes”.\n\n"
    "/cancel reset the job\n"
    "/logs debug logs"
)

STYLE_PROMPT = "What vibe should the post have?"
PLATFORM_PROMPT = "Where are we posting?"
PREFS_PROMPT = (
    "You can send one message to tune the style: film, on-screen text, camera, tone. "
    "The base style stays; your notes win on conflict. Or tap “No extra notes”."
)
RETRY_PROMPT = "Done. Pick another style or send a new photo pack."


def _file_of(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    return None


async def _load_photo(bot: Bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    buf = BytesIO()
    await bot.download_file(tg_file.file_path, buf)
    return buf.getvalue()


def _status_animate(text: str) -> bool:
    first = text.split("\n", 1)[0]
    return not first.startswith("Done") and not first.startswith("Error:")


def _stop_status_anim(session: Session) -> None:
    task = session.status_anim_task
    session.status_anim_task = None
    session.status_animate = False
    if task and not task.done():
        task.cancel()


async def _status_anim_loop(bot: Bot, session: Session) -> None:
    try:
        while True:
            await asyncio.sleep(1)
            if not session.status_animate or not session.status_message_id or not session.status_base:
                return
            session.status_dots = (session.status_dots + 1) % 4
            text = session.status_base + ("." * session.status_dots)
            try:
                await bot.edit_message_text(
                    text,
                    chat_id=session.status_chat_id,
                    message_id=session.status_message_id,
                )
            except Exception:
                pass
    except asyncio.CancelledError:
        return


async def set_status(bot: Bot, session: Session, chat_id: int, text: str) -> None:
    animate = _status_animate(text)
    session.status_base = text.rstrip(".")
    session.status_dots = 0
    _stop_status_anim(session)
    session.status_animate = animate
    if session.status_message_id and session.status_chat_id == chat_id:
        try:
            await bot.edit_message_text(
                session.status_base,
                chat_id=chat_id,
                message_id=session.status_message_id,
            )
            if animate:
                session.status_anim_task = asyncio.create_task(_status_anim_loop(bot, session))
            return
        except Exception:
            pass
    msg = await bot.send_message(chat_id, session.status_base)
    session.status_chat_id = chat_id
    session.status_message_id = msg.message_id
    if animate:
        session.status_anim_task = asyncio.create_task(_status_anim_loop(bot, session))


async def _ensure_uploads(bot: Bot, session: Session) -> None:
    async with session.upload_lock:
        if session.photo_urls and len(session.photo_urls) == len(session.photos):
            return
        session.log("download telegram files")
        for photo in session.photos:
            if not photo.data:
                photo.data = await _load_photo(bot, photo.file_id)
        session.log("upload to fal cdn")
        session.photo_urls = list(
            await asyncio.gather(
                *[asyncio.to_thread(upload, p.data, f"event_{i}.jpg") for i, p in enumerate(session.photos)]
            )
        )


async def clear_status(bot: Bot, session: Session) -> None:
    _stop_status_anim(session)
    if session.status_message_id and session.status_chat_id:
        try:
            await bot.delete_message(
                chat_id=session.status_chat_id,
                message_id=session.status_message_id,
            )
        except Exception:
            pass
    session.status_message_id = None


async def _offer_platform(bot: Bot, chat_id: int, session: Session) -> None:
    session.state = "waiting_platform"
    await bot.send_message(chat_id, PLATFORM_PROMPT, reply_markup=platform_keyboard())


async def _offer_styles(bot: Bot, chat_id: int, session: Session, text: str) -> None:
    session.state = "waiting_style"
    await bot.send_message(chat_id, text, reply_markup=style_keyboard(session.style_id or None))


async def _background_clarify(bot: Bot, chat_id: int, user_id: int, pack_id: int) -> None:
    session = get_session(user_id)
    try:
        await _ensure_uploads(bot, session)
        session.log("outlier check start")
        outliers = await asyncio.to_thread(flag_outliers, session.photo_urls, session.caption)
        session.log(f"outlier check done: {len(outliers)}")
    except asyncio.CancelledError:
        return
    except Exception as exc:
        session.log(f"outlier check failed, skip: {exc}")
        log.exception("outlier check failed")
        return

    session = get_session(user_id)
    if session.pack_id != pack_id:
        return
    if not session.photos:
        return
    if not outliers:
        return

    item = outliers[0]
    if not (0 <= item["index"] < len(session.photos)):
        return
    session.pending_outlier = item
    session.skip_indices.add(item["index"])
    session.log(f"outlier queued photo {item['index']}: {item.get('reason')}")


async def _ask_outlier(bot: Bot, chat_id: int, session: Session) -> bool:
    item = session.pending_outlier
    if not item:
        return False
    idx = int(item["index"])
    if not (0 <= idx < len(session.photos)):
        session.pending_outlier = None
        return False
    session.pending_outlier = None
    session.awaiting_clarify = True
    session.state = "waiting_clarify"
    questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(item["questions"]))
    session.log(f"clarify photo {idx}: {item.get('reason')}")
    await bot.send_photo(chat_id, session.photos[idx].file_id, caption=questions)
    return True


async def _start_job_or_clarify(bot: Bot, chat_id: int, user_id: int) -> None:
    session = get_session(user_id)
    task = session.clarify_task
    if task and not task.done():
        await set_status(bot, session, chat_id, "Still checking the frames")
        try:
            await task
        except asyncio.CancelledError:
            pass
        await clear_status(bot, session)
    session = get_session(user_id)
    if await _ask_outlier(bot, chat_id, session):
        return
    await run_job(bot, chat_id, user_id)


async def _finish_collect(bot: Bot, chat_id: int, user_id: int) -> None:
    session = get_session(user_id)
    if session.state != "collecting":
        return
    if not session.caption.strip():
        session.log("missing caption")
        await bot.send_message(
            chat_id,
            "Need a caption: what the event is and who it is for. Send the photos again with text.",
        )
        session.reset_job()
        return
    if not (MIN_PHOTOS <= len(session.photos) <= MAX_PHOTOS):
        session.log(f"bad photo count {len(session.photos)}")
        await bot.send_message(chat_id, f"Need {MIN_PHOTOS} to {MAX_PHOTOS} photos. Send the pack again.")
        session.reset_job()
        return

    session.pack_id += 1
    pack_id = session.pack_id
    session.log(f"pack ready: {len(session.photos)} photos")
    await _offer_platform(bot, chat_id, session)
    session.clarify_task = asyncio.create_task(_background_clarify(bot, chat_id, user_id, pack_id))


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    session = get_session(message.from_user.id)
    session.reset_job()
    session.log("cancelled")
    await message.answer("Reset. Send a new photo pack with a caption.")


@router.message(Command("logs"))
async def cmd_logs(message: Message) -> None:
    session = get_session(message.from_user.id)
    text = "\n".join(session.logs) if session.logs else "No logs yet."
    if len(text) > 3500:
        await message.answer_document(BufferedInputFile(text.encode("utf-8"), filename="logs.txt"))
        return
    await message.answer(f"<pre>{html.escape(text)}</pre>", parse_mode="HTML")


@router.callback_query(F.data.startswith("platform:"))
async def on_platform(query: CallbackQuery) -> None:
    session = get_session(query.from_user.id)
    if session.state not in {"waiting_platform", "waiting_style", "ready"}:
        await query.answer("Send photos with a caption first")
        return
    key = query.data.split(":", 1)[1]
    if key not in {"instagram", "linkedin"}:
        await query.answer("Unknown platform")
        return
    await query.answer()
    session.platform = key
    session.log(f"platform={key}")
    await _offer_styles(query.bot, query.message.chat.id, session, STYLE_PROMPT)


@router.callback_query(F.data == "skip_prefs")
async def skip_prefs(query: CallbackQuery) -> None:
    session = get_session(query.from_user.id)
    if session.state != "waiting_prefs":
        await query.answer("Not waiting for extra notes")
        return
    await query.answer()
    session.style_text = ""
    await _start_job_or_clarify(query.bot, query.message.chat.id, query.from_user.id)


@router.callback_query(F.data.startswith("style:"))
async def on_style(query: CallbackQuery) -> None:
    session = get_session(query.from_user.id)
    if session.state not in {"waiting_style", "ready"}:
        await query.answer("Pick a platform first")
        return
    if not session.platform:
        await query.answer()
        await _offer_platform(query.bot, query.message.chat.id, session)
        return
    await query.answer()
    key = query.data.split(":", 1)[1]
    if key == "custom":
        session.state = "waiting_custom"
        await query.message.answer("Describe your style in one message. Cover caption and images.")
        return
    if key not in STYLES:
        await query.message.answer("Unknown style")
        return
    session.style_id = key
    session.style_text = ""
    session.state = "waiting_prefs"
    title = STYLES[key]["title"]
    await query.message.answer(
        f"Style: {title}\n{PREFS_PROMPT}",
        reply_markup=prefs_keyboard(),
    )


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    session = get_session(message.from_user.id)
    file_id = _file_of(message)
    if not file_id:
        return

    if session.state == "busy":
        await message.answer("Already building the post. /cancel to abort.")
        return
    if session.state == "waiting_custom":
        await message.answer("For a custom style, text is enough for now.")
        return
    if session.state == "waiting_prefs":
        await message.answer("Send a text note for the style, or tap “No extra notes”.")
        return
    if session.state == "waiting_clarify":
        await message.answer("Reply in text about this photo.")
        return

    if session.state != "collecting":
        session.reset_job()
        session.state = "collecting"

    if message.media_group_id:
        if session.media_group_id and session.media_group_id != message.media_group_id:
            session.photos = []
        session.media_group_id = message.media_group_id
    if message.caption:
        session.caption = message.caption.strip()
    if len(session.photos) >= MAX_PHOTOS:
        return
    session.photos.append(Photo(file_id=file_id))

    if session.collect_task and not session.collect_task.done():
        session.collect_task.cancel()

    async def _flush() -> None:
        try:
            await asyncio.sleep(ALBUM_WAIT_SEC)
            await _finish_collect(bot, message.chat.id, message.from_user.id)
        except asyncio.CancelledError:
            return

    session.collect_task = asyncio.create_task(_flush())


@router.message(F.text)
async def on_text(message: Message, bot: Bot) -> None:
    session = get_session(message.from_user.id)
    if session.state == "waiting_clarify":
        session.clarify = message.text.strip()
        session.awaiting_clarify = False
        lower = session.clarify.lower()
        keep = any(
            w in lower
            for w in (
                "keep",
                "use it",
                "use this",
                "include",
                "it's mine",
                "its mine",
                "mascot",
                "name is",
                "оставь",
                "используй",
                "это мо",
                "к ивенту",
                "талисман",
                "зовут",
            )
        )
        drop = any(
            w in lower
            for w in (
                "don't use",
                "do not use",
                "skip",
                "drop",
                "delete",
                "remove",
                "accident",
                "не использу",
                "не то",
                "случайно",
                "удали",
                "убер",
            )
        )
        if keep and not drop:
            session.skip_indices.clear()
            session.log("clarify: keep odd photo")
            await message.answer("Ok, I'll keep that photo.")
        else:
            session.log("clarify: drop odd photo")
            await message.answer("Ok, that photo won't go in the post.")
        await run_job(bot, message.chat.id, message.from_user.id)
        return
    if session.state == "busy":
        await message.answer("Already working on this. /logs to see progress.")
        return
    if session.state == "waiting_prefs":
        session.style_text = message.text.strip()
        session.log(f"style prefs: {session.style_text[:200]!r}")
        await _start_job_or_clarify(bot, message.chat.id, message.from_user.id)
        return
    if session.state == "waiting_custom":
        session.style_id = "custom"
        session.style_text = message.text.strip()
        await _start_job_or_clarify(bot, message.chat.id, message.from_user.id)
        return
    await message.answer(HELP)


def _skip_edit_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    markers = (
        "do not use",
        "don't use",
        "accidental",
        "exclude this",
        "not part of the event",
        "skip this photo",
        "do not include",
        "не использу",
    )
    return any(m in p for m in markers)


async def run_job(bot: Bot, chat_id: int, user_id: int) -> None:
    session = get_session(user_id)
    if session.state == "busy":
        return
    session.state = "busy"
    _stop_status_anim(session)
    session.status_message_id = None
    try:
        await set_status(bot, session, chat_id, "Analyzing the task")
        await _ensure_uploads(bot, session)
        keep = [i for i in range(len(session.photo_urls)) if i not in session.skip_indices]
        if not keep:
            keep = list(range(len(session.photo_urls)))
        urls = [session.photo_urls[i] for i in keep]
        caption = session.caption
        if session.clarify:
            caption += f"\n\nUser note about an odd photo: {session.clarify}"
        if session.skip_indices:
            caption += "\nDo not mention excluded photos. They must not appear in the post."
        brief = style_brief(session.style_id, session.style_text, session.platform)
        session.log(f"x.ai analyze + web search, photos={keep} skip={sorted(session.skip_indices)} platform={session.platform}")
        plan = await asyncio.to_thread(
            analyze,
            urls,
            caption,
            brief,
            len(urls),
            session.platform,
        )
        session.log(
            f"plan: {plan.get('summary')} platform={plan.get('platform')} "
            f"hero={plan.get('hero_index')} scores="
            f"{[round(p['animate_score'], 2) for p in plan['photos']]}"
        )
        await set_status(bot, session, chat_id, "Creating content")

        async def _edit_one(i: int, src: str) -> str | None:
            prompt = plan["photos"][i]["edit_prompt"]
            if _skip_edit_prompt(prompt):
                session.log(f"image {i}: drop, prompt says not to use")
                return None
            if not prompt:
                session.log(f"image {i}: skip edit")
                return src
            session.log(f"fal edit image {i}: {prompt[:180]}")
            try:
                return await edit_image(src, prompt, plan["aspect_ratio"], [], session.log)
            except Exception as exc:
                if "no_media_generated" in str(exc):
                    session.log(f"image {i}: fal refused, drop photo")
                    return None
                raise

        edited = list(await asyncio.gather(*[_edit_one(i, src) for i, src in enumerate(urls)]))
        pairs = [(i, url) for i, url in enumerate(edited) if url]
        if not pairs:
            raise RuntimeError("No photos left after edits")
        planned = plan["hero_index"]
        hero = edited[planned] if 0 <= planned < len(edited) and edited[planned] else pairs[0][1]
        still_urls = [url for _, url in pairs if url != hero]

        await set_status(bot, session, chat_id, "Creating content, video is queued on Fal")
        session.log(f"fal video from hero={planned}: {plan.get('hero_reason')}")
        video_url = await animate_image(hero, plan["video_prompt"], session.log)
        session.log("download outputs")
        video_bytes = await download(video_url)
        image_blobs = [await download(u) for u in still_urls]

        post = (plan.get("post_text") or "").strip()
        await bot.send_video(
            chat_id,
            BufferedInputFile(video_bytes, filename="event.mp4"),
            caption=post[:1024] or None,
        )
        if len(image_blobs) == 1:
            await bot.send_photo(chat_id, BufferedInputFile(image_blobs[0], filename="event_0.jpg"))
        elif len(image_blobs) > 1:
            media = [
                InputMediaPhoto(media=BufferedInputFile(blob, filename=f"event_{i}.jpg"))
                for i, blob in enumerate(image_blobs)
            ]
            await bot.send_media_group(chat_id, media)
        await set_status(bot, session, chat_id, "Done")
        session.log("done")
        session.state = "ready"
        await bot.send_message(
            chat_id,
            RETRY_PROMPT,
            reply_markup=style_keyboard(session.style_id if session.style_id in STYLES else None),
        )
    except Exception as exc:
        log.exception("job failed")
        session.log(f"ERROR: {exc}")
        await set_status(bot, session, chat_id, f"Error: {exc}\nYou can /logs or /cancel")
        session.state = "waiting_style" if session.photo_urls else "idle"
    finally:
        if session.state == "busy":
            session.state = "ready"


async def main() -> None:
    require_env()
    bot = Bot(TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


def run() -> None:
    asyncio.run(main())
