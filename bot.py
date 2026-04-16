#!/usr/bin/env python3
"""
Telegram AI Agent
-----------------
A Telegram-controlled AI assistant that lives on your Linux VPS.
It can run shell commands, manage files, fetch web content, and
answer any question — all through natural language via Telegram.

Setup:
  1. Copy .env.example to .env and fill in your credentials.
  2. pip install -r requirements.txt
  3. python bot.py
"""

import asyncio
import ipaddress
import json
import logging
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ALLOWED_USER_IDS = set(
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
WORK_DIR = os.getenv("WORK_DIR", str(Path.home()))

# Maximum characters returned from any single tool call (keeps context window manageable)
MAX_OUTPUT_SIZE = 8000
# Maximum tool-call rounds per user turn (prevents infinite agentic loops)
MAX_TOOL_ITERATIONS = 10
# Maximum timeout for shell commands in seconds (balances usability vs. resource consumption)
MAX_SHELL_TIMEOUT = 60

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Per-user conversation history: {user_id: [{"role": ..., "content": ...}]}
conversation_history: dict[int, list[dict]] = {}

# ---------------------------------------------------------------------------
# Tool definitions exposed to the AI
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Execute a shell command on the Linux VPS and return its output. "
                "Use for file management, process control, package installation, "
                "system information, running scripts, etc. "
                "Commands run in the configured WORK_DIR. "
                "Prefer non-interactive commands; avoid commands that hang indefinitely."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": f"Maximum seconds to wait for the command (default 30, max {MAX_SHELL_TIMEOUT}).",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file on the VPS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file on the VPS with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch the text content of a URL (web page or API endpoint).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default 4000).",
                        "default": 4000,
                    },
                },
                "required": ["url"],
            },
        },
    },
]

SYSTEM_PROMPT = f"""\
You are a powerful AI assistant running on a Linux VPS, controlled exclusively \
through Telegram by the server owner. You have full access to the system via \
tools and can do virtually anything the owner asks.

Guidelines:
- Be concise but complete in your answers.
- When asked to do something on the system, use the appropriate tool(s).
- After executing commands, report the result clearly.
- If a task requires multiple steps, work through them methodically.
- Always show command output so the owner can verify what happened.
- Format code and command output in Markdown code blocks.
- If something could be destructive (e.g. deleting files, stopping services), \
  describe what you will do before doing it, unless the owner has already confirmed.
- Working directory: {WORK_DIR}
"""

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _resolve_path(path: str) -> Path:
    """Resolve path relative to WORK_DIR if not absolute."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(WORK_DIR) / p
    return p.resolve()


async def tool_run_shell(command: str, timeout: int = 30) -> str:
    timeout = min(max(1, timeout), MAX_SHELL_TIMEOUT)
    logger.info("run_shell: %s", command)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,  # noqa: S602 — intentional; only whitelisted users can reach this
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=WORK_DIR,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if not output:
            output = f"(exited with code {result.returncode}, no output)"
        else:
            output = f"Exit code: {result.returncode}\n{output}"
        return output[:MAX_OUTPUT_SIZE]
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds."
    except Exception as exc:
        return f"Error running command: {exc}"


async def tool_read_file(path: str) -> str:
    resolved = _resolve_path(path)
    logger.info("read_file: %s", resolved)
    try:
        content = resolved.read_text(errors="replace")
        if len(content) > MAX_OUTPUT_SIZE:
            content = content[:MAX_OUTPUT_SIZE] + "\n... (truncated)"
        return content
    except Exception as exc:
        return f"Error reading file: {exc}"


async def tool_write_file(path: str, content: str) -> str:
    resolved = _resolve_path(path)
    logger.info("write_file: %s", resolved)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        return f"File written successfully: {resolved} ({len(content)} chars)"
    except Exception as exc:
        return f"Error writing file: {exc}"


async def tool_fetch_url(url: str, max_chars: int = 4000) -> str:
    logger.info("fetch_url: %s", url)
    # Validate scheme to prevent SSRF — only allow public http/https URLs
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: only http and https URLs are supported (got '{parsed.scheme}')."
    hostname = parsed.hostname or ""
    # Block requests to private/loopback/link-local addresses (SSRF mitigation)
    blocked_names = {"localhost", "127.0.0.1", "::1"}
    if hostname in blocked_names:
        return "Error: requests to loopback or private addresses are not allowed."
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
            return "Error: requests to loopback or private addresses are not allowed."
    except ValueError:
        pass  # hostname is a domain name, not an IP — allow it through DNS
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            text = response.text
            if len(text) > max_chars:
                text = text[:max_chars] + "\n... (truncated)"
            return f"HTTP {response.status_code}\n{text}"
    except Exception as exc:
        return f"Error fetching URL: {exc}"


TOOL_MAP = {
    "run_shell": tool_run_shell,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "fetch_url": tool_fetch_url,
}

# ---------------------------------------------------------------------------
# Core AI loop
# ---------------------------------------------------------------------------


async def run_agent(user_id: int, user_message: str) -> str:
    """Run the agentic loop for a user message; returns the final reply text."""
    history = conversation_history.setdefault(user_id, [])
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    # Agentic loop: keep calling the model until it stops requesting tool calls.
    # MAX_TOOL_ITERATIONS prevents infinite loops on pathological inputs.
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        choice = response.choices[0]
        assistant_message = choice.message

        # Add assistant message (may include tool_calls) to messages
        messages.append(assistant_message.model_dump(exclude_unset=True))

        if choice.finish_reason == "tool_calls" and assistant_message.tool_calls:
            # Execute all requested tools in parallel
            tool_results = await asyncio.gather(
                *[_dispatch_tool(tc) for tc in assistant_message.tool_calls]
            )
            for result in tool_results:
                messages.append(result)
        else:
            # Model is done; extract final text
            final_text = assistant_message.content or "(no response)"
            # Persist only the new exchanges into history.
            # Each turn = 1 user msg + 1 assistant msg, so multiply by 2.
            history.append({"role": "assistant", "content": final_text})
            if len(history) > MAX_HISTORY * 2:
                conversation_history[user_id] = history[-(MAX_HISTORY * 2):]
            return final_text

    return "⚠️ Reached maximum tool-call iterations without a final answer."


async def _dispatch_tool(tool_call) -> dict:
    """Execute a single tool call and return the tool result message."""
    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        args = {}

    func = TOOL_MAP.get(name)
    if func is None:
        result = f"Unknown tool: {name}"
    else:
        result = await func(**args)

    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": result,
    }


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------


def is_allowed(user_id: int) -> bool:
    """Return True if the user is on the whitelist (or no whitelist configured)."""
    if not ALLOWED_USER_IDS:
        return True  # no restriction configured
    return user_id in ALLOWED_USER_IDS


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_allowed(user.id):
        logger.warning("Blocked unauthorized user %s", user)
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    user_text = update.message.text or ""
    if not user_text.strip():
        return

    logger.info("Message from %s (%d): %s", user.username, user.id, user_text[:80])

    # Show typing indicator while processing
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    try:
        reply = await run_agent(user.id, user_text)
    except Exception as exc:
        logger.exception("Agent error for user %d", user.id)
        reply = f"❌ An error occurred: {exc}"

    # Split long replies to respect Telegram's 4096-char limit
    for chunk in _split_message(reply):
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return
    await update.message.reply_text(
        "👋 AI Agent online and ready.\n\n"
        "Just send me any message or command in plain English.\n"
        "I can run shell commands, manage files, browse the web, and more.\n\n"
        "Use /clear to reset conversation history.\n"
        "Use /help for more info."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_allowed(user.id):
        return
    await update.message.reply_text(
        "*AI Agent — Help*\n\n"
        "Send any natural language message and I will do my best to help.\n\n"
        "*Examples:*\n"
        "• `What is the disk usage on this server?`\n"
        "• `List running services`\n"
        "• `Install nginx and start it`\n"
        "• `Show me the last 50 lines of /var/log/syslog`\n"
        "• `Write a Python script that monitors CPU and save it to ~/monitor.py`\n"
        "• `Fetch the latest news from https://example.com`\n\n"
        "*Commands:*\n"
        "/start — Show welcome message\n"
        "/help  — Show this help\n"
        "/clear — Clear conversation history\n"
        "/id    — Show your Telegram user ID",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_allowed(user.id):
        return
    conversation_history.pop(user.id, None)
    await update.message.reply_text("🗑️ Conversation history cleared.")


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(f"Your Telegram user ID is: `{user.id}`", parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split a message into chunks that fit within Telegram's size limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting AI Agent bot (model: %s, work_dir: %s)", OPENAI_MODEL, WORK_DIR)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
