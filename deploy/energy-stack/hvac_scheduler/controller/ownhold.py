"""Rev 4 own-hold record: what the controller last pushed, on disk, so a
restarted controller can clean up ONLY its own zombie holds. Spec: Safety #3.
The record's expiry_utc carries the DATE the device's dateless until-slot lacks.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

_FILENAME = "own_hold.json"


@dataclass(frozen=True)
class OwnHoldRecord:
    value: float
    until_minutes: int
    expiry_utc: str


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, _FILENAME)


def load_record(data_dir: str) -> OwnHoldRecord | None:
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    try:
        return OwnHoldRecord(value=float(doc["value"]),
                             until_minutes=int(doc["until_minutes"]),
                             expiry_utc=str(doc["expiry_utc"]))
    except (KeyError, TypeError, ValueError):
        return None


def save_record(data_dir: str, rec: OwnHoldRecord) -> None:
    with open(_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(asdict(rec), f)


def clear_record(data_dir: str) -> None:
    with open(_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(None, f)
