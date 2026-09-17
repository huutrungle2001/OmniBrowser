"""
Pytest configuration and safety fixtures for OmniBrowser test suite.
Enforces the critical safety invariant: Zero Live Profile Pollution.
"""

import os
import shutil
import tempfile
import pytest


@pytest.fixture
def ephemeral_user_data_dir():
    """Provides an isolated ephemeral user data directory for Chrome."""
    temp_dir = tempfile.mkdtemp(prefix="omnibrowser_test_profile_")
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def guard_live_profile():
    """Autouse fixture ensuring no test inadvertently targets the user live profile port."""
    live_profile_path = os.path.expanduser("~/.chrome-ai-profile")
    # Verify environment does not force live port 17082
    if os.environ.get("CDP_PORT") == "17082":
        pytest.fail(
            "SAFETY INVARIANT VIOLATION: CDP_PORT is set to 17082. "
            "Automated tests must never connect to live user Chrome profile."
        )
    yield
