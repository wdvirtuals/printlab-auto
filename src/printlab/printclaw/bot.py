"""PrintClaw - Claude-powered conversational Telegram bot with tool calling."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx
from anthropic import AsyncAnthropic
from telegram import Update
from telegram.ext import ContextTypes, Application

logger = logging.getLogger(__name__)

SKILLS_DIR = Path.home() / ".printclaw" / "skills"

SYSTEM_PROMPT_BASE = """\
You are PrintClaw, an autonomous AI agent running on macOS. You have full access to the \
local system via shell commands, file read/write, and a 3D printer connected via REST API.

You operate with HIGH AUTONOMY. When the user asks you to do something, DO IT YOURSELF \
using your tools. Never tell the user to run commands manually — you have run_command, \
read_file, write_file, and list_directory tools for that. Just execute what's needed and \
report the result.

Core capabilities:
- Run any shell command (install packages, git, curl, npm, pip, etc.)
- Read and write files anywhere on the system
- Search for and print 3D models on a Bambu Lab X1C printer
- Install skills from GitHub repos to learn new capabilities
- Manage the system: install software, edit configs, run scripts

Guidelines:
- Be concise but helpful. Use a casual tone.
- When asked to install something, use run_command to do it immediately.
- When asked to create or edit files, use write_file or read_file + write_file.
- For 3D printing: search models, check printer status, start prints — all via tools.
- When showing search results, include name, author, likes, and model ID.
- Before printing, check printer status first. If busy, tell the user.
- If you receive an ACP job notification, inform the user clearly.
- Keep responses under 3000 characters when possible.
- For long-running commands, warn the user it may take a moment.
"""


# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

def _parse_skill_md(content: str) -> dict:
    """Parse a SKILL.md file into name, description, and body."""
    skill: dict = {"name": "", "description": "", "body": content}

    # Try to extract YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        frontmatter, body = fm_match.group(1), fm_match.group(2)
        skill["body"] = body.strip()
        for line in frontmatter.splitlines():
            if line.startswith("name:"):
                skill["name"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("description:"):
                skill["description"] = line.split(":", 1)[1].strip().strip("'\"")
    else:
        # Try to get name from first heading
        heading = re.match(r"^#\s+(.+)", content)
        if heading:
            skill["name"] = heading.group(1).strip()

    return skill


def load_skills() -> list[dict]:
    """Load all installed skills from ~/.printclaw/skills/."""
    skills = []
    if not SKILLS_DIR.exists():
        return skills
    for f in sorted(SKILLS_DIR.iterdir()):
        if f.suffix == ".md" and f.is_file():
            try:
                content = f.read_text(encoding="utf-8")
                skill = _parse_skill_md(content)
                skill["file"] = f.name
                if not skill["name"]:
                    skill["name"] = f.stem
                skills.append(skill)
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", f.name, e)
    return skills


def build_system_prompt(skills: list[dict]) -> str:
    """Build system prompt with installed skills appended."""
    if not skills:
        return SYSTEM_PROMPT_BASE

    parts = [SYSTEM_PROMPT_BASE, "\n--- Installed Skills ---\n"]
    for skill in skills:
        parts.append(f"\n### Skill: {skill['name']}\n")
        if skill["description"]:
            parts.append(f"{skill['description']}\n")
        parts.append(f"\n{skill['body']}\n")
    return "".join(parts)


def _repo_to_owner_repo(repo: str) -> str:
    """Normalize GitHub repo input to 'owner/repo' format."""
    # Handle full URLs: https://github.com/owner/repo[/...]
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)", repo)
    if m:
        return m.group(1).rstrip("/")
    # Already owner/repo
    if "/" in repo and not repo.startswith("http"):
        return repo.strip("/")
    return repo

TOOLS = [
    {
        "name": "search_models",
        "description": "Search Thingiverse for 3D printable models by keyword. Returns a list of models with names, authors, download counts, likes, and IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords, e.g. 'dragon figurine'"},
                "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_printer_status",
        "description": "Check the current state of the 3D printer: idle, printing progress, temperatures, remaining time, errors.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "print_model",
        "description": "Start printing a 3D model. Provide either a Thingiverse model ID or a direct download URL. The system downloads, slices STL to 3MF if needed, and starts the print.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_id": {"type": "string", "description": "Thingiverse thing ID (e.g. '6428358')"},
                "model_url": {"type": "string", "description": "Direct download URL for .stl or .3mf file"},
                "filename": {"type": "string", "description": "Filename to save as (optional)"},
                "ams_mapping": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "AMS tray indices for filament selection",
                },
            },
        },
    },
    {
        "name": "get_filaments",
        "description": "List the filaments currently loaded in the AMS (Automatic Material System). Shows material type, color, and tray index for each slot.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "pause_print",
        "description": "Pause the current print job.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "resume_print",
        "description": "Resume a paused print job.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "stop_print",
        "description": "Stop and cancel the current print job. This cannot be undone.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "install_skill",
        "description": "Install a skill from a GitHub repository. Fetches the SKILL.md file from the repo and saves it locally. The skill's instructions become available in future conversations. Accepts a GitHub repo URL (e.g. 'https://github.com/user/repo') or shorthand 'user/repo'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHub repo URL or 'owner/repo' shorthand",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name (default: main)",
                    "default": "main",
                },
                "path": {
                    "type": "string",
                    "description": "Path to SKILL.md within the repo (default: SKILL.md)",
                    "default": "SKILL.md",
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "list_skills",
        "description": "List all installed skills with their names and descriptions.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "remove_skill",
        "description": "Remove an installed skill by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to remove (as shown by list_skills)",
                },
            },
            "required": ["name"],
        },
    },
    # --- System tools ---
    {
        "name": "run_command",
        "description": "Execute a shell command on the local system and return stdout/stderr. Use this for installing packages (pip, npm, brew), running scripts, git operations, curl, and any other terminal commands. Commands run from the project root directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (e.g. 'pip install requests', 'ls -la', 'git status')",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory (optional, defaults to project root)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120)",
                    "default": 120,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the full text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path to read",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Creates parent directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a given path. Returns names, sizes, and types.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: current directory)",
                    "default": ".",
                },
            },
        },
    },
]


class PrintClawBot:
    """Conversational Telegram bot powered by Claude with tool calling."""

    def __init__(
        self,
        telegram_token: str,
        anthropic_key: str,
        api_client,  # PrintLabAPIClient
        owner_chat_id: Optional[int] = None,
    ):
        self.telegram_token = telegram_token
        self.anthropic = AsyncAnthropic(api_key=anthropic_key)
        self.model = "claude-sonnet-4-20250514"
        self.api = api_client
        self.owner_chat_id = owner_chat_id
        self.conversations: dict[int, list[dict]] = {}
        self.max_history = 30
        self._app: Optional[Application] = None
        # Load installed skills
        self.skills = load_skills()
        self._system_prompt = build_system_prompt(self.skills)
        if self.skills:
            logger.info("Loaded %d skill(s): %s",
                        len(self.skills), ", ".join(s["name"] for s in self.skills))

    # --- Telegram Handlers ---

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.conversations.pop(chat_id, None)
        await update.message.reply_text(
            "Hey! I'm PrintClaw, your 3D printing assistant.\n\n"
            "Tell me what you'd like to print, ask about the printer status, "
            "or just chat about 3D printing. I'm here to help!"
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_and_reply(update, "What's the current printer status?")

    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        await self._handle_and_reply(update, update.message.text.strip())

    async def _handle_and_reply(self, update: Update, user_text: str):
        chat_id = update.effective_chat.id
        await update.effective_chat.send_action("typing")
        try:
            response = await self._handle_conversation(chat_id, user_text)
            for chunk in _split_message(response, 4096):
                await update.message.reply_text(chunk)
        except Exception as e:
            logger.exception("Error in conversation")
            await update.message.reply_text(f"Something went wrong: {e}")

    # --- Core Conversation Loop ---

    async def _handle_conversation(self, chat_id: int, user_message: str) -> str:
        history = self.conversations.setdefault(chat_id, [])
        history.append({"role": "user", "content": user_message})

        # Trim old messages
        if len(history) > self.max_history:
            history[:] = history[-self.max_history:]

        # Tool-calling loop
        while True:
            response = await self.anthropic.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self._system_prompt,
                tools=TOOLS,
                messages=history,
            )

            if response.stop_reason == "tool_use":
                # Append assistant turn with tool_use blocks
                history.append({"role": "assistant", "content": response.content})

                # Execute each tool call
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = await self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                history.append({"role": "user", "content": tool_results})
                # Continue loop — Claude will process results and respond
            else:
                # Extract final text
                text = "".join(
                    block.text for block in response.content if hasattr(block, "text")
                )
                history.append({"role": "assistant", "content": text})
                return text

    # --- Tool Execution ---

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        try:
            if tool_name == "search_models":
                models = await self.api.search(
                    tool_input["query"], tool_input.get("limit", 5)
                )
                return json.dumps(models, indent=2)

            elif tool_name == "get_printer_status":
                status = await self.api.status()
                return json.dumps(status, indent=2)

            elif tool_name == "print_model":
                result = await self.api.print_model(
                    model_id=tool_input.get("model_id"),
                    model_url=tool_input.get("model_url"),
                    filename=tool_input.get("filename"),
                    ams_mapping=tool_input.get("ams_mapping"),
                )
                return json.dumps(result)

            elif tool_name == "get_filaments":
                trays = await self.api.trays()
                return json.dumps(trays, indent=2)

            elif tool_name == "pause_print":
                result = await self.api.pause()
                return json.dumps(result)

            elif tool_name == "resume_print":
                result = await self.api.resume()
                return json.dumps(result)

            elif tool_name == "stop_print":
                result = await self.api.stop()
                return json.dumps(result)

            elif tool_name == "install_skill":
                return await self._install_skill(
                    tool_input["repo"],
                    tool_input.get("branch", "main"),
                    tool_input.get("path", "SKILL.md"),
                )

            elif tool_name == "list_skills":
                return self._list_skills()

            elif tool_name == "remove_skill":
                return self._remove_skill(tool_input["name"])

            elif tool_name == "run_command":
                return await self._run_command(
                    tool_input["command"],
                    tool_input.get("working_dir"),
                    tool_input.get("timeout", 120),
                )

            elif tool_name == "read_file":
                return self._read_file(tool_input["path"])

            elif tool_name == "write_file":
                return self._write_file(tool_input["path"], tool_input["content"])

            elif tool_name == "list_directory":
                return self._list_directory(tool_input.get("path", "."))

            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

        except Exception as e:
            return json.dumps({"error": str(e)})

    # --- Skill Management ---

    async def _install_skill(self, repo: str, branch: str = "main", path: str = "SKILL.md") -> str:
        """Fetch a SKILL.md from a GitHub repo and save it locally."""
        owner_repo = _repo_to_owner_repo(repo)
        raw_url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{path}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(raw_url)
            if resp.status_code == 404:
                return json.dumps({
                    "error": f"SKILL.md not found at {raw_url}. "
                    "Check the repo URL, branch, or path."
                })
            resp.raise_for_status()
            content = resp.text

        skill = _parse_skill_md(content)
        name = skill["name"] or owner_repo.replace("/", "-")
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-").lower()
        filename = f"{safe_name}.md"

        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        (SKILLS_DIR / filename).write_text(content, encoding="utf-8")

        # Reload skills and rebuild prompt
        self.skills = load_skills()
        self._system_prompt = build_system_prompt(self.skills)

        return json.dumps({
            "installed": name,
            "file": filename,
            "description": skill.get("description", ""),
            "source": f"github.com/{owner_repo}",
            "note": "Skill loaded and active for this session.",
        })

    def _list_skills(self) -> str:
        """List all installed skills."""
        if not self.skills:
            return json.dumps({"skills": [], "message": "No skills installed."})
        return json.dumps({
            "skills": [
                {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "file": s["file"],
                }
                for s in self.skills
            ]
        })

    def _remove_skill(self, name: str) -> str:
        """Remove a skill by name."""
        for skill in self.skills:
            if skill["name"].lower() == name.lower() or skill["file"].lower() == name.lower():
                filepath = SKILLS_DIR / skill["file"]
                if filepath.exists():
                    filepath.unlink()
                self.skills = load_skills()
                self._system_prompt = build_system_prompt(self.skills)
                return json.dumps({"removed": skill["name"], "file": skill["file"]})
        return json.dumps({"error": f"Skill '{name}' not found. Use list_skills to see installed skills."})

    # --- System Tools ---

    async def _run_command(self, command: str, working_dir: Optional[str] = None, timeout: int = 120) -> str:
        """Execute a shell command and return output."""
        cwd = working_dir or str(Path.home() / "printlab-auto")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ, "PATH": f"/opt/homebrew/bin:{os.environ.get('PATH', '')}"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            result = {}
            if stdout:
                out = stdout.decode(errors="replace")
                # Truncate very long output
                result["stdout"] = out[:8000] + ("...[truncated]" if len(out) > 8000 else "")
            if stderr:
                err = stderr.decode(errors="replace")
                result["stderr"] = err[:4000] + ("...[truncated]" if len(err) > 4000 else "")
            result["exit_code"] = proc.returncode
            return json.dumps(result)

        except asyncio.TimeoutError:
            return json.dumps({"error": f"Command timed out after {timeout}s", "command": command})

    def _read_file(self, path: str) -> str:
        """Read a file's contents."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path.home() / "printlab-auto" / p
        if not p.exists():
            return json.dumps({"error": f"File not found: {p}"})
        if not p.is_file():
            return json.dumps({"error": f"Not a file: {p}"})
        try:
            content = p.read_text(encoding="utf-8")
            if len(content) > 15000:
                content = content[:15000] + f"\n...[truncated, total {len(content)} chars]"
            return json.dumps({"path": str(p), "content": content})
        except Exception as e:
            return json.dumps({"error": f"Failed to read {p}: {e}"})

    def _write_file(self, path: str, content: str) -> str:
        """Write content to a file, creating parent dirs as needed."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path.home() / "printlab-auto" / p
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return json.dumps({"written": str(p), "size": len(content)})
        except Exception as e:
            return json.dumps({"error": f"Failed to write {p}: {e}"})

    def _list_directory(self, path: str = ".") -> str:
        """List directory contents."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path.home() / "printlab-auto" / p
        if not p.exists():
            return json.dumps({"error": f"Directory not found: {p}"})
        if not p.is_dir():
            return json.dumps({"error": f"Not a directory: {p}"})
        try:
            entries = []
            for item in sorted(p.iterdir()):
                entry = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
                if item.is_file():
                    entry["size"] = item.stat().st_size
                entries.append(entry)
            return json.dumps({"path": str(p), "entries": entries})
        except Exception as e:
            return json.dumps({"error": f"Failed to list {p}: {e}"})

    # --- ACP Event Notifications ---

    async def notify_owner(self, message: str):
        """Send a proactive message to the owner's Telegram chat."""
        if self.owner_chat_id and self._app:
            try:
                await self._app.bot.send_message(
                    chat_id=self.owner_chat_id,
                    text=f"[ACP] {message}",
                )
            except Exception as e:
                logger.warning("Failed to notify owner: %s", e)


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Split text into chunks that fit Telegram's message limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find last newline within limit
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
