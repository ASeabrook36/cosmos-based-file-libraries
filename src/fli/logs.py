"""Log lines with their structured fields (``extra=``) appended as JSON.

Callers put only metrics and ids in ``extra``; query and chunk text never go to a log
(CLAUDE.md rule 12).
"""

from __future__ import annotations

import json
import logging

_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class FieldsFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = {k: v for k, v in vars(record).items() if k not in _STANDARD}
        return f"{line} {json.dumps(fields, default=str, sort_keys=True)}" if fields else line


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(FieldsFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("azure", "httpx", "uamqp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
