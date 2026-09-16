# Quorum

One room, several agents, a single thread.

Quorum is a terminal application where you talk with several AI agents at once, in a single
conversation, like a team chat. Each bot has its role, its model, its tools and its
permissions. They really work — they read files, run commands, call MCP servers — and they
can call each other out with `@name`. Permission requests come up to the user, who allows,
refuses, or refuses and explains why.

```
◈ atlas / auth-rework · turn 12                               ◐2 ▸1 ·2

  ▌ you · 14:02
    @sonar look at how we validate tokens, and @audit tell me whether key
    rotation is correct. Do not touch anything for now.

  ▌ @sonar  research · 14:02  ◐ thinking · 0:18
    ▸ shell · ls src/auth/                    ✓ 0.2s · output +1.4s
        jwt.py  legacy/  tokens.py  __init__.py
    ▸ fs · reads src/auth/jwt.py              ✓ 0.4s
    ┊ Comparing Two Validation Paths

  ◆ @forge asks for permission                              ⇥ next request
    rm -rf .venv && uv sync
    1 ✓  Allow
    2 ✓✓ Allow for this session
    3 ✕  Reject
    1-9 decide · r refuse with a reason · esc back to typing

◇ type while they work…▏
◐@sonar  ◆@forge  ·@audit                     ^C interrupt · 0:18
```

## Install

```sh
git clone <this repo> && cd quorum
uv sync
```

You need at least one agent that speaks **ACP** (Agent Client Protocol):

| Provider | Command |
|---|---|
| Gemini CLI | `gemini --acp` |
| Claude Code | `npx @zed-industries/claude-code-acp` |
| Codex | `npx @zed-industries/codex-acp` |

Quorum is tied to none of them: a bot simply declares the command to launch.

## Run

```sh
uv run quorum          # home: resume a room, create one, manage the bots
uv run quorum demo     # open a room directly
```

**At home** — `↑↓` walk · `⏎` open · `n` new room · `b` new bot ·
`,` settings · `^Q` quit.

**In a room** — `⏎` send · `1`…`9` decide a permission · `r` refuse with a reason ·
`^C` interrupt the turn · `^R` watch the last active bot work · `^B` its card ·
`^O` set up the room · `^G` settings · `end` follow the stream ·
`^Q` back to home.

**In a bot card** — `⇥` next field · `^S` save · `esc` discard. In the permission table:
`a` add · `e` edit the pattern · `d` change the decision · `x` remove · `⇧↑↓` reorder.

## A bot is a folder

```
bots/forge/
  bot.toml       name, role, hue, command, model, capabilities
  system.md      its role — replaces the agent's system prompt
  policy.toml    its permissions — first matching rule wins
  settings.json  its settings — isolates the bot from the machine's personal MCP servers
```

Everything is editable by hand; the card (`^B`) writes exactly those files.

Whatever depends on the machine (company certificate, proxy) goes through the bot's `env`
block as `${VARIABLE}`, resolved from an unversioned `.env`. See `.env.example`.

## A room is a folder

```
rooms/demo/
  room.toml          members, work folder, chaining budget — written by a human
  transcript.jsonl   the thread, source of truth — not versioned
  state.json         sessions and read index per bot — written by the machine
```

**The transcript is the source of truth.** The agents' sessions are only their private
memory: a room whose sessions are dead stays readable, and the bots start again from the
thread.

## What is measured, and what does not work

These points come from trials against the real protocol, not from the documentation.

- **Command output is not in the ACP stream.** Neither it nor the content of files read.
  For Gemini, we pick it up from the local telemetry log — it arrives **~5 s after** the
  tool ends, in one block. The interface holds that delay (`✓ 0.6s · output on the way`)
  and says so when it never comes. Without that log, or with another provider, the thread
  shows the commands without their answers.
- **`session/load` resumes nothing with Gemini CLI 0.59.** The agent does announce
  `loadSession: true`, but answers "No previous sessions found for this project": the
  session is never written to disk. The resume code is there and tested; meanwhile, the
  transcript is what carries the memory — and it works.
- **A bot inherits the machine's personal settings.** A `settings.json` with
  `{"mcp": {"allowed": []}}` cuts the MCP servers, and `-e` with no valid extension cuts
  the extensions. The A2A servers of the user settings, however, still load.
- **The token count only exists at the end of a turn.** Nothing to show during it.
- **The agents' thoughts arrive in English**, as titled blocks. The thread keeps only the
  titles; the body opens in the reasoning screen.

## Verify

```sh
./verify.sh
```

Twelve checks, with no network and no model: a scripted fake ACP agent plays the scenarios
(permission, commented refusal, cancellation, rounds, resume, late outputs). No test
dependency — `assert` statements and a `__main__`.

## Choices

Python and [Textual](https://textual.textualize.io/), managed with `uv`. A single
dependency. No database: files. The ACP client is about 250 lines and depends on nothing.
Everything in English, interface and identifiers alike.
