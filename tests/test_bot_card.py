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
        assert "--policy" in args and str(folder / "policy.toml") in args, args

        level, phrase = power(rules)
        assert "runs commands" in phrase and "changes no file" in phrase, phrase
        assert "3 guards, 3 denials" in phrase, phrase
        assert 1 <= level <= 7, level

        # A bot with no allowed rule at all sits at the bottom.
        low, _ = power([Rule("everything else", "ask_user")])
        assert low < level, (low, level)
    print("test_bot_card: write ok · read back ok · rule order ok · gauge ok")


if __name__ == "__main__":
    main()
