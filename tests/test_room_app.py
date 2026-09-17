"""The room end to end: two bots in parallel, two permissions, a thread that survives."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.widgets import Input  # noqa: E402

from quorum import bot as bots  # noqa: E402
from quorum.app import Bubble, Quorum  # noqa: E402
from quorum.room import Transcript, load_room  # noqa: E402

from workshop import wait_for, build_project, input_ready  # noqa: E402

async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root)
        room = load_room(root, "trial")
        known = bots.load_all(root / "bots")

        app = Quorum(room, known)
        app.transcript = Transcript(room.root / "transcript.jsonl")
        async with app.run_test() as pilot:
            one, two = app.participants["fake1"], app.participants["fake2"]
            await wait_for(pilot, lambda: one.ready and two.ready, "both sessions open")
            await input_ready(pilot, app)

            # Typing @ offers the room, then the project's files — and ⇥ writes the name.
            app.paths = ["src/auth.py"]
            await pilot.press("at", "f")
            await wait_for(pilot, lambda: app.picker.rows, "the @ list")
            assert app.picker.rows[0] == ("fake1", "bot"), app.picker.rows
            assert app.picker.display and [k for _, k in app.picker.rows].count("bot") == 2
            await pilot.press("down")
            assert app.picker.cursor == 1, "the arrows walk the list, they do not move the cursor"
            await pilot.press("up")
            await pilot.press("tab")
            assert app.query_one(Input).value == "@fake1 ", app.query_one(Input).value
            assert not app.picker.rows and not app.picker.display, "inserting closes the list"
            # A file keeps its @: recipients() only matches members, so it wakes nobody.
            await pilot.press("at", "a", "u")
            await wait_for(pilot, lambda: app.picker.rows, "the file list")
            assert app.picker.rows == [("src/auth.py", "file")], app.picker.rows
            app.query_one(Input).value = ""
            app.picker.close()

            # An explicit mention only wakes one bot.
            app.query_one(Input).value = "@fake1 PERM delete .venv"
            await pilot.press("enter")
            await wait_for(pilot, lambda: one.panel is not None, "fake1's permission")
            assert two.turn is None, "an explicit mention must wake nobody else"
            assert [o["kind"] for o in one.panel.options] == \
                ["allow_once", "allow_always", "reject_once"], \
                "key 1 must never land on the widest permission"
            assert one.panel.options[0]["name"] == "Allow", "the agent's label, as it is"
            # A tool line is cut to one row; a permission request never is — you cannot
            # allow a command you have not read whole.
            asked = one.panel.render().plain
            assert "--refresh --all-extras" in asked, f"the whole command, not the title: {asked}"
            assert "…" not in asked, f"nothing clipped in a decision: {asked}"
            assert one.bubble.tools and one.bubble.tools[0]["title"].startswith("rm -rf")
            assert one.bubble.tools[0]["family"] == "shell", one.bubble.tools[0]
            one.panel.choose(one.panel.options[0])
            await wait_for(pilot, lambda: one.turn.done(), "fake1's turn ends")

            # Without a mention, everyone answers — and in parallel.
            app.query_one(Input).value = "PERM and what do you think?"
            await pilot.press("enter")
            await wait_for(
                pilot,
                lambda: one.panel is not None and two.panel is not None,
                "two permissions pending at the same time",
            )
            assert one.bubble is not two.bubble, "two streams, two blocks"

            # One request on screen at a time: the second waits its turn instead of
            # stacking under the first, where it could only be reached with ⇥.
            mounted = [p.name for p in app.participants.values() if p.panel.is_mounted]
            assert len(mounted) == 1, f"one panel on screen, not {mounted}"
            live = next(p for p in (one, two) if p.panel.is_mounted)
            queued = next(p for p in (one, two) if not p.panel.is_mounted)
            assert live.panel.has_focus, "the request on screen holds the focus"
            assert live.panel.queued == 1, live.panel.queued
            assert "1 more waiting" in live.panel.render().plain

            answered = live.panel          # the participant drops it once it is decided
            answered.choose(answered.options[0])
            await wait_for(pilot, lambda: queued.panel is not None and queued.panel.is_mounted,
                           "the next request takes its place")
            assert queued.panel.queued == 0 and queued.panel.has_focus
            decided = answered.render().plain
            assert decided.count("\n") == 1, f"an answered request is one line: {decided!r}"

            queued.panel.choose(queued.panel.options[0])
            await wait_for(pilot, lambda: one.turn.done() and two.turn.done(), "both turns")

            assert "fake1" in one.bubble.body and "fake2" not in one.bubble.body, one.bubble.body
            assert "fake2" in two.bubble.body and "fake1" not in two.bubble.body, two.bubble.body

        # The thread survived the shutdown, and reads back without any agent.
        back = Transcript(room.root / "transcript.jsonl")
        kinds = [(e.kind, e.author) for e in back.entries]
        assert kinds == [
            ("user", "you"), ("bot", "fake1"),
            ("user", "you"), ("bot", "fake1"), ("bot", "fake2"),
        ], kinds

        cold = Quorum(load_room(root, "trial"), known)
        async with cold.run_test() as pilot:
            await wait_for(pilot, lambda: all(p.ready for p in cold.participants.values()),
                           "the resumed sessions")
            await input_ready(pilot, cold)
            bubbles = cold.query_one("#thread").query(Bubble)
            assert len(bubbles) >= 5, f"{len(bubbles)} bubbles restored"
            # The seen index survives the restart: a bot is not made to re-read what it knows.
            # It differs from one bot to the other, and rightly so — fake1 finished its turn
            # before fake2 wrote, so it has not seen its message yet.
            for participant in cold.participants.values():
                assert participant.startup == "resumed", participant.startup
                assert participant.seen == cold.room_state["seen"][participant.name] > 0, (
                    participant.name, participant.seen, cold.room_state["seen"]
                )
                assert not participant.memory_lost
            assert not cold.query(".expiry"), "nothing expired: no S10 panel"
    print("test_room_app: @ list ok · mention ok · parallel ok · 2 permissions ok · whole command ok · persistent thread ok")


if __name__ == "__main__":
    asyncio.run(main())
