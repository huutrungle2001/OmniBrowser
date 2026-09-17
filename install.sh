#!/usr/bin/env bash
# ==============================================================================
# OmniBrowser Skill Installer
#
# One-Line Remote Install (curl):
#   curl -fsSL https://raw.githubusercontent.com/huutrungle2001/OmniBrowser/master/install.sh | bash
#
# Local Repository Install:
#   ./install.sh [--symlink | --copy]
# ==============================================================================

set -e

REPO_URL="https://github.com/huutrungle2001/OmniBrowser.git"
SKILLS_DIR="${HOME}/.gemini/config/skills"
TARGET_DIR="${SKILLS_DIR}/omnibrowser"
LEGACY_DIR="${SKILLS_DIR}/browser-automation-cdp"

# Detect if running locally inside a cloned OmniBrowser checkout
IS_LOCAL=false
if [[ -n "${BASH_SOURCE[0]}" && -f "${BASH_SOURCE[0]}" ]]; then
    CANDIDATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${CANDIDATE_DIR}/SKILL.md" && -d "${CANDIDATE_DIR}/src/browser_core" ]]; then
        IS_LOCAL=true
        SCRIPT_DIR="${CANDIDATE_DIR}"
    fi
fi

MODE="symlink"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --copy|-c)
            MODE="copy"
            shift
            ;;
        --symlink|-s)
            MODE="symlink"
            shift
            ;;
        --help|-h)
            echo "Usage: ./install.sh [options]"
            echo "Options:"
            echo "  --symlink, -s   Create a live symlink from ~/.gemini/config/skills/omnibrowser to this repository (default for local)"
            echo "  --copy, -c      Copy all files to ~/.gemini/config/skills/omnibrowser as a standalone snapshot"
            echo "  --help, -h      Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            shift
            ;;
    esac
done

mkdir -p "${SKILLS_DIR}"

if [[ "${IS_LOCAL}" == "true" ]]; then
    if [[ "${MODE}" == "symlink" ]]; then
        echo "🔗 Creating development symlink: ${TARGET_DIR} -> ${SCRIPT_DIR}..."
        if [[ -d "${TARGET_DIR}" && ! -L "${TARGET_DIR}" ]]; then
            BACKUP="${TARGET_DIR}.backup.$(date +%s)"
            echo "⚠️ Existing target directory detected. Backing up to ${BACKUP}..."
            mv "${TARGET_DIR}" "${BACKUP}"
        fi
        rm -rf "${TARGET_DIR}"
        ln -s "${SCRIPT_DIR}" "${TARGET_DIR}"
        echo "✅ Symlinked: ${TARGET_DIR} -> ${SCRIPT_DIR}"
    else
        echo "📦 Copying OmniBrowser skill files to ${TARGET_DIR}..."
        mkdir -p "${TARGET_DIR}"
        cp -r "${SCRIPT_DIR}/SKILL.md" "${TARGET_DIR}/"
        cp -r "${SCRIPT_DIR}/src" "${TARGET_DIR}/"
        cp -r "${SCRIPT_DIR}/scripts" "${TARGET_DIR}/"
        cp -r "${SCRIPT_DIR}/recipes" "${TARGET_DIR}/"
        cp -r "${SCRIPT_DIR}/requirements.txt" "${TARGET_DIR}/"
        if [[ -d "${SCRIPT_DIR}/references" ]]; then
            cp -r "${SCRIPT_DIR}/references" "${TARGET_DIR}/"
        fi
        if [[ -d "${SCRIPT_DIR}/examples" ]]; then
            cp -r "${SCRIPT_DIR}/examples" "${TARGET_DIR}/"
        fi
        echo "✅ Copied successfully to: ${TARGET_DIR}"
    fi
else
    echo "🚀 Installing OmniBrowser from GitHub (${REPO_URL})..."
    if [[ -d "${TARGET_DIR}" && -d "${TARGET_DIR}/.git" ]]; then
        echo "🔄 Existing installation detected. Updating to latest version..."
        git -C "${TARGET_DIR}" pull --ff-only origin master
    elif [[ -d "${TARGET_DIR}" && ! -L "${TARGET_DIR}" ]]; then
        BACKUP="${TARGET_DIR}.backup.$(date +%s)"
        echo "⚠️ Existing non-git directory detected. Backing up to ${BACKUP}..."
        mv "${TARGET_DIR}" "${BACKUP}"
        git clone --depth 1 "${REPO_URL}" "${TARGET_DIR}"
    else
        rm -rf "${TARGET_DIR}"
        git clone --depth 1 "${REPO_URL}" "${TARGET_DIR}"
    fi
    echo "✅ Installed latest OmniBrowser repository into: ${TARGET_DIR}"
fi

# Backward-compatibility alias
echo "🔗 Setting up legacy backward-compatibility symlink: browser-automation-cdp..."
if [[ -d "${LEGACY_DIR}" && ! -L "${LEGACY_DIR}" ]]; then
    BACKUP="${LEGACY_DIR}.backup.$(date +%s)"
    echo "⚠️ Backing up existing legacy folder to ${BACKUP}..."
    mv "${LEGACY_DIR}" "${BACKUP}"
fi
rm -rf "${LEGACY_DIR}"
ln -s "${TARGET_DIR}" "${LEGACY_DIR}"
echo "✅ Legacy symlink created: ${LEGACY_DIR} -> ${TARGET_DIR}"

echo ""
echo "🎉 OmniBrowser successfully installed as an active agent skill!"
echo "📍 Skill Path: ${TARGET_DIR}"
echo "🧪 Try running: python3 ${TARGET_DIR}/scripts/cdp_controller.py list-tabs"
