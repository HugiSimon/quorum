"""One table, three agents: what translates, what does not, and what survives a save.

The two promises checked here are the ones the feature rests on. A rule an agent cannot
express reaches no file and is named instead. And a key quorum did not write — a hand edit,
a block from a newer version of the agent — is still there, untouched, after a save.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum import harness  # noqa: E402
from quorum.bot import Bot, Rule, read_rules, write_bot  # noqa: E402


def bot(folder: Path, provider: str, model: str = "") -> Bot:
    return Bot(name="pilot", role="trial", hue=10, folder=folder,
               command=harness.by_name(provider).command,
               args=list(harness.by_name(provider).args),
               model=model or None, provider=provider)


def naming() -> None:
    assert harness.detect("gemini", ["--acp"]) == "gemini"
    assert harness.detect("/Users/x/.opencode/bin/opencode", ["acp"]) == "opencode"
    # The command is a generic npx: the harness is named in the arguments.
    assert harness.detect("npx", ["-y", "@agentclientprotocol/claude-agent-acp@0.79.0"]) \
        == "claude-code"
    assert harness.detect("my-own-agent", ["--acp"]) == "other"
    assert harness.by_name("does-not-exist") is harness.OTHER


def pointers() -> None:
    doc: dict = {}
    harness.put_at(doc, ("agent", "quorum-pilot", "model"), "small")
    assert doc == {"agent": {"quorum-pilot": {"model": "small"}}}, doc
    assert harness.get_at(doc, ("agent", "quorum-pilot", "model")) == "small"
    assert harness.get_at(doc, ("agent", "nobody", "model")) is None
    # A branch that is not a table is replaced, not walked into.
    harness.put_at(doc, ("agent", "quorum-pilot"), {"prompt": "{file:/x}"})
    assert doc["agent"]["quorum-pilot"] == {"prompt": "{file:/x}"}


def opencode_translation() -> None:
    rules = [
        Rule("shell:git *", "allow"),
        Rule("shell~push", "deny"),
        Rule("fs:replace", "ask_user"),
        Rule("fs:read", "allow"),
        Rule("mcp:vault", "ask_user"),
        Rule("everything else", "deny"),
    ]
    native, blocked = harness.opencode_to_native(rules)
    assert native["bash"] == {"git *": "allow"}, native
    assert native["edit"] == "ask" and native["vault_*"] == "ask", native
    assert native["*"] == "deny", native
    # A regex over the arguments and a tool opencode does not gate: both named, neither
    # written. This is the promise — nothing is rounded to its nearest neighbour.
    assert set(blocked) == {1, 3}, blocked
    assert "regular expression" in blocked[1], blocked[1]
    assert "does not gate reading" in blocked[3], blocked[3]
    assert "read" not in native and "grep" not in native, native

    back = harness.opencode_from_native(native)
    patterns = [(r.pattern, r.decision) for r in back]
    assert ("shell:git *", "allow") in patterns, patterns
    assert ("mcp:vault", "ask_user") in patterns, patterns
    assert patterns[-1] == ("everything else", "deny"), patterns

    # A key nobody here knows — added by hand, or by a newer opencode — comes back as a
    # rule of its own rather than being dropped on the floor.
    strange = harness.opencode_from_native({"future_tool": "deny"})
    assert [(r.pattern, r.decision) for r in strange] == [("tool:future_tool", "deny")]
    again, _ = harness.opencode_to_native(strange)
    assert again == {"future_tool": "deny"}, again


def claude_translation() -> None:
    rules = [
        Rule("shell:git commit *", "allow"),
        Rule("fs:read", "allow"),
        Rule("net:search", "deny"),
        Rule("shell~rm", "deny"),
        Rule("everything else", "deny"),
    ]
    native, blocked = harness.claude_to_native(rules)
    assert native["allow"] == ["Bash(git commit:*)", "Read"], native
    assert native["deny"] == ["WebSearch"], native
    assert set(blocked) == {3, 4}, blocked
    assert "no blanket allow or deny" in blocked[4], blocked[4]
    assert "defaultMode" not in native, native

    back = [(r.pattern, r.decision) for r in harness.claude_from_native(native)]
    assert ("shell:git commit *", "allow") in back, back
    assert ("net:search", "deny") in back, back
    assert back[-1] == ("everything else", "ask_user"), back

    asking, blocked = harness.claude_to_native([Rule("everything else", "ask_user")])
    assert asking["defaultMode"] == "manual" and not blocked, (asking, blocked)


def keys_not_files() -> None:
    """The heart of it: a save replaces what quorum claims and nothing else."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "pilot"
        folder.mkdir()
        (folder / "system.md").write_text("a role\n", encoding="utf-8")
        pilot = bot(folder, "opencode", model="openai/gpt-5.6-luna")
        harness.write(folder, pilot, "a role", [Rule("shell:*", "ask_user")])

        file = folder / "opencode.json"
        document = json.loads(file.read_text())
        # Everything opencode reads is at the top level: an agent block is never opened by
        # the ACP bridge. Measured with tools/harness_check.py, not read in a manual.
        assert document["model"] == "openai/gpt-5.6-luna", document
        assert document["small_model"] == "openai/gpt-5.6-luna", document
        assert document["instructions"] == [str((folder / "system.md").resolve())], document
        assert document["permission"] == {"bash": "ask"}, document

        # What a hand — or another agent — adds between two saves.
        document["mcp"] = {"vault": {"type": "local", "command": ["vault-mcp"]}}
        document["from_a_newer_opencode"] = {"deep": ["untouched", 1, True]}
        document["agent"] = {"mine": {"mode": "primary", "prompt": "kept"}}
        file.write_text(json.dumps(document, indent=2), encoding="utf-8")

        harness.write(folder, pilot, "a role", [Rule("shell:*", "deny")])
        after = json.loads(file.read_text())
        assert after["mcp"] == document["mcp"], after
        assert after["from_a_newer_opencode"] == document["from_a_newer_opencode"], after
        assert after["agent"] == document["agent"], after
        assert after["permission"] == {"bash": "deny"}, after

        # And it reads back through the card the way it was written.
        (folder / "bot.toml").write_text(
            'name = "pilot"\ncommand = "opencode"\nprovider = "opencode"\n', encoding="utf-8"
        )
        assert [(r.pattern, r.decision) for r in read_rules(folder)] == [("shell:*", "deny")]


def nothing_for_a_stranger() -> None:
    """`other` is the promise of the README: quorum launches it and configures nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "stranger"
        stranger = Bot(name="stranger", role="", hue=10, folder=folder,
                       command="my-own-agent", args=["--acp"], provider="other")
        write_bot(folder, stranger, "a role", [Rule("everything else", "ask_user")])
        assert {f.name for f in folder.iterdir()} == {"bot.toml", "system.md"}, \
            sorted(f.name for f in folder.iterdir())
        assert harness.extras(folder, stranger) == ([], {}), "nothing is added at launch"


def levers_at_launch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "pilot"
        folder.mkdir()
        (folder / "system.md").write_text("a role\n", encoding="utf-8")
        (folder / "policy.toml").write_text("", encoding="utf-8")
        (folder / "settings.json").write_text("{}", encoding="utf-8")

        args, env = harness.extras(folder, bot(folder, "gemini", model="flash"))
        assert args[args.index("-e") + 1] == "none", args
        assert "--approval-mode" in args and args[args.index("-m") + 1] == "flash", args
        assert args[args.index("--policy") + 1].endswith("policy.toml"), args
        assert env["GEMINI_SYSTEM_MD"].endswith("system.md"), env

        args, env = harness.extras(folder, bot(folder, "opencode", model="small"))
        assert env["OPENCODE_CONFIG"].endswith("opencode.json"), env
        assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1", env
        # opencode takes its model from the file, so nothing about it is on the argv.
        assert args == [], args

        # A lever pointed at a file the folder does not hold is dropped, not passed on.
        bare = Path(tmp) / "bare"
        bare.mkdir()
        _, env = harness.extras(bare, bot(bare, "gemini"))
        assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" not in env, env


def carried_by_the_session() -> None:
    """Claude Code reads no file quorum can drop: what it gets, it gets in `session/new`."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "pilot"
        folder.mkdir()
        (folder / "system.md").write_text("You are terse.\n", encoding="utf-8")
        pilot = bot(folder, "claude-code", model="haiku")
        harness.write(folder, pilot, "You are terse.",
                      [Rule("shell:git *", "allow"), Rule("net:fetch", "deny")])

        meta = harness.session_meta(folder, pilot)
        options = meta["claudeCode"]["options"]
        assert meta["systemPrompt"].startswith("You are terse"), meta
        # The one that matters: without it the bot inherits the machine's own settings —
        # its plugins, its hooks and its approval mode — and never asks for anything.
        assert options["settingSources"] == [], options
        assert options["permissionMode"] == "default", options
        assert options["allowDangerouslySkipPermissions"] is False, options
        assert options["allowedTools"] == ["Bash(git:*)"], options
        assert options["disallowedTools"] == ["WebFetch"], options
        assert options["model"] == "haiku", options

        # An agent that reads its own files carries nothing in the session.
        assert harness.session_meta(folder, bot(folder, "gemini")) == {}


def main() -> None:
    naming()
    pointers()
    opencode_translation()
    claude_translation()
    keys_not_files()
    nothing_for_a_stranger()
    levers_at_launch()
    carried_by_the_session()
    print("test_harness: naming ok · pointers ok · translations ok · "
          "keys kept ok · stranger untouched ok · launch levers ok · session meta ok")


if __name__ == "__main__":
    main()
