#!/usr/bin/env bash
# ==============================================================================
# Launch Google Chrome with dedicated AI Profile (~/.chrome-ai-profile) & CDP
# Default Port: 17082
# ==============================================================================

PORT="${CDP_PORT:-17082}"
PROFILE_DIR="${CHROME_PROFILE_DIR:-$HOME/.chrome-ai-profile}"

echo "🚀 Checking Chrome on port ${PORT}..."
echo "📁 Profile directory: ${PROFILE_DIR}"

if curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "ℹ️ Chrome is already running with remote debugging on port ${PORT}."
    exit 0
fi

# Launch via macOS LaunchServices so Chrome lives independently as a standard app
open -na "/Applications/Google Chrome.app" --args \
    --remote-debugging-port="${PORT}" \
    --user-data-dir="${PROFILE_DIR}" \
    --no-first-run \
    --no-default-browser-check \
    "$@"

for i in {1..10}; do
    if curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
        echo "✅ Chrome CDP ready! Active on http://127.0.0.1:${PORT}"
        exit 0
    fi
    sleep 1
done

echo "❌ Could not verify Chrome CDP on port ${PORT}."
exit 1
