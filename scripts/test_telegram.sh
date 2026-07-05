#!/usr/bin/env bash
# Send a one-off Telegram test message using the bot token / chat id in your
# environment (or .env), without touching the draft board. Use this to
# confirm delivery before relying on it during the live draft.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../python_app"

MESSAGE="${1:-MLB Draft Tracker: test notification sent $(date -u +%FT%TZ)}"
python3 main.py test-telegram --message "$MESSAGE"
