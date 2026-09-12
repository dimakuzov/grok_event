# grok_event

Telegram-бот: 1–5 фото с мероприятия + описание → пост (видео + обработанные кадры).

1. Пользователь шлёт альбом (или одно фото) **с подписью**.
2. Опционально — референс стиля (фото или текст) или «Пропустить».
3. Если кадр явно не про ивент, Grok задаёт 2-3 вопроса с этим фото.
4. Пользователь выбирает стиль кнопкой.
5. **x.ai** собирает промпты. **Nano Banana 2** правит фото. **H3 Max Turbo** оживляет hero (камера как с телефона).
6. В чат: 1 видео + остальные фото (hero не дублируется кадром). Потом можно другой стиль.

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
