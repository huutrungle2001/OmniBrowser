#!/usr/bin/env bash
#
# scripts/send_prompt.sh - Robust, lightweight, standalone prompt transmission script
# Specifically tuned for tmux sessions running Codex CLI (Rust/Crossterm TUI) & Agym/shells.
#
# Features:
#   1. Target Pane Check: Verifies session/pane exists before transmission.
#   2. Startup Banner Auto-Skip: Detects 'Update available!' in Codex and sends '2' + Enter.
#   3. CRLF Normalization: Strips '\r' to prevent double line breaks in multiline TUI buffers.
#   4. Paced Injection: Uses tmux load-buffer/paste-buffer, waits 250ms, then sends Enter.
#   5. Closed-Loop Verification: Captures pane and retries Enter if unsubmitted, verifies Working/Queued/Cleared.
#   6. Safety: Never sends blind C-c to empty composer buffers.
#

set -eo pipefail

show_usage() {
    cat <<'EOF'
Usage:
  scripts/send_prompt.sh -s <target_session_or_pane> -p "<prompt_text>"
  scripts/send_prompt.sh -s <target_session_or_pane> -f <prompt_file>
  scripts/send_prompt.sh <target_session_or_pane> "<prompt_text>"

Flags:
  -s, --session, -t, --target   Tmux session or pane ID (e.g. portfolio-hive, slscm-hive, omni-hive)
  -p, --prompt                  Prompt string to send
  -f, --file                    File path containing prompt text
  -w, --wait                    Timeout for verification in seconds (default: 5)
  -h, --help                    Print usage and exit

Examples:
  scripts/send_prompt.sh -s portfolio-hive -p "Run tests and verify"
  scripts/send_prompt.sh -s slscm-hive -f /path/to/task.md -w 10
  scripts/send_prompt.sh workbench "[AGENT_NOTIFY] Task complete"
EOF
}

target=""
prompt_text=""
file_path=""
record_arg=""
wait_timeout=5

# Parse CLI options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--session|-t|--target|-a|--agent)
            target="${2:-}"
            shift 2
            ;;
        -p|--prompt|-m|--message)
            prompt_text="${2:-}"
            shift 2
            ;;
        -f|--file)
            file_path="${2:-}"
            shift 2
            ;;
        -r|--record)
            record_arg="${2:-}"
            shift 2
            ;;
        -w|--wait)
            wait_timeout="${2:-5}"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "ERROR: Unknown option: $1" >&2
            show_usage >&2
            exit 2
            ;;
        *)
            if [[ -z "$target" ]]; then
                target="$1"
            elif [[ -z "$prompt_text" && -z "$file_path" ]]; then
                prompt_text="$1"
            else
                echo "ERROR: Unexpected argument: $1" >&2
                show_usage >&2
                exit 2
            fi
            shift
            ;;
    esac
done

# Validate conflict between -p and -f
if [[ -n "$prompt_text" && -n "$file_path" ]]; then
    echo "ERROR: Cannot specify both --prompt (-p) and --file (-f)." >&2
    exit 2
fi

# Read from file if -f specified
if [[ -n "$file_path" ]]; then
    if [[ ! -f "$file_path" ]]; then
        echo "ERROR: Prompt file '$file_path' does not exist." >&2
        exit 1
    fi
    prompt_text="$(cat "$file_path")"
fi

# Validate required inputs
if [[ -z "$target" ]]; then
    echo "ERROR: Target session or pane (-s/--session/-t/--target) is required." >&2
    show_usage >&2
    exit 2
fi

if [[ -z "$prompt_text" ]]; then
    echo "ERROR: Prompt text (-p/--prompt) or prompt file (-f/--file) is required." >&2
    show_usage >&2
    exit 2
fi

if [[ -n "$record_arg" ]]; then
    prompt_text="${prompt_text} | RECORD=${record_arg}"
fi

# Validate timeout
if ! [[ "$wait_timeout" =~ ^[0-9]+$ ]] || [[ "$wait_timeout" -le 0 ]]; then
    echo "ERROR: Invalid wait timeout: '$wait_timeout'. Must be a positive integer." >&2
    exit 2
fi

# Multi-target support: if target contains commas, broadcast to all targets
if [[ "$target" == *","* ]]; then
    IFS=',' read -ra target_list <<< "$target"
    overall_exit=0
    for single_t in "${target_list[@]}"; do
        single_t="$(echo "$single_t" | xargs)"
        if [[ -n "$single_t" ]]; then
            echo "--- Broadcasting prompt to target: $single_t ---"
            "$0" -s "$single_t" -p "$prompt_text" -w "$wait_timeout" || overall_exit=1
        fi
    done
    exit $overall_exit
fi

# 1. Target Pane Check: resolve and verify session/pane exists
resolve_target() {
    local t="$1"
    if tmux list-panes -t "$t" >/dev/null 2>&1; then
        echo "$t"
        return 0
    fi
    if tmux list-panes -t "${t}:0.0" >/dev/null 2>&1; then
        echo "${t}:0.0"
        return 0
    fi
    # Check standard project prefixes if bare target was given
    for prefix in "portfolio-" "portfolio_" "slscm-" "slscm_" "agym-" "agym_" "agy-" "agy_" "omni-" "omni_" "parking-" "parking_" "onteractive-" "onteractive_" "trunglh-" "trunglh_" "workbench-" "workbench_"; do
        if tmux list-panes -t "${prefix}${t}" >/dev/null 2>&1; then
            echo "${prefix}${t}"
            return 0
        fi
        if tmux list-panes -t "${prefix}${t}:0.0" >/dev/null 2>&1; then
            echo "${prefix}${t}:0.0"
            return 0
        fi
    done
    return 1
}

if ! resolved_target="$(resolve_target "$target")"; then
    echo "ERROR: Target tmux session or pane '$target' not found." >&2
    exit 1
fi

# 2. Startup Banner Auto-Skip: check if 'Update available!' is present in target pane
current_pane="$(tmux capture-pane -p -t "$resolved_target" 2>/dev/null || true)"
if [[ "$current_pane" == *"Update available!"* ]]; then
    echo "Detected update banner in $resolved_target. Sending '2' + Enter to skip..."
    tmux send-keys -t "$resolved_target" 2
    sleep 0.25
    tmux send-keys -t "$resolved_target" Enter
    sleep 0.5
fi

# 3. CRLF Normalization: Strip carriage returns to prevent double line breaks
normalized_prompt="$(printf '%s' "$prompt_text" | tr -d '\r')"
if ! [[ "$normalized_prompt" =~ [^[:space:]] ]]; then
    echo "ERROR: Prompt text is empty after whitespace stripping." >&2
    exit 1
fi

# 4. Paced Injection: Load text via named tmux buffer, paste bracketed (-p), wait 0.25s, then send Enter
buffer_name="sp_buf_${$}_$(date +%s)"
printf '%s' "$normalized_prompt" | tmux load-buffer -b "$buffer_name" -
tmux paste-buffer -p -b "$buffer_name" -d -t "$resolved_target"

# Wait 250ms for Crossterm TUI event loop to update input buffer before Enter
sleep 0.25
tmux send-keys -t "$resolved_target" Enter

# 5. Closed-Loop Verification: Wait 0.5s, capture pane, retry Enter if unsubmitted, verify state transition
sleep 0.5

prompt_first_line="$(printf '%s\n' "$normalized_prompt" | grep -m 1 -v '^[[:space:]]*$' || true)"
prompt_prefix="${prompt_first_line:0:30}"

start_time=$(date +%s)
is_submitted=0

while true; do
    pane_content="$(tmux capture-pane -p -t "$resolved_target" 2>/dev/null || true)"

    if printf '%s\n' "$pane_content" | grep -qF "›"; then
        # Target is Codex CLI
        last_codex_line="$(printf '%s\n' "$pane_content" | grep -F "›" | tail -n 1 || true)"

        stuck_in_composer=0
        if [[ -n "$prompt_prefix" && "$last_codex_line" == *"$prompt_prefix"* ]]; then
            stuck_in_composer=1
        fi

        has_working=0
        if printf '%s\n' "$pane_content" | grep -qE "• Working|Working \(|• Thinking|Running command\.\.\."; then
            has_working=1
        fi

        has_queued=0
        if printf '%s\n' "$pane_content" | grep -qE "Messages to be submitted\.\.\.|Message[s]? queued"; then
            has_queued=1
        fi

        prompt_cleared=0
        if [[ "$last_codex_line" == *"Ask Codex to do anything"* ]] || [[ "$last_codex_line" =~ ^[[:space:]]*›[[:space:]]*$ ]]; then
            prompt_cleared=1
        fi

        if [[ $stuck_in_composer -eq 1 ]]; then
            echo "Prompt remains sitting unsubmitted at '› ', sending extra Enter..."
            tmux send-keys -t "$resolved_target" Enter
            sleep 0.5
        elif [[ $has_working -eq 1 || $has_queued -eq 1 || $prompt_cleared -eq 1 ]]; then
            is_submitted=1
            break
        fi
    elif printf '%s\n' "$pane_content" | grep -qE "esc to cancel|\? for shortcuts"; then
        # Target is Antigravity CLI (agym)
        last_agym_line="$(printf '%s\n' "$pane_content" | grep -E "^[[:space:]]*>[[:space:]]*" | tail -n 1 || true)"

        stuck_in_agym=0
        if [[ -n "$prompt_prefix" && "$last_agym_line" == *"$prompt_prefix"* ]]; then
            stuck_in_agym=1
        fi
        if [[ "$last_agym_line" == *"Pasted text"* ]]; then
            stuck_in_agym=1
        fi

        has_generating=0
        if printf '%s\n' "$pane_content" | grep -qE "Generating\.\.\.|Thinking\.\.\.|Running command\.\.\."; then
            has_generating=1
        fi

        if [[ $stuck_in_agym -eq 1 ]]; then
            echo "Prompt remains sitting unsubmitted at '>', sending extra Enter..."
            tmux send-keys -t "$resolved_target" Enter
            sleep 0.5
        elif [[ $has_generating -eq 1 ]]; then
            is_submitted=1
            break
        else
            is_submitted=1
            break
        fi
    else
        # Generic CLI target
        is_submitted=1
        break
    fi

    current_time=$(date +%s)
    elapsed=$((current_time - start_time))
    if [[ "$elapsed" -ge "$wait_timeout" ]]; then
        break
    fi

    sleep 0.5
done

if [[ $is_submitted -eq 1 ]]; then
    echo "Prompt successfully submitted to $resolved_target."
    exit 0
else
    echo "ERROR: Timed out waiting for prompt to submit in $resolved_target after ${wait_timeout}s." >&2
    exit 1
fi
