"""Boots a harness for real and reports what it actually does.

Not part of `verify.sh`: that suite is promised network-free and model-free, and this one
is neither. It exists because the only thing that matters about a harness row cannot be
checked offline — whether the agent, configured the way quorum configures it, really stops
and asks before it runs a command. An agent that never asks turns the permission panel into
decoration, and no unit test can tell you that.

    uv run python tools/harness_check.py opencode openai/gpt-5.6-luna
    uv run python tools/harness_check.py gemini
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum import bot as bots  # noqa: E402
from quorum import harness as harnesses  # noqa: E402
from quorum.acp import AcpClient, AcpError  # noqa: E402

# One turn answers both questions: whether the role reached the agent (the codeword only
# exists there) and whether running a command stops for a permission.
CODEWORD = "QRM-7788"
# A command that changes something: an agent may wave a read-only one through, and the
# question here is whether quorum's panel opens at all, not how clever the agent's triage is.
COMMAND = "touch quorum-probe.txt"
PROBE = f"First say your codeword. Then run the shell command `{COMMAND}`."
ROLE = (f"You are a test bot. Be terse. Your codeword is {CODEWORD} — say it when asked.\n"
        "Do exactly what you are asked, nothing more.")
RULES = [bots.Rule("shell:*", "ask_user"), bots.Rule("everything else", "ask_user")]
STEP = 120


def mark(ok: bool | None) -> str:
    return {True: "  ok  ", False: " FAIL ", None: "  --  "}[ok]


async def probe(name: str, model: str) -> dict:
    harness = harnesses.by_name(name)
    if harness is harnesses.OTHER:
        raise SystemExit(f"no row named {name!r} — one of {', '.join(harnesses.NAMES)}")
    found = shutil.which(harness.command)
    if not found:
        return {"command": f"{harness.command} is not on PATH", "start": False}

    report: dict = {"command": f"{found} {' '.join(harness.args)}"}
    with tempfile.TemporaryDirectory() as tmp:
        home, work = Path(tmp) / "home", Path(tmp) / "work"
        work.mkdir(parents=True)
        folder = home / "bots" / "probe"
        bot = bots.Bot(name="probe", role="probe", hue=10, folder=folder,
                       command=harness.command, args=list(harness.args),
                       model=model or None, provider=name)
        blocked = bots.write_bot(folder, bot, ROLE, RULES)
        report["blocked"] = blocked
        report["files"] = sorted(f.name for f in folder.iterdir())

        command, args, env = bots.launch(bots.load(folder))
        report["argv"] = [command] + args
        seen: list[dict] = []
        asked: asyncio.Future = asyncio.get_running_loop().create_future()

        async def permission(params: dict) -> dict:
            if not asked.done():
                asked.set_result(params)
            # Refused with a reason, which is the other half of quorum's promise.
            option = next((o for o in params.get("options", [])
                           if o.get("kind", "").startswith("reject")), None)
            if option is None:
                return {"outcome": "cancelled"}
            return {"outcome": "selected", "optionId": option["optionId"]}

        client = AcpClient(lambda message: seen.append(message), permission)
        log = Path(tmp) / "stderr.log"
        try:
            await client.start(command, args, env, work, log)
            capabilities = await asyncio.wait_for(client.initialize(), STEP)
            report["start"] = True
            report["protocol"] = capabilities.get("protocolVersion")
            session = await asyncio.wait_for(
                client.new_session(work, meta=harnesses.session_meta(folder, bot)), STEP)
            report["session"] = True
            models = session.get("models") or {}
            report["models"] = [m.get("modelId") for m in models.get("availableModels") or []]
            report["current"] = models.get("currentModelId")
            report["modes"] = [m.get("id") for m in (session.get("modes") or {}).get(
                "availableModes") or []]

            if model and model in (report["models"] or []) and model != report["current"]:
                try:
                    await asyncio.wait_for(
                        client.set_model(session["sessionId"], model), STEP)
                    report["set_model"] = True
                except (AcpError, asyncio.TimeoutError):
                    report["set_model"] = False

            try:
                await asyncio.wait_for(client.prompt(session["sessionId"], PROBE), STEP * 2)
            except asyncio.TimeoutError:
                report["turn"] = False
            else:
                report["turn"] = True
            report["asked"] = asked.done()
            if asked.done():
                request = asked.result()
                report["ask_title"] = (request.get("toolCall") or {}).get("title")
                report["ask_options"] = [o.get("kind") for o in request.get("options", [])]
            texts = []
            for update in seen:
                if update.get("method") != "session/update":
                    continue
                content = (update.get("params", {}).get("update", {}) or {}).get("content")
                for piece in (content if isinstance(content, list) else [content]):
                    if isinstance(piece, dict) and piece.get("text"):
                        texts.append(piece["text"])
            # The codeword arrives in chunks: it has to be reassembled before looking
            # for it, or a streaming agent reads as an agent that ignored its role.
            report["said"] = "".join(texts)[:500]
            report["role"] = CODEWORD in "".join(texts)
            report["tools"] = sorted({
                (u.get("params", {}).get("update", {}) or {}).get("title", "")
                for u in seen if u.get("method") == "session/update"
            } - {""})
        except (OSError, AcpError, asyncio.TimeoutError) as error:
            report.setdefault("start", False)
            report["error"] = f"{type(error).__name__}: {error}"
        finally:
            await client.close()
        report["stderr"] = log.read_text(errors="replace").strip()[-400:] if log.exists() else ""
    return report


def show(name: str, report: dict) -> None:
    print(f"\n── {name} " + "─" * (66 - len(name)))
    print(f"  {' '.join(report.get('argv', [])) or report.get('command', '')}")
    for label, key in (("starts", "start"), ("opens a session", "session"),
                       ("finishes a turn", "turn"), ("the role reached it", "role"),
                       ("asks before running", "asked")):
        print(f"  [{mark(report.get(key))}] {label}")
    if report.get("models"):
        print(f"  models      {', '.join(report['models'][:6])}"
              f"{' …' if len(report['models']) > 6 else ''}")
        print(f"  current     {report.get('current')}")
    if "set_model" in report:
        print(f"  [{mark(report['set_model'])}] session/set_model")
    if report.get("modes"):
        print(f"  modes       {', '.join(m for m in report['modes'] if m)}")
    if report.get("ask_options"):
        print(f"  the ask     {report.get('ask_title')} · {', '.join(report['ask_options'])}")
    if report.get("said"):
        print(f"  said        {report['said'][:160]!r}")
    if report.get("tools"):
        print(f"  tool calls  {', '.join(report['tools'][:8])}")
    if report.get("blocked"):
        print(f"  not written {report['blocked']}")
    print(f"  files       {', '.join(report.get('files', []))}")
    if report.get("error"):
        print(f"  error       {report['error']}")
    if report.get("stderr"):
        print(f"  stderr      {report['stderr'][:200]}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    name, model = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    report = asyncio.run(probe(name, model))
    show(name, report)
    Path("runtime").mkdir(exist_ok=True)
    out = Path("runtime") / f"harness-{name}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  written to {out}")


if __name__ == "__main__":
    main()
