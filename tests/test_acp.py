"""Checks the ACP client against a fake agent: the round trip, id 0, the cancellation."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.acp import AcpClient  # noqa: E402

FAKE = Path(__file__).with_name("fake_agent.py")


async def _client(on_permission, notifs, folder):
    client = AcpClient(lambda m: notifs.append(m), on_permission)
    await client.start(sys.executable, [str(FAKE)], {}, Path.cwd(), folder / "agent.stderr.log")
    await client.initialize()
    session = await client.new_session(Path.cwd())
    return client, session


async def nominal_scenario(folder: Path) -> None:
    """A full turn: thought, tool, permission granted, clean end."""
    notifs, requests = [], []

    async def allow(params):
        requests.append(params)
        return {"outcome": "selected", "optionId": params["options"][0]["optionId"]}

    client, session = await _client(allow, notifs, folder)
    assert session["sessionId"], "the session must have an id"
    assert session["models"]["availableModels"], "the model list must come from the session"

    result = await client.prompt(session["sessionId"], "PERM delete .venv")
    assert result["stopReason"] == "end_turn", result
    assert result["_meta"]["outcome"]["optionId"] == "proceed_always", result

    assert len(requests) == 1, requests
    options = requests[0]["options"]
    assert [o["kind"] for o in options] == ["allow_always", "allow_once", "reject_once"], \
        "the client passes the options in the order received: the display is what sorts"

    kinds = [n["params"]["update"]["sessionUpdate"] for n in notifs]
    assert "agent_thought_chunk" in kinds and "tool_call" in kinds, kinds
    await client.close()


async def cancellation_scenario(folder: Path) -> None:
    """^C during an in-flight permission: the turn closes and the request is resolved."""
    notifs = []
    arrived = asyncio.Event()

    async def allow(params):
        arrived.set()
        await asyncio.Event().wait()  # nobody ever answers

    client, session = await _client(allow, notifs, folder)
    turn = asyncio.create_task(client.prompt(session["sessionId"], "PERM delete .venv"))
    await asyncio.wait_for(arrived.wait(), 5)

    client.cancel(session["sessionId"])
    result = await asyncio.wait_for(turn, 5)
    assert result["stopReason"] == "cancelled", result

    for _ in range(50):  # the "cancelled" answer reaches the fake agent
        await asyncio.sleep(0.02)
        echoes = [n["params"]["update"] for n in notifs
                  if n["params"]["update"]["sessionUpdate"] == "_permission_answer"]
        if echoes:
            break
    assert echoes, "the in-flight permission was not resolved: the turn would have stayed open"
    assert echoes[0]["outcome"] == {"outcome": "cancelled"}, echoes
    await client.close()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        await nominal_scenario(Path(tmp))
        await cancellation_scenario(Path(tmp))
    print("test_acp: nominal ok · id 0 ok · cancellation ok")


if __name__ == "__main__":
    asyncio.run(main())
