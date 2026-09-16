"""Writing a bot from its card, reading it back, and landing on the same thing."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.bot import (  # noqa: E402
    Bot,
    Rule,
    load,
    write_bot,
    launch,
    read_rules,
    power,
)


def main() -> None:
    rules = [
        Rule("shell:*", "deny"),
        Rule("shell:git *", "allow"),
        Rule("shell:rm *", "ask_user"),
        Rule("shell~push", "ask_user"),
        Rule("fs:glob", "allow"),
        Rule("tool:some_new_tool", "allow"),
        Rule("fs:write", "ask_user"),
        Rule("net:fetch", "deny"),
        Rule("mcp:vault", "deny"),
        Rule("everything else", "ask_user"),
    ]
    bot = Bot(
        name="trial", role="execution", hue=150, folder=Path("."),
        command="gemini", args=["--acp"], env={"NO_PROXY": "localhost"},
        model="", provider="gemini", workdir="copy",
    )

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "trial"
        write_bot(folder, bot, "You execute.", rules)

        back = load(folder)
        assert back.name == "trial" and back.hue == 150, back
        assert back.args == ["--acp"] and back.env == {"NO_PROXY": "localhost"}
        assert back.provider == "gemini" and back.own_copy, back
        assert (folder / "system.md").read_text().strip() == "You execute."

        # The round trip through the disk loses neither a pattern nor a decision, nor the order.
        rendered = read_rules(folder)
        assert [(r.pattern, r.decision) for r in rendered] == [
            (r.pattern, r.decision) for r in rules
        ], [(r.pattern, r.decision) for r in rendered]

        # And the permission engine does receive the file.
        _, args, _ = launch(back)
        # Absolute: the agent resolves what it is given against its own cwd, not ours.
        assert "--policy" in args and str(folder.resolve() / "policy.toml") in args, args
        assert all(Path(a).is_absolute() for a in args if a.endswith(".toml")), args

        # A rule the card cannot name used to be read as "everything else", and written back
        # as a rule with no criteria at all: opening a bot and saving it turned one allowed
        # tool into every tool allowed. Nothing may widen on a round trip.
        catch_alls = [r for r in rendered if r.pattern == "everything else"]
        assert len(catch_alls) == 1 and catch_alls[0].decision == "ask_user", catch_alls
        for starter in sorted((Path(__file__).resolve().parents[1] / "quorum" / "starter"
                               / "bots").iterdir()):
            shipped = read_rules(starter)
            wide = [r for r in shipped if r.pattern == "everything else"]
            assert len(wide) == 1, f"{starter.name}: {[r.pattern for r in shipped]}"
            assert wide[0].decision != "allow", f"{starter.name} ships an allow-all"
            assert wide[-1] is shipped[-1], f"{starter.name}: the catch-all is not last"

        level, phrase = power(rules)
        assert "runs commands" in phrase and "changes no file" in phrase, phrase
        assert "4 guards, 3 denials" in phrase, phrase
        assert 1 <= level <= 7, level

        # A bot with no allowed rule at all sits at the bottom.
        low, _ = power([Rule("everything else", "ask_user")])
        assert low < level, (low, level)
    print("test_bot_card: write ok · read back ok · rule order ok · gauge ok")


if __name__ == "__main__":
    main()
