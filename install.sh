#!/usr/bin/env bash
# install.sh — One-shot setup script for the Telegram AI Agent on a Linux VPS
# Run as the user you want to own the bot (NOT as root).
set -euo pipefail

INSTALL_DIR="$HOME/ai-agent"
REPO_URL="https://github.com/kmageorge/ai.git"
SERVICE_NAME="ai-agent"

echo "=== Telegram AI Agent Installer ==="

# 1. Ensure Python 3.10+ is available
python3 --version >/dev/null 2>&1 || { echo "ERROR: python3 not found."; exit 1; }

# 2. Clone or update the repo
if [ -d "$INSTALL_DIR/.git" ]; then
    echo ">> Updating existing installation at $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull
else
    echo ">> Cloning repository to $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# 3. Create virtual environment
if [ ! -d venv ]; then
    echo ">> Creating Python virtual environment"
    python3 -m venv venv
fi

echo ">> Installing Python dependencies"
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q

# 4. Create .env from example if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "============================================================"
    echo "  IMPORTANT: Edit .env before starting the bot!"
    echo "  Fill in your TELEGRAM_BOT_TOKEN, OPENAI_API_KEY,"
    echo "  and ALLOWED_USER_IDS."
    echo "  Run: nano $INSTALL_DIR/.env"
    echo "============================================================"
    echo ""
fi

# 5. Install systemd service (requires sudo)
USERNAME="$(whoami)"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo ">> Installing systemd service (requires sudo)"
# Use envsubst for safe variable substitution instead of building sed expressions
# with user-controlled strings.
export USERNAME HOME INSTALL_DIR
envsubst '${USERNAME} ${HOME} ${INSTALL_DIR}' \
    < "$INSTALL_DIR/systemd/ai-agent.service.template" \
    | sudo tee "$SERVICE_FILE" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit your config:  nano $INSTALL_DIR/.env"
echo "  2. Start the bot:     sudo systemctl start $SERVICE_NAME"
echo "  3. Check status:      sudo systemctl status $SERVICE_NAME"
echo "  4. View logs:         journalctl -u $SERVICE_NAME -f"
