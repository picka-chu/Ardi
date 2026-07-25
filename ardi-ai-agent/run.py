"""Entry point for Render — runs the Telegram bot alongside a health-check web server."""

import logging
import os
import threading
import traceback

import uvicorn

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("run")


def start_bot():
    import main as bot_main
    try:
        bot_main.main()
    except Exception as e:
        logger.error("Bot thread crashed: %s", e)
        logger.error(traceback.format_exc())
        raise


if __name__ == "__main__":
    t = threading.Thread(target=start_bot, daemon=True)
    t.start()

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("miniapp:app", host="0.0.0.0", port=port, log_level="info")
