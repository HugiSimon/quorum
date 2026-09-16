"""T5: session resume, expired memory, eviction and wake-up, separate copy."""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from workshop import wait_for, build_project, input_ready  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import ExpiryPanel, Quorum  # noqa: E402
from quorum.room import Transcript, load_room  # noqa: E402


async def one_turn(root: Path) -> None:
    """A room that has lived: one message, two answers, a state on disk."""
    app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: all(p.ready for p in app.participants.values()), "sessions")
        await input_ready(pilot, app)
        app.query_one("#message", Input).value = "hello"
        await pilot.press("enter")
        await wait_for(pilot, lambda: app.conversation.done(), "the turn", 1500)


async def expiry_scenario() -> None:
    """A lost session must not take the thread with it: S10 offers its three ways out."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=0)
        await one_turn(root)

        room = load_room(root, "trial")
        state = json.loads((room.root / "state.json").read_text())
        assert state["sessions"], state
        state["sessions"] = {name: "sess-lost" for name in state["sessions"]}
        (room.root / "state.json").write_text(json.dumps(state))
        before = len(Transcript(room.root / "transcript.jsonl").entries)

        app = Quorum(room, bots.load_all(root / "bots"))
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: bool(app.query(ExpiryPanel)), "the S10 panel")
            for participant in app.participants.values():
                assert participant.memory_lost, participant.name
                assert participant.startup == "fresh", participant.startup
                assert participant.seen == 0, "memory lost: the bot starts from the whole thread"
            await wait_for(pilot, lambda: app.query_one(ExpiryPanel).plain_text,
                           "the painted panel")
            assert app.query_one(ExpiryPanel).plain_text.count("\n") >= 4

            # Way out 3: archive. The thread moves aside, nothing is deleted.
            app.query_one(ExpiryPanel).focus()
            await pilot.press("3")
            await wait_for(pilot, lambda: bool(list(room.root.glob("transcript-*.jsonl"))),
                           "the archive")
            archive = next(room.root.glob("transcript-*.jsonl"))
            assert len(Transcript(archive).entries) == before, archive
            assert app.transcript.entries == [], "the thread starts empty again"


async def summary_scenario() -> None:
    """Way out 2: the bots write the summary of the thread they forgot themselves."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=0)
        await one_turn(root)

        room = load_room(root, "trial")
        state = json.loads((room.root / "state.json").read_text())
        state["sessions"] = {name: "sess-lost" for name in state["sessions"]}
        (room.root / "state.json").write_text(json.dumps(state))

        app = Quorum(room, bots.load_all(root / "bots"))
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: bool(app.query(ExpiryPanel)), "the S10 panel")
            app.query_one(ExpiryPanel).focus()
            await pilot.press("2")
            await wait_for(pilot, lambda: app.conversation is not None and app.conversation.done(),
                           "the summary", 1500)
            for participant in app.participants.values():
                assert participant.bubble is not None and participant.bubble.body.strip()
                assert participant.seen > 0, "after the summary, the thread starts from there"


async def eviction_scenario() -> None:
    """An evicted bot gives back its live memory, and its next turn wakes it up."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=0)
        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test() as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)

            await app.evict(one)
            assert not one.ready and one.startup == "evicted" and one.client is None

            app.query_one("#message", Input).value = "@fake1 are you awake?"
            await pilot.press("enter")
            await wait_for(pilot, lambda: app.conversation.done(), "the wake-up turn", 1500)
            assert one.ready, "the turn must wake the evicted bot"
            assert one.startup == "resumed", one.startup
            assert one.bubble.body.strip(), one.bubble.body


async def separate_copy_scenario() -> None:
    """Inside a git repo, a bot that writes gets its copy; outside, we share and say so."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=0, copy=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                        "commit", "-q", "--allow-empty", "-m", "start"], cwd=root, check=True)

        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test() as pilot:
            one, two = app.participants["fake1"], app.participants["fake2"]
            await wait_for(pilot, lambda: one.ready and two.ready, "the sessions")
            await input_ready(pilot, app)
            assert one.folder is not None and one.folder.name == "fake1", one.folder
            assert one.folder.exists() and one.folder != app.room.folder
            assert two.folder == app.room.folder, "a read-only bot shares"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=0, copy=True)
        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test() as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)
            assert one.folder == app.room.folder, "outside a git repo, no copy possible"
            thread = "\n".join(getattr(b, "plain_text", "") for b in app.query("#thread > Static"))
            assert "no separate copy possible" in thread, thread


async def main() -> None:
    await expiry_scenario()
    await summary_scenario()
    await eviction_scenario()
    await separate_copy_scenario()
    print("test_resume: expiry ok · summary ok · eviction and wake-up ok · separate copy ok")


if __name__ == "__main__":
    asyncio.run(main())
