"""Entry point for Render — runs the Telegram bot alongside a health-check web server."""

import logging
import os
import threading
import traceback

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("run")


def start_web():
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("miniapp:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    t = threading.Thread(target=start_web, daemon=True)
    t.start()
    logger.info("Web server started in background thread")

    from main import main as run_bot
    try:
        run_bot()
    except Exception as e:
        logger.error("Bot crashed: %s", e)
        logger.error(traceback.format_exc())
        raise
