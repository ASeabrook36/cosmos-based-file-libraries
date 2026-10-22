"""Ingestion worker entry point (spec §2, §7).

Phase 0 has no message handlers. The worker checks its configuration and exits non-zero, so
nothing can be deployed believing it consumes the ``ingest`` queue. Phase 1 builds §7.
"""

from __future__ import annotations

import sys

from fli.config import Settings


def main() -> None:
    Settings.from_env()
    print("fli-worker: no message handlers yet (Phase 1 builds spec §7)", file=sys.stderr)
    sys.exit(2)
