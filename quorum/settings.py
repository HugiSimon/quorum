"""The home: one folder that holds everything the user owns, and the global settings.

Everything is written to ~/.quorum (or to QUORUM_HOME) and stays hand-editable — the bots,
the rooms, their threads, and this file. The code lives elsewhere: an installed quorum must
never write next to its own package.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

DEFAULTS = {
    "theme": "follow terminal",
    "thinking": "folded",
    "density": "automatic",
    "ask_outside_folder": True,
    "keep_outputs": True,
    # How many bots may work at once. "all" is the room as it was: everyone answers together.
    "parallel": "all",
}

STARTER = Path(__file__).with_name("starter")


def home() -> Path:
    return Path(os.environ.get("QUORUM_HOME", Path.home() / ".quorum"))


def seed(path: Path) -> Path:
    """First run: the starter bots and rooms, copied once.

    Only when the home does not exist at all — a user who deletes a bot does not want it
    back at the next launch.
    """
    if not path.exists() and STARTER.exists():
        shutil.copytree(STARTER, path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read() -> dict:
    file = home() / "settings.json"
    if not file.exists():
        return dict(DEFAULTS)
    try:
        return {**DEFAULTS, **json.loads(file.read_text(encoding="utf-8"))}
    except ValueError:
        return dict(DEFAULTS)


def write(values: dict) -> Path:
    file = home() / "settings.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps({k: values.get(k, d) for k, d in DEFAULTS.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return file


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["QUORUM_HOME"] = tmp
        assert home() == Path(tmp)
        assert read() == DEFAULTS
        path = write({**DEFAULTS, "thinking": "unfolded", "unknown": 1})
        back = read()
        assert back["thinking"] == "unfolded" and "unknown" not in back, back
        path.write_text("not json", encoding="utf-8")
        assert read() == DEFAULTS, "a damaged file must not stop the app from starting"

    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "home"
        assert seed(fresh) == fresh and (fresh / "bots").is_dir(), "a new home gets the starters"
        names = {p.name for p in (fresh / "rooms").iterdir()}
        assert names == {"review", "pair"}, names
        shutil.rmtree(fresh / "bots" / "scribe")
        seed(fresh)
        assert not (fresh / "bots" / "scribe").exists(), "a deleted bot must not come back"
    del os.environ["QUORUM_HOME"]
    print("settings: home ok · seed ok · defaults ok · write ok · damaged file tolerated ok")
