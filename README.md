# grok_event

Telegram bot: 1–5 event photos plus a caption → a post (one video + edited stills).

1. The user sends an album (or one photo) **with a caption**.
2. They pick a platform, then a style. After the style they can add one taste note (film, overlays, camera) or tap **No extra notes**.
3. If a shot is clearly off-event, Grok asks 2–3 questions with that photo attached.
4. **x.ai** writes the prompts. **Nano Banana 2** edits photos. **H3 Max Turbo** animates the hero (phone-camera motion).
5. The chat gets 1 video + the remaining photos (the hero is not duplicated as a still). Then they can retry another style.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, XAI_API_KEY, FAL_KEY
python -m app
```

Bot token: [@BotFather](https://t.me/BotFather). Keys: [console.x.ai](https://console.x.ai), [fal.ai/dashboard](https://fal.ai/dashboard).
