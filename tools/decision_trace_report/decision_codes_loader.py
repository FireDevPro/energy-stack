"""Reflect the reason_code enums from the hvac-scheduler's decision_codes
module. Imports the actual module so adding a new code there is picked
up automatically by §5 coverage."""
import importlib
import sys
from pathlib import Path


def load_reference_codes() -> dict[str, list[str]]:
    """Return `{enum_class_name: [code_value, ...]}` for every str-Enum
    declared in `deploy/energy-stack/hvac-scheduler/decision_codes.py`.

    The path is computed relative to this file so the tool works from
    any cwd as long as the repo root is on disk.
    """
    repo_root = Path(__file__).resolve().parents[2]
    scheduler_dir = repo_root / "deploy" / "energy-stack" / "hvac-scheduler"
    if str(scheduler_dir) not in sys.path:
        sys.path.insert(0, str(scheduler_dir))
    module = importlib.import_module("decision_codes")
    importlib.reload(module)  # in case tests run multiple times

    from enum import Enum
    out: dict[str, list[str]] = {}
    for name in dir(module):
        cls = getattr(module, name)
        if isinstance(cls, type) and issubclass(cls, Enum) and cls is not Enum:
            out[name] = [member.value for member in cls]
    return out
