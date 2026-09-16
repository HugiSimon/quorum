"""Shared tools for the checks: a throwaway project and a bounded wait."""

import sys
from pathlib import Path

FAKE = Path(__file__).with_name("fake_agent.py")


def build_project(
    root: Path, max_rounds: int = 3, relay: bool = False, copy: bool = False
) -> None:
    """Two bots served by the fake agent, in a throwaway room.

    `relay` gives each one the other's name: that is what lets it call the other out.
    """
    neighbours = {"fake1": "fake2", "fake2": "fake1"}
    for name, neighbour in neighbours.items():
        folder = root / "bots" / name
        folder.mkdir(parents=True)
        args = f'"{FAKE}", "{name}"' + (f', "{neighbour}"' if relay else "")
        (folder / "bot.toml").write_text(
            f'name = "{name}"\nrole = "trial"\nhue = {150 if name == "fake1" else 250}\n'
            f'command = "{sys.executable}"\nargs = [{args}]\n'
            f'provider = "gemini"\n'
            + ('workdir = "copy"\n' if copy and name == "fake1" else ""),
            encoding="utf-8",
        )
    room = root / "rooms" / "trial"
    room.mkdir(parents=True)
    (room / "room.toml").write_text(
        f'name = "trial"\nfolder = "."\nmembers = ["fake1", "fake2"]\n'
        f"max_rounds = {max_rounds}\n",
        encoding="utf-8",
    )


async def input_ready(pilot, app, what: str = "the input") -> None:
    """A "ready" bot is ready before the screen accepts keys: we wait for the focus."""
    await wait_for(pilot, lambda: app.query("#message")
                   and app.query_one("#message").has_focus, what)


async def wait_for(pilot, condition, what: str, rounds: int = 600) -> None:
    for _ in range(rounds):
        if condition():
            return
        await pilot.pause(0.02)
    raise AssertionError(f"never happened: {what}")
