"""Shared location for persistent state files.

Set STATE_DIR to a persistent mount (e.g. /data on a Railway volume) so
routing/settings survive deploys. Defaults to the code directory, which
works locally but is wiped on every Railway deploy.
"""

import os

STATE_DIR = os.getenv("STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(STATE_DIR, exist_ok=True)


def state_file(name: str) -> str:
    return os.path.join(STATE_DIR, name)
