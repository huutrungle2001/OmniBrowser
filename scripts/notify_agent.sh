#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  Flag-based (any order):
    scripts/notify_agent.sh \
      -a|--agent|-t|--target <hub|orchestrator|implementer|reviewer|tmux_target> \
      -m|--message <message_string> \
      -f|--file <file_path_containing_message> \
      [-r|--record <committed-task-result-or-review.md>] \
      [--timeout-seconds <seconds>]

  Positional:
    scripts/notify_agent.sh <tmux_target> <message> [record_path]

Examples:
  scripts/notify_agent.sh -a implementer -m "Task task-001 ready" -r .agents/communication/tasks/task-001-core-foundation.md
  scripts/notify_agent.sh -a reviewer -m "Result for task-001 ready" -r .agents/communication/results/result-001.md
  scripts/notify_agent.sh -a hub -m "Phase 1 complete"
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

check_pane_alive() {
    local pane="$1"
    tmux list-panes -t "$pane" >/dev/null 2>&1
}

resolve_target() {
    local target="$1"

    # 1. Exact match checks
    for candidate in "${target}:0.0" "${target}" "${target//_/-}:0.0" "${target//-/_}:0.0" "${target//_/-}" "${target//-/_}"; do
        if check_pane_alive "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    # 2. Canonical role aliases fallback (supporting omni-hub, workbench sessions, etc.)
    case "$target" in
        hub|omni_hub|omni-hub|workbench_hub)
            for cand in "omni-hub:0.0" "omni_hub:0.0" "omni-hub" "omni_hub" "workbench_hub:0.0" "workbench_hub"; do
                if check_pane_alive "$cand"; then
                    echo "$cand"
                    return 0
                fi
            done
            ;;
        orchestrator|omni_orchestrator|omni-orchestrator|workbench_orchestrator)
            for cand in "omni_orchestrator:0.0" "omni-orchestrator:0.0" "omni_orchestrator" "workbench_orchestrator:0.0" "workbench_orchestrator"; do
                if check_pane_alive "$cand"; then
                    echo "$cand"
                    return 0
                fi
            done
            ;;
        implementer|coder|dev|omni_implementer|omni-implementer|workbench-codex)
            for cand in "omni-hub:0.0" "omni-hub" "omni_implementer:0.0" "omni-implementer:0.0" "omni_implementer" "workbench-codex:0.0" "workbench-codex"; do
                if check_pane_alive "$cand"; then
                    echo "$cand"
                    return 0
                fi
            done
            ;;
        reviewer|review|omni_reviewer|omni-reviewer|workbench_reviewer)
            # In OmniBrowser, reviewer is Workbench Hub. Map reviewer notifications to hub.
            for cand in "omni-hub:0.0" "omni_hub:0.0" "omni-hub" "omni_hub" "workbench_hub:0.0" "workbench_hub"; do
                if check_pane_alive "$cand"; then
                    echo "$cand"
                    return 0
                fi
            done
            ;;
    esac

    # 3. Check standard prefixes
    for prefix in "omni_" "omni-" "workbench_" "workbench-"; do
        for cand in "${prefix}${target}:0.0" "${prefix}${target}"; do
            if check_pane_alive "$cand"; then
                echo "$cand"
                return 0
            fi
        done
    done

    return 1
}

if ! target_pane="$(resolve_target "$target_arg")"; then
    echo "[INFO] No active tmux pane found for target '$target_arg'."
    echo "[INFO] Communication record is committed in Git and ready for Hub."
    exit 0
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: run this helper from inside the project Git repository." >&2
    exit 3
}

# Lint check if record is provided
if [[ -n "$record_arg" ]]; then
    linter_script="${repo_root}/scripts/lint_communication_records.py"
    if [[ ! -f "$linter_script" ]]; then
        # Check skill location fallback
        linter_script="${repo_root}/.agents/skills/agent-protocol/scripts/lint_communication_records.py"
    fi
    if [[ -f "$linter_script" ]]; then
        python3 "$linter_script" --handoff "$record_arg" --target "$target_arg"
    fi
fi

# Dispatch notification to target tmux session
payload_line="[AGENT_NOTIFY] FROM=$(git config user.name 2>/dev/null || echo "agent") | MSG=${message_arg}"
if [[ -n "$record_arg" ]]; then
    payload_line="${payload_line} | RECORD=${record_arg}"
fi

# Send literal payload line first to avoid key translation glitches, then send C-m (Enter)
tmux send-keys -t "$target_pane" -l "$payload_line"
tmux send-keys -t "$target_pane" C-m

echo "Notification delivered to $target_pane"
exit 0
