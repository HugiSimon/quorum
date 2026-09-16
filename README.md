# Quorum

One room, several agents, a single thread.

Quorum is a terminal application where you talk with several AI agents at once, in a single
conversation, like a team chat. Each bot has its role, its model, its tools and its
permissions. They really work — they read files, run commands, call MCP servers — and they
can call each other out with `@name`. Permission requests come up to the user, who allows,
refuses, or refuses and explains why.

```
◈ pair / ~/dev/atlas · turn 12                                ◐1 ▸1

  ▌ you · 14:02
    @scout look at how we validate tokens, and @forge run the auth tests.
    Change nothing for now.

  ▌ @scout  research · 14:02  ◐ thinking · 0:18
    ┊ 6 earlier tools
    ▸ fs · reads src/auth/jwt.py              ✓ 0.4s
        def verify(token): …
    ┊ Comparing Two Validation Paths

  ◆ @forge asks for permission                             · 1 more waiting
    rm -rf .venv && uv sync
    1 ✓  Allow
    2 ✓✓ Allow for this session
    3 ✕  Reject
    1-9 decide · r refuse with a reason · esc back to typing

◇ type while they work…▏
◐@scout  ◆@forge                              ^C interrupt · 0:18
```

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/HugiSimon/quorum/master/install.sh | sh
```

Windows, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/HugiSimon/quorum/master/install.ps1 | iex
```

Nothing is compiled: the script installs [uv](https://docs.astral.sh/uv/) if it is missing,
then quorum as a tool — its own isolated environment, a `quorum` command in `~/.local/bin`.
To remove it: `uv tool uninstall quorum`.

You need at least one agent that speaks **ACP** (Agent Client Protocol):

| Provider | Command |
|---|---|
| Gemini CLI | `gemini --acp` |
| Claude Code | `npx @zed-industries/claude-code-acp` |
| Codex | `npx @zed-industries/codex-acp` |

Quorum is tied to none of them: a bot simply declares the command to launch.

## Run

```sh
cd ~/dev/my-project
quorum                 # home: resume a room, create one, manage the bots
quorum review          # open a room directly
```

**The bots work in the folder you launched it from.** Your rooms, bots and threads live in
`~/.quorum` — one folder, editable by hand, moved with `QUORUM_HOME`:

```
~/.quorum/
  bots/scout/…      a bot is a folder
  rooms/review/…    a room is a folder, with its thread
  runtime/          logs and separate copies — throwaway
  settings.json     what the settings screen writes
  .env              what is machine-specific, and nothing else
```

**At home** — `↑↓` walk · `⏎` open · `n` new room · `b` new bot ·
`,` settings · `^Q` quit.

**In a room** — `⏎` send · `1`…`9` decide a permission · `r` refuse with a reason ·
`^C` interrupt the turn · **click a block** to see how it was written ·
`^R` watch the last active bot work · `^B` its card ·
`^O` set up the room · `^G` settings · `end` follow the stream ·
`^Q` back to home.

**In a bot card** — `⇥` next field · `^S` save · `esc` discard. In the permission table:
`a` add · `e` edit the pattern · `d` change the decision · `x` remove · `⇧↑↓` reorder.

## What comes with it

Four bots, on `gemini --acp`, with the machine's personal MCP servers and extensions cut.
Rename them, change their model, rewrite their permissions — they are yours from the first
launch.

| bot | it can | it cannot |
|---|---|---|
| `@scout` | read, search, cite, with exact paths | write anything, run anything |
| `@forge` | `git`, `ls`, read the code | `push`, `reset`, `clean`, `rm`, `sudo` — those ask you |
| `@critic` | read the code and argue against it, ranked by severity | write anything, run anything |
| `@scribe` | write the README, the comment, the commit message | run a command |

And two rooms:

| room | members | what it is for |
|---|---|---|
| `review` | `@scout` `@critic` | reading a change apart: neither member can touch a file |
| `pair` | `@forge` `@scout` | one changes, the other checks |

A room whose `folder` is `.` — both of those — works wherever you launch quorum. Give it an
absolute path and it stays pinned to one project; the room screen (`^O`) writes one for you.

## A bot is a folder

```
~/.quorum/bots/forge/
  bot.toml       name, role, hue, command, model, capabilities
  system.md      its role — replaces the agent's system prompt
  policy.toml    its permissions — first matching rule wins
  settings.json  its settings — isolates the bot from the machine's personal MCP servers
```

Everything is editable by hand; the card (`^B`) writes exactly those files. Its permission
table reads one pattern per rule, and writes back exactly what it shows:

| pattern | the rule it writes |
|---|---|
| `shell:git *` | that root command, `commandPrefix` |
| `shell~push` | a regex over the arguments, `argsPattern` — `git push`, but not `git log` |
| `fs:read` `fs:write` `fs:replace` `fs:list` `fs:search` `fs:glob` | one file tool each |
| `net:fetch` `net:search` · `mcp:vault` | the network tools · one MCP server |
| `tool:anything_else` | a tool this table does not name — another provider's, an MCP one |
| `everything else` | no criteria: what is left. **The last line of every shipped bot, and it asks** |

Whatever depends on the machine (company certificate, proxy) goes through the bot's `env`
block as `${VARIABLE}`, resolved from `~/.quorum/.env`. See `.env.example`.

## A room is a folder

```
~/.quorum/rooms/review/
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
- **A policy file the engine cannot read is not an error.** `[[rules]]` instead of `[[rule]]`,
  or a rule without `toolName`, and it loads nothing at all — every tool then asks, which
  looks like a working policy and is not one. What is shipped is checked against the engine,
  not against the documentation.

## Working on quorum

```sh
git clone https://github.com/HugiSimon/quorum && cd quorum
QUORUM_HOME=$PWD/.home uv run quorum    # your own home, next to the code
./verify.sh
```

Twelve checks, with no network and no model: a scripted fake ACP agent plays the scenarios
(permission, commented refusal, cancellation, rounds, resume, late outputs). No test
dependency — `assert` statements and a `__main__`.

## Choices

Python and [Textual](https://textual.textualize.io/), managed with `uv`. A single
dependency. No database: files. The ACP client is about 250 lines and depends on nothing.
Everything in English, interface and identifiers alike.
