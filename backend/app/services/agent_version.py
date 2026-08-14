"""Bundled remna-node-agent version shipped with the panel."""

from __future__ import annotations

import re
from functools import lru_cache

from app.services.agent_install import agent_files_dir

_VERSION_RE = re.compile(r'^VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


@lru_cache(maxsize=1)
def get_bundled_agent_version() -> str:
    try:
        path = agent_files_dir() / "remna_node_agent.py"
    except Exception:  # noqa: BLE001
        return "0.0.0"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    match = _VERSION_RE.search(text)
    return match.group(1) if match else "0.0.0"
