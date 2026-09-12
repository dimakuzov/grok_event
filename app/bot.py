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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from app.config import ALBUM_WAIT_SEC, MAX_PHOTOS, MIN_PHOTOS, TELEGRAM_BOT_TOKEN, require_env
from app.fal_media import animate_image, download, edit_image, upload
from app.memory import Photo, get_session
from app.xai_plan import analyze

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("grok_event")

router = Router()

HELP = (
    "Пришли одним сообщением 1–5 фото и описание события в подписи "
    "(что за ивент и для кого).\n\n"
    "Потом можно опционально прислать референс стиля — фото или текст — "
    "или нажать «Пропустить».\n\n"
    "/cancel — сбросить текущую задачу\n"
    "/logs — последние логи для отладки"
)

SKIP_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip_style")]]
)


def _file_of(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    return None


async def _load_photo(bot: Bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    buf = BytesIO()
    await bot.download_file(tg_file.file_path, buf)
    return buf.getvalue()


async def _finish_collect(bot: Bot, chat_id: int, user_id: int) -> None:
    session = get_session(user_id)
    if session.state != "collecting":
        return
    if not session.caption.strip():
        session.log("missing caption")
        await bot.send_message(
            chat_id,
            "Нужна подпись: что за событие и для кого. Пришли фото заново, уже с текстом.",
        )
        session.reset_job()
        return
    if not (MIN_PHOTOS <= len(session.photos) <= MAX_PHOTOS):
        session.log(f"bad photo count {len(session.photos)}")
        await bot.send_message(chat_id, f"Нужно от {MIN_PHOTOS} до {MAX_PHOTOS} фото. Пришли пачку заново.")
        session.reset_job()
        return
    n = len(session.photos)
    session.state = "waiting_style"
    session.log(f"pack ready: {n} photos, caption={session.caption[:120]!r}")
    await bot.send_message(
        chat_id,
        f"Получил {n} фото и описание.\n"
        "Референс стиля — по желанию: одно фото или короткий текст. "
        "Или нажми «Пропустить».",
        reply_markup=SKIP_KB,
    )


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    session = get_session(message.from_user.id)
    session.reset_job()
    session.log("cancelled")
    await message.answer("Сбросил. Пришли новую пачку фото с описанием.")


@router.message(Command("logs"))
async def cmd_logs(message: Message) -> None:
    session = get_session(message.from_user.id)
    text = "\n".join(session.logs) if session.logs else "Логов пока нет."
    if len(text) > 3500:
        await message.answer_document(BufferedInputFile(text.encode("utf-8"), filename="logs.txt"))
        return
    await message.answer(f"<pre>{html.escape(text)}</pre>", parse_mode="HTML")


@router.callback_query(F.data == "skip_style")
async def skip_style(query: CallbackQuery) -> None:
    session = get_session(query.from_user.id)
    if session.state != "waiting_style":
        await query.answer("Сейчас не жду референс")
        return
    await query.answer()
    session.style_text = ""
    session.style_photo = None
    await query.message.answer("Ок, без референса. Начинаю.")
    await run_job(query.bot, query.message.chat.id, query.from_user.id)


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    session = get_session(message.from_user.id)
    file_id = _file_of(message)
    if not file_id:
        return

    if session.state == "busy":
        await message.answer("Сейчас уже собираю контент. /cancel если надо оборвать.")
        return

    if session.state == "waiting_style":
        session.style_photo = Photo(file_id=file_id)
        session.style_text = (message.caption or "").strip()
        session.log("got style photo")
        await message.answer("Референс принял. Начинаю.")
        await run_job(bot, message.chat.id, message.from_user.id)
        return

    if session.state == "idle":
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
    if session.state == "busy":
        await message.answer("Уже работаю над задачей. /logs если нужно посмотреть ход.")
        return
    if session.state == "waiting_style":
        session.style_text = message.text.strip()
        session.style_photo = None
        session.log(f"got style text: {session.style_text[:120]!r}")
        await message.answer("Стиль принял. Начинаю.")
        await run_job(bot, message.chat.id, message.from_user.id)
        return
    await message.answer(HELP)


async def run_job(bot: Bot, chat_id: int, user_id: int) -> None:
    session = get_session(user_id)
    if session.state == "busy":
        return
    session.state = "busy"
    try:
        session.log("download telegram files")
        for photo in session.photos:
            photo.data = await _load_photo(bot, photo.file_id)
        style_bytes = None
        if session.style_photo:
            style_bytes = await _load_photo(bot, session.style_photo.file_id)

        session.log("upload to fal cdn")
        photo_urls = list(
            await asyncio.gather(
                *[asyncio.to_thread(upload, p.data, f"event_{i}.jpg") for i, p in enumerate(session.photos)]
            )
        )
        style_url = await asyncio.to_thread(upload, style_bytes, "style.jpg") if style_bytes else None
        session.log(f"cdn photos={len(photo_urls)} style={bool(style_url)}")

        session.log("x.ai analyze + web search")
        plan = await asyncio.to_thread(
            analyze,
            photo_urls,
            session.caption,
            session.style_text,
            style_url,
            len(photo_urls),
        )
        session.log(
            f"plan: {plan.get('summary')} platform={plan.get('platform')} "
            f"hero={plan.get('hero_index')} scores="
            f"{[round(p['animate_score'], 2) for p in plan['photos']]}"
        )
        await bot.send_message(chat_id, "Задача проанализирована")

        await bot.send_message(chat_id, "Контент создаётся")
        extra = [style_url] if style_url else []
        aspect = plan["aspect_ratio"]

        async def _edit_one(i: int, src: str) -> str:
            prompt = plan["photos"][i]["edit_prompt"]
            if not prompt:
                session.log(f"image {i}: skip edit")
                return src
            session.log(f"fal edit image {i}: {prompt[:180]}")
            return await edit_image(src, prompt, aspect, extra, session.log)

        image_urls = list(
            await asyncio.gather(*[_edit_one(i, src) for i, src in enumerate(photo_urls)])
        )
        hero = image_urls[plan["hero_index"]]
        session.log(f"fal video from edited hero {plan['hero_index']}: {plan.get('hero_reason')}")
        video_url = await animate_image(hero, plan["video_prompt"], session.log)
        session.log("download outputs")
        video_bytes = await download(video_url)
        image_blobs = [await download(u) for u in image_urls]

        caption = (plan.get("post_text") or "").strip()
        await bot.send_video(
            chat_id,
            BufferedInputFile(video_bytes, filename="event.mp4"),
            caption=caption[:1024] or None,
        )
        media = [
            InputMediaPhoto(media=BufferedInputFile(blob, filename=f"event_{i}.jpg"))
            for i, blob in enumerate(image_blobs)
        ]
        if len(media) == 1:
            await bot.send_photo(chat_id, media[0].media)
        else:
            await bot.send_media_group(chat_id, media)
        session.log("done")
        await bot.send_message(chat_id, "Готово. Можно прислать следующую пачку.")
    except Exception as exc:
        log.exception("job failed")
        session.log(f"ERROR: {exc}")
        await bot.send_message(
            chat_id,
            f"Ошибка: {exc}\nМожно /logs или прислать пачку заново.",
        )
    finally:
        logs_backup = session.logs
        session.reset_job()
        session.logs = logs_backup
        session.state = "idle"


async def main() -> None:
    require_env()
    bot = Bot(TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


def run() -> None:
    asyncio.run(main())
