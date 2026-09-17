"""Builds the set the recordings are shot on: a fake home, five rooms, five bots.

Everything lives under `demo/root`, which the tapes hand to the app as `HOME`. Two things
follow from that: the app writes nowhere near a real installation, and every path it prints
collapses to `~/dev/…`, so no account name ever reaches a capture.

Run it again whenever a recording drifts — it wipes and rebuilds, and nothing here is
precious.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE / "root"
QUORUM = ROOT / ".quorum"
STARTER = HERE.parent / "quorum" / "starter"

MINUTE, HOUR, DAY = 60, 3600, 86400

# A fifth bot, on another vendor. It is never spoken to: it exists so the home sheet shows
# two different providers in the same list, which is the one claim no sentence makes better.
VERA = {
    "bot.toml": '''name = "vera"
role = "security"
hue = 195

command = "npx"
args = ["@zed-industries/claude-code-acp"]
model = ""
provider = "claude-code"
''',
    "system.md": "You read code for what an attacker would do with it. You never write.\n",
    "policy.toml": '''[[rule]]
toolName = ["read_file", "list_directory", "glob", "search_file_content"]
decision = "allow"
priority = 100

[[rule]]
decision = "ask_user"
priority = 1
''',
    "settings.json": json.dumps({"mcp": {"allowed": []}}, indent=2) + "\n",
}

# name, members, folder under the fake home, how long ago it was last touched, the thread.
ROOMS = [
    ("pair", ["forge", "scout"], "dev/atlas", 3 * MINUTE, "pair"),
    ("review", ["scout", "critic"], "dev/atlas", 22 * HOUR, "review"),
    ("parser", ["forge", "critic"], "dev/siphon", 3 * DAY, "parser"),
    ("release", ["scribe", "forge"], "dev/quorum", 8 * DAY, "release"),
    ("triage", ["scout", "critic", "scribe"], None, None, None),
]

THREADS = {
    "pair": [
        ("user", "you", "@scout is the token cache shared between workers?"),
        ("bot", "scout", "No — each worker builds its own `TokenCache` in `wsgi.py`. "
                         "Four workers, four caches, and none of them invalidates the others."),
        ("user", "you", "@forge can you reproduce it?"),
        ("bot", "forge", "Reproduced. Two workers, revoke on one, the other still accepts "
                         "the token for the rest of its TTL.", ["shell · pytest -q tests/auth"]),
        ("user", "you", "what would it take to share it?"),
        ("bot", "forge", "Redis, or a signed revocation list the workers re-read. "
                         "The second one adds no dependency.", ["fs · reads src/auth/cache.py"]),
        ("bot", "scout", "@forge the revocation list still needs a clock both sides agree on."),
        ("bot", "forge", "Agreed. Skew tolerance of one minute, and the list carries its own "
                         "issued-at."),
        ("user", "you", "park it, I want to look at the expiry path first"),
        ("bot", "scout", "Parked. The expiry path is `verify()` in `legacy/session.py`."),
    ],
    "review": [
        ("user", "you", "@scout @critic read the diff on the refresh endpoint"),
        ("bot", "scout", "Three files. The endpoint moved from `auth/` to `auth/v1/`, and the "
                         "old route is kept as an alias.", ["fs · reads src/auth/v1/refresh.py"]),
        ("bot", "critic", "The alias has no test, and it is the one production still calls. "
                          "P1: you cannot delete it safely and you cannot keep it blindly."),
        ("bot", "scout", "@critic the alias is covered — indirectly, through "
                         "`test_compat.py::test_legacy_routes`."),
        ("bot", "critic", "Then it is covered for routing, not for expiry. Downgrading to P2, "
                          "and the gap stands."),
    ],
    "parser": [
        ("user", "you", "@forge the streaming parser drops the last frame on an odd payload"),
        ("bot", "forge", "Found it: `read_exact` returns short at EOF and the loop treats "
                         "short as done.", ["fs · reads src/siphon/stream.py"]),
        ("bot", "critic", "@forge that same loop is copied in `udp.py`. Fix one, you have "
                          "fixed half the bug."),
    ],
    "release": [
        ("user", "you", "@scribe draft the notes for 0.2, @forge check the tag"),
        ("bot", "scribe", "Draft written to `CHANGELOG.md`: four entries, the permission table "
                          "first since it is the one thing people will notice."),
        ("bot", "forge", "Tag is clean, working tree is clean, and the wheel builds.",
         ["shell · git status --porcelain"]),
    ],
}

# A thread is padded to this many messages, so the home sheet shows a project that has been
# lived in rather than a demo that was set up ten minutes ago.
LENGTHS = {"pair": 12, "review": 132, "parser": 61, "release": 12}

FILLER = [
    ("user", "you", "go on"),
    ("bot", "scout", "Same conclusion on the second pass — nothing new to add."),
    ("bot", "critic", "Noted, no objection."),
]

# The work folders. Real files, because the reasoning screen shows real paths and the eye
# catches a fake one immediately.
FILES = {
    "dev/atlas/src/auth/jwt.py": '''"""Token validation. The only entry point is verify()."""

import time

from .keys import KEY, decode


def verify(token: str) -> dict:
    claims = decode(token, KEY)
    if claims["exp"] < time.time():
        raise Expired(token)
    return claims
''',
    "dev/atlas/src/auth/legacy/session.py": '''"""Kept for /v1/refresh, which has not been migrated."""

from ..keys import KEY, decode


def verify(token: str) -> dict:
    claims = decode(token, KEY)
    # no expiry check
    return claims
''',
    "dev/atlas/src/auth/cache.py": "class TokenCache:\n    def __init__(self):\n        self.seen = {}\n",
    "dev/atlas/tests/auth/test_jwt.py": "def test_expired_is_refused():\n    assert True\n",
    "dev/atlas/README.md": "# atlas\n\nAuthentication service.\n",
    "dev/siphon/src/siphon/stream.py": "def read_exact(sock, n):\n    return sock.recv(n)\n",
    "dev/siphon/README.md": "# siphon\n\nStreaming parser.\n",
    "dev/quorum/CHANGELOG.md": "# Changelog\n\n## 0.2 — unreleased\n",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def thread(name: str, started: float) -> str:
    """The room's transcript, padded to its advertised length and ending on its real lines.

    The filler sits at the front: what the home sheet counts is the whole file, what the
    room shows on opening is the tail.
    """
    lines = THREADS[name]
    padding = max(0, LENGTHS[name] - len(lines))
    entries = [FILLER[i % len(FILLER)] for i in range(padding)] + list(lines)
    step = 90.0
    out = []
    for index, (kind, author, text, *rest) in enumerate(entries):
        out.append(json.dumps({
            "ts": started - (len(entries) - index) * step,
            "kind": kind, "author": author, "text": text,
            "tools": rest[0] if rest else [],
        }, ensure_ascii=False))
    return "\n".join(out) + "\n"


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    now = time.time()

    shutil.copytree(STARTER, QUORUM)
    for filename, text in VERA.items():
        write(QUORUM / "bots" / "vera" / filename, text)

    shutil.rmtree(QUORUM / "rooms")
    for name, members, folder, age, key in ROOMS:
        room = QUORUM / "rooms" / name
        target = f'"{ROOT / folder}"' if folder else '"."'
        write(room / "room.toml", "\n".join([
            f'name = "{name}"',
            "members = [" + ", ".join(f'"{m}"' for m in members) + "]",
            f"folder = {target}",
            "max_rounds = 3",
            "stop_on_repeat = true",
            "",
        ]))
        if key:
            path = room / "transcript.jsonl"
            write(path, thread(key, now - age))
            # The home sheet reads "2 minutes ago" off the file itself.
            os.utime(path, (now - age, now - age))

    for relative, text in FILES.items():
        write(ROOT / relative, text)

    write(QUORUM / "settings.json", json.dumps({"theme": "dark"}, indent=2) + "\n")

    # The bots on camera run the scripted agent instead of a real one. `provider` stays
    # "gemini": that is what makes the app follow a telemetry log, and the late arrival of a
    # command's output is half of what the recording has to show.
    python = HERE.parent / ".venv" / "bin" / "python"
    scenario = HERE / "scenarios" / "pair.json"
    for name in ("scout", "forge", "critic", "scribe"):
        path = QUORUM / "bots" / name / "bot.toml"
        text = path.read_text(encoding="utf-8")
        text = text.replace('command = "gemini"', f'command = "{python}"')
        text = text.replace('args = ["--acp"]', f'args = ["{HERE / "agent.py"}", "{name}", "{scenario}"]')
        path.write_text(text, encoding="utf-8")

    rooms = len(ROOMS)
    bots = len(list((QUORUM / "bots").iterdir()))
    print(f"set built: {rooms} rooms, {bots} bots, home at {ROOT}")


if __name__ == "__main__":
    main()
