from __future__ import annotations

import logging

from fli.logs import FieldsFormatter


def test_fields_are_appended_as_json():
    record = logging.makeLogRecord({"msg": "library_search", "levelname": "INFO", "name": "fli.search"})
    record.requestCharge = 4.2
    line = FieldsFormatter("%(name)s %(message)s").format(record)
    assert line == 'fli.search library_search {"requestCharge": 4.2}'


def test_plain_records_stay_plain():
    record = logging.makeLogRecord({"msg": "started", "name": "fli"})
    assert FieldsFormatter("%(message)s").format(record) == "started"
