"""Bounded rounds, the commented refusal and the cut on repetition."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from workshop import wait_for, build_project, input_ready  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.room import Transcript, load_room  # noqa: E402


def thread_lines(app) -> str:
    return "\n".join(getattr(block, "plain_text", "") for block in app.query("#thread > Static"))


async def open_room(root: Path, max_rounds: int, relay: bool):
    build_project(root, max_rounds=max_rounds, relay=relay)
    room = load_room(root, "trial")
    return Quorum(room, bots.load_all(root / "bots")), room


async def budget_scenario() -> None:
    """One bot calls another out: next round, then the budget hands back control."""
    with tempfile.TemporaryDirectory() as tmp:
        app, room = await open_room(Path(tmp), max_rounds=1, relay=True)
        async with app.run_test() as pilot:
            one, two = app.participants["fake1"], app.participants["fake2"]
            await wait_for(pilot, lambda: one.ready and two.ready, "the sessions")
            await input_ready(pilot, app)

            app.query_one("#message", Input).value = "@fake1 RELAY the debate"
            await pilot.press("enter")
            await wait_for(pilot, lambda: app.conversation.done(), "the conversation stops", 1500)

            # Nothing was scrolled: the thread followed by itself, and says nothing about it.
            assert app.unread == 0, app.unread
            assert not app.query_one("#backlog").display, "no backlog banner when following"

            thread = thread_lines(app)
            assert "round 2 · @fake2" in thread, thread
            assert "chaining budget spent (1)" in thread, thread
            authors = [e.author for e in Transcript(room.root / "transcript.jsonl").entries]
            assert authors == ["you", "fake1", "fake2"], authors


async def repetition_scenario() -> None:
    """A bot that repeats itself cuts the round, even if the budget is still open."""
    with tempfile.TemporaryDirectory() as tmp:
        app, _ = await open_room(Path(tmp), max_rounds=6, relay=True)
        async with app.run_test() as pilot:
            one, two = app.participants["fake1"], app.participants["fake2"]
            await wait_for(pilot, lambda: one.ready and two.ready, "the sessions")
            await input_ready(pilot, app)

            app.query_one("#message", Input).value = "@fake1 RELAY the debate"
            await pilot.press("enter")
            await wait_for(pilot, lambda: app.conversation.done(), "the conversation stops", 2000)

            thread = thread_lines(app)
            assert "repeats itself — round cut" in thread, thread
            assert "round 3" in thread and "round 6" not in thread, thread
            assert one.repeats, one.fingerprints


async def commented_refusal_scenario() -> None:
    """A refusal does not stop the turn: it becomes an instruction, and the thread keeps it."""
    with tempfile.TemporaryDirectory() as tmp:
        app, room = await open_room(Path(tmp), max_rounds=0, relay=False)
        async with app.run_test() as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)

            app.query_one("#message", Input).value = "@fake1 PERM delete .venv"
            await pilot.press("enter")
            await wait_for(pilot, lambda: one.panel is not None, "the permission")
            first = one.bubble

            one.panel.focus()
            await pilot.press("r")
            await wait_for(pilot, lambda: bool(app.query("#refusal")), "the comment box")
            app.query_one("#refusal", Input).value = "do not delete .venv, it is built by hand"
            await pilot.press("enter")

            await wait_for(pilot, lambda: one.bubble is not first, "the bot's bounce", 1500)
            await wait_for(pilot, lambda: one.turn.done(), "the end of the turn", 1500)

            assert "suggest" in one.bubble.body, one.bubble.body
            assert "« do not delete .venv" in thread_lines(app)

            entries = Transcript(room.root / "transcript.jsonl").entries
            kinds = [(e.kind, e.author) for e in entries]
            assert ("refusal", "you") in kinds, kinds
            assert kinds[-1] == ("bot", "fake1"), kinds
            # The comment must be readable by the other bots, so it belongs in the thread.
            assert any("built by hand" in e.text for e in entries if e.kind == "refusal")


async def interruption_scenario() -> None:
    """^C closes the blocks, keeps what was said, and cuts the chaining."""
    with tempfile.TemporaryDirectory() as tmp:
        app, room = await open_room(Path(tmp), max_rounds=5, relay=True)
        async with app.run_test() as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)

            app.query_one("#message", Input).value = "@fake1 PERM delete .venv"
            await pilot.press("enter")
            await wait_for(pilot, lambda: one.panel is not None, "the in-flight permission")

            app.action_interrupt()
            await wait_for(pilot, lambda: one.turn.done(), "the end of the interrupted turn", 1500)

            # The block closes: it does not animate forever.
            assert one.state == "out" and one.bubble.state == "out", one.state
            assert one.panel is None or one.panel.decision == "cancelled"
            assert app.interrupted is True
            await wait_for(pilot, lambda: app.conversation.done(), "the chaining cut", 1500)

            kinds = [(e.kind, e.author) for e in Transcript(room.root / "transcript.jsonl").entries]
            assert ("system", "you") in kinds, kinds
            # And the next turn starts again normally.
            previous = app.conversation
            app.query_one("#message", Input).value = "@fake1 and now?"
            await pilot.press("enter")
            await wait_for(pilot,
                           lambda: app.conversation is not previous and app.conversation.done(),
                           "the following turn", 1500)
            assert one.bubble.body.strip(), one.bubble.body


async def parallel_scenario() -> None:
    """The settings cap how many bots work at once; the round still finishes as a whole."""
    with tempfile.TemporaryDirectory() as tmp:
        app, room = await open_room(Path(tmp), max_rounds=0, relay=False)
        app.settings = {**app.settings, "parallel": "1"}
        async with app.run_test() as pilot:
            one, two = app.participants["fake1"], app.participants["fake2"]
            await wait_for(pilot, lambda: one.ready and two.ready, "the sessions")
            await input_ready(pilot, app)
            assert app.at_once == 1, app.at_once

            # No mention: both are targeted, but only one may hold a slot at a time.
            app.query_one("#message", Input).value = "PERM and what do you think?"
            await pilot.press("enter")
            await wait_for(pilot, lambda: any(p.panel is not None for p in (one, two)),
                           "the first bot asks")
            live = next(p for p in (one, two) if p.panel is not None)
            queued = next(p for p in (one, two) if p is not live)
            assert queued.bubble is None, "a bot still queued has no block of its own yet"
            assert "waits for a slot" in thread_lines(app), thread_lines(app)

            live.panel.choose(live.panel.options[0])
            await wait_for(pilot, lambda: queued.panel is not None, "the slot passes on")
            assert queued.bubble is not None, "its block opens when its slot does"
            queued.panel.choose(queued.panel.options[0])
            await wait_for(pilot, lambda: app.conversation.done(), "the round ends", 1500)

            authors = [e.author for e in Transcript(room.root / "transcript.jsonl").entries]
            assert authors.count("fake1") == 1 and authors.count("fake2") == 1, authors


async def main() -> None:
    await budget_scenario()
    await repetition_scenario()
    await commented_refusal_scenario()
    await interruption_scenario()
    await parallel_scenario()
    print("test_rounds: budget ok · repetition ok · commented refusal ok · interruption ok · parallel cap ok")


if __name__ == "__main__":
    asyncio.run(main())
