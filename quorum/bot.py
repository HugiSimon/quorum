"""A bot is a folder: bot.toml, system.md, policy.toml, settings.json.

The four configuration levers all go through the process — the `cwd` stays the work folder.
Nothing environment-specific is written here: whatever changes from one machine to the next
(certificate, proxy) arrives through the bot's `env` block, as `${VAR}`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from string import Template


# The pattern is what the user writes in the bot card; the TOML rule is what the permission
# engine reads. This table translates both ways.
PATTERNS = (
    ("shell:", {"toolName": "run_shell_command"}, "commandPrefix"),
    ("fs:read", {"toolName": "read_file"}, None),
    ("fs:write", {"toolName": "write_file"}, None),
    ("fs:replace", {"toolName": "replace"}, None),
    ("fs:list", {"toolName": "list_directory"}, None),
    ("fs:search", {"toolName": "search_file_content"}, None),
    ("net:fetch", {"toolName": "web_fetch"}, None),
    ("net:search", {"toolName": "google_web_search"}, None),
    ("mcp:", {}, "mcpName"),
)

DECISIONS = {
    "allow": ("✓ allowed", "always"),
    "ask_user": ("◆ ask me", "every time"),
    "deny": ("✕ denied", "no exception"),
}


@dataclass
class Rule:
    """One line of the permission table: a pattern, a decision. Order sets priority."""

    pattern: str
    decision: str = "ask_user"

    @property
    def labels(self) -> tuple[str, str]:
        return DECISIONS.get(self.decision, ("?", "?"))


@dataclass(frozen=True)
class Bot:
    name: str
    role: str
    hue: int
    folder: Path
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    provider: str = "other"
    # "shared": every bot shares the room folder. "copy": this bot works in its own git
    # worktree — useful as soon as it writes.
    workdir: str = "shared"
    # The three capabilities of the bot card: each has a real effect on the conversation.
    can_mention: bool = True
    thinking_visible: bool = True
    speaks_unprompted: bool = True

    @property
    def mention(self) -> str:
        return f"@{self.name}"

    @property
    def own_copy(self) -> bool:
        return self.workdir == "copy"


def read_env(file: Path) -> dict[str, str]:
    """Reads an unversioned .env: one variable per line, # for comments."""
    values: dict[str, str] = {}
    if not file.exists():
        return values
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load(folder: Path) -> Bot:
    """Builds a Bot from its folder. The folder name wins if bot.toml says nothing."""
    conf = tomllib.loads((folder / "bot.toml").read_text(encoding="utf-8"))
    return Bot(
        name=conf.get("name", folder.name),
        role=conf.get("role", ""),
        hue=int(conf.get("hue", 0)),
        folder=folder,
        command=conf["command"],
        args=list(conf.get("args", [])),
        env=dict(conf.get("env", {})),
        model=conf.get("model") or None,
        workdir=conf.get("workdir", "shared"),
        can_mention=bool(conf.get("can_mention", True)),
        thinking_visible=bool(conf.get("thinking_visible", True)),
        speaks_unprompted=bool(conf.get("speaks_unprompted", True)),
        provider=conf.get(
            "provider", "gemini" if "gemini" in conf["command"] else "other"
        ),
    )


def load_all(root: Path) -> dict[str, Bot]:
    """Every bot of a bots/ folder, keyed by name. No folder means no bot."""
    bots: dict[str, Bot] = {}
    if not root.exists():
        return bots
    for folder in sorted(p for p in root.iterdir() if (p / "bot.toml").exists()):
        bot = load(folder)
        bots[bot.name] = bot
    return bots


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_toml(tables: list[tuple[str, dict]], header: str = "") -> str:
    """Writes the TOML we need: flat tables, nothing more.

    This is the only TOML the application produces and its shape is fixed — it is not worth
    one more dependency.
    """
    parts = [f"# {line}" for line in header.splitlines() if line] + [""] if header else []
    for name, fields in tables:
        parts.append(f"[{name}]" if not name.startswith("[") else name)
        for key, value in fields.items():
            if value is not None and value != "":
                parts.append(f"{key} = {_toml_value(value)}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def rule_to_toml(rule: Rule, priority: int) -> dict:
    """Translates a bot-card pattern into fields the permission engine understands."""
    fields: dict = {}
    for prefix, fixed, free_field in PATTERNS:
        if not rule.pattern.startswith(prefix):
            continue
        fields.update(fixed)
        rest = rule.pattern[len(prefix):].strip().rstrip("*").strip()
        if free_field and rest:
            fields[free_field] = rest
        break
    fields["decision"] = rule.decision
    fields["priority"] = priority
    return fields


def toml_to_pattern(fields: dict) -> str:
    """Rebuilds the displayable pattern of a rule read from disk."""
    for prefix, fixed, free_field in PATTERNS:
        if fixed and all(fields.get(k) == v for k, v in fixed.items()):
            if not free_field:
                return prefix
            rest = fields.get(free_field)
            return f"{prefix}{rest} *" if rest else f"{prefix}*"
        if not fixed and free_field and fields.get(free_field):
            return f"{prefix}{fields[free_field]}"
    return "everything else"


def read_rules(folder: Path) -> list[Rule]:
    """A bot's rules, in decreasing priority order — the first one wins."""
    file = folder / "policy.toml"
    if not file.exists():
        return [Rule("everything else", "ask_user")]
    raw = tomllib.loads(file.read_text(encoding="utf-8")).get("rules", [])
    raw.sort(key=lambda r: -int(r.get("priority", 0)))
    return [Rule(toml_to_pattern(r), r.get("decision", "ask_user")) for r in raw]


def power(rules: list[Rule]) -> tuple[int, str]:
    """The bot card's gauge: what the rules really allow, promising nothing more."""
    allowed = [r.pattern for r in rules if r.decision == "allow"]
    denied = sum(1 for r in rules if r.decision == "deny")
    guards = sum(1 for r in rules if r.decision == "ask_user")
    writes = any(p.startswith(("fs:write", "fs:replace")) for p in allowed)
    runs = any(p.startswith("shell") for p in allowed)
    goes_out = any(p.startswith("net") for p in allowed)
    level = 1 + 2 * writes + 2 * runs + 2 * goes_out
    phrases = []
    phrases.append("can change files" if writes else "changes no file")
    phrases.append("runs commands" if runs else "runs no command")
    phrases.append("reaches the network" if goes_out else "stays off the network")
    return level, (
        " ; ".join(phrases)
        + f". {guards} guard{'s' if guards > 1 else ''}, "
        + f"{denied} denial{'s' if denied > 1 else ''}."
    )


def write_bot(folder: Path, bot: Bot, role: str, rules: list[Rule]) -> None:
    """Writes a bot's three files. A bot stays a folder, editable by hand."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "system.md").write_text(role.rstrip() + "\n", encoding="utf-8")

    lines = ["# written by quorum · stays editable by hand", ""]
    for key, value in (
        ("name", bot.name), ("role", bot.role), ("hue", bot.hue),
        ("command", bot.command), ("model", bot.model or ""),
        ("provider", bot.provider), ("workdir", bot.workdir),
        ("can_mention", bot.can_mention),
        ("thinking_visible", bot.thinking_visible),
        ("speaks_unprompted", bot.speaks_unprompted),
    ):
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("args = [" + ", ".join(_toml_value(a) for a in bot.args) + "]")
    lines += ["", "[env]"] + [f"{k} = {_toml_value(v)}" for k, v in bot.env.items()]
    (folder / "bot.toml").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    (folder / "policy.toml").write_text(
        write_toml(
            [("[[rules]]", rule_to_toml(rule, 100 - index * 5))
             for index, rule in enumerate(rules)],
            header="first matching rule wins, decreasing priority",
        ),
        encoding="utf-8",
    )


def launch(
    bot: Bot,
    project_env: dict[str, str] | None = None,
    telemetry: Path | None = None,
) -> tuple[str, list[str], dict[str, str]]:
    """Returns the (command, args, env) triple, ready for asyncio.

    The Gemini-specific levers are only set when the command is one: a Claude Code or Codex
    bot keeps exactly what its bot.toml declares.

    `telemetry` is the only path by which command output comes back: the agent does not
    send it in the stream. Without that log, the thread shows commands without what they
    answered.
    """
    substitutions = {**os.environ, **(project_env or {})}
    env = dict(os.environ)
    for key, value in bot.env.items():
        resolved = Template(value).safe_substitute(substitutions)
        # Unresolved or empty means "do not set it": an empty HTTPS_PROXY, or one left at
        # ${HTTPS_PROXY}, is enough to bring the agent down at startup.
        if not resolved or "${" in resolved:
            env.pop(key, None)
        else:
            env[key] = resolved

    args = list(bot.args)
    if bot.provider == "gemini":
        if (bot.folder / "system.md").exists():
            env["GEMINI_SYSTEM_MD"] = str(bot.folder / "system.md")
        if (bot.folder / "settings.json").exists():
            env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(bot.folder / "settings.json")
        if (bot.folder / "policy.toml").exists():
            args += ["--policy", str(bot.folder / "policy.toml")]
        # Without this mode, the agent decides on its own and no permission request comes up.
        args += ["--approval-mode", "default"]
        # A bot does not inherit the machine's personal extensions: its tools come from its
        # policy and from the MCP servers passed to session/new. A name that does not exist
        # is enough to load none. (The A2A servers of the user settings do still load: no
        # lever found.)
        args += ["-e", "none"]
        if bot.model:
            args += ["-m", bot.model]
        if telemetry is not None:
            telemetry.parent.mkdir(parents=True, exist_ok=True)
            env["GEMINI_TELEMETRY_ENABLED"] = "true"
            env["GEMINI_TELEMETRY_TARGET"] = "local"
            env["GEMINI_TELEMETRY_OUTFILE"] = str(telemetry)
    return bot.command, args, env
