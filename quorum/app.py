"""The application: one room, several bots, a single thread.

The transcript is the source of truth. A bubble is created when a bot starts talking —
before it has any content — and fills in place; several may fill at the same time, and a
bot never inserts a line inside another one's block.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static

from . import bot as bots
from .screens import Home, BotCard, SettingsScreen, RoomScreen
from .acp import AcpError, AcpClient, write_file
from .room import (
    Entry,
    archive,
    write_state,
    read_state,
    thread_summary,
    split_thought,
    Room,
    Transcript,
    load_room,
    recipients,
    fingerprint,
    prompt_for,
    handoffs,
)
from . import settings as config
from . import telemetry
from .telemetry import GeminiOutputs, short_id
from .theme import (
    ANIM_THINK,
    apply_theme,
    ANIM_WORK,
    ATTENTION,
    CLICKABLE,
    STATES,
    FAMILIES,
    N,
    RED,
    GREEN,
    bot_color,
    divider,
    terminal_theme,
)

LIVE = ("thinking", "running", "asking")

# Beyond that, we consider the memory lost rather than leaving the room shut.
RESUME_TIMEOUT = 30.0

# The `kind` is the only stable thing: it decides the color and the place, never the label.
# The real agent sends allow_always first — keeping that order would put the widest
# permission under key 1.
CHOICE_ORDER = {"allow_once": 0, "allow_always": 1, "reject_once": 2, "reject_always": 3}


def _duration(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class Participant:
    """A room member: its bot, its process, its session, its current bubble."""

    def __init__(self, bot: bots.Bot) -> None:
        self.bot = bot
        self.color = bot_color(bot.hue)
        self.client: AcpClient | None = None
        self.session: dict = {}
        self.state = "out"
        self.bubble: Bubble | None = None
        # The bot's latest bubbles: an output arrives several seconds later, sometimes once
        # it has already bounced into a new block.
        self.bubbles: list[Bubble] = []
        self.panel: PermissionPanel | None = None
        self.turn: asyncio.Task | None = None
        self.seen = 0
        self.fingerprints: list[str] = []
        self.refusal: str | None = None  # comment to pass on to the next turn
        self.outputs: GeminiOutputs | None = None
        self.startup = "queued"
        self.memory_lost = False
        self.last_activity = 0.0
        self.folder: Path | None = None
        self.files: dict[str, str] = {}
        self.timeline: list[tuple[float, str]] = []
        self.turn_start = 0.0

    @property
    def repeats(self) -> bool:
        """Two identical messages in a row: the bot is going in circles, we cut."""
        return len(self.fingerprints) >= 2 and self.fingerprints[-1] == self.fingerprints[-2]

    @property
    def name(self) -> str:
        return self.bot.name

    @property
    def ready(self) -> bool:
        return bool(self.session) and self.client is not None and self.client.alive


class Bubble(Static):
    """One author's block: header, tools, body. The same template for everyone."""

    def __init__(
        self,
        author: str,
        role: str,
        color: str,
        clock: str,
        state: str | None = None,
        start: float | None = None,
    ) -> None:
        super().__init__()
        self.author, self.role, self.color, self.clock = author, role, color, clock
        self.state = state
        self.start = start
        self.body = ""
        self.tools: list[dict] = []
        self.steps: list[dict] = []
        self.owner: str | None = None
        self.thinking_visible = True
        self.thinking = "folded"
        self.compact = False
        self.phase = 0

    def draw(self) -> Text:
        text = Text()
        text.append("▌ ", style=self.color)
        text.append(self.author, style=f"bold {self.color}")
        if self.role and not self.compact:
            text.append(f"  {self.role}", style=N["dim"])
        text.append(f" · {self.relative_time if self.compact else self.clock}", style=N["faint"])

        if self.state is not None:
            glyph, word = STATES[self.state]
            if self.state == "thinking":
                glyph = ANIM_THINK[self.phase % len(ANIM_THINK)]
            elif self.state == "running":
                glyph = ANIM_WORK[self.phase % len(ANIM_WORK)]
            text.append(f"  {glyph} {word}", style=self.color)
            if self.state in LIVE and self.start is not None:
                text.append(f" · {_duration(time.monotonic() - self.start)}", style=N["faint"])
        text.append("\n")

        for tool in self.tools:
            text.append_text(self.tool_line(tool))

        for line in self.body.rstrip().splitlines():
            text.append(f"    {line}\n", style=N["ink"])

        # While it works we show the steps; once the message is written, it stands alone.
        done = self.state in ("done", "out", "failed")
        if self.thinking_visible and self.steps and not done and not self.compact:
            shown = (
                self.steps if self.thinking == "unfolded"
                else self.steps[-1:] if self.thinking == "last line"
                else []
            )
            for step in shown:
                text.append("  ┊ ", style=self.color)
                text.append(f"{step['title']}\n", style=N["dim"])

        if done and self.thinking_visible and (self.steps or self.tools):
            count = f"{len(self.tools)} tools"
            if self.steps:
                count += f" · {len(self.steps)} steps"
            text.append(f"    ┊ {count}\n", style=N["faint"])
        return text

    def tool_line(self, tool: dict) -> Text:
        """A tool line: family · title · duration, and the fate of its output.

        The output does not arrive with the end of the tool: one or two seconds later,
        through the local log. "output on the way" is the normal state, not an anomaly.
        """
        braille = ANIM_WORK[self.phase % len(ANIM_WORK)]
        text = Text()
        text.append("  ▸ ", style=self.color)
        if tool.get("family"):
            text.append(f"{tool['family']} · ", style=N["faint"])
        text.append(tool["title"], style=N["dim"])
        if not tool.get("end"):
            text.append(f"  {braille}\n", style=N["faint"])
            return text

        text.append(f"  ✓ {tool['end'] - tool['start']:.1f}s", style=N["faint"])
        if tool.get("output") is not None:
            late = tool.get("arrival", tool["end"]) - tool["end"]
            text.append(f" · output +{late:.1f}s\n", style=N["faint"])
            for line in str(tool["output"]).rstrip().splitlines()[:12]:
                text.append(f"      {line}\n", style=N["dim"])
        elif tool.get("awaiting_output"):
            text.append(f" · output on the way {braille}\n", style=N["faint"])
        elif tool.get("output_lost"):
            text.append(" · output never came\n", style=N["faint"])
        else:
            text.append("\n")
        return text

    def redraw(self) -> None:
        self.update(self.draw())

    @property
    def relative_time(self) -> str:
        """Under 100 columns the timestamp goes relative: two characters instead of five."""
        if self.start is None:
            return self.clock
        minutes = int((time.monotonic() - self.start) // 60)
        return "now" if minutes < 1 else f"{minutes} min ago"

    @property
    def plain_text(self) -> str:
        return f"{self.author} {self.body}"

    def tool_titles(self) -> list[str]:
        return [f"{t.get('family', '')} {t['title']}".strip() for t in self.tools]


class PermissionPanel(Static):
    """The choices come from the agent: labels as they are, placement set by the design.

    Nothing is hard-coded: the number of options varies from one tool to the next and from
    one provider to another. Several bots may each have one — ⇥ moves to the next.
    """

    can_focus = True

    def __init__(self, participant: Participant, params: dict, answer: asyncio.Future) -> None:
        super().__init__()
        self.participant = participant
        self.options = sorted(
            params.get("options", []),
            key=lambda o: CHOICE_ORDER.get(str(o.get("kind", "")), 4),
        )
        self.call = params.get("toolCall", {})
        self.answer = answer
        self.decision: str | None = None
        self.phase = 0

    def on_mount(self) -> None:
        self.redraw()

    def redraw(self) -> None:
        blink = "◆" if self.decision or self.phase % 2 == 0 else " "
        text = Text()
        text.append(f"  {blink} ", style=ATTENTION)
        text.append(f"@{self.participant.name}", style=f"bold {self.participant.color}")
        text.append(" asks for permission", style=ATTENTION)
        text.append("\n", style=N["faint"])

        title = self.call.get("title") or self.call.get("toolCallId", "")
        if title:
            text.append(f"    {title}\n", style=N["ink"])

        if self.decision:
            text.append(f"    → {self.decision}\n", style=N["dim"])
        elif self.app.compact:
            text.append("    ")
            for i, option in enumerate(self.options, 1):
                kind = str(option.get("kind", ""))
                refused = kind.startswith("reject")
                mark = ("✕" if refused else "✓") * (2 if kind.endswith("always") else 1)
                verb = str(option.get("name", option.get("optionId", ""))).split()[0].lower()
                text.append(f"{i} ", style=CLICKABLE)
                text.append(f"{mark}{verb}", style=RED if refused else GREEN)
                text.append(" · ", style=N["faint"])
            text.append("r comment\n", style=N["faint"])
        else:
            for i, option in enumerate(self.options, 1):
                kind = str(option.get("kind", ""))
                refused = kind.startswith("reject")
                mark = ("✕" if refused else "✓") * (2 if kind.endswith("always") else 1)
                text.append(f"    {i} ", style=CLICKABLE)
                text.append(f"{mark} ", style=RED if refused else GREEN)
                text.append(f"{option.get('name', option.get('optionId'))}\n", style=N["ink"])
            text.append("    1-9 ", style=CLICKABLE)
            text.append("decide   ", style=N["dim"])
            text.append("r ", style=CLICKABLE)
            text.append("refuse with a reason   ", style=N["dim"])
            text.append("⇥ ", style=CLICKABLE)
            text.append("next request   ", style=N["dim"])
            text.append("esc ", style=CLICKABLE)
            text.append("back to typing\n", style=N["dim"])
        self.update(text)

    def give_back_input(self) -> None:
        """Hands the focus back to the input, if it is still there."""
        inputs = self.screen.query("#message")
        if inputs:
            inputs.first(Input).focus()

    def on_focus(self) -> None:
        self.redraw()

    def on_blur(self) -> None:
        self.redraw()

    def on_key(self, event) -> None:
        if self.decision is not None:
            return
        if event.key == "escape":
            self.give_back_input()
            event.stop()
        elif event.key == "r":
            event.stop()
            refusal = next(
                (o for o in self.options if str(o.get("kind", "")) == "reject_once"), None
            )
            if refusal is not None:
                self.app.open_refusal(self, refusal)
        elif event.key.isdigit() and 1 <= int(event.key) <= len(self.options):
            self.choose(self.options[int(event.key) - 1])
            event.stop()

    def choose(self, option: dict) -> None:
        self.decision = option.get("name", option.get("optionId"))
        if not self.answer.done():
            self.answer.set_result({"outcome": "selected", "optionId": option["optionId"]})
        self.redraw()
        self.give_back_input()

    def refuse(self, option: dict, comment: str) -> None:
        """Refuses, then passes the comment to the bot as a prompt — and to the thread as a line."""
        self.participant.refusal = comment or None
        if comment:
            self.app.log_refusal(self.participant, comment)
        self.choose(option)

    def abandon(self) -> None:
        """Cancelling already answered the agent: here we keep the trace and give the input back.

        Without that last gesture, the focus stays on a dead panel and nothing gets written.
        """
        if self.decision is None:
            self.decision = "cancelled"
            self.redraw()
        self.give_back_input()


class RefusalBox(Vertical):
    """The refusal comment goes to the bot, and lands in the thread too.

    The permission answer has no text field: the comment cannot travel there. It leaves as
    a prompt right after, and the thread keeps the trace for the other bots.
    """

    def __init__(self, panel: "PermissionPanel", option: dict) -> None:
        super().__init__()
        self.panel, self.option = panel, option

    def compose(self) -> ComposeResult:
        header = Static()
        header.update(
            Text.assemble(
                ("  ✕ ", RED),
                (f"refuse @{self.panel.participant.name}'s request", N["ink"]),
                (" — tell it why (optional)\n", N["dim"]),
                ("    ⏎ refuse and send · esc back to the choices", N["faint"]),
            )
        )
        yield header
        yield Input(placeholder="…", id="refusal")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.panel.refuse(self.option, event.value.strip())
        self.remove()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.panel.focus()
            self.remove()



class StartupPanel(Static):
    """S3: the thread shows up right away, the members stand up one by one."""

    def __init__(self, participants: dict) -> None:
        super().__init__()
        self.participants = participants
        self.phase = 0

    def redraw(self) -> None:
        self.phase += 1
        text = Text()
        text.append_text(divider("STARTING UP", 60))
        for participant in self.participants.values():
            mark = {
                "queued": ("·", N["faint"]),
                "connecting": (ANIM_THINK[self.phase % len(ANIM_THINK)], participant.color),
                "session": (ANIM_THINK[self.phase % len(ANIM_THINK)], participant.color),
                "resumed": ("✓", GREEN),
                "fresh": ("✓", GREEN),
                "evicted": ("◌", N["faint"]),
                "failed": ("✕", RED),
            }.get(participant.startup, ("·", N["faint"]))
            text.append("  ▌ ", style=participant.color)
            text.append(f"@{participant.name}", style=f"bold {participant.color}")
            detail = {
                "queued": "queued",
                "connecting": "connecting to the provider",
                "session": "opening the session",
                "resumed": "context resumed",
                "fresh": "fresh session",
                "evicted": "memory released",
                "failed": "could not start",
            }.get(participant.startup, participant.startup)
            text.append(f"  {detail} ", style=N["dim"])
            text.append(f"{mark[0]}\n", style=mark[1])
        self.update(text)
        self.plain_text = text.plain


class ExpiryPanel(Static):
    """S10: the bots lost their working memory; the thread itself is intact."""

    can_focus = True

    CHOICES = ("re-read the thread and start from there", "summarise the thread for them",
               "archive")

    def __init__(self, names: list[str], messages: int) -> None:
        super().__init__()
        self.names, self.messages = names, messages
        self.plain_text = ""

    def on_mount(self) -> None:
        text = Text()
        text.append("  ◌ memory expired · ", style=ATTENTION)
        text.append(", ".join(f"@{n}" for n in self.names), style=N["ink"])
        text.append(
            f"\n    the thread is intact ({self.messages} messages), their working memory is not.\n",
            style=N["dim"],
        )
        for i, choice in enumerate(self.CHOICES, 1):
            text.append(f"    {i} ", style=CLICKABLE)
            text.append(f"{choice}\n", style=N["ink"])
        text.append("    esc back to typing — the thread stays readable\n", style=N["faint"])
        self.update(text)
        self.plain_text = text.plain
        self.call_after_refresh(self.focus)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            inputs = self.screen.query("#message")
            if inputs:
                inputs.first(Input).focus()
        elif event.key in ("1", "2", "3"):
            event.stop()
            self.app.settle_expiry(int(event.key), self.names)
            self.remove()


class ThinkingScreen(Screen):
    """S7: a bot's work, live and full screen.

    Steps and tools mingle in the order they arrived. Nothing about tokens: the count only
    exists at the end of the turn.
    """

    CSS = """
    ThinkingScreen { background: $bg; }
    #detail { padding: 1 2; }
    #footer { height: 1; padding: 0 2; color: $dim; background: $panel; }
    """

    BINDINGS = [
        Binding("escape", "close", "back to the thread", priority=True),
        Binding("tab", "next", "next reasoning", priority=True),
    ]

    def __init__(self, bot_name: str) -> None:
        super().__init__()
        self.bot_name = bot_name
        self.last_render = Text()

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(id="detail")
        yield Static(id="footer")

    def on_mount(self) -> None:
        self.set_interval(0.16, self.redraw)
        self.redraw()

    def action_close(self) -> None:
        self.app.pop_screen()

    def action_next(self) -> None:
        names = [n for n, p in self.app.participants.items() if p.bubble is not None]
        if len(names) > 1:
            self.bot_name = names[(names.index(self.bot_name) + 1) % len(names)] if self.bot_name in names else names[0]
            self.redraw()

    def redraw(self) -> None:
        participant = self.app.participants[self.bot_name]
        bubble = participant.bubble
        width = max(40, self.size.width - 4)
        text = Text()

        glyph, word = STATES[participant.state]
        text.append("▌ ", style=participant.color)
        text.append(f"@{participant.name}", style=f"bold {participant.color}")
        text.append(" · reasoning", style=N["dim"])
        if bubble is not None:
            text.append(
                f"    {glyph} {word} · {_duration(time.monotonic() - participant.turn_start)}"
                f" · {len(bubble.tools)} tools\n\n",
                style=N["faint"],
            )
        else:
            text.append("    nothing in flight\n\n", style=N["faint"])

        if bubble is not None:
            text.append_text(
                divider("STEPS", width, "titles emitted by the agent · English, in whole blocks")
            )
            text.append_text(self.interleave(bubble, participant, width))
            text.append("\n")

            text.append_text(divider("FILES TOUCHED", width))
            if participant.files:
                for path, action in participant.files.items():
                    text.append(f"  {path}", style=N["ink"])
                    text.append(f"  {action}\n", style=N["faint"])
            else:
                text.append("  none so far\n", style=N["faint"])
            text.append("\n")

            text.append_text(divider("TIMELINE", width))
            for moment, what in participant.timeline[-12:]:
                text.append(f"  {_duration(moment)} ", style=N["faint"])
                text.append(f"{what}\n", style=N["dim"])
            text.append("\n")

        text.append_text(divider("OTHER BOTS", width))
        for other in self.app.participants.values():
            if other.name == self.bot_name:
                continue
            sign, meaning = STATES[other.state]
            text.append(f"  {sign} ", style=other.color)
            text.append(f"@{other.name} {meaning}\n", style=N["dim"])
        text.append("\n")
        text.append_text(
            divider("TOKENS", width, "the count only exists at the end of the turn")
        )
        self.last_render = text
        self.query_one("#detail", Static).update(text)

        footer = Text()
        footer.append("esc back to the thread · ⇥ next reasoning", style=N["faint"])
        footer.append(f"    ^C stop @{self.bot_name} alone", style=N["faint"])
        self.query_one("#footer", Static).update(footer)

    def interleave(self, bubble: Bubble, participant: Participant, width: int) -> Text:
        """Steps and tools in arrival order — the last step opens, the others do not."""
        events: list[tuple[float, str, object]] = [
            (step["t"], "step", step) for step in bubble.steps
        ]
        events += [
            (tool["start"] - participant.turn_start, "tool", tool) for tool in bubble.tools
        ]
        events.sort(key=lambda e: e[0])
        last = bubble.steps[-1] if bubble.steps else None

        text = Text()
        for moment, kind, item in events:
            if kind == "tool":
                text.append_text(bubble.tool_line(item))
                continue
            open_step = item is last
            text.append("  ▾ " if open_step else "  ┊ ", style=participant.color)
            text.append(item["title"], style=N["ink"] if open_step else N["dim"])
            text.append(f"  {_duration(moment)}\n", style=N["faint"])
            if open_step and item["body"]:
                for line in item["body"].splitlines():
                    text.append(f"      {line}\n", style=N["dim"])
        if participant.state == "thinking" and bubble.body == "":
            text.append(
                f"  {ANIM_WORK[bubble.phase % len(ANIM_WORK)]} writing its answer…\n",
                style=N["faint"],
            )
        return text


class Quorum(App):
    CSS = """
    Screen { background: $bg; color: $ink; }
    #header { height: 1; padding: 0 2; color: $dim; background: $panel; }
    #backlog { height: 1; padding: 0 2; color: $attention; }
    #middle { height: 1fr; }
    #thread { width: 1fr; padding: 1 2; }
    #side { width: 46; padding: 1 2; background: $panel; }
    #thread > Static { margin-bottom: 1; }
    #thread > Static:focus { background: $panel; }
    #thread.compact > Static { margin-bottom: 0; }
    #status { height: 1; padding: 0 2; color: $dim; background: $panel; }
    #input { height: 1; background: $frame; }
    #chevron { width: 4; padding: 0 0 0 2; color: $clickable; text-style: bold;
               background: $frame; }
    Input { border: none; background: $frame; padding: 0; height: 1; color: $ink; }
    Input > .input--placeholder { color: $dim; }
    #thread { scrollbar-size-vertical: 1; scrollbar-color: $frame; scrollbar-color-hover: $dim;
              scrollbar-color-active: $clickable; scrollbar-background: $bg;
              scrollbar-background-hover: $bg; scrollbar-background-active: $bg; }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "interrupt the turn", priority=True),
        Binding("ctrl+q", "quit", "quit", priority=True),
        Binding("end", "follow", "follow the stream", priority=True),
        Binding("ctrl+r", "thinking", "reasoning of the last active bot", priority=True),
        Binding("ctrl+b", "bot_card", "bot card", priority=True),
        Binding("ctrl+o", "compose", "set up the room", priority=True),
        Binding("ctrl+g", "settings", "settings", priority=True),
    ]

    def __init__(self, room: Room, known_bots: dict[str, bots.Bot]) -> None:
        super().__init__()
        # The theme first: a participant's color is computed when it is created.
        self.settings = config.read()
        apply_theme(self.wanted_theme())
        self.room = room
        self.transcript = Transcript(room.root / "transcript.jsonl")
        self.participants = {
            name: Participant(known_bots[name]) for name in room.members if name in known_bots
        }
        self.missing = [name for name in room.members if name not in known_bots]
        self.unread = 0
        self.turn_start = 0.0
        self.conversation: asyncio.Task | None = None
        self.interrupted = False
        self.room_state = read_state(room)
        self.startup: StartupPanel | None = None

    def wanted_theme(self) -> bool:
        choice = self.settings.get("theme", "follow terminal")
        return terminal_theme() if choice == "follow terminal" else choice == "dark"

    def get_css_variables(self) -> dict[str, str]:
        """The palette goes into the CSS variables: one single place to switch."""
        return {
            **super().get_css_variables(),
            "bg": N["bg"], "panel": N["panel"], "frame": N["frame"],
            "ink": N["ink"], "dim": N["dim"], "faint": N["faint"],
            "attention": ATTENTION, "clickable": CLICKABLE,
        }

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield Static(id="backlog")
        with Horizontal(id="middle"):
            yield VerticalScroll(id="thread")
            yield Static(id="side")
        yield Static(id="status")
        with Horizontal(id="input"):
            yield Static("◇ ", id="chevron")
            yield Input(placeholder="type here — ⏎ send · @name to aim at a bot", id="message")

    async def on_mount(self) -> None:
        self.query_one("#backlog", Static).display = False
        self.query_one("#header", Static).update(
            Text(f"◈ quorum · {self.room.name} · {self.room.folder}", style=N["dim"])
        )
        for entry in self.transcript.entries:
            await self.add(self.past_bubble(entry))
        if self.transcript.entries:
            await self.add(
                self.notice(
                    f"{len(self.transcript.entries)} message"
                    f"{'s' if len(self.transcript.entries) > 1 else ''} restored",
                    N["faint"],
                )
            )
        for name in self.missing:
            await self.add(self.notice(f"✕ no bot named « {name} » in bots/", RED))
        self.startup = StartupPanel(self.participants)
        await self.add(self.startup)
        self.set_interval(0.16, self.beat)
        if self.room.evict_minutes:
            self.set_interval(30.0, self.evict_idle)
        self.run_worker(self.start_all())

    # ── display ─────────────────────────────────────────────────────────────────────

    def notice(self, text: str, color: str) -> Static:
        """An announcement line in the thread. `plain_text` makes it readable without Rich."""
        block = Static()
        block.plain_text = text
        block.update(Text(f"  {text}", style=color))
        return block

    def past_bubble(self, entry: Entry) -> Static:
        """An entry read back from disk: frozen, no timer, with the tool count."""
        if entry.kind == "system":
            return self.notice(f"^C {entry.text} · {entry.clock}", N["faint"])
        if entry.kind == "user":
            bubble = Bubble(entry.author, "", N["ink"], entry.clock)
        else:
            participant = self.participants.get(entry.author)
            color = participant.color if participant else N["dim"]
            role = participant.bot.role if participant else ""
            bubble = Bubble(f"@{entry.author}", role, color, entry.clock, state="done")
            bubble.owner = entry.author if participant else None
        bubble.body = entry.text
        if entry.tools:
            bubble.body = f"({len(entry.tools)} tools) " + bubble.body
        bubble.redraw()
        return bubble

    async def add(self, widget: Static) -> None:
        """Mounts a block at the bottom of the thread, and only follows if we are there."""
        thread = self.query_one("#thread", VerticalScroll)
        at_bottom = thread.scroll_offset.y >= thread.max_scroll_y - 1
        await thread.mount(widget)
        if at_bottom:
            thread.scroll_end(animate=False)
        else:
            self.unread += 1

    @property
    def compact(self) -> bool:
        """Under 100 columns, everything is shortened — never removed."""
        density = self.settings.get("density", "automatic")
        if density == "compact":
            return True
        return density == "automatic" and self.size.width < 100

    def beat(self) -> None:
        """A single beat for the whole interface: three movements, not one more."""
        if self.startup is not None:
            self.startup.redraw()
            if all(p.startup in ("resumed", "fresh", "failed") for p in self.participants.values()):
                # Starting up is a state, not a trace: it fades once it is over.
                if not any(p.startup == "failed" for p in self.participants.values()):
                    self.startup.remove()
                self.startup = None
        for participant in self.participants.values():
            if participant.bubble is not None and participant.bubble.state in LIVE:
                participant.bubble.phase += 1
                participant.bubble.redraw()
            if participant.panel is not None and participant.panel.decision is None:
                participant.panel.phase += 1
                if not participant.panel.has_focus:
                    participant.panel.redraw()

        # The thread is not always there: a reasoning screen may sit on top, and on closing
        # the widgets go before the timer.
        threads, backlogs = self.query("#thread"), self.query("#backlog")
        if not threads or not backlogs:
            return
        thread = threads.first(VerticalScroll)
        thread.set_class(self.compact, "compact")
        for bubble in self.query(Bubble):
            if bubble.compact != self.compact:
                bubble.compact = self.compact
                bubble.redraw()
        self.paint_side()
        if thread.scroll_offset.y >= thread.max_scroll_y - 1:
            self.unread = 0
        backlog_banner = backlogs.first(Static)
        if self.unread:
            backlog_banner.display = True
            backlog_banner.update(
                Text(
                    f"↑ you are reading back — the thread will not move    "
                    f"{self.unread} new messages ↓ end",
                    style=ATTENTION,
                )
            )
        else:
            backlog_banner.display = False
        self.paint_status()

    def paint_side(self) -> None:
        """Beyond 160 columns the margins become useful: the room and the active bot."""
        sides = self.query("#side")
        if not sides:
            return
        side = sides.first(Static)
        side.display = self.size.width >= 160
        if not side.display:
            return
        text = Text()
        text.append_text(divider("IN THIS ROOM", 42))
        for participant in self.participants.values():
            glyph, word = STATES[participant.state]
            text.append(f"  {glyph} ", style=participant.color)
            text.append(f"@{participant.name:<10}", style=participant.color)
            text.append(f"{word}\n", style=N["dim"])
        text.append("\n")

        active = next(
            (p for p in self.participants.values()
             if p.bubble is not None and p.state in LIVE), None
        )
        if active is None:
            text.append_text(divider("REASONING", 42))
            text.append("  nobody is working\n", style=N["faint"])
        else:
            text.append_text(divider(f"@{active.name}", 42, "live"))
            for step in active.bubble.steps[-4:]:
                text.append("  ┊ ", style=active.color)
                text.append(f"{step['title'][:36]}\n", style=N["dim"])
            for tool in active.bubble.tools[-4:]:
                text.append("  ▸ ", style=active.color)
                text.append(f"{tool['title'][:34]}", style=N["dim"])
                text.append(" ✓\n" if tool["end"] else " ◆\n", style=N["faint"])
        side.update(text)

    def paint_status(self) -> None:
        """One glyph and one name per member, and on the right what interrupting costs."""
        left = Text()
        for participant in self.participants.values():
            glyph, _ = STATES[participant.state]
            if participant.state == "thinking":
                glyph = ANIM_THINK[(participant.bubble.phase if participant.bubble else 0) % len(ANIM_THINK)]
            left.append(f"{glyph}", style=participant.color)
            left.append(f"@{participant.name}  ", style=N["dim"])

        busy = [p for p in self.participants.values() if p.turn is not None and not p.turn.done()]
        right = (
            f"^C interrupt · {_duration(time.monotonic() - self.turn_start)}"
            if busy
            else "^C interrupt · ^Q quit"
        )
        padding = max(1, self.size.width - 4 - left.cell_len - len(right))
        left.append(" " * padding)
        left.append(right, style=N["faint"])
        bars = self.query("#status")
        if bars:
            bars.first(Static).update(left)

    # ── agents ──────────────────────────────────────────────────────────────────────

    async def start_all(self) -> None:
        """The members stand up in parallel; the input opens as soon as one is ready."""
        await asyncio.gather(*(self.start_bot(p) for p in self.participants.values()))
        lost = [p.name for p in self.participants.values() if p.memory_lost]
        if lost and self.transcript.entries:
            await self.add(ExpiryPanel(lost, len(self.transcript.entries)))

    def focus_input(self) -> None:
        """The input opens as soon as the first bot is ready — not when everyone is up."""
        inputs = self.query("#message")
        if inputs and not inputs.first(Input).has_focus:
            inputs.first(Input).focus()

    async def folder_for(self, participant: Participant) -> Path:
        """A bot's work folder: shared, or its own copy if it writes.

        Outside a git repository there is no worktree possible: we share the folder and say
        so, rather than pretending an isolation that does not exist.
        """
        if not participant.bot.own_copy:
            return self.room.folder
        copy = self.room.root.parents[1] / "runtime" / self.room.name / "copies" / participant.name
        if copy.exists():
            return copy
        copy.parent.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "--detach", str(copy),
            cwd=str(self.room.folder),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, error = await process.communicate()
        if process.returncode == 0:
            await self.add(
                self.notice(f"· @{participant.name} works in its own copy {copy.name}", N["faint"])
            )
            return copy
        await self.add(
            self.notice(
                f"· @{participant.name}: no separate copy possible here "
                f"({error.decode(errors='replace').strip().splitlines()[-1:] or ['outside a git repository']}"
                f") — shared folder and a strict policy",
                ATTENTION,
            )
        )
        return self.room.folder

    async def start_bot(self, participant: Participant) -> None:
        participant.startup = "connecting"
        participant.folder = await self.folder_for(participant)
        project_env = bots.read_env(self.room.root.parents[1] / ".env")
        workshop = self.room.root.parents[1] / "runtime" / self.room.name
        telemetry_log = getattr(self, "telemetry_log", None) or (
            workshop / f"{participant.name}.telemetry.jsonl"
        )
        outputs = (
            self.room.keep_outputs and participant.bot.provider == "gemini"
        )
        if not outputs:
            telemetry_log = None
        else:
            # Strictly before the launch: deleting the file afterwards would leave the agent
            # writing into a removed inode, and no output would ever come back.
            telemetry_log.parent.mkdir(parents=True, exist_ok=True)
            telemetry_log.unlink(missing_ok=True)
        command, args, env = bots.launch(participant.bot, project_env, telemetry_log)
        participant.client = AcpClient(
            lambda message, p=participant: self.agent_notification(p, message),
            lambda params, p=participant: self.ask_permission(p, params),
            lambda params, p=participant: self.agent_write(p, params),
        )
        log = workshop / f"{participant.name}.stderr.log"
        try:
            await participant.client.start(command, args, env, participant.folder, log)
            capabilities = await participant.client.initialize()
            participant.startup = "session"
            await self.open_session(participant, capabilities)
        except (OSError, AcpError) as error:
            participant.state = "failed"
            participant.startup = "failed"
            await self.add(
                self.notice(f"✕ @{participant.name} could not start — {error}", RED)
            )
            return
        participant.state = "idle"
        participant.last_activity = time.monotonic()
        self.focus_input()
        if telemetry_log is not None:
            participant.outputs = GeminiOutputs(
                telemetry_log,
                lambda call_id, output, p=participant: self.tool_output(p, call_id, output),
            )
            participant.outputs.start()

    async def open_session(self, participant: Participant, capabilities: dict) -> None:
        """Resumes the bot's session if it still exists, otherwise opens a fresh one.

        `session/load` replays the whole history as notifications: the bot's private
        context rebuilds itself without sending anything. If the session expired, we start
        over and the thread says so — that is state S10.
        """
        previous = self.room_state["sessions"].get(participant.name)
        can_load = bool((capabilities.get("agentCapabilities") or {}).get("loadSession"))
        if previous and can_load:
            try:
                # An agent that ignores session/load would leave the room shut forever.
                resumed = await asyncio.wait_for(
                    participant.client.load_session(previous, participant.folder), RESUME_TIMEOUT
                )
                participant.session = {"sessionId": previous, **(resumed or {})}
                participant.seen = int(self.room_state["seen"].get(participant.name, 0))
                participant.startup = "resumed"
                return
            except (AcpError, asyncio.TimeoutError):
                participant.memory_lost = True

        participant.session = await participant.client.new_session(participant.folder)
        participant.seen = 0
        participant.startup = "fresh"
        self.room_state["sessions"][participant.name] = participant.session["sessionId"]
        self.room_state["seen"][participant.name] = 0
        write_state(self.room, self.room_state)

    async def ensure_ready(self, participant: Participant) -> bool:
        """Wakes an evicted bot before its turn; does nothing if it is already there."""
        if participant.ready:
            return True
        if participant.client is not None:
            await participant.client.close()
        participant.client = None
        participant.session = {}
        await self.start_bot(participant)
        return participant.ready

    def evict_idle(self) -> None:
        """An idle bot gives back its live memory; its session stays on disk."""
        threshold = self.room.evict_minutes * 60
        for participant in self.participants.values():
            busy = participant.turn is not None and not participant.turn.done()
            if busy or not participant.ready or not participant.last_activity:
                continue
            if time.monotonic() - participant.last_activity > threshold:
                self.run_worker(self.evict(participant))

    async def evict(self, participant: Participant) -> None:
        if participant.outputs is not None:
            participant.outputs.stop()
            participant.outputs = None
        if participant.client is not None:
            await participant.client.close()
        participant.client = None
        participant.session = {}
        participant.state = "out"
        participant.startup = "evicted"
        await self.add(
            self.notice(f"◌ @{participant.name} evicted — its next turn wakes it up", N["faint"])
        )

    def settle_expiry(self, choice: int, names: list[str]) -> None:
        """The three ways out of S10: start from the thread, have it summarised, or archive it."""
        if choice == 1:
            self.status_note("· the bots start again from the whole thread")
        elif choice == 2:
            self.conversation = asyncio.create_task(self.ask_summary(names))
        elif choice == 3:
            self.run_worker(self.archive_thread())

    async def ask_summary(self, names: list[str]) -> None:
        """A summary written by the bots themselves, then the thread starts again from there."""
        targets = [self.participants[n] for n in names if self.participants[n].ready]
        if not targets:
            return
        for participant in targets:
            participant.bubble = self.new_bubble(participant)
            await self.add(participant.bubble)
        text = thread_summary(self.transcript.entries)
        for participant in targets:
            participant.turn = asyncio.create_task(self.speak(participant, text))
        await asyncio.gather(*(p.turn for p in targets), return_exceptions=True)

    async def archive_thread(self) -> None:
        target = archive(self.room)
        self.transcript = Transcript(self.room.root / "transcript.jsonl")
        self.room_state = read_state(self.room)
        for participant in self.participants.values():
            participant.seen = 0
            participant.memory_lost = False
        await self.add(
            self.notice(f"· thread archived in {target.name if target else '—'}", N["faint"])
        )

    def tool_output(self, participant: Participant, call_id: str, output: str) -> None:
        """Links an output from the log to the tool line waiting for it."""
        for bubble in reversed(participant.bubbles[-5:]):
            for tool in bubble.tools:
                if short_id(str(tool["id"])) == call_id and tool["output"] is None:
                    tool["output"] = output
                    tool["arrival"] = time.monotonic()
                    tool["awaiting_output"] = False
                    tool["output_lost"] = False  # it came in the end: we take it back
                    if not tool["end"]:
                        tool["end"] = tool["arrival"]
                    bubble.redraw()
                    return

    def agent_notification(self, participant: Participant, message: dict) -> None:
        """Called from an agent's read loop: fast, and nothing that waits."""
        if message.get("method") != "session/update" or participant.bubble is None:
            return
        params = message.get("params") or {}
        update = params.get("update", params)
        kind = update.get("sessionUpdate")
        bubble = participant.bubble

        if kind == "agent_thought_chunk":
            participant.state = bubble.state = "thinking"
            self.note_thought(participant, (update.get("content") or {}).get("text", ""))
        elif kind == "agent_message_chunk":
            participant.state = bubble.state = "thinking"
            bubble.body += (update.get("content") or {}).get("text", "")
        elif kind == "tool_call":
            participant.state = bubble.state = "running"
            tool_kind = str(update.get("kind", "other"))
            bubble.tools.append({
                "id": update.get("toolCallId"),
                "family": FAMILIES.get(tool_kind, "tool"),
                "title": update.get("title") or tool_kind,
                "start": time.monotonic(),
                "end": None,
                "output": None,
                "awaiting_output": False,
                "output_lost": False,
            })
            self.note_files(participant, tool_kind, update.get("locations") or [])
            participant.timeline.append(
                (time.monotonic() - participant.turn_start, update.get("title") or tool_kind)
            )
        elif kind == "tool_call_update":
            self.note_files(participant, str(update.get("kind", "other")), update.get("locations") or [])
            for tool in bubble.tools:
                done = update.get("status") in ("completed", "failed")
                if tool["id"] == update.get("toolCallId") and done and not tool["end"]:
                    tool["end"] = time.monotonic()
                    tool["awaiting_output"] = participant.outputs is not None
            if all(t["end"] for t in bubble.tools):
                participant.state = bubble.state = "thinking"
        bubble.redraw()

    def note_thought(self, participant: Participant, text: str) -> None:
        """Steps stack up; a block without a title extends the body of the current step."""
        bubble = participant.bubble
        assert bubble is not None
        rest, fresh = split_thought(text)
        if rest and bubble.steps:
            bubble.steps[-1]["body"] += ("\n" if bubble.steps[-1]["body"] else "") + rest
        for title, body in fresh:
            bubble.steps.append({"title": title, "body": body,
                                 "t": time.monotonic() - participant.turn_start})
            participant.timeline.append((bubble.steps[-1]["t"], f"thinking · {title}"))

    def note_files(self, participant: Participant, kind: str, locations: list[dict]) -> None:
        """`locations` is filled for reads and writes: the panel stays up to date."""
        action = {"read": "read", "edit": "changed", "delete": "deleted", "move": "moved"}
        for location in locations:
            path = location.get("path")
            if path:
                participant.files[path] = action.get(kind, kind)

    async def agent_write(self, participant: Participant, params: dict) -> None:
        """Guard: a write outside the room folder goes through a permission request.

        The agent asks the client to write; without this check, it writes anywhere on the
        disk with nothing coming back. The setting can remove it, but it is on by default.
        """
        path = Path(params["path"]).resolve()
        root = (participant.folder or self.room.folder).resolve()
        outside = not path.is_relative_to(root)
        if outside and self.settings.get("ask_outside_folder", True):
            answer = await self.ask_permission(participant, {
                "toolCall": {"title": f"write outside the room folder: {path}"},
                "options": [
                    {"optionId": "yes", "name": "Write this file", "kind": "allow_once"},
                    {"optionId": "no", "name": "Refuse", "kind": "reject_once"},
                ],
            })
            if answer.get("optionId") != "yes":
                raise AcpError("write outside the folder refused by the user")
        write_file(params)

    async def ask_permission(self, participant: Participant, params: dict) -> dict:
        """Mounts the panel and waits for the decision — as long as it takes.

        At most one pending request per bot; several bots may each have one. The panel only
        takes the focus if no other one is being decided.
        """
        participant.state = "asking"
        if participant.bubble is not None:
            participant.bubble.state = "asking"
            participant.bubble.redraw()
        answer = asyncio.get_running_loop().create_future()
        panel = PermissionPanel(participant, params, answer)
        participant.panel = panel
        await self.add(panel)
        if not any(
            p.panel is not None and p.panel.has_focus for p in self.participants.values()
        ):
            panel.focus()
        try:
            return await answer
        finally:
            participant.panel = None

    # ── turns ───────────────────────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "message":
            return
        text = event.value.strip()
        if not text:
            return
        reachable = [p for p in self.participants.values() if p.state != "failed"]
        if not reachable:
            self.status_note("no bot reachable")
            return
        event.input.value = ""
        self.transcript.add("user", "you", text)
        await self.add(self.past_bubble(self.transcript.entries[-1]))

        names = [q.name for q in reachable]
        targeted = recipients(text, names)
        spontaneous = targeted == names and "@" not in text
        targets = [
            p for p in reachable
            if p.name in targeted and (not spontaneous or p.bot.speaks_unprompted)
        ]
        busy = [p for p in targets if p.turn is not None and not p.turn.done()]
        if busy:
            self.status_note(
                "· " + ", ".join(f"@{p.name}" for p in busy)
                + " is still working — the message is in the thread, it will see it"
            )
            targets = [p for p in targets if p not in busy]
        if targets:
            self.turn_start = time.monotonic()
            self.interrupted = False
            self.conversation = asyncio.create_task(self.lead(targets))

    def status_note(self, text: str) -> None:
        bars = self.query("#status")
        if bars:
            bars.first(Static).update(Text(f"  {text}", style=N["dim"]))

    # ── rounds ──────────────────────────────────────────────────────────────────────

    async def lead(self, targets: list[Participant]) -> None:
        """One round per wave: parallel inside, sequential between waves, and bounded.

        The room's budget is the real limit; cutting on repetition is only a comfort guard.
        """
        await self.play_round(targets)
        for chain in range(self.room.max_rounds):
            if self.interrupted:
                return
            if self.room.stop_on_repeat:
                repeaters = [p.name for p in targets if p.repeats]
                if repeaters:
                    await self.add(
                        self.notice("↳ " + ", ".join(f"@{n}" for n in repeaters)
                                    + " repeats itself — round cut", ATTENTION)
                    )
                    return
            following = self.next_wave(targets)
            if not following:
                return
            await self.add(
                self.notice(
                    f"↳ round {chain + 2} · " + ", ".join(f"@{p.name}" for p in following),
                    N["faint"],
                )
            )
            targets = following
            await self.play_round(targets)
        if self.next_wave(targets):
            await self.add(
                self.notice(
                    f"↳ chaining budget spent ({self.room.max_rounds}) — your call now",
                    ATTENTION,
                )
            )

    def next_wave(self, previous: list[Participant]) -> list[Participant]:
        """The bots called out by those who just spoke, and that are able to answer."""
        targeted: list[Participant] = []
        members = [p.name for p in self.participants.values()]
        for speaker in previous:
            if speaker.bubble is None or not speaker.bot.can_mention:
                continue
            for name in handoffs(speaker.bubble.body, members, except_for=speaker.name):
                candidate = self.participants[name]
                if candidate.state != "failed" and candidate not in targeted:
                    targeted.append(candidate)
        return targeted

    async def play_round(self, targets: list[Participant]) -> None:
        """The bots of one wave answer in parallel, each in its own block."""
        for participant in targets:
            participant.bubble = self.new_bubble(participant)
            await self.add(participant.bubble)
        for participant in targets:
            participant.turn = asyncio.create_task(self.speak(participant))
        await asyncio.gather(*(p.turn for p in targets), return_exceptions=True)

    def new_bubble(self, participant: Participant) -> Bubble:
        participant.state = "thinking"
        participant.turn_start = time.monotonic()
        participant.timeline = []
        bubble = Bubble(
            f"@{participant.name}",
            participant.bot.role,
            participant.color,
            time.strftime("%H:%M"),
            state="thinking",
            start=time.monotonic(),
        )
        bubble.owner = participant.name
        participant.bubbles.append(bubble)
        bubble.thinking_visible = participant.bot.thinking_visible
        bubble.thinking = self.settings.get("thinking", "folded")
        return bubble

    async def speak(self, participant: Participant, forced: str | None = None) -> None:
        """One turn, and the refusal bounce: a refusal does not stop the bot, it steers it."""
        if not await self.ensure_ready(participant):
            await self.fail(participant, "could not be relaunched")
            return
        text = forced or prompt_for(participant.name, self.transcript.entries, participant.seen)
        while True:
            participant.seen = len(self.transcript.entries)
            bubble = participant.bubble
            assert bubble is not None and participant.client is not None
            try:
                result = await participant.client.prompt(participant.session["sessionId"], text)
            except AcpError as error:
                await self.fail(participant, str(error))
                return
            stop = result.get("stopReason", "")
            participant.state = bubble.state = "out" if stop == "cancelled" else "done"
            bubble.redraw()
            if bubble.body.strip():
                self.transcript.add(
                    "bot", participant.name, bubble.body.strip(), bubble.tool_titles()
                )
                participant.fingerprints.append(fingerprint(bubble.body))
            participant.seen = len(self.transcript.entries)
            participant.last_activity = time.monotonic()
            self.room_state["seen"][participant.name] = participant.seen
            write_state(self.room, self.room_state)

            # The path that works: the turn ends cleanly, then the comment leaves as a
            # prompt. No bounce after a cancellation.
            if bubble.tools:
                asyncio.create_task(self.close_outputs(bubble))
            if participant.refusal is None or stop == "cancelled":
                return
            text = f"[refusal from the user] {participant.refusal}"
            participant.refusal = None
            participant.bubble = self.new_bubble(participant)
            await self.add(participant.bubble)

    async def close_outputs(self, bubble: Bubble) -> None:
        """An output arrives with the next model request: if there is none, it will never
        come. Past the grace delay, we announce it instead of animating into the void."""
        await asyncio.sleep(telemetry.GRACE)
        changed = False
        for tool in bubble.tools:
            if tool.get("awaiting_output") and tool["output"] is None:
                tool["awaiting_output"] = False
                tool["output_lost"] = True
                changed = True
        if changed:
            bubble.redraw()

    async def fail(self, participant: Participant, message: str) -> None:
        """A failure fits on one line, never in a modal — and the room carries on."""
        participant.state = "failed"
        if participant.bubble is not None:
            participant.bubble.state = "failed"
            participant.bubble.redraw()
        await self.add(self.notice(f"✕ @{participant.name} — {message}", RED))
        await self.add(self.notice("the other bots carry on", N["faint"]))

    # ── commented refusal ───────────────────────────────────────────────────────────

    def open_refusal(self, panel: PermissionPanel, option: dict) -> None:
        """Opens the comment box right under the panel, as S9 shows."""
        self.query_one("#thread", VerticalScroll).mount(RefusalBox(panel, option), after=panel)

    def log_refusal(self, participant: Participant, comment: str) -> None:
        """The refusal enters the thread: without it the other bots miss the turn taken."""
        self.transcript.add(
            "refusal", "you", f"refused @{participant.name}'s request: « {comment} »"
        )
        self.run_worker(
            self.add(self.notice(f"✕ refused · you · « {comment} »", RED)), exclusive=False
        )

    def action_interrupt(self) -> None:
        """^C: cancel every running turn and resolve the in-flight permission requests.

        We do not cancel the conversation task: it carries the code that closes the bubbles
        and keeps what was already said. We ask it to stop after the current round, and the
        turns end by themselves on `stopReason: cancelled`.
        """
        self.interrupted = True
        cut = []
        for participant in self.participants.values():
            if participant.turn is not None and not participant.turn.done():
                participant.client.cancel(participant.session["sessionId"])
                cut.append(participant.name)
            if participant.panel is not None:
                participant.panel.abandon()
        if cut:
            self.transcript.add(
                "system", "you", "turn interrupted · " + ", ".join(f"@{n}" for n in cut)
            )

    def open_thinking(self, name: str | None) -> None:
        if name in self.participants and self.participants[name].bot.thinking_visible:
            self.push_screen(ThinkingScreen(name))

    def known_models(self) -> list[str]:
        """The model list comes from the open sessions — never from a hard-coded name."""
        for participant in self.participants.values():
            models = (participant.session.get("models") or {}).get("availableModels") or []
            if models:
                return [m.get("modelId", "") for m in models if m.get("modelId")]
        return []

    def action_bot_card(self) -> None:
        """The card of the last bot that spoke, otherwise a fresh bot."""
        target = self.last_active()
        taken = {p.bot.hue for p in self.participants.values() if p.name != target}
        self.push_screen(
            BotCard(self.room.root.parents[1], target, self.known_models(), taken),
            self.bot_saved,
        )

    def bot_saved(self, name: str | None) -> None:
        """An edited bot is evicted: its next turn relaunches it with its new card."""
        if not name:
            return
        folder = self.room.root.parents[1] / "bots" / name
        participant = self.participants.get(name)
        if participant is None:
            self.run_worker(
                self.add(self.notice(f"· @{name} saved — add it to the room to hear it",
                                     N["faint"]))
            )
            return
        participant.bot = bots.load(folder)
        participant.color = bot_color(participant.bot.hue)
        self.run_worker(self.evict(participant))

    def action_compose(self) -> None:
        root = self.room.root.parents[1]
        self.push_screen(
            RoomScreen(root, self.room, bots.load_all(root / "bots")), self.room_saved
        )

    def room_saved(self, name: str | None) -> None:
        """room.toml is written; the line-up takes effect the next time the room opens."""
        if name:
            self.run_worker(self.add(self.notice(
                f"· room « {name} » saved — reopen it for the line-up to apply",
                N["faint"],
            )))

    def action_settings(self) -> None:
        root = self.room.root.parents[1]
        self.push_screen(
            SettingsScreen(self.settings, bots.load_all(root / "bots"), self.known_models()),
            self.settings_changed,
        )

    def settings_changed(self, values: dict | None) -> None:
        """What shows right away applies right away."""
        if not values:
            return
        self.settings = values
        apply_theme(self.wanted_theme())
        self.refresh_css()
        for participant in self.participants.values():
            participant.color = bot_color(participant.bot.hue)
            if participant.bubble is not None:
                participant.bubble.color = participant.color
                participant.bubble.thinking = values["thinking"]
                participant.bubble.redraw()
        self.paint_status()

    def last_active(self) -> str | None:
        live = [p for p in self.participants.values() if p.bubble is not None]
        return max(live, key=lambda p: p.turn_start).name if live else None

    def action_thinking(self) -> None:
        """The reasoning of the last bot that spoke."""
        self.open_thinking(self.last_active())

    def action_follow(self) -> None:
        self.query_one("#thread", VerticalScroll).scroll_end(animate=False)
        self.unread = 0

    async def on_unmount(self) -> None:
        """On the way out, no process outlives the application."""
        for participant in self.participants.values():
            if participant.outputs is not None:
                participant.outputs.stop()
        # In parallel and short: sequentially at 4 s per bot, we were giving the terminal
        # back several seconds before returning to home.
        await asyncio.gather(*(
            p.client.close(grace=1.0)
            for p in self.participants.values() if p.client is not None
        ), return_exceptions=True)


def main() -> None:
    """Home, then a room, then home: ^Q comes back, ^Q at home quits."""
    root = Path(__file__).resolve().parents[1]
    asked = sys.argv[1] if len(sys.argv) > 1 else None
    while True:
        name = asked or Home(root).run()
        asked = None
        if not name:
            return
        path = root / "rooms" / name / "room.toml"
        if not path.exists():
            print(f"no room named « {name} » in {root / 'rooms'}")
            return
        room = load_room(root, name)
        Quorum(room, bots.load_all(root / "bots")).run()


if __name__ == "__main__":
    main()
