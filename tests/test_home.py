"""S1 and S2: the empty state that explains, and the list that opens a room."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workshop import build_project  # noqa: E402
from quorum.screens import Home, HomeScreen, BotCard  # noqa: E402
from quorum.room import Transcript  # noqa: E402


async def empty_scenario() -> None:
    """S2: with nothing, home explains and offers a single gesture."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ["QUORUM_HOME"] = str(root / "config")
        app = Home(root)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause(0.2)
            home = app.screen
            assert isinstance(home, HomeScreen), home
            assert home.rooms == [] and home.bots == {}
            text = home.query_one("#body").render().plain
            assert "create my first bot" in text, text
            assert "A room gathers your bots" in text

            await pilot.press("b")
            await pilot.pause(0.2)
            assert isinstance(app.screen, BotCard), app.screen
            await pilot.press("escape")
            await pilot.pause(0.2)
        del os.environ["QUORUM_HOME"]


async def listing_scenario() -> None:
    """S1: two rooms, the most recent on top, ⏎ returns its name."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ["QUORUM_HOME"] = str(root / "config")
        build_project(root, max_rounds=0)
        (root / "rooms" / "older").mkdir(parents=True)
        (root / "rooms" / "older" / "room.toml").write_text(
            'name = "older"\nfolder = "."\nmembers = ["fake1"]\n', encoding="utf-8"
        )
        thread = Transcript(root / "rooms" / "trial" / "transcript.jsonl")
        thread.add("user", "you", "hello")
        thread.add("bot", "fake1", "hi")

        app = Home(root)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.2)
            home = app.screen
            names = [r["name"] for r in home.rooms]
            assert names == ["trial", "older"], names  # the most recently written on top
            assert home.rooms[0]["messages"] == 2, home.rooms[0]
            assert home.rooms[1]["when"] == "never opened", home.rooms[1]

            text = home.query_one("#body").render().plain
            assert "@fake1" in text and "@fake2" in text, text
            assert "ROOMS" in text and "BOTS" in text

            await pilot.press("down")
            assert home.cursor["rooms"] == 1

            # ⇥ moves to the bots, and ⏎ opens the card there — not a room.
            await pilot.press("tab")
            assert home.section == "bots"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, BotCard), app.screen
            assert app.screen.bot.name == "fake1", app.screen.bot.name
            await pilot.press("escape")
            await pilot.pause(0.2)

            await pilot.press("tab")
            assert home.section == "rooms"
            await pilot.press("enter")
            await pilot.pause(0.1)
        assert app.return_value == "older", app.return_value
        del os.environ["QUORUM_HOME"]


async def main() -> None:
    await empty_scenario()
    await listing_scenario()
    print("test_home: first run ok · listing ok · opening ok")


if __name__ == "__main__":
    asyncio.run(main())
