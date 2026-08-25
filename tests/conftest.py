"""Isolate tests from the developer's real .env database."""

import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="levels_tester_")
os.environ["DATABASE_URL"] = "sqlite:///" + _TEST_DIR.replace("\\", "/") + "/test.db"
