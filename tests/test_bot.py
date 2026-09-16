"""Loading a bot: missing variables must never reach the process."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.bot import load, launch, read_env  # noqa: E402

BOT = """
name = "trial"
role = "trial"
hue = 150
command = "gemini"
args = ["--acp"]

[env]
HTTPS_PROXY = "${HTTPS_PROXY}"
NODE_EXTRA_CA_CERTS = "${PROJECT_CA}"
EMPTY = ""
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "trial"
        folder.mkdir()
        (folder / "bot.toml").write_text(BOT, encoding="utf-8")
        (folder / "system.md").write_text("a role", encoding="utf-8")
        (folder / "policy.toml").write_text("", encoding="utf-8")
        (Path(tmp) / ".env").write_text("PROJECT_CA=/tmp/ca.pem\n", encoding="utf-8")

        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("GEMINI_SYSTEM_MD", None)
        bot = load(folder)
        command, args, env = launch(bot, read_env(Path(tmp) / ".env"))

        assert "HTTPS_PROXY" not in env, "an unresolved variable must not be set"
        assert "EMPTY" not in env, "an empty variable must not be set"
        assert env["NODE_EXTRA_CA_CERTS"] == "/tmp/ca.pem", env.get("NODE_EXTRA_CA_CERTS")
        assert env["GEMINI_SYSTEM_MD"].endswith("system.md")
        assert "--policy" in args and "--approval-mode" in args, args
        assert args[args.index("-e") + 1] == "none", "no personal extension inherited"
        assert command == "gemini"

        # The provider is deduced from the command when bot.toml says nothing.
        assert bot.provider == "gemini", bot.provider

        # A bot that is not Gemini keeps exactly what its bot.toml declares.
        other = bot.__class__(**{**bot.__dict__, "command": "npx", "provider": "other"})
        other_command, other_args, other_env = launch(other)
        assert other_args == ["--acp"], other_args
        assert "GEMINI_SYSTEM_MD" not in other_env and other_command == "npx"
    print("test_bot: missing variables dropped · gemini levers set · other provider untouched")


if __name__ == "__main__":
    main()
