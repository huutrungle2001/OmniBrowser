#!/usr/bin/env python3
"""
lint_communication_records.py - Standardized Schema & Protocol Validator for OmniBrowser.
Validates that markdown communication records in .agents/communication/
strictly comply with the universal multi-agent protocol schema.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Canonical roles + optional project-prefixed roles
ROLE_PATTERN = re.compile(r"^([a-zA-Z0-9_-]+_)?(hub|orchestrator|implementer|reviewer|oracle)$")
RFC3339_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

VALID_STATUSES = {
    "TASK": {"TASK_READY", "BLOCKED"},
    "RESULT": {"READY_FOR_REVIEW", "BLOCKED"},
    "REVIEW": {"APPROVED", "REVISION_REQUIRED", "BLOCKED"},
    "CONSULTATION_REQUEST": {"REQUESTED", "BLOCKED"},
    "CONSULTATION_RESPONSE": {"ANSWERED", "BLOCKED"},
}

REQUIRED_SECTIONS = {
    "TASK": ["Objective", "Scope", "Acceptance Criteria", "Validation"],
    "RESULT": ["Summary", "Scope Modified", "Validation"],
    "REVIEW": ["Scope Audited", "Findings", "Independent Verification", "Decision Rationale"],
    "CONSULTATION_REQUEST": ["Background", "Questions"],
    "CONSULTATION_RESPONSE": ["Executive Summary", "Formal Analysis", "Operational"],
}


def find_repo_root(start_path: Path) -> Path:
    """Finds git repository root from given path."""
    current = start_path.resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists():
            return parent
    return start_path.parent


def verify_git_commit(commit_sha: str, repo_root: Path) -> bool:
    """Verifies that commit_sha exists in git history."""
    if not re.match(r"^[0-9a-fA-F]{7,40}$", commit_sha):
        return False
    try:
        res = subprocess.run(
            ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def parse_metadata_header(content: str):
    """Parses scalar metadata block immediately following the H1 title."""
    lines = content.strip().splitlines()
    if not lines:
        return {}, [], ["Empty file"]

    if not lines[0].startswith("# "):
        return {}, [], ["First line must be an H1 title (e.g. '# Task: <id>')"]

    metadata = {}
    errors = []

    idx = 1
    # Skip empty lines between title and header
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    header_pattern = re.compile(r"^([A-Z_]+):\s*(.*)$")

    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            # End of header block
            break
        if line.startswith("## ") or line.startswith("#"):
            break

        match = header_pattern.match(line)
        if not match:
            # Check for legacy bolding or improper formatting
            if re.match(r"^\*\*[A-Z_]+:\*\*", line) or re.match(r"^\*[A-Z_]+:\*", line):
                errors.append(f"Line {idx+1}: Markdown emphasis around metadata key is forbidden: '{line}'")
            else:
                errors.append(f"Line {idx+1}: Invalid metadata header format: '{line}' (expected 'KEY: Value')")
            idx += 1
            continue

        key, val = match.groups()
        metadata[key] = val.strip()
        idx += 1

    body = lines[idx:]
    return metadata, body, errors


def validate_record(record_path: Path, target_role: str = None, is_handoff: bool = False, check_git: bool = True):
    errors = []
    warnings = []

    if not record_path.exists():
        return [f"File not found: {record_path}"], []

    repo_root = find_repo_root(record_path)
    content = record_path.read_text(encoding="utf-8")
    metadata, body, parse_errors = parse_metadata_header(content)
    errors.extend(parse_errors)

    if parse_errors and not metadata:
        return errors, warnings

    # 1. RECORD_TYPE
    rec_type = metadata.get("RECORD_TYPE")
    if not rec_type:
        errors.append("Missing required header: 'RECORD_TYPE'")
    elif rec_type not in VALID_STATUSES:
        errors.append(f"Invalid RECORD_TYPE '{rec_type}'. Valid types: {sorted(VALID_STATUSES.keys())}")

    # 2. RECORD_ID
    rec_id = metadata.get("RECORD_ID")
    if not rec_id:
        errors.append("Missing required header: 'RECORD_ID'")
    else:
        # Check filename matches RECORD_ID exactly
        expected_stem = record_path.stem
        if rec_id != expected_stem:
            errors.append(f"RECORD_ID '{rec_id}' does not match filename stem '{expected_stem}'")

    # 3. TIMESTAMPS (RFC 3339 strict check)
    for ts_field in ["CREATED_AT", "UPDATED_AT"]:
        ts_val = metadata.get(ts_field)
        if not ts_val:
            errors.append(f"Missing required header: '{ts_field}'")
        else:
            if not RFC3339_PATTERN.match(ts_val):
                errors.append(f"Invalid RFC-3339 timestamp for {ts_field}: '{ts_val}'. Expected format: YYYY-MM-DDTHH:MM:SSZ")

    # 4. STATUS
    status = metadata.get("STATUS")
    if not status:
        errors.append("Missing required header: 'STATUS'")
    elif rec_type in VALID_STATUSES and status not in VALID_STATUSES[rec_type]:
        errors.append(f"Invalid STATUS '{status}' for RECORD_TYPE '{rec_type}'. Valid: {sorted(VALID_STATUSES[rec_type])}")

    # 5. ATTEMPT
    attempt = metadata.get("ATTEMPT")
    if not attempt:
        errors.append("Missing required header: 'ATTEMPT'")
    else:
        try:
            att_int = int(attempt)
            if att_int < 1:
                errors.append(f"ATTEMPT must be a positive integer >= 1, got '{attempt}'")
        except ValueError:
            errors.append(f"ATTEMPT must be an integer, got '{attempt}'")

    # 6. FROM & TO
    from_role = metadata.get("FROM")
    to_role = metadata.get("TO")
    if not from_role:
        errors.append("Missing required header: 'FROM'")
    elif not ROLE_PATTERN.match(from_role):
        errors.append(f"Invalid FROM role '{from_role}'. Expected role (hub, orchestrator, implementer, reviewer) or project-prefixed role.")

    if not to_role:
        errors.append("Missing required header: 'TO'")
    else:
        to_roles = [r.strip() for r in to_role.split(",") if r.strip()]
        for r in to_roles:
            if not ROLE_PATTERN.match(r):
                errors.append(f"Invalid TO role '{r}'. Expected role (hub, orchestrator, implementer, reviewer) or project-prefixed role.")

    # 7. Target role routing check (if handed off)
    if target_role and to_role:
        to_roles = [r.strip() for r in to_role.split(",") if r.strip()]
        clean_target = re.sub(r"^[a-zA-Z0-9_-]+_", "", target_role).replace("-", "_")
        clean_to_roles = [re.sub(r"^[a-zA-Z0-9_-]+_", "", r).replace("-", "_") for r in to_roles]
        if target_role not in to_roles and clean_target not in clean_to_roles:
            errors.append(f"Handoff target '{target_role}' not listed in record TO field: '{to_role}'")

    # 8. Record-specific commit verification
    if rec_type == "RESULT":
        base_commit = metadata.get("BASE_COMMIT")
        tip_commit = metadata.get("IMPLEMENTATION_TIP")
        if not base_commit:
            errors.append("RESULT record must include 'BASE_COMMIT' header")
        elif check_git and not verify_git_commit(base_commit, repo_root):
            errors.append(f"BASE_COMMIT '{base_commit}' is not a valid commit in Git history")

        if not tip_commit:
            errors.append("RESULT record must include 'IMPLEMENTATION_TIP' header")
        elif check_git and not verify_git_commit(tip_commit, repo_root):
            errors.append(f"IMPLEMENTATION_TIP '{tip_commit}' is not a valid commit in Git history")

        # State machine check: Task must exist and be TASK_READY
        task_stem = rec_id.replace("result-", "task-") if rec_id.startswith("result-") else rec_id
        task_path = record_path.parent.parent / "tasks" / f"{task_stem}.md"
        if not task_path.exists():
            # Also try direct id
            task_path = record_path.parent.parent / "tasks" / f"{rec_id}.md"
        if not task_path.exists():
            warnings.append(f"Referenced task file not found under tasks/ for result '{rec_id}'")
        else:
            task_meta, _, _ = parse_metadata_header(task_path.read_text(encoding="utf-8"))
            if task_meta.get("STATUS") != "TASK_READY":
                warnings.append(f"Task '{task_path.name}' status is '{task_meta.get('STATUS')}', expected 'TASK_READY'")

    elif rec_type == "REVIEW":
        base_commit = metadata.get("BASE_COMMIT")
        reviewed_commit = metadata.get("REVIEWED_COMMIT")
        if not base_commit:
            errors.append("REVIEW record must include 'BASE_COMMIT' header")
        elif check_git and not verify_git_commit(base_commit, repo_root):
            errors.append(f"BASE_COMMIT '{base_commit}' is not a valid commit in Git history")

        if not reviewed_commit:
            errors.append("REVIEW record must include 'REVIEWED_COMMIT' header")
        elif check_git and not verify_git_commit(reviewed_commit, repo_root):
            errors.append(f"REVIEWED_COMMIT '{reviewed_commit}' is not a valid commit in Git history")

    # 9. Required Sections Check (Fatal during handoff, Warning otherwise)
    if rec_type in REQUIRED_SECTIONS:
        body_text = "\n".join(body)
        for sec in REQUIRED_SECTIONS[rec_type]:
            if not re.search(rf"^##\s+.*{re.escape(sec)}", body_text, re.MULTILINE | re.IGNORECASE):
                msg = f"Required section '## {sec}' not found in record body"
                if is_handoff:
                    errors.append(msg)
                else:
                    warnings.append(msg)

    # 10. Git Status Check (if check_git)
    if check_git:
        try:
            # Check if the record itself has uncommitted changes
            res = subprocess.run(
                ["git", "status", "--porcelain", str(record_path)],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                errors.append(f"Record has uncommitted Git modifications: '{record_path}'. All records must be committed before handoff.")

            # On handoff of RESULT, verify that entire worktree is clean
            if is_handoff and rec_type == "RESULT":
                res_all = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res_all.returncode == 0 and res_all.stdout.strip():
                    dirty_lines = [
                        line for line in res_all.stdout.strip().splitlines()
                        if not line.strip().endswith(".tmp/") and not line.strip().startswith("?? .tmp/")
                    ]
                    if dirty_lines:
                        errors.append(
                            "Worktree has uncommitted modifications before RESULT handoff. "
                            "All implementation code and tests must be committed:\n  "
                            + "\n  ".join(dirty_lines[:5])
                        )
        except Exception as e:
            warnings.append(f"Git check skipped due to error: {e}")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description="Lint multi-agent communication records for schema compliance.")
    parser.add_argument("record", nargs="?", help="Path to markdown communication record")
    parser.add_argument("--handoff", help="Path to record being handed off")
    parser.add_argument("--target", help="Target agent role for handoff verification")
    parser.add_argument("--no-git", action="store_true", help="Skip git clean status check")

    args = parser.parse_args()

    is_handoff = bool(args.handoff)
    target_path = Path(args.handoff or args.record or "")
    if not str(target_path):
        parser.print_help()
        sys.exit(2)

    errors, warnings = validate_record(
        target_path,
        target_role=args.target,
        is_handoff=is_handoff,
        check_git=not args.no_git,
    )

    for w in warnings:
        print(f"[WARN] {w}", file=sys.stderr)

    if errors:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        print(f"FAILED: {len(errors)} error(s) found in {target_path}", file=sys.stderr)
        sys.exit(1)

    print(f"PASSED: {target_path} is valid.")
    sys.exit(0)


if __name__ == "__main__":
    main()
