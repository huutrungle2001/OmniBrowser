#!/usr/bin/env bash
# ==============================================================================
# OmniBrowser Skill Installer
# Installs or symlinks OmniBrowser into ~/.gemini/config/skills/omnibrowser
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="${HOME}/.gemini/config/skills"
TARGET_DIR="${SKILLS_DIR}/omnibrowser"
LEGACY_DIR="${SKILLS_DIR}/browser-automation-cdp"

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
            echo "  --symlink, -s   Create a live symlink from ~/.gemini/config/skills/omnibrowser to this repository (default)"
            echo "  --copy, -c      Copy all files to ~/.gemini/config/skills/omnibrowser as a standalone snapshot"
            echo "  --help, -h      Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

mkdir -p "${SKILLS_DIR}"

if [[ "${MODE}" == "symlink" ]]; then
    echo "🔗 Creating development symlink to ${TARGET_DIR}..."
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
