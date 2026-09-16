"""S12, the settings, and the guard that stops an agent writing anywhere it likes."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from workshop import wait_for, build_project, input_ready  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum import settings as config  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.screens import SettingsScreen, RoomScreen, MemberList, Step  # noqa: E402
from quorum.room import load_room  # noqa: E402


async def room_scenario() -> None:
    """Set up a room: one member removed, a budget changed, a readable room.toml."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=3)
        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test(size=(110, 45)) as pilot:
            await wait_for(pilot, lambda: all(p.ready for p in app.participants.values()), "sessions")
            await input_ready(pilot, app)
            app.action_compose()
            await pilot.pause(0.2)
            screen = app.screen
            assert isinstance(screen, RoomScreen), screen

            members = screen.query_one(MemberList)
            assert members.chosen == ["fake1", "fake2"], members.chosen

            # The aimed line lights up as soon as it takes focus, without moving first.
            members.focus()
            await pilot.pause(0.1)
            assert "▌" in members.plain_text, members.plain_text
            assert "⏎ add or remove" in members.plain_text, "the key must be written out"

            await pilot.press("down")
            await pilot.press("enter")
            assert members.chosen == ["fake1"], members.chosen
            await pilot.press("enter")
            assert members.chosen == ["fake1", "fake2"], members.chosen
            await pilot.press("enter")

            rounds = next(s for s in screen.query(Step) if s.label == "rounds")
            rounds.focus()
            await pilot.press("left")   # 3 → 2
            assert rounds.value == "2", rounds.value

            await pilot.press("ctrl+s")
            await wait_for(pilot, lambda: not isinstance(app.screen, RoomScreen), "the close")

        back = load_room(root, "trial")
        assert back.members == ["fake1"], back.members
        assert back.max_rounds == 2 and back.stop_on_repeat, back


async def settings_scenario() -> None:
    """The settings are written, and what shows applies right away."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ["QUORUM_CONFIG"] = str(root / "config")
        build_project(root, max_rounds=0)
        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test(size=(110, 45)) as pilot:
            await wait_for(pilot, lambda: all(p.ready for p in app.participants.values()), "sessions")
            await input_ready(pilot, app)
            assert app.settings["thinking"] == "folded"
            app.action_settings()
            await pilot.pause(0.2)
            screen = app.screen
            assert isinstance(screen, SettingsScreen), screen

            thinking = next(s for s in screen.query(Step) if s.label == "thinking")
            thinking.focus()
            await pilot.press("right")
            assert thinking.value == "last line"
            outside = next(s for s in screen.query(Step) if s.label == "outside folder")
            outside.focus()
            await pilot.press("right")
            assert outside.value == "let it go"

            await pilot.press("ctrl+s")
            await wait_for(pilot, lambda: not isinstance(app.screen, SettingsScreen), "the close")
            assert app.settings["thinking"] == "last line", app.settings
            assert app.settings["ask_outside_folder"] is False

        written = json.loads((root / "config" / "settings.json").read_text())
        assert written["thinking"] == "last line" and not written["ask_outside_folder"]
        del os.environ["QUORUM_CONFIG"]


async def guard_scenario() -> None:
    """A write outside the room folder does not get through without the user's decision."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ["QUORUM_CONFIG"] = str(root / "config")
        build_project(root, max_rounds=0)
        outside = Path(tempfile.mkdtemp()) / "stolen.txt"
        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test() as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)
            assert app.settings["ask_outside_folder"] is True

            app.query_one("#message", Input).value = f"@fake1 WRITE:{outside}"
            await pilot.press("enter")
            await wait_for(pilot, lambda: one.panel is not None, "the write permission")
            assert "outside the room folder" in one.panel.call["title"], one.panel.call
            refusal = next(o for o in one.panel.options if o["kind"] == "reject_once")
            one.panel.choose(refusal)
            await wait_for(pilot, lambda: one.turn.done(), "the end of the turn")
            assert not outside.exists(), "the file must not have been written"
            assert "refused" in one.bubble.body, one.bubble.body

            # A write inside the room folder goes through without asking anything.
            inside = app.room.folder / "inside.txt"
            app.query_one("#message", Input).value = f"@fake1 WRITE:{inside}"
            await pilot.press("enter")
            await wait_for(pilot, lambda: one.turn.done() and inside.exists(), "the allowed write")
            assert inside.read_text().startswith("written by the agent")
        del os.environ["QUORUM_CONFIG"]


async def main() -> None:
    await room_scenario()
    await settings_scenario()
    await guard_scenario()
    print("test_room_screen: line-up ok · settings ok · write guard ok")


if __name__ == "__main__":
    asyncio.run(main())
