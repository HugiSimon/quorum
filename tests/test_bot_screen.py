"""S11 driven by keyboard: create a bot without touching a file, then read it back."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input, TextArea  # noqa: E402

from workshop import wait_for, build_project, input_ready  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.screens import BotCard, Step, RulesTable, Hue  # noqa: E402
from quorum.room import load_room  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_project(root, max_rounds=0)
        app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
        async with app.run_test(size=(110, 45)) as pilot:
            await wait_for(pilot, lambda: all(p.ready for p in app.participants.values()), "sessions")
            await input_ready(pilot, app)

            # The model list comes from the session, not from a constant.
            assert app.known_models() == ["fake-1"], app.known_models()

            app.action_bot_card()
            await pilot.pause(0.2)
            screen = app.screen
            assert isinstance(screen, BotCard), screen
            assert screen.is_new, "with no bubble selected, we open a fresh bot"
            model = next(s for s in screen.query(Step) if s.label == "model")
            assert model.values == ["fake-1"], model.values

            screen.query_one("#name", Input).value = "watcher"
            screen.query_one("#role", Input).value = "watch"
            screen.query_one("#prompt", TextArea).text = "You watch and you warn."

            # The hue avoids the bands reserved for the interface.
            hue = screen.query_one(Hue)
            hue.hue = 40
            hue.focus()
            await pilot.press("right")
            assert hue.hue not in range(45, 76), hue.hue

            # The table: one rule added, its decision changed, the order swapped.
            table = screen.query_one(RulesTable)
            table.focus()
            await pilot.pause(0.1)
            assert "▌" in table.plain_text, "the aimed rule lights up as soon as it has focus"
            assert "a add" in table.plain_text, "the table's keys are written out"
            start = len(table.rules)
            await pilot.press("a")
            await wait_for(pilot, lambda: bool(screen.query("#pattern")), "the pattern input")
            screen.query_one("#pattern", Input).value = "shell:git *"
            await pilot.press("enter")
            assert len(table.rules) == start + 1, table.rules
            assert table.rules[0].pattern == "shell:git *", table.rules

            table.focus()
            await pilot.press("d")
            assert table.rules[0].decision == "deny", table.rules[0]
            await pilot.press("d")  # deny → allow, the cycle loops over three values
            assert table.rules[0].decision == "allow", table.rules[0]

            await pilot.press("shift+down")
            assert table.rules[1].pattern == "shell:git *", table.rules
            assert table.cursor == 1

            # The table names the rule its own agent plays by, not a fixed sentence.
            assert "read-only" in table.plain_text, table.plain_text

            await pilot.press("ctrl+s")
            await wait_for(pilot, lambda: not isinstance(app.screen, BotCard), "the close")

            # The bot's own files, plus the ones its harness needs — the settings file is
            # what keeps a new bot from inheriting the machine's personal MCP servers.
            folder = root / "bots" / "watcher"
            assert {f.name for f in folder.iterdir()} == {
                "bot.toml", "system.md", "policy.toml", "settings.json"
            }, sorted(f.name for f in folder.iterdir())
            back = bots.load(folder)
            assert back.name == "watcher" and back.role == "watch"
            assert back.hue == hue.hue and not back.own_copy
            assert "You watch" in (folder / "system.md").read_text()
            patterns = [(r.pattern, r.decision) for r in bots.read_rules(folder)]
            assert ("shell:git *", "allow") in patterns, patterns
            assert "watcher" in bots.load_all(root / "bots")

            # ── the same bot, moved to another agent ────────────────────────────────
            app.push_screen(BotCard(root, "watcher", [], set()))
            await pilot.pause(0.2)
            screen = app.screen
            step = next(s for s in screen.query(Step) if s.label == "harness")
            assert step.value == "gemini", step.value
            table = screen.query_one(RulesTable)
            assert table.editable and "read-only" in table.plain_text

            # A rule gemini reads and opencode has no key for.
            table.focus()
            await pilot.press("a")
            screen.query_one("#pattern", Input).value = "fs:read"
            await pilot.press("enter")
            assert not table.blocked, "gemini expresses every one of them"

            step.focus()
            await pilot.press("right")
            assert step.value == "opencode", step.value
            # The command follows, because it was still the previous agent's default.
            assert screen.query_one("#command", Input).value == "opencode"
            assert "most precise key wins" in table.plain_text, table.plain_text
            # `shell:git *` survives the move, `fs:read` does not — and the table says
            # which and why instead of writing it anyway.
            assert table.blocked, "the table names what opencode cannot express"
            assert any("does not gate" in why for why in table.blocked.values()), table.blocked
            assert "not written" in table.plain_text, table.plain_text

            await pilot.press("ctrl+s")
            await wait_for(pilot, lambda: not isinstance(app.screen, BotCard), "the close")

            assert (folder / "opencode.json").exists(), "the new agent's file is written"
            assert (folder / "policy.toml").exists(), "the old one stays on disk, unused"
            assert bots.load(folder).provider == "opencode"
            moved = [(r.pattern, r.decision) for r in bots.read_rules(folder)]
            assert ("shell:git *", "allow") in moved, moved
            assert not any(p == "fs:read" for p, _ in moved), "a blocked rule reaches no file"

    print("test_bot_screen: models from the session ok · hue ok · table ok · "
          "harness files ok · harness switch ok")


if __name__ == "__main__":
    asyncio.run(main())
