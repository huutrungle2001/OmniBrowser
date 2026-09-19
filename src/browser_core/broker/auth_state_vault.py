"""Auth State Vault: Single-Writer / Multi-Reader snapshot storage.

Enforces Invariant 9 (Profile Sanctity):
`~/.chrome-ai-profile` is Auth Source of Truth only, never a concurrent runtime profile.
Snapshots are stored with strict permissions (chmod 600) and versioned immutably.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any


def get_default_vault_dir() -> Path:
    env_dir = os.environ.get("OMNIBROWSER_VAULT_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path.home() / ".omnibrowser" / "vault"


PROTECTED_PROFILE_PATH = Path("~/.chrome-ai-profile").expanduser().resolve()


class AuthStateVault:
    """Manages immutable, versioned auth snapshots with strict file permissions."""

    def __init__(self, vault_dir: Path | str | None = None):
        self.vault_dir = Path(vault_dir).expanduser().resolve() if vault_dir else get_default_vault_dir()
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        # Ensure vault root directory has restricted permissions (0700)
        try:
            os.chmod(self.vault_dir, 0o700)
        except OSError:
            pass

    def _get_identity_dir(self, identity: str) -> Path:
        clean_identity = "".join(c for c in identity if c.isalnum() or c in ("-", "_", ".")).strip()
        if not clean_identity:
            raise ValueError(f"Invalid auth identity: {identity!r}")
        identity_dir = self.vault_dir / clean_identity
        identity_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(identity_dir, 0o700)
        except OSError:
            pass
        return identity_dir

    def save_snapshot(
        self,
        identity: str,
        cookies: list[dict[str, Any]] | None = None,
        local_storage: dict[str, str] | None = None,
        session_storage: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Save a new immutable snapshot for an identity. Returns version string."""
        identity_dir = self._get_identity_dir(identity)
        timestamp = int(time.time())
        
        # Count existing versions to create monotonic version tag
        existing = sorted(identity_dir.glob("v*.json"))
        next_ver = len(existing) + 1
        version_str = f"v{next_ver}"
        snapshot_file = identity_dir / f"{version_str}.json"

        snapshot_data = {
            "identity": identity,
            "version": version_str,
            "created_at": timestamp,
            "cookies": cookies or [],
            "local_storage": local_storage or {},
            "session_storage": session_storage or {},
            "metadata": metadata or {},
        }

        # Write to temp file first then atomically rename with 0600
        temp_file = identity_dir / f".tmp_{version_str}_{timestamp}.json"
        temp_file.write_text(json.dumps(snapshot_data, indent=2), encoding="utf-8")
        try:
            os.chmod(temp_file, 0o600)
        except OSError:
            pass
        temp_file.replace(snapshot_file)
        try:
            os.chmod(snapshot_file, 0o600)
        except OSError:
            pass

        # Update symlink or pointer for 'latest'
        latest_file = identity_dir / "latest.json"
        latest_file.write_text(json.dumps({"latest": version_str, "updated_at": timestamp}), encoding="utf-8")
        try:
            os.chmod(latest_file, 0o600)
        except OSError:
            pass

        return version_str

    def get_snapshot(self, identity: str, version: str | None = None) -> dict[str, Any] | None:
        """Get snapshot data for an identity and optional version (defaults to latest)."""
        identity_dir = self._get_identity_dir(identity)
        if version is None:
            latest_file = identity_dir / "latest.json"
            if not latest_file.exists():
                # Try finding highest numbered version
                versions = sorted(identity_dir.glob("v*.json"))
                if not versions:
                    return None
                snapshot_file = versions[-1]
            else:
                latest_info = json.loads(latest_file.read_text(encoding="utf-8"))
                version = latest_info.get("latest")
                snapshot_file = identity_dir / f"{version}.json"
        else:
            snapshot_file = identity_dir / f"{version}.json"

        if not snapshot_file.exists():
            return None

        return json.loads(snapshot_file.read_text(encoding="utf-8"))

    def list_snapshots(self, identity: str | None = None) -> list[dict[str, Any]]:
        """List snapshots across all identities or for a specific identity."""
        results = []
        if identity:
            identity_dirs = [self._get_identity_dir(identity)]
        else:
            identity_dirs = [d for d in self.vault_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]

        for id_dir in identity_dirs:
            for s_file in sorted(id_dir.glob("v*.json")):
                try:
                    data = json.loads(s_file.read_text(encoding="utf-8"))
                    results.append({
                        "identity": data.get("identity", id_dir.name),
                        "version": data.get("version", s_file.stem),
                        "created_at": data.get("created_at", 0),
                        "cookie_count": len(data.get("cookies", [])),
                        "local_storage_keys": len(data.get("local_storage", {})),
                        "path": str(s_file),
                    })
                except Exception:
                    continue
        return results

    @staticmethod
    def assert_not_protected_profile(path: Path | str) -> None:
        """Enforces Invariant 9: never allow direct concurrent access to ~/.chrome-ai-profile."""
        resolved = Path(path).expanduser().resolve()
        if resolved == PROTECTED_PROFILE_PATH or PROTECTED_PROFILE_PATH in resolved.parents:
            raise PermissionError(
                f"SAFETY INVARIANT 9 VIOLATION: Direct concurrent access or modification "
                f"of {PROTECTED_PROFILE_PATH} is strictly forbidden."
            )
