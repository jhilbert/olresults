#!/usr/bin/env python3
"""Local dev server; chdir first so it works regardless of launch cwd."""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import runpy  # noqa: E402

sys.argv = ["http.server", "8643", "--bind", "127.0.0.1"]
runpy.run_module("http.server", run_name="__main__")
