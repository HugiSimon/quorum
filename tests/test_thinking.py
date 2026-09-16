"""S7: the steps, the files touched, and the fate of an output that lags or never comes."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from workshop import wait_for, build_project, input_ready  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import ThinkingScreen, Quorum  # noqa: E402
from quorum.room import load_room  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=0)
        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test(size=(100, 40)) as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)

            app.query_one("#message", Input).value = "@fake1 PERM delete .venv"
            await pilot.press("enter")
            await wait_for(pilot, lambda: one.panel is not None, "the permission")

            # Two titled steps, taken from a single thought block.
            assert [s["title"] for s in one.bubble.steps] == [
                "Reading The Sources", "Executing Shell Commands"
            ], one.bubble.steps
            assert one.bubble.steps[0]["body"] == "I am looking at the notes first."

            # `locations` feeds the panel of files touched.
            assert one.files == {"/tmp/notes.md": "read"}, one.files

            # ^R opens the reasoning of the last active bot — by keyboard, without a mouse.
            assert one.bubble.owner == "fake1"
            assert app.last_active() == "fake1", app.last_active()
            app.action_thinking()
            await pilot.pause(0.25)
            screen = app.screen
            assert isinstance(screen, ThinkingScreen), screen
            rendered = screen.last_render.plain
            for expected in ("STEPS", "Reading The Sources", "Executing Shell Commands",
                             "FILES TOUCHED", "/tmp/notes.md", "TIMELINE", "TOKENS"):
                assert expected in rendered, f"{expected} missing from the S7 screen"
            # The last step is open, the others folded.
            assert "▾ Executing Shell Commands" in rendered and "┊ Reading The Sources" in rendered

            await pilot.press("escape")
            await pilot.pause(0.25)

            # The full path of an output: the fake agent writes it in its log, the follower
            # finds it again and links it to the right tool line. This test also catches the
            # inode trap: emptying the log after the agent launched would make the output
            # vanish forever.
            assert one.outputs is not None, "a gemini bot must have an output adapter"
            shell = next(t for t in one.bubble.tools if t["family"] == "shell")
            assert shell["awaiting_output"] is False and shell["end"] is None, shell

            one.panel.choose(one.panel.options[0])
            await wait_for(pilot, lambda: one.turn.done(), "the end of the turn")
            await wait_for(pilot, lambda: shell["output"] is not None, "the output from the log", 1500)

            assert shell["output"] == "        2 data.txt", repr(shell["output"])
            assert "output +" in one.bubble.draw().plain
            assert one.outputs.path.exists(), "the log must stay on disk, not be wiped"

            # And an output that will never come is announced, not animated forever.
            read = next(t for t in one.bubble.tools if t["family"] == "fs")
            read["end"] = read["start"] + 0.2
            read["awaiting_output"] = True
            from quorum import telemetry
            keep, telemetry.GRACE = telemetry.GRACE, 0.05
            try:
                await app.close_outputs(one.bubble)
            finally:
                telemetry.GRACE = keep
            assert read["output_lost"] and not read["awaiting_output"], read
            assert "output never came" in one.bubble.draw().plain

            # And if it does come in the end, the interface takes it back.
            app.tool_output(one, "call_7", "late content")
            assert not read["output_lost"] and read["output"] == "late content", read
            assert "output never came" not in one.bubble.draw().plain

            # Once the turn is over, the thread keeps only the message and a quiet count.
            one.bubble.state = "done"
            done = one.bubble.draw().plain
            assert "Reading The Sources" not in done, done
            assert "open the reasoning" not in done, done
            assert "2 tools" in done, done
    print("test_thinking: steps ok · files ok · S7 screen ok · late and lost output ok")


if __name__ == "__main__":
    asyncio.run(main())
