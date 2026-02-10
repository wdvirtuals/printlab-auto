"""Telegram bot handlers."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from ..agent import ClaudeAgent
from ..search import ThingiverseSearch, Model
from ..printer import X1CClient


def _esc(text: str) -> str:
    """Escape Telegram Markdown special characters in external data."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


class PrintLabBot:
    """Telegram bot for PrintLab Auto."""

    def __init__(
        self,
        telegram_token: str,
        anthropic_key: str,
        x1c_ip: str,
        x1c_access_code: str,
        x1c_serial: str,
        allowed_users: Optional[list[int]] = None,
        bed_type: str = "auto",
    ):
        self.telegram_token = telegram_token
        self.allowed_users = allowed_users

        # Initialize components
        self.agent = ClaudeAgent(anthropic_key)
        self.printer = X1CClient(x1c_ip, x1c_access_code, x1c_serial, bed_type=bed_type)

        # Search providers
        self.search_providers = [
            ThingiverseSearch(),
        ]

        # Session state ((chat_id, user_id) -> data)
        self.sessions: dict[tuple[int, int], dict] = {}

    def _is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized."""
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def _check_auth(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """Check authorization and send error if not authorized.

        In group/supergroup chats, all users are allowed.
        In private chats, ALLOWED_USERS is enforced.
        """
        chat_type = update.effective_chat.type
        if chat_type in ("group", "supergroup"):
            return True
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("⛔ You are not authorized to use this bot.")
            return False
        return True

    async def _check_printer_ready(self) -> tuple[bool, str]:
        """Check if the printer is ready to accept a new print job.

        Returns:
            (is_ready, status_message) — is_ready is True if printer can print,
            status_message is a human-readable description of the current state.
        """
        if not self.printer.is_connected:
            connected = await self.printer.connect()
            if not connected:
                return False, "🔴 Printer is offline or unreachable"

        status = await self.printer.get_status()
        if not status:
            return False, "🔴 Printer is not responding"

        if status.state == "IDLE":
            return True, "🟢 Printer is idle and ready"
        if status.state == "FINISHED":
            return True, "🟢 Printer is ready (previous print finished)"
        if status.state == "PRINTING":
            hours, mins = divmod(status.remaining_time, 60)
            time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
            return False, (
                f"🟡 Printer is currently printing ({status.progress}%)\n"
                f"⏱️ {time_str} remaining"
            )
        if status.state == "PREPARING":
            return False, "🟡 Printer is preparing a print job"
        if status.state == "PAUSED":
            return False, f"🟡 Printer is paused at {status.progress}%"
        if status.state == "ERROR":
            return False, (
                f"🔴 Previous print failed ({status.error_message or 'Unknown error'})\n"
                f"Clear the error on the printer touchscreen first."
            )
        return False, f"🟡 Printer status: {status.state}"

    async def _edit_message(
        self, query, text: str, parse_mode: str = None, reply_markup=None
    ) -> None:
        """Edit message handling both photo and text messages."""
        try:
            if query.message.photo:
                await query.edit_message_caption(
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            else:
                await query.edit_message_text(
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise

    async def start_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /start command."""
        if not await self._check_auth(update, context):
            return

        welcome = (
            "👋 Welcome to *PrintLab Auto*!\n\n"
            "I help you find and print 3D models on your Bambu Lab X1C.\n\n"
            "*Commands:*\n"
            "/print <description> - Search for models\n"
            "/printfile - Print a local .3mf file\n"
            "/status - Check printer status\n"
            "/cancel - Cancel current operation\n\n"
            "Or just tell me what you want to print!"
        )
        await update.message.reply_text(welcome, parse_mode="Markdown")

    async def status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /status command."""
        if not await self._check_auth(update, context):
            return

        msg = await update.message.reply_text("🔄 Checking printer status...")

        if not self.printer.is_connected:
            connected = await self.printer.connect()
            if not connected:
                await msg.edit_text(
                    "❌ Could not connect to printer.\n"
                    "Check that the printer is on and accessible."
                )
                return

        try:
            status = await self.printer.get_status()
            if status:
                await msg.edit_text(status.display_text(), parse_mode=None)
            else:
                await msg.edit_text("❌ Could not get printer status. No response from printer.")
        except Exception as e:
            await msg.edit_text(f"❌ Error getting status: {e}")

    async def cancel_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /cancel command."""
        if not await self._check_auth(update, context):
            return

        session_key = (update.effective_chat.id, update.effective_user.id)
        if session_key in self.sessions:
            del self.sessions[session_key]
            await update.message.reply_text("✅ Operation cancelled.")
        else:
            await update.message.reply_text("No active operation to cancel.")

    async def printfile_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /printfile command - print a local .3mf file."""
        if not await self._check_auth(update, context):
            return

        from pathlib import Path

        download_dir = Path.home() / ".printlab" / "downloads"

        if not context.args:
            # List available files
            files = list(download_dir.glob("*.3mf")) if download_dir.exists() else []
            if not files:
                await update.message.reply_text(
                    f"No .3mf files found.\n\n"
                    f"Place files in:\n`{download_dir}`\n\n"
                    f"Then use: `/printfile filename.3mf`",
                    parse_mode="Markdown",
                )
                return

            file_list = "\n".join(f"• `{f.name}`" for f in files[:10])
            await update.message.reply_text(
                f"Available files:\n{file_list}\n\n"
                f"Use: `/printfile filename.3mf`",
                parse_mode="Markdown",
            )
            return

        filename = " ".join(context.args)
        file_path = download_dir / filename

        if not file_path.exists():
            await update.message.reply_text(f"File not found: `{filename}`", parse_mode="Markdown")
            return

        if not file_path.suffix.lower() == ".3mf":
            await update.message.reply_text("Only .3mf files are supported.")
            return

        msg = await update.message.reply_text(f"🖨️ Sending `{filename}` to printer...", parse_mode="Markdown")

        # Connect if needed
        if not self.printer.is_connected:
            await msg.edit_text("🔌 Connecting to printer...")
            connected = await self.printer.connect()
            if not connected:
                await msg.edit_text("❌ Could not connect to printer.")
                return

        # Send print job
        result = await self.printer.send_print_job(file_path)

        if result is True:
            await msg.edit_text(
                f"✅ Print job sent!\n\n"
                f"*{_esc(filename)}*\n"
                f"Use /status to check progress.",
                parse_mode="Markdown",
            )
        else:
            await msg.edit_text(f"❌ Failed to send print job: {result}")

    async def print_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /print command."""
        if not await self._check_auth(update, context):
            return

        if not context.args:
            await update.message.reply_text(
                "Please describe what you want to print.\n"
                "Example: `/print phone stand`",
                parse_mode="Markdown",
            )
            return

        query = " ".join(context.args)
        await self._search_models(update, query)

    async def message_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle regular text messages."""
        if not await self._check_auth(update, context):
            return

        user_message = update.message.text.strip()

        # Pass previous query for context (e.g. "5 more options" → reuse last search)
        session_key = (update.effective_chat.id, update.effective_user.id)
        previous_query = None
        session = self.sessions.get(session_key)
        if session:
            previous_query = session.get("query")

        # Parse the message with Claude
        parsed = await self.agent.parse_query(user_message, previous_query=previous_query)
        query = parsed.get("query", user_message)

        await self._search_models(update, query, user_intent=user_message)

    PAGE_SIZE = 5

    async def _search_models(self, update: Update, query: str, user_intent: str | None = None) -> None:
        """Search for models and display results."""
        msg = await update.message.reply_text(f"🔍 Searching for: *{_esc(query)}*...", parse_mode="Markdown")

        # Fetch a large pool then sort by popularity
        search_tasks = [provider.search(query, limit=100) for provider in self.search_providers]
        results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Flatten results
        all_models: list[Model] = []
        for result in results:
            if isinstance(result, list):
                all_models.extend(result)

        if not all_models:
            await msg.edit_text(
                "😕 No models found. Try a different search term.",
            )
            return

        # Filter: keep models whose name contains at least one search keyword
        keywords = [w.lower() for w in query.split() if len(w) > 2]
        if keywords:
            filtered = [
                m for m in all_models
                if any(kw in m.name.lower() for kw in keywords)
            ]
            # Fall back to all results if filter is too aggressive
            if len(filtered) < 3:
                filtered = all_models
        else:
            filtered = all_models

        # Sort by downloads + likes, show top 20
        ranked_models = sorted(
            filtered,
            key=lambda m: (m.downloads or 0) + (m.likes or 0),
            reverse=True,
        )[:20]

        # Store all ranked models in session for pagination
        session_key = (update.effective_chat.id, update.effective_user.id)
        self.sessions[session_key] = {
            "query": query,
            "models": {str(i): m for i, m in enumerate(ranked_models)},
            "page": 0,
            "total_ranked": len(ranked_models),
        }

        # Delete the "searching" message
        await msg.delete()

        await update.message.reply_text(
            f"Found {len(all_models)} models for *{_esc(query)}*\\. Here are the top picks:",
            parse_mode="Markdown",
        )

        # Display first page
        await self._send_model_page(update.message, session_key)

    async def _send_model_page(self, message, session_key: tuple[int, int]) -> None:
        """Send a page of model results."""
        session = self.sessions.get(session_key)
        if not session:
            return

        page = session["page"]
        start = page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        total = session["total_ranked"]

        for i in range(start, min(end, total)):
            model = session["models"].get(str(i))
            if not model:
                continue

            caption = (
                f"*{i+1}\\. {_esc(model.name)}*\n"
                f"by {_esc(model.author)} ({_esc(model.provider)})"
            )
            if model.downloads:
                caption += f"\n📥 {model.downloads:,} downloads"
            if model.likes:
                caption += f" | ❤️ {model.likes:,}"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Select", callback_data=f"select:{i}"),
                    InlineKeyboardButton("🔗 View", url=model.url),
                ]
            ])

            if model.thumbnail:
                try:
                    await message.reply_photo(
                        photo=model.thumbnail,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                except Exception:
                    await message.reply_text(
                        caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
            else:
                await message.reply_text(
                    caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

        # Bottom buttons: "More" if there are more results, always "Cancel"
        has_more = end < total
        buttons = []
        if has_more:
            remaining = total - end
            buttons.append(InlineKeyboardButton(f"➡️ Show more ({remaining} left)", callback_data="more"))
        buttons.append(InlineKeyboardButton("❌ Cancel Search", callback_data="cancel"))

        await message.reply_text(
            "Select a model above, or:",
            reply_markup=InlineKeyboardMarkup([buttons]),
        )

    async def callback_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle inline keyboard callbacks."""
        query = update.callback_query
        await query.answer()

        session_key = (update.effective_chat.id, update.effective_user.id)
        data = query.data

        if data == "cancel":
            if session_key in self.sessions:
                del self.sessions[session_key]
            await self._edit_message(query, "Operation cancelled.")
            return

        if data.startswith("select:"):
            await self._handle_model_selection(query, session_key, data)
            return

        if data.startswith("tray:"):
            await self._handle_tray_selection(query, session_key, data)
            return

        if data.startswith("confirm:"):
            await self._handle_print_confirmation(query, session_key, data)
            return

        if data == "more":
            session = self.sessions.get(session_key)
            if session and "models" in session:
                session["page"] = session.get("page", 0) + 1
                await self._edit_message(query, "Loading more results...")
                await self._send_model_page(query.message, session_key)
            return

        if data == "back":
            # Go back to model selection
            session = self.sessions.get(session_key)
            if session and "models" in session:
                await self._show_model_selection(query, session)
            return

    async def _handle_model_selection(self, query, session_key: tuple[int, int], data: str) -> None:
        """Handle model selection from search results."""
        session = self.sessions.get(session_key)
        if not session or "models" not in session:
            await self._edit_message(query, "Session expired. Please search again.")
            return

        model_idx = data.split(":")[1]
        model = session["models"].get(model_idx)

        if not model:
            await self._edit_message(query, "Model not found. Please search again.")
            return

        # Store selected model, clear any previous filament selection
        session["selected_model"] = model
        session.pop("ams_mapping", None)

        # Check printer readiness
        printer_ready, printer_status_msg = await self._check_printer_ready()

        # Build keyboard and text
        keyboard = []
        filament_text = ""

        if printer_ready:
            # Get AMS tray data
            trays = await self.printer.get_ams_trays()

            if len(trays) >= 2:
                filament_text = "\n\n*Choose filament:*"
                for tray in trays:
                    keyboard.append([InlineKeyboardButton(
                        tray.display_text(),
                        callback_data=f"tray:{model_idx}:{tray.global_index}",
                    )])
                keyboard.append([InlineKeyboardButton(
                    "🖨️ Auto\\-select", callback_data=f"confirm:{model_idx}",
                )])
            else:
                keyboard.append([InlineKeyboardButton(
                    "🖨️ Print Now", callback_data=f"confirm:{model_idx}",
                )])
        else:
            keyboard.append([InlineKeyboardButton(
                "🔄 Check Again", callback_data=f"select:{model_idx}",
            )])

        keyboard.append([InlineKeyboardButton("🔗 View Online", url=model.url)])
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back")])

        text = (
            f"*Selected:* {_esc(model.name)}\n\n"
            f"Author: {_esc(model.author)}\n"
            f"Source: {_esc(model.provider)}\n"
            f"Downloads: {model.downloads:,}\n\n"
            f"*Printer:* {_esc(printer_status_msg)}"
            f"{filament_text}"
        )

        await self._edit_message(
            query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def _handle_tray_selection(self, query, session_key: tuple[int, int], data: str) -> None:
        """Handle AMS tray (filament) selection."""
        session = self.sessions.get(session_key)
        if not session or "selected_model" not in session:
            await self._edit_message(query, "Session expired. Please search again.")
            return

        # data format: "tray:{model_idx}:{tray_global_index}"
        parts = data.split(":")
        tray_index = int(parts[2])
        session["ams_mapping"] = [tray_index]

        # Proceed directly to print
        await self._handle_print_confirmation(query, session_key, f"confirm:{parts[1]}")

    async def _handle_print_confirmation(self, query, session_key: tuple[int, int], data: str) -> None:
        """Handle print confirmation."""
        session = self.sessions.get(session_key)
        if not session or "selected_model" not in session:
            await self._edit_message(query, "Session expired. Please search again.")
            return

        model = session["selected_model"]

        # Verify printer is still ready before committing
        printer_ready, printer_status_msg = await self._check_printer_ready()
        if not printer_ready:
            model_idx = data.split(":")[1]
            keyboard = [
                [InlineKeyboardButton("🔄 Check Again", callback_data=f"select:{model_idx}")],
                [InlineKeyboardButton("◀️ Back", callback_data="back")],
            ]
            await self._edit_message(
                query,
                f"❌ Cannot print right now\n\n{_esc(printer_status_msg)}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        await self._edit_message(query, f"⏳ Preparing to print *{_esc(model.name)}*...", parse_mode="Markdown")

        # Get download URL
        provider = None
        for p in self.search_providers:
            if p.name == model.provider:
                provider = p
                break

        if not provider:
            await self._edit_message(query, "❌ Error: Search provider not found.")
            return

        download_url = await provider.get_download_url(model)

        if not download_url:
            await self._edit_message(
                query,
                f"❌ Could not get download link.\n\n"
                f"You can download manually from:\n{model.url}"
            )
            return

        # Reject multi-part models (require multiple print sessions)
        if model.file_count > 1:
            model_idx = data.split(":")[1]
            keyboard = [
                [InlineKeyboardButton("🔗 View on site", url=model.url)],
                [InlineKeyboardButton("◀️ Back to results", callback_data="back")],
            ]
            await self._edit_message(
                query,
                f"⚠️ *Multi\\-part model* \\({model.file_count} files\\)\n\n"
                f"This model requires {_esc(str(model.file_count))} separate prints\\. "
                f"Only single\\-file models are supported\\.\n\n"
                f"You can download all parts manually from the site\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        # Check if this is a page URL (not a direct download)
        # Direct downloads end with file extension or contain it before query params
        url_lower = download_url.lower()
        is_direct_download = (
            url_lower.endswith(('.3mf', '.stl', '.zip')) or
            '.3mf?' in url_lower or '.stl?' in url_lower or '.zip?' in url_lower
        )
        if not is_direct_download:
            await self._edit_message(
                query,
                f"⚠️ *Manual download required*\n\n"
                f"This site requires login for downloads.\n\n"
                f"1. Download the .3mf from:\n{model.url}\n\n"
                f"2. Place it in:\n`~/.printlab/downloads/`\n\n"
                f"3. Use `/printfile` to print it",
                parse_mode="Markdown",
            )
            return

        # Download the file
        await self._edit_message(query, "📥 Downloading model...")

        filename = f"{model.name.replace(' ', '_')}"
        file_path = await self.printer.download_model(download_url, filename)

        if not file_path:
            await self._edit_message(query, "❌ Failed to download model.")
            return

        # Connect to printer if needed
        if not self.printer.is_connected:
            await self._edit_message(query, "🔌 Connecting to printer...")
            connected = await self.printer.connect()
            if not connected:
                await self._edit_message(
                    query,
                    "❌ Could not connect to printer.\n"
                    f"File saved to: `{file_path}`",
                    parse_mode="Markdown",
                )
                return

        # Send print job (will auto-slice STL files if slicer is available)
        is_stl = str(file_path).lower().endswith(".stl")
        if is_stl:
            await self._edit_message(query, "🔪 Slicing STL file (this may take a minute)...")
        else:
            await self._edit_message(query, "🖨️ Sending to printer...")

        ams_mapping = session.get("ams_mapping")
        result = await self.printer.send_print_job(file_path, ams_mapping=ams_mapping)

        if result == "no_slicer":
            await self._edit_message(
                query,
                f"⚠️ *No slicer installed*\n\n"
                f"STL files need to be sliced before printing.\n\n"
                f"For Bambu printers, install:\n"
                f"• Orca Slicer (recommended)\n"
                f"• Bambu Studio\n\n"
                f"File saved to:\n`{file_path}`",
                parse_mode="Markdown",
            )
        elif result == "wrong_slicer":
            await self._edit_message(
                query,
                f"⚠️ *Incompatible slicer*\n\n"
                f"PrusaSlicer cannot generate Bambu-compatible files.\n\n"
                f"Please install:\n"
                f"• Orca Slicer: github.com/SoftFever/OrcaSlicer\n"
                f"• Bambu Studio: bambulab.com/download\n\n"
                f"File saved to:\n`{file_path}`",
                parse_mode="Markdown",
            )
        elif result == "no_template":
            await self._edit_message(
                query,
                f"⚠️ *Slicing template needed*\n\n"
                f"To enable auto-slicing:\n"
                f"1. Open Orca Slicer\n"
                f"2. Load any STL and configure settings\n"
                f"3. Click Slice\n"
                f"4. File > Export > Export sliced file\n"
                f"5. Save to: `~/.printlab/template.3mf`\n\n"
                f"File saved to:\n`{file_path}`",
                parse_mode="Markdown",
            )
        elif result == "slice_failed":
            await self._edit_message(
                query,
                f"❌ *Slicing failed*\n\n"
                f"Could not slice the STL file.\n"
                f"Try opening it manually in your slicer.\n\n"
                f"File saved to:\n`{file_path}`",
                parse_mode="Markdown",
            )
        elif result == "storage_error":
            await self._edit_message(
                query,
                f"❌ *Printer storage unavailable*\n\n"
                f"The printer's internal storage is not writable.\n"
                f"On the printer, go to:\n"
                f"Settings > Storage and format the SD card/storage.\n\n"
                f"File saved to:\n`{file_path}`",
                parse_mode="Markdown",
            )
        elif result == "upload_failed":
            await self._edit_message(
                query,
                f"❌ *File upload failed*\n\n"
                f"Could not upload file to printer via FTPS.\n"
                f"Check that the printer is accessible.\n\n"
                f"File saved to:\n`{file_path}`",
                parse_mode="Markdown",
            )
        elif result:
            sliced_msg = " (sliced from STL)" if is_stl else ""
            await self._edit_message(
                query,
                f"✅ Print job sent!{sliced_msg}\n\n"
                f"*{_esc(model.name)}*\n"
                f"Use /status to check progress.",
                parse_mode="Markdown",
            )
        else:
            await self._edit_message(
                query,
                f"❌ Failed to send print job.\n"
                f"File saved to: `{file_path}`",
                parse_mode="Markdown",
            )

        # Clean up session
        del self.sessions[session_key]

    async def _show_model_selection(self, query, session: dict) -> None:
        """Show model selection again."""
        models = session["models"]
        search_query = session["query"]

        response_lines = [f"Search results for: *{_esc(search_query)}*\n"]
        keyboard = []

        for i, model in models.items():
            response_lines.append(f"{int(i)+1}\\. {_esc(model.display_text())}\n")
            keyboard.append(
                [InlineKeyboardButton(f"Select #{int(i)+1}", callback_data=f"select:{i}")]
            )

        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

        await self._edit_message(
            query,
            "\n".join(response_lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def shutdown(self):
        """Clean up resources."""
        await self.printer.disconnect()
        for provider in self.search_providers:
            await provider.close()


def setup_handlers(app: Application, bot: PrintLabBot) -> None:
    """Set up all command and message handlers."""
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("status", bot.status_command))
    app.add_handler(CommandHandler("cancel", bot.cancel_command))
    app.add_handler(CommandHandler("print", bot.print_command))
    app.add_handler(CommandHandler("printfile", bot.printfile_command))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.message_handler))
