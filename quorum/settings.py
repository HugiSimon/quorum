"""Global settings: one JSON file, and values that all have an effect.

Everything is written to ~/.config/quorum/ (or to QUORUM_CONFIG), and stays hand-editable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "theme": "follow terminal",
    "thinking": "folded",
    "density": "automatic",
    "ask_outside_folder": True,
    "keep_outputs": True,
}


def folder() -> Path:
    return Path(os.environ.get("QUORUM_CONFIG", Path.home() / ".config" / "quorum"))


def read() -> dict:
    file = folder() / "settings.json"
    if not file.exists():
        return dict(DEFAULTS)
    try:
        return {**DEFAULTS, **json.loads(file.read_text(encoding="utf-8"))}
    except ValueError:
        return dict(DEFAULTS)


def write(values: dict) -> Path:
    file = folder() / "settings.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps({k: values.get(k, d) for k, d in DEFAULTS.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return file


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["QUORUM_CONFIG"] = tmp
        assert read() == DEFAULTS
        path = write({**DEFAULTS, "thinking": "unfolded", "unknown": 1})
        back = read()
        assert back["thinking"] == "unfolded" and "unknown" not in back, back
        path.write_text("not json", encoding="utf-8")
        assert read() == DEFAULTS, "a damaged file must not stop the app from starting"
    print("settings: defaults ok · write ok · damaged file tolerated ok")
