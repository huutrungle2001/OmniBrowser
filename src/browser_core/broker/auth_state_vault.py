"""Auth State Vault: Single-Writer / Multi-Reader snapshot storage.

Enforces Invariant 9 (Profile Sanctity):
`~/.chrome-ai-profile` is Auth Source of Truth only, never a concurrent runtime profile.
Snapshots are stored with strict permissions (chmod 600) and versioned immutably.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid


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
        self.assert_not_protected_profile(self.vault_dir)
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
        self.assert_not_protected_profile(identity_dir)
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

        # Inter-process lock for atomic version allocation and write
        lock_file = identity_dir / ".lock"
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            # Count existing versions numerically to create monotonic version tag
            existing = sorted(
                identity_dir.glob("v*.json"),
                key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0,
            )
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

            # Write to unique temp file first then atomically rename with 0600
            temp_file = identity_dir / f".tmp_{version_str}_{uuid.uuid4().hex}.json"
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

            # Update pointer for 'latest' atomically via temp file
            latest_file = identity_dir / "latest.json"
            latest_temp = identity_dir / f".tmp_latest_{uuid.uuid4().hex}.json"
            latest_temp.write_text(json.dumps({"latest": version_str, "updated_at": timestamp}), encoding="utf-8")
            try:
                os.chmod(latest_temp, 0o600)
            except OSError:
                pass
            latest_temp.replace(latest_file)
            try:
                os.chmod(latest_file, 0o600)
            except OSError:
                pass

            return version_str
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)

    def get_snapshot(self, identity: str, version: str | None = None) -> dict[str, Any] | None:
        """Get snapshot data for an identity and optional version (defaults to latest)."""
        identity_dir = self._get_identity_dir(identity)
        if version is None:
            latest_file = identity_dir / "latest.json"
            if not latest_file.exists():
                # Try finding highest numbered version
                versions = sorted(
                    identity_dir.glob("v*.json"),
                    key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0,
                )
                if not versions:
                    return None
                target_file = versions[-1]
            else:
                try:
                    data = json.loads(latest_file.read_text(encoding="utf-8"))
                    target_file = identity_dir / f"{data['latest']}.json"
                except Exception:
                    versions = sorted(
                        identity_dir.glob("v*.json"),
                        key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0,
                    )
                    if not versions:
                        return None
                    target_file = versions[-1]
        else:
            target_file = identity_dir / f"{version}.json"

        if not target_file.exists():
            return None

        try:
            return json.loads(target_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_latest_snapshot(self, identity: str) -> dict[str, Any] | None:
        """Get the latest snapshot data for an identity."""
        return self.get_snapshot(identity, version=None)

    def list_snapshots(self, identity: str | None = None) -> list[dict[str, Any]]:
        """List all available snapshots, optionally filtered by identity."""
        results = []
        if identity:
            clean_identity = "".join(c for c in identity if c.isalnum() or c in ("-", "_", ".")).strip()
            identity_dirs = [self.vault_dir / clean_identity] if (self.vault_dir / clean_identity).exists() else []
        else:
            identity_dirs = [d for d in self.vault_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]

        for id_dir in identity_dirs:
            for s_file in sorted(id_dir.glob("v*.json"), key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0):
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
