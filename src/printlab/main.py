"""PrintLab Auto - Main entry point."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram.ext import Application

from .bot.handlers import PrintLabBot, setup_handlers


def load_config() -> dict:
    """Load configuration from environment variables."""
    # Load .env file if it exists
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    config = {
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "anthropic_key": os.getenv("ANTHROPIC_API_KEY"),
        "x1c_ip": os.getenv("X1C_IP"),
        "x1c_access_code": os.getenv("X1C_ACCESS_CODE"),
        "x1c_serial": os.getenv("X1C_SERIAL"),
        "bed_type": os.getenv("BED_TYPE", "auto"),
        "api_port": os.getenv("API_PORT"),
        "api_key": os.getenv("API_KEY"),
    }

    # Parse allowed users
    allowed_users_str = os.getenv("ALLOWED_USERS", "")
    if allowed_users_str:
        config["allowed_users"] = [
            int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()
        ]
    else:
        config["allowed_users"] = None

    # Validate required config
    missing = []
    for key in ["telegram_token", "anthropic_key", "x1c_ip", "x1c_access_code", "x1c_serial"]:
        if not config.get(key):
            missing.append(key.upper())

    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}")
        print("Please set these in your .env file or environment.")
        sys.exit(1)

    return config


async def _run_api(bot: PrintLabBot, port: int, api_key: Optional[str] = None):
    """Start the FastAPI server in the background."""
    try:
        import uvicorn
        from .api.server import create_app
    except ImportError:
        print("Error: FastAPI/uvicorn not installed. Run: pip install -e '.[api]'")
        return

    # Share the bot's printer and search provider with the API
    api_app = create_app(
        printer=bot.printer,
        search=bot.search_providers[0] if bot.search_providers else None,
        api_key=api_key,
    )

    config = uvicorn.Config(api_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main_async():
    """Async main function."""
    print("🖨️  PrintLab Auto starting...")

    config = load_config()

    # Create bot instance
    bot = PrintLabBot(
        telegram_token=config["telegram_token"],
        anthropic_key=config["anthropic_key"],
        x1c_ip=config["x1c_ip"],
        x1c_access_code=config["x1c_access_code"],
        x1c_serial=config["x1c_serial"],
        allowed_users=config["allowed_users"],
        bed_type=config["bed_type"],
    )

    # Build Telegram application
    app = Application.builder().token(config["telegram_token"]).build()

    # Set up handlers
    setup_handlers(app, bot)

    # Optionally start REST API
    api_port = config.get("api_port")
    api_task = None
    if api_port:
        api_task = asyncio.create_task(
            _run_api(bot, int(api_port), config.get("api_key"))
        )
        print(f"🌐 REST API starting on port {api_port}")

    # Start the bot
    print("✅ Bot is running. Press Ctrl+C to stop.")

    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
            poll_interval=1.0,
        )

        # Keep running until interrupted
        stop_event = asyncio.Event()
        await stop_event.wait()

    except asyncio.CancelledError:
        pass
    finally:
        print("\n🛑 Shutting down...")
        if api_task:
            api_task.cancel()
        await bot.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    """Main entry point."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
