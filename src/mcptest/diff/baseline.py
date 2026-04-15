"""Baseline trace storage — `.mcptest/baselines/<id>.json` on disk.

The baseline store is a small key-value layer over the filesystem. IDs are
derived from `(suite_name, case_name)` by slugifying into a single filename
so the directory listing is scan-friendly for humans.

We deliberately do not version the store format — each file is a
`Trace.to_json()` payload plus `from_dict` on load. If the schema changes,
`Trace.from_dict` should remain tolerant of missing fields.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcptest.runner.trace import Trace


_SLUG_RE = re.compile(r"[^a-zA-Z0-9_]+")


def baseline_id(suite: str, case: str) -> str:
    """Deterministic file-friendly id for a (suite, case) pair.

    Non-alphanumeric characters (other than `_`) are collapsed into single
    underscores so the suite/case double-underscore separator survives.
    """
    slug = _SLUG_RE.sub("_", f"{suite}__{case}").strip("_").lower()
    return slug or "case"


class BaselineStore:
    """A directory of snapshotted Trace files."""

    def __init__(self, root: str | Path = ".mcptest/baselines") -> None:
        self.root = Path(root)

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, suite: str, case: str) -> Path:
        return self.root / f"{baseline_id(suite, case)}.json"

    def save(self, suite: str, case: str, trace: Trace) -> Path:
        self.ensure()
        p = self.path_for(suite, case)
        trace.save(p)
        return p

    def load(self, suite: str, case: str) -> Trace | None:
        from mcptest.runner.trace import Trace as TraceCls

        p = self.path_for(suite, case)
        if not p.exists():
            return None
        return TraceCls.load(p)

    def exists(self, suite: str, case: str) -> bool:
        return self.path_for(suite, case).exists()

    def delete(self, suite: str, case: str) -> bool:
        p = self.path_for(suite, case)
        if p.exists():
            p.unlink()
            return True
        return False

    def list_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))

    def clear(self) -> None:
        if not self.root.exists():
            return
        for p in self.root.glob("*.json"):
            p.unlink()
