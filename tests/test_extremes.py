"""S13: 80×24, 200 columns, and the light theme. Nothing is removed, everything shortened."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input, Static  # noqa: E402

from workshop import wait_for, build_project, input_ready  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum import settings as config  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.room import load_room  # noqa: E402
from quorum.theme import N, NEUTRALS_LIGHT, NEUTRALS_DARK, bot_color  # noqa: E402


async def open_room(root: Path, size):
    build_project(root, max_rounds=0)
    app = Quorum(load_room(root, "trial"), bots.load_all(root / "bots"))
    return app, app.run_test(size=size)


async def cramped_scenario() -> None:
    """80×24: the role goes, the time goes relative, the choices shrink to the verb."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app, context = await open_room(root, (80, 24))
        async with context as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)
            assert app.compact, app.size.width

            app.query_one("#message", Input).value = "@fake1 PERM delete .venv"
            await pilot.press("enter")
            await wait_for(pilot, lambda: one.panel is not None, "the permission")
            await pilot.pause(0.25)

            rendered = one.bubble.draw().plain
            assert one.bubble.compact, "the bubble must shorten"
            assert "trial" not in rendered, f"the role must disappear: {rendered}"
            assert "now" in rendered, f"relative timestamp expected: {rendered}"
            assert "@fake1" in rendered, "the name, on the other hand, never goes"

            choices = one.panel.render().plain
            assert "1 ✓allow" in choices and "3 ✕reject" in choices, choices
            assert "r comment" in choices, choices
            assert choices.count("\n") <= 3, f"everything must fit tight: {choices!r}"

            assert not app.query_one("#side", Static).display, "no margin at 80 columns"
            one.panel.choose(one.panel.options[0])
            await wait_for(pilot, lambda: one.turn.done(), "the end of the turn")


async def huge_scenario() -> None:
    """200 columns: the margins become useful, the thread does not sprawl."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app, context = await open_room(root, (200, 48))
        async with context as pilot:
            one = app.participants["fake1"]
            await wait_for(pilot, lambda: one.ready, "the session")
            await input_ready(pilot, app)
            await pilot.pause(0.3)
            assert not app.compact

            side = app.query_one("#side", Static)
            assert side.display, "beyond 160 columns, the side panel opens"
            text = side.render().plain
            assert "IN THIS ROOM" in text and "@fake1" in text and "@fake2" in text, text
            assert "nobody is working" in text, text


async def light_theme_scenario() -> None:
    """Same hue, other lightness: a bot's identity does not change color, only tone."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ["QUORUM_HOME"] = str(root / "config")
        config.write({**config.DEFAULTS, "theme": "light"})
        app, context = await open_room(root, (120, 30))
        async with context as pilot:
            await wait_for(pilot, lambda: all(p.ready for p in app.participants.values()), "sessions")
            await input_ready(pilot, app)
            assert N["bg"] == NEUTRALS_LIGHT["bg"], N["bg"]
            assert app.get_css_variables()["ink"] == NEUTRALS_LIGHT["ink"]

            fake1 = app.participants["fake1"]
            assert fake1.color == bot_color(fake1.bot.hue, dark=False)
            assert fake1.color != bot_color(fake1.bot.hue, dark=True)

            # And we switch back live, without restarting.
            app.settings_changed({**app.settings, "theme": "dark"})
            await pilot.pause(0.2)
            assert N["bg"] == NEUTRALS_DARK["bg"], N["bg"]
            assert app.participants["fake1"].color == bot_color(fake1.bot.hue, dark=True)
        del os.environ["QUORUM_HOME"]


async def main() -> None:
    await cramped_scenario()
    await huge_scenario()
    await light_theme_scenario()
    print("test_extremes: 80×24 ok · 200 columns ok · light theme and live switch ok")


if __name__ == "__main__":
    asyncio.run(main())
