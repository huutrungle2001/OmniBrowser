#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  Flag-based (any order):
    scripts/notify_agent.sh \
      -a|--agent|-t|--target <tmux_target|omni_hub|omni_orchestrator|omni_implementer|omni_reviewer|workbench-codex> \
      -m|--message <message_string> \
      -f|--file <file_path_containing_message> \
      [-r|--record <committed-task-result-or-review.md>] \
      [--timeout-seconds <seconds>]

  Positional:
    scripts/notify_agent.sh <tmux_target> <message> [record_path]

Examples:
  scripts/notify_agent.sh -a omni_implementer -m "Task task-001 ready" -r .agents/communication/tasks/task-001.md
  scripts/notify_agent.sh -a omni_reviewer -m "Result for task-001 ready" -r .agents/communication/results/result-001.md
  scripts/notify_agent.sh -a omni_hub -m "Phase 1 complete"
EOF
}

target_arg=""
record_arg=""
message_arg=""
file_arg=""
timeout_seconds=60
reset_arg=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -a|--agent|-t|--target)
            target_arg="${2:-}"
            shift 2
            ;;
        -m|--message)
            message_arg="${2:-}"
            shift 2
            ;;
        -f|--file|--message-file)
            file_arg="${2:-}"
            shift 2
            ;;
        -r|--record)
            record_arg="${2:-}"
            shift 2
            ;;
        --timeout-seconds|--timeout)
            timeout_seconds="${2:-}"
            shift 2
            ;;
        -R|--reset)
            reset_arg=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "ERROR: unknown flag: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [[ -z "$target_arg" ]]; then
                target_arg="$1"
            elif [[ -z "$message_arg" && -z "$file_arg" ]]; then
                message_arg="$1"
            elif [[ -z "$record_arg" ]]; then
                record_arg="$1"
            else
                echo "ERROR: unexpected extra argument: $1" >&2
                usage >&2
                exit 2
            fi
            shift
            ;;
    esac
done

if [[ -n "$file_arg" ]]; then
    if [[ ! -f "$file_arg" ]]; then
        echo "ERROR: message file does not exist: $file_arg" >&2
        exit 3
    fi
    message_arg="$(cat "$file_arg")"
fi

if [[ -z "$target_arg" || -z "$message_arg" ]]; then
    echo "ERROR: agent target (-a/-t) and message (-m/-f) are required." >&2
    usage >&2
    exit 2
fi

resolve_target() {
    local target="$1"

    # 1. Exact match
    if tmux display-message -p -t "${target}:0.0" '#{pane_dead}' >/dev/null 2>&1; then
        echo "${target}:0.0"
        return 0
    elif tmux display-message -p -t "${target}" '#{pane_dead}' >/dev/null 2>&1; then
        echo "${target}"
        return 0
    fi

    # 2. Check prefixes
    for prefix in "omni_" "omni-" "workbench_" "workbench-"; do
        if tmux display-message -p -t "${prefix}${target}:0.0" '#{pane_dead}' >/dev/null 2>&1; then
            echo "${prefix}${target}:0.0"
            return 0
        elif tmux display-message -p -t "${prefix}${target}" '#{pane_dead}' >/dev/null 2>&1; then
            echo "${prefix}${target}"
            return 0
        fi
    done

    # 3. Role name aliases fallback
    case "$target" in
        hub)
            if tmux display-message -p -t "omni_hub:0.0" '#{pane_dead}' >/dev/null 2>&1; then
                echo "omni_hub:0.0"
            else
                echo "workbench_hub:0.0"
            fi
            ;;
        orchestrator)
            if tmux display-message -p -t "omni_orchestrator:0.0" '#{pane_dead}' >/dev/null 2>&1; then
                echo "omni_orchestrator:0.0"
            else
                echo "workbench_orchestrator:0.0"
            fi
            ;;
        implementer|coder|dev)
            if tmux display-message -p -t "omni_implementer:0.0" '#{pane_dead}' >/dev/null 2>&1; then
                echo "omni_implementer:0.0"
            elif tmux display-message -p -t "workbench-codex:0.0" '#{pane_dead}' >/dev/null 2>&1; then
                echo "workbench-codex:0.0"
            elif tmux display-message -p -t "workbench-codex" '#{pane_dead}' >/dev/null 2>&1; then
                echo "workbench-codex"
            else
                echo "omni_implementer:0.0"
            fi
            ;;
        reviewer|review)
            echo "omni_reviewer:0.0"
            ;;
        *)
            return 1
            ;;
    esac
}

if ! target_pane="$(resolve_target "$target_arg")"; then
    echo "ERROR: unsupported target or session does not exist: $target_arg" >&2
    echo "Allowed targets: hub, orchestrator, implementer, reviewer, or active tmux session (e.g. omni_orchestrator, workbench-codex)." >&2
    exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: run this helper from inside the project Git repository." >&2
    exit 3
}

# Lint check if record is provided
if [[ -n "$record_arg" ]]; then
    linter_script="${repo_root}/scripts/lint_communication_records.py"
    if [[ -f "$linter_script" ]]; then
        python3 "$linter_script" --handoff "$record_arg" --target "$target_arg"
    fi
fi

# Dispatch notification to target tmux session
payload_line="[AGENT_NOTIFY] FROM=$(git config user.name 2>/dev/null || echo "agent") | MSG=${message_arg}"
if [[ -n "$record_arg" ]]; then
    payload_line="${payload_line} | RECORD=${record_arg}"
fi

tmux send-keys -t "$target_pane" "$payload_line" Enter
echo "Notification delivered to $target_pane"
exit 0
