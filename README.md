# AI Telegram Agent

A powerful AI chat agent that lives on your Linux VPS and is controlled entirely through Telegram. Ask it anything in plain English — it can run shell commands, manage files, browse the web, and much more.

---

## Features

- 🤖 **Conversational AI** — Powered by OpenAI (GPT-4o or any model you choose)
- 🖥️ **Shell execution** — Run any command on your VPS
- 📁 **File management** — Read and write files on the server
- 🌐 **Web fetching** — Pull content from URLs / APIs
- 🔐 **User whitelist** — Only authorized Telegram user IDs can interact with the bot
- 💬 **Conversation memory** — The bot remembers context within a session
- 🔄 **Systemd service** — Runs as a background daemon, restarts on failure

---

## Quick Start (Linux VPS)

### 1. Prerequisites

- Python 3.10 or later
- A Telegram bot token — create one via [@BotFather](https://t.me/BotFather)
- An [OpenAI API key](https://platform.openai.com/api-keys)
- Your Telegram user ID — send `/start` to [@userinfobot](https://t.me/userinfobot)

### 2. Clone the repository

```bash
git clone https://github.com/kmageorge/ai.git ~/ai-agent
cd ~/ai-agent
```

### 3. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure

```bash
cp .env.example .env
nano .env
```

Fill in:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `OPENAI_API_KEY` | Your OpenAI API key |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs that can use the bot |
| `OPENAI_MODEL` | Model name (default: `gpt-4o`) |
| `MAX_HISTORY` | Number of conversation turns to remember (default: `20`) |
| `WORK_DIR` | Default working directory for shell commands (default: home dir) |

### 5. Run manually (for testing)

```bash
source venv/bin/activate
python bot.py
```

### 6. Install as a systemd service (for production)

```bash
bash install.sh
sudo systemctl start ai-agent
sudo systemctl status ai-agent
```

View live logs:
```bash
journalctl -u ai-agent -f
```

---

## Usage

Once the bot is running, open Telegram and chat with your bot.

### Bot commands

| Command | Description |
|---|---|
| `/start` | Show welcome message |
| `/help` | Show usage examples |
| `/clear` | Reset conversation history |
| `/id` | Show your Telegram user ID |

### Example interactions

> *What's the disk usage on this server?*

> *Show me all running Docker containers*

> *Install and start nginx*

> *Write a Python script that monitors CPU usage and save it to ~/monitor.py*

> *Show me the last 50 lines of /var/log/syslog*

> *Fetch the content from https://api.github.com/repos/kmageorge/ai*

---

## Security

- **User whitelist**: Only Telegram user IDs listed in `ALLOWED_USER_IDS` can use the bot. Keep this list limited to yourself and trusted users. Do not share the bot link publicly.
- **Credentials**: Keep your `.env` file private (`chmod 600 .env`). It is excluded from git via `.gitignore`. Never commit it.
- **Shell access**: The bot executes shell commands directly on your server as the OS user it runs under. **Never run it as root.** Create a dedicated low-privilege user account if you want an additional layer of isolation. Any authorized Telegram user has effectively the same OS permissions as the bot's process.
- **URL fetching**: The `fetch_url` tool validates that the URL scheme is `http` or `https` and blocks requests to loopback (`127.x`, `::1`, `localhost`), private (RFC 1918: `10.x`, `172.16-31.x`, `192.168.x`), link-local (`169.254.x`), and reserved IP ranges to reduce SSRF risk. Domain-based hostnames are not resolved server-side before the check — do not expose the bot on networks with sensitive internal services.
- **Audit logs**: Shell commands and file operations are logged to the systemd journal (`journalctl -u ai-agent`). Review logs regularly to detect unexpected activity.
- **Least privilege**: Consider restricting the bot user's write access with filesystem permissions so that accidental or malicious commands cannot overwrite critical system files.

---

## File Structure

```
ai-agent/
├── bot.py                          # Main bot application
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variable template
├── install.sh                      # One-shot VPS setup script
├── systemd/
│   ├── ai-agent.service            # Systemd unit file (static, for reference)
│   └── ai-agent.service.template   # Template used by install.sh
└── README.md
```

---

## License

MIT