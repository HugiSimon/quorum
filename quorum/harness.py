"""A harness is the agent a bot runs on, and the shape its configuration takes.

Quorum has four levers — the role, the model, the permissions, the isolation from the
machine's own config — and every agent puts them somewhere else. Gemini takes its prompt
from an environment variable and its policy from a file named on the command line; opencode
takes all four from one JSON document; the Claude Code bridge reads a whole config folder.
Before this module only the gemini shape existed in the code, and a bot on any other agent
had to be assembled by hand.

What lets the three sit in one table instead of three implementations: **every lever has a
carrier**. It is an environment variable, a piece of argv, a path inside a config file, or a
file of its own. `Env` and `Argv` are read at launch, `Key` is written on save, and what an agent takes
over the protocol instead of from a file goes through `meta`. One writer, one launcher. The only thing written per agent is the permission translation,
because the agents disagree about permissions far below the level any pointer reaches.

Two rules hold the file together.

**Quorum owns keys, not files.** A save loads the agent's config, replaces the paths listed
here, and writes the rest back untouched. A key from a version of opencode younger than this
file survives; so does a block a user — or their own agent — added by hand.

**What cannot be said is shown.** Every `to_native` returns, with the value, the rules it
could not express and why. They are never rounded to the nearest neighbour: they reach no
file, and the bot card paints them. The comment at the top of `bot.py` says what that
rounding cost the last time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import bot as bots
from .bot import EVERYTHING, Rule


# ── carriers ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Env:
    """The lever is an environment variable read by the agent at startup."""

    name: str
    shape: str = "{value}"


@dataclass(frozen=True)
class Argv:
    """The lever is a fragment of command line, appended when the value is set."""

    flags: tuple[str, ...]


@dataclass(frozen=True)
class Key:
    """The lever is a path inside a config file the agent reads."""

    file: str
    path: tuple[str, ...]
    shape: str = "{value}"
    # Some keys take a list of one rather than a string — opencode's instruction files.
    listed: bool = False


@dataclass(frozen=True)
class Perms:
    """Where the permission table goes, and the pair that translates it both ways.

    `path == ()` means the file holds nothing else, so the native value is its whole text —
    that is gemini's `policy.toml`. Any other path is a place inside a JSON document.
    """

    file: str
    path: tuple[str, ...]
    to_native: Callable[[list[Rule]], tuple[Any, dict[int, str]]]
    from_native: Callable[[Any], list[Rule]]
    caption: str
    # How the file is named on the command line, when the agent needs telling where it is.
    argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class Harness:
    name: str
    label: str
    command: str
    # What a new bot writes into its bot.toml, and stays editable there afterwards.
    args: tuple[str, ...] = ()
    # What is appended at every launch on top of it: the flags without which the agent
    # would decide on its own, or read the machine's own configuration.
    extra: tuple[str, ...] = ()
    # Substrings that name this harness inside a command already written by hand: it is how
    # a bot.toml from before this table still resolves to the right row.
    detect: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    # Set only when the file they name is really there. A redirect belongs in `env` — it
    # isolates whether or not the file exists — but a lever pointed at a missing file is
    # how an agent dies at startup.
    optional_env: dict[str, str] = field(default_factory=dict)
    # Written only when the file is missing, so a block rewritten by hand for a newer
    # version of the agent is never put back on the next save.
    scaffold: dict[str, dict] = field(default_factory=dict)
    # One carrier, or several when an agent wants the same value in two places.
    prompt: Any = None
    model: Any = None
    rules: Perms | None = None
    # Some agents take their configuration over the protocol rather than from a file: this
    # builds the `_meta` of `session/new` out of what the card wrote.
    meta: Callable[[Path, Any], dict] | None = None
    # Command output does not travel in the ACP stream. Gemini writes it to a local
    # telemetry log we tail; no other agent here offers an equivalent.
    outputs: str | None = None

    @property
    def levers(self) -> tuple:
        """The prompt and model carriers, however many there are of each."""
        spread = []
        for carrier in (self.prompt, self.model):
            spread += list(carrier) if isinstance(carrier, tuple) else [carrier]
        return tuple(c for c in spread if c is not None)

    @property
    def files(self) -> tuple[str, ...]:
        """Every file this harness writes inside the bot folder, in writing order."""
        names = []
        for carrier in self.levers:
            if isinstance(carrier, Key):
                names.append(carrier.file)
        if self.rules is not None:
            names.append(self.rules.file)
        names += list(self.scaffold)
        return tuple(dict.fromkeys(names))


# ── walking a config document ───────────────────────────────────────────────────────

def get_at(doc: Any, path: tuple[str, ...]) -> Any:
    for step in path:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(step)
    return doc


def put_at(doc: dict, path: tuple[str, ...], value: Any) -> None:
    """Sets one path, creating what is missing and touching nothing else."""
    for step in path[:-1]:
        nested = doc.get(step)
        if not isinstance(nested, dict):
            nested = {}
            doc[step] = nested
        doc = nested
    doc[path[-1]] = value


def _fill(value: Any, bot_name: str) -> Any:
    """`{bot}` is the bot's name — opencode files its agents under theirs."""
    if isinstance(value, str):
        return value.replace("{bot}", bot_name)
    if isinstance(value, tuple):
        return tuple(_fill(step, bot_name) for step in value)
    if isinstance(value, list):
        return [_fill(step, bot_name) for step in value]
    if isinstance(value, dict):
        return {key: _fill(item, bot_name) for key, item in value.items()}
    return value


# ── the permission translations ─────────────────────────────────────────────────────
#
# Quorum's vocabulary is the PATTERNS table of bot.py, which was read off gemini. Each
# translation below says, for its own agent, what maps and what does not. `to_native`
# returns `(value, blocked)`, `blocked` mapping the index of a rule the agent cannot
# express to the reason it cannot. Those rules reach no file.

WORD = {"allow": "allow", "ask_user": "ask", "deny": "deny"}
DECISION = {v: k for k, v in WORD.items()}
NO_REGEX = "matched by name here, not by a regular expression"


def gemini_to_native(rules: list[Rule]) -> tuple[str, dict[int, str]]:
    """Gemini reads quorum's own table: this is the translation that already existed."""
    tables = [("[[rule]]", bots.rule_to_toml(rule, 100 - index * 5))
              for index, rule in enumerate(rules)]
    return bots.write_toml(tables, header="first matching rule wins, decreasing priority"), {}


def gemini_from_native(text: str) -> list[Rule]:
    import tomllib

    conf = tomllib.loads(text or "")
    raw = conf.get("rule") or conf.get("rules") or []
    raw.sort(key=lambda r: -int(r.get("priority", 0)))
    return [Rule(bots.toml_to_pattern(r), r.get("decision", "ask_user")) for r in raw]


# opencode gates whole families rather than single tools: one key for editing whatever the
# tool, one for the shell, one for the web. The file tools it does not gate are refused
# here rather than written as a key it would ignore — a rule that reads "denied" in the card
# and does nothing in the agent is the one thing this table must never produce.
OPENCODE_KEYS = {"fs:write": "edit", "fs:replace": "edit", "net:fetch": "webfetch"}
OPENCODE_UNGATED = {
    "fs:read": "opencode does not gate reading",
    "fs:list": "opencode does not gate listing",
    "fs:search": "opencode does not gate searching",
    "fs:glob": "opencode does not gate globbing",
    "net:search": "opencode reaches the web through one tool — use net:fetch",
}


def opencode_to_native(rules: list[Rule]) -> tuple[dict, dict[int, str]]:
    """Projects an ordered list onto a map that has no order at all.

    opencode decides by key specificity, not by priority, so the order carries exactly this
    far: the first rule to claim a key keeps it, and a later rule for the same key is shown
    as blocked rather than quietly overwriting — or being overwritten by — it.
    """
    native: dict[str, Any] = {}
    blocked: dict[int, str] = {}
    for index, rule in enumerate(rules):
        pattern, decision = rule.pattern, WORD[rule.decision]
        if pattern == EVERYTHING:
            key, nested = "*", None
        elif pattern.startswith("shell~"):
            blocked[index] = NO_REGEX
            continue
        elif pattern.startswith("shell:"):
            rest = pattern[len("shell:"):].strip().rstrip("*").strip()
            key, nested = "bash", (f"{rest} *" if rest else None)
        elif pattern.startswith("mcp:"):
            name = pattern[len("mcp:"):].strip()
            key, nested = (f"{name}_*" if name else None), None
        elif pattern.startswith("tool:"):
            key, nested = (pattern[len("tool:"):].strip() or None), None
        else:
            plain = pattern.rstrip("*").strip()
            if plain in OPENCODE_UNGATED:
                blocked[index] = OPENCODE_UNGATED[plain]
                continue
            key, nested = OPENCODE_KEYS.get(plain), None
        if key is None:
            blocked[index] = "no key in opencode's permission vocabulary"
            continue
        if nested:
            branch = native.setdefault("bash", {})
            if not isinstance(branch, dict) or nested in branch:
                blocked[index] = "a rule above already decided this command"
            else:
                branch[nested] = decision
        elif key in native:
            blocked[index] = "a rule above already decided this key"
        else:
            native[key] = decision
    return native, blocked


def opencode_from_native(native: Any) -> list[Rule]:
    # `edit` covers writing and replacing alike: read back, it can only be shown as one of
    # them. The decision survives the trip, the finer of the two labels does not.
    back = {"edit": "fs:write", "webfetch": "net:fetch"}
    rules: list[Rule] = []
    catch_all: Rule | None = None
    for key, value in (native or {}).items():
        if isinstance(value, dict):
            for command, decision in value.items():
                word = DECISION.get(decision, "ask_user")
                rest = command.rstrip("*").strip()
                rules.append(Rule(f"shell:{rest} *" if rest else "shell:*", word))
            continue
        word = DECISION.get(value, "ask_user")
        if key == "*":
            catch_all = Rule(EVERYTHING, word)
        elif key == "bash":
            rules.append(Rule("shell:*", word))
        elif key in back:
            rules.append(Rule(back[key], word))
        elif key.endswith("_*"):
            rules.append(Rule(f"mcp:{key[:-2]}", word))
        else:
            rules.append(Rule(f"tool:{key}", word))
    # The catch-all goes last whatever the map said: it is the rule that takes what is left.
    return rules + ([catch_all] if catch_all else [])


# Claude Code addresses its tools by name and narrows a shell rule inside the specifier:
# `Bash(git:*)`. It has no listing tool of its own — a directory is read with Glob or
# through Bash — so `fs:list` has nowhere to land.
CLAUDE_TOOLS = {
    "fs:read": "Read", "fs:write": "Write", "fs:replace": "Edit",
    "fs:search": "Grep", "fs:glob": "Glob",
    "net:fetch": "WebFetch", "net:search": "WebSearch",
}
CLAUDE_BUCKET = {"allow": "allow", "ask_user": "ask", "deny": "deny"}
# The catch-all is not a specifier but a mode, and only "ask me about everything else" has
# an honest equivalent: a blanket allow would be `bypassPermissions`, which is not a rule
# but the removal of every rule, and a blanket deny has no mode at all.
CLAUDE_MODE = {"ask_user": "manual"}


def claude_to_native(rules: list[Rule]) -> tuple[dict, dict[int, str]]:
    native: dict[str, Any] = {"allow": [], "ask": [], "deny": []}
    blocked: dict[int, str] = {}
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        pattern = rule.pattern
        if pattern == EVERYTHING:
            mode = CLAUDE_MODE.get(rule.decision)
            if mode is None:
                blocked[index] = "claude code has no blanket allow or deny — name the tools"
            else:
                native["defaultMode"] = mode
            continue
        if pattern.startswith("shell~"):
            blocked[index] = NO_REGEX
            continue
        if pattern.startswith("shell:"):
            rest = pattern[len("shell:"):].strip().rstrip("*").strip()
            specifier = f"Bash({rest}:*)" if rest else "Bash"
        elif pattern.startswith("mcp:"):
            name = pattern[len("mcp:"):].strip()
            specifier = f"mcp__{name}__*" if name else ""
        elif pattern.startswith("tool:"):
            specifier = pattern[len("tool:"):].strip()
        else:
            specifier = CLAUDE_TOOLS.get(pattern.rstrip("*").strip(), "")
        if not specifier:
            blocked[index] = "no tool of that name in claude code"
        elif specifier in seen:
            blocked[index] = "a rule above already decided this tool"
        else:
            seen.add(specifier)
            native[CLAUDE_BUCKET[rule.decision]].append(specifier)
    return native, blocked


def claude_from_native(native: Any) -> list[Rule]:
    back = {v: k for k, v in CLAUDE_TOOLS.items()}
    modes = {v: k for k, v in CLAUDE_MODE.items()}
    rules: list[Rule] = []
    for bucket, decision in (("deny", "deny"), ("ask", "ask_user"), ("allow", "allow")):
        for specifier in (native or {}).get(bucket) or []:
            shell = re.fullmatch(r"Bash\((.+?):\*\)", specifier)
            served = re.fullmatch(r"mcp__(.+?)__\*", specifier)
            if shell:
                rules.append(Rule(f"shell:{shell.group(1)} *", decision))
            elif specifier == "Bash":
                rules.append(Rule("shell:*", decision))
            elif served:
                rules.append(Rule(f"mcp:{served.group(1)}", decision))
            elif specifier in back:
                rules.append(Rule(back[specifier], decision))
            else:
                rules.append(Rule(f"tool:{specifier}", decision))
    rules.append(Rule(EVERYTHING, modes.get((native or {}).get("defaultMode"), "ask_user")))
    return rules


def claude_meta(folder: Path, bot) -> dict:
    """Claude Code is configured over the protocol, not by a file quorum can drop.

    Its bridge reads the user's own `~/.claude` — the machine's plugins, hooks and
    `defaultMode` included — unless the session says otherwise, and pointing
    `CLAUDE_CONFIG_DIR` at the bot folder takes the credentials away with the settings.
    So the bot folder holds the record, in Claude's own shape, and the session carries it:
    `settingSources: []` cuts the personal configuration without touching the login.
    """
    role = folder / "system.md"
    native = get_at(_read_json(folder / "settings.json"), ("permissions",)) or {}
    options: dict[str, Any] = {
        "settingSources": [],
        "permissionMode": "default",
        "allowDangerouslySkipPermissions": False,
        "allowedTools": list(native.get("allow") or []),
        "disallowedTools": list(native.get("deny") or []),
    }
    if bot.model:
        options["model"] = bot.model
    meta: dict[str, Any] = {"claudeCode": {"options": options}}
    if role.exists():
        meta["systemPrompt"] = role.read_text(encoding="utf-8")
    return meta


def _read_json(file: Path) -> dict:
    if not file.exists():
        return {}
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return document if isinstance(document, dict) else {}


# ── the table ───────────────────────────────────────────────────────────────────────

GEMINI = Harness(
    name="gemini",
    label="Google's CLI · a policy file, argv and environment",
    command="gemini",
    args=("--acp",),
    # Without the approval mode the agent decides alone and no permission ever comes up;
    # `-e` names an extension that does not exist, which is enough to load none of the
    # machine's own. (The A2A servers of the user settings do still load: no lever found.)
    extra=("--approval-mode", "default", "-e", "none"),
    detect=("gemini",),
    optional_env={
        "GEMINI_SYSTEM_MD": "${BOT_DIR}/system.md",
        "GEMINI_CLI_SYSTEM_SETTINGS_PATH": "${BOT_DIR}/settings.json",
    },
    # A bot does not inherit the machine's personal servers: the settings file is written
    # once, empty of them.
    scaffold={"settings.json": {"mcp": {"allowed": []}, "hooks": {}}},
    model=Argv(("-m", "{value}")),
    rules=Perms(
        "policy.toml", (), gemini_to_native, gemini_from_native,
        "the agent's read-only tools come before the default rule",
        argv=("--policy", "{value}"),
    ),
    outputs="gemini-telemetry",
)

OPENCODE = Harness(
    name="opencode",
    label="opencode · one JSON document, read from the top",
    command="opencode",
    args=("acp", "--pure"),
    detect=("opencode",),
    env={
        "OPENCODE_CONFIG": "${BOT_DIR}/opencode.json",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    },
    scaffold={"opencode.json": {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
    }},
    # Everything here is top level, and that is a measurement, not a reading of the manual.
    # An `agent` block with its own `prompt` and `permission` is never opened by the ACP
    # bridge: a bot configured that way answers, and runs its commands without asking.
    # See tools/harness_check.py — it is the check that caught it.
    prompt=Key("opencode.json", ("instructions",), "{value}", listed=True),
    model=(Key("opencode.json", ("model",)), Key("opencode.json", ("small_model",))),
    rules=Perms(
        "opencode.json", ("permission",),
        opencode_to_native, opencode_from_native,
        "opencode has no priority: the most precise key wins, not the first line",
    ),
)

CLAUDE_CODE = Harness(
    name="claude-code",
    label="Claude Code through its ACP bridge · configured in the session itself",
    command="npx",
    args=("-y", "@agentclientprotocol/claude-agent-acp@0.78.0"),
    detect=("claude-agent-acp", "claude-code-acp"),
    scaffold={"settings.json": {"permissions": {"allow": [], "ask": [], "deny": []}}},
    # The prompt and the model travel in the session, not in a file: see claude_meta.
    model=Key("settings.json", ("model",)),
    meta=claude_meta,
    rules=Perms(
        "settings.json", ("permissions",), claude_to_native, claude_from_native,
        "allowed, denied, and what is neither asks you — order does not count here",
    ),
)

OTHER = Harness(
    name="other",
    label="any other ACP agent · quorum launches it and configures nothing",
    command="",
    detect=(),
)

HARNESSES = (GEMINI, OPENCODE, CLAUDE_CODE, OTHER)
NAMES = [h.name for h in HARNESSES]
BY_NAME = {h.name: h for h in HARNESSES}


def by_name(name: str) -> Harness:
    """The row for a name. Anything unknown falls back to the row that promises nothing."""
    return BY_NAME.get(name or "", OTHER)


def detect(command: str, args: list[str] | None = None) -> str:
    """Names the harness of a bot written before this table existed."""
    haystack = " ".join([command] + list(args or []))
    for harness in HARNESSES:
        if any(mark in haystack for mark in harness.detect):
            return harness.name
    return OTHER.name


# ── reading and writing a bot's harness files ───────────────────────────────────────

def write(folder: Path, bot, role: str, rules: list[Rule]) -> dict[int, str]:
    """Writes the harness's own files, replacing only the keys this table claims.

    Returns the rules the harness cannot express, by index — they reach no file.
    """
    harness = by_name(bot.provider)
    if harness is OTHER:
        return {}
    folder.mkdir(parents=True, exist_ok=True)
    documents: dict[str, dict] = {}

    def document(name: str) -> dict:
        if name not in documents:
            file = folder / name
            current = None
            if file.exists():
                try:
                    current = json.loads(file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    # A file we cannot read is a file we must not flatten: a half-written
                    # hand edit stays as it is, and the save says so upstream.
                    raise
            if not isinstance(current, dict):
                current = _fill(harness.scaffold.get(name, {}), bot.name)
            documents[name] = current
        return documents[name]

    prompt_file = str((folder / "system.md").resolve())
    for carrier in harness.levers:
        value = prompt_file if carrier is harness.prompt or (
            isinstance(harness.prompt, tuple) and carrier in harness.prompt
        ) else (bot.model or "")
        if isinstance(carrier, Key) and value:
            shaped = carrier.shape.replace("{value}", value)
            put_at(document(carrier.file), _fill(carrier.path, bot.name),
                   [shaped] if carrier.listed else shaped)

    blocked: dict[int, str] = {}
    if harness.rules is not None:
        native, blocked = harness.rules.to_native(rules)
        if harness.rules.path:
            put_at(document(harness.rules.file), _fill(harness.rules.path, bot.name), native)
        else:
            (folder / harness.rules.file).write_text(native, encoding="utf-8")

    for name in harness.scaffold:
        document(name)
    for name, doc in documents.items():
        (folder / name).write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return blocked


def read_rules(folder: Path, bot) -> list[Rule] | None:
    """A bot's rules as its own harness holds them — None when it holds none at all."""
    harness = by_name(bot.provider)
    if harness.rules is None:
        return None
    file = folder / harness.rules.file
    if not file.exists():
        return [Rule(EVERYTHING, "ask_user")]
    text = file.read_text(encoding="utf-8")
    if not harness.rules.path:
        return harness.rules.from_native(text)
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return [Rule(EVERYTHING, "ask_user")]
    native = get_at(document, _fill(harness.rules.path, bot.name))
    return harness.rules.from_native(native) or [Rule(EVERYTHING, "ask_user")]


def extras(folder: Path, bot) -> tuple[list[str], dict[str, str]]:
    """The argv and environment a harness adds at launch. It writes nothing, ever."""
    harness = by_name(bot.provider)
    if harness is OTHER:
        return [], {}
    place = str(folder.resolve())
    args = [piece.replace("${BOT_DIR}", place) for piece in harness.extra]
    env = {key: value.replace("${BOT_DIR}", place) for key, value in harness.env.items()}
    for key, value in harness.optional_env.items():
        named = value.replace("${BOT_DIR}", place)
        if Path(named).exists():
            env[key] = named
    if harness.rules is not None and harness.rules.argv:
        policy = folder.resolve() / harness.rules.file
        if policy.exists():
            args += [piece.replace("{value}", str(policy)) for piece in harness.rules.argv]
    prompt_file = str((folder / "system.md").resolve())
    for carrier in harness.levers:
        value = prompt_file if carrier is harness.prompt or (
            isinstance(harness.prompt, tuple) and carrier in harness.prompt
        ) else (bot.model or "")
        if not value:
            continue
        if isinstance(carrier, Env):
            env[carrier.name] = carrier.shape.replace("{value}", value)
        elif isinstance(carrier, Argv):
            args += [piece.replace("{value}", value) for piece in carrier.flags]
    return args, env


def session_meta(folder: Path, bot) -> dict:
    """What `session/new` carries for this bot — empty for an agent that reads files."""
    harness = by_name(bot.provider)
    return harness.meta(folder, bot) if harness.meta is not None else {}
