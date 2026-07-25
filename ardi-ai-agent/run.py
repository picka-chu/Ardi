"""Entry point for Render — runs the Telegram bot alongside a health-check web server."""

import os
import threading
import uvicorn
from main import main as run_bot


def start_bot():
    run_bot()


if __name__ == "__main__":
    t = threading.Thread(target=start_bot, daemon=True)
    t.start()

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("miniapp:app", host="0.0.0.0", port=port, log_level="info")
