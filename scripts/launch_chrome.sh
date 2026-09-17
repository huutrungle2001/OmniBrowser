#!/usr/bin/env bash
# ==============================================================================
# Launch Google Chrome with dedicated AI Profile (~/.chrome-ai-profile) & CDP
# Default Port: 17082
# ==============================================================================

PORT="${CDP_PORT:-17082}"
PROFILE_DIR="${CHROME_PROFILE_DIR:-$HOME/.chrome-ai-profile}"

echo "🚀 Checking Chrome on port ${PORT}..."
echo "📁 Profile directory: ${PROFILE_DIR}"

if pgrep -f "Google Chrome.*remote-debugging-port=${PORT}" >/dev/null 2>&1; then
    echo "ℹ️ Chrome is already running with remote debugging on port ${PORT}."
    exit 0
fi

nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port="${PORT}" \
    --user-data-dir="${PROFILE_DIR}" \
    --no-first-run \
    --no-default-browser-check \
    "$@" >/dev/null 2>&1 &

sleep 1

if curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "✅ Chrome CDP ready! Active on http://127.0.0.1:${PORT}"
else
    echo "⚠️ Waiting an extra second for Chrome CDP to bind..."
    sleep 2
    if curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
        echo "✅ Chrome CDP ready on port ${PORT}!"
    else
        echo "❌ Could not verify Chrome CDP on port ${PORT}."
        exit 1
    fi
fi
