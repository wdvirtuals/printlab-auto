"""PrintClaw — Entry point. Starts the conversational TG bot + ACP seller."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("printclaw")


def load_config() -> dict:
    """Load configuration from .env."""
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    config = {
        # PrintClaw Telegram bot
        "printclaw_tg_token": os.getenv("PRINTCLAW_TG_TOKEN"),
        "anthropic_key": os.getenv("ANTHROPIC_API_KEY"),
        "owner_chat_id": os.getenv("PRINTCLAW_OWNER_CHAT_ID"),
        # ACP seller (all optional)
        "virtuals_wallet_address": os.getenv("VIRTUALS_AGENT_WALLET_ADDRESS"),
        "virtuals_private_key": os.getenv("VIRTUALS_WALLET_PRIVATE_KEY"),
        "virtuals_entity_id": os.getenv("VIRTUALS_ENTITY_ID"),
        # REST API connection
        "api_key": os.getenv("API_KEY"),
        "api_port": os.getenv("API_PORT", "8000"),
        # Printer config (for embedded API server)
        "x1c_ip": os.getenv("X1C_IP", ""),
        "x1c_access_code": os.getenv("X1C_ACCESS_CODE", ""),
        "x1c_serial": os.getenv("X1C_SERIAL", ""),
        "bed_type": os.getenv("BED_TYPE", "auto"),
    }

    # Validate minimum config
    missing = []
    if not config["printclaw_tg_token"]:
        missing.append("PRINTCLAW_TG_TOKEN")
    if not config["anthropic_key"]:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"Error: Missing required env vars: {', '.join(missing)}")
        sys.exit(1)

    return config


async def _start_api_server(config: dict) -> Optional[asyncio.Task]:
    """Start the embedded PrintLab REST API server."""
    try:
        import uvicorn
        from printlab.bot.handlers import PrintLabBot
        from printlab.api.server import create_app
    except ImportError:
        logger.warning("FastAPI/uvicorn not installed. Run: pip install -e '.[api]'")
        return None

    # Create a PrintLabBot just to get the printer + search instances
    bot_backend = PrintLabBot(
        telegram_token="unused",
        anthropic_key=config["anthropic_key"],
        x1c_ip=config["x1c_ip"],
        x1c_access_code=config["x1c_access_code"],
        x1c_serial=config["x1c_serial"],
        bed_type=config["bed_type"],
    )

    api_app = create_app(
        printer=bot_backend.printer,
        search=bot_backend.search_providers[0] if bot_backend.search_providers else None,
        api_key=config["api_key"],
    )

    port = int(config["api_port"])
    uvi_config = uvicorn.Config(api_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(uvi_config)
    task = asyncio.create_task(server.serve())
    logger.info("REST API starting on port %d", port)

    # Give it a moment to bind
    await asyncio.sleep(1)
    return task


async def main_async():
    logger.info("PrintClaw starting...")

    config = load_config()

    # 1. Start embedded REST API
    api_task = await _start_api_server(config)

    # 2. Create API client
    from .api_client import PrintLabAPIClient

    port = int(config["api_port"])
    api_client = PrintLabAPIClient(
        base_url=f"http://localhost:{port}",
        api_key=config["api_key"],
    )

    # 3. Create TG bot
    from .bot import PrintClawBot
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    owner_chat_id = int(config["owner_chat_id"]) if config.get("owner_chat_id") else None

    claw_bot = PrintClawBot(
        telegram_token=config["printclaw_tg_token"],
        anthropic_key=config["anthropic_key"],
        api_client=api_client,
        owner_chat_id=owner_chat_id,
    )

    tg_app = Application.builder().token(config["printclaw_tg_token"]).build()
    tg_app.add_handler(CommandHandler("start", claw_bot.start_command))
    tg_app.add_handler(CommandHandler("status", claw_bot.status_command))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, claw_bot.message_handler))
    claw_bot._app = tg_app

    # 4. Start ACP seller (if configured)
    acp_seller = None
    has_acp = all([
        config.get("virtuals_wallet_address"),
        config.get("virtuals_private_key"),
        config.get("virtuals_entity_id"),
    ])

    if has_acp:
        from .acp_seller import ACPSellerAgent

        acp_seller = ACPSellerAgent(
            wallet_address=config["virtuals_wallet_address"],
            wallet_private_key=config["virtuals_private_key"],
            entity_id=int(config["virtuals_entity_id"]),
            api_client=api_client,
            on_event=claw_bot.notify_owner,
            loop=asyncio.get_event_loop(),
        )
        try:
            acp_seller.start()
            logger.info("ACP seller connected")
        except Exception as e:
            logger.error("ACP seller failed to start: %s", e)
            acp_seller = None
    else:
        logger.info("ACP seller disabled (missing VIRTUALS_* env vars)")

    # 5. Run Telegram bot
    logger.info("PrintClaw bot is running. Press Ctrl+C to stop.")

    try:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
            poll_interval=1.0,
        )

        stop_event = asyncio.Event()
        await stop_event.wait()

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")
        if acp_seller:
            acp_seller.shutdown()
        await api_client.close()
        if api_task:
            api_task.cancel()
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
