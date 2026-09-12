# grok_event

Telegram-бот: 1–5 фото с мероприятия + описание → пост (видео + обработанные кадры).

1. Пользователь шлёт альбом (или одно фото) **с подписью**.
2. Опционально — референс стиля (фото или текст) или «Пропустить».
3. **x.ai (Grok)** смотрит кадры, при необходимости ищет событие в интернете: промпты правок, оценка «какой кадр оживить», motion-промпт.
4. **Fal Nano Banana 2** правит фото (кроп стены, стиль). **Потом Kling 3** оживляет уже отредактированный hero-кадр.
5. Бот присылает результат в чат. Статусы: «Задача проанализирована» → «Контент создаётся». `/logs` — отладка.

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# вписать TELEGRAM_BOT_TOKEN, XAI_API_KEY, FAL_KEY
python -m app
```

Токен бота: [@BotFather](https://t.me/BotFather). Ключи: [console.x.ai](https://console.x.ai), [fal.ai/dashboard](https://fal.ai/dashboard).
