#!/usr/bin/env bash
# Install a new Telegram bot token everywhere it is used, after revoking the old one in @BotFather.
#
# The token is typed at a hidden prompt: it is never echoed, never passed on a command line,
# and never written anywhere except the two local .env files (gitignored) and GitHub's
# encrypted Actions secrets.
#
#   bash tools/rotate_telegram_token.sh
set -euo pipefail

REPOS=("unrxge/momentum-strategy" "unrxge/leveraged-trend")
ENV_FILES=("$HOME/momentum-strategy/.env" "$HOME/leveraged-trend/.env")

read -r -s -p "Paste the NEW bot token from @BotFather (input hidden), then Enter: " TOKEN
echo
[[ "$TOKEN" =~ ^[0-9]{8,12}:[A-Za-z0-9_-]{30,}$ ]] || { echo "✗ That does not look like a bot token."; exit 1; }

api() { curl -sS --max-time 20 "https://api.telegram.org/bot${TOKEN}/$1" "${@:2}"; }

api getMe | grep -q '"ok":true' || { echo "✗ Telegram rejected the token."; exit 1; }
echo "✓ Token accepted by Telegram"

for f in "${ENV_FILES[@]}"; do
  [[ -f "$f" ]] || { echo "· $f not found, skipped"; continue; }
  if grep -q "^TELEGRAM_BOT_TOKEN=" "$f" && [[ "$(grep '^TELEGRAM_BOT_TOKEN=' "$f" | cut -d= -f2-)" == "$TOKEN" ]]; then
    echo "✗ This is the OLD token already in $f — revoke it in @BotFather first, then paste the new one."; exit 1
  fi
done

# A webhook belongs to the bot, not the token, so one set by somebody else survives a revoke.
api deleteWebhook -d drop_pending_updates=true | grep -q '"ok":true' && echo "✓ Any webhook removed"

for f in "${ENV_FILES[@]}"; do
  [[ -f "$f" ]] || continue
  tmp="$(mktemp)"
  grep -v "^TELEGRAM_BOT_TOKEN=" "$f" > "$tmp" || true
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" >> "$tmp"
  chmod 600 "$tmp" && mv "$tmp" "$f"
  echo "✓ Updated $f"
done

for r in "${REPOS[@]}"; do
  printf '%s' "$TOKEN" | gh secret set TELEGRAM_BOT_TOKEN --repo "$r"
  echo "✓ Updated GitHub secret on $r"
done

CHAT="$(grep '^TELEGRAM_CHAT_ID=' "${ENV_FILES[0]}" | cut -d= -f2- | tr -d '"'"'"' ')"
if api sendMessage -d chat_id="$CHAT" --data-urlencode "text=✅ Bot token replaced. Only your two trading bots can post here now." | grep -q '"ok":true'; then
  echo "✓ Test message sent — check Telegram"
else
  echo "⚠️  Token installed, but the test message failed. Open the bot in Telegram and press Start, then re-run."
fi
