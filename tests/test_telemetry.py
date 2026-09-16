"""The output adapter, on a captured log: no agent, no network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.telemetry import (  # noqa: E402
    GeminiOutputs,
    short_id,
    clean,
    objects,
    outputs_of,
)

LOG = Path(__file__).with_name("gemini_log.json")


def main() -> None:
    text = LOG.read_text(encoding="utf-8")

    # The log is not JSONL: three indented objects, glued together.
    found, consumed = objects(text)
    assert len(found) == 3, len(found)
    assert consumed == len(text.rstrip()) or consumed <= len(text)

    # Only the api_request record carries an output.
    harvest = [o for obj in found for o in outputs_of(obj)]
    assert len(harvest) == 1, harvest
    name, call_id, output = harvest[0]
    assert (name, call_id) == ("run_shell_command", "call_8728"), harvest[0]
    assert output == "        2 data.txt", repr(output)

    # The ACP → log link.
    assert short_id("run_shell_command__call_8728") == "call_8728"
    assert short_id("call_8728") == "call_8728", "an id without a prefix stays itself"

    # The wrapping goes, the output's alignment stays.
    assert clean("<untrusted_context>\nOutput: ok\n</untrusted_context>") == " ok"
    assert clean("raw, unwrapped") == "raw, unwrapped"

    # An incomplete object is left for the next pass, not lost.
    half = text[: text.index("api_request") + 5]
    partial, rest = objects(half)
    assert len(partial) == 1 and rest < len(half), (len(partial), rest)

    # And the class that follows the file reports each output only once.
    seen = []
    follower = GeminiOutputs(LOG, lambda i, o: seen.append((i, o)))
    follower.harvest()
    follower.harvest()
    assert seen == [("call_8728", "        2 data.txt")], seen
    print("test_telemetry: splitting ok · extraction ok · link ok · only once ok")


if __name__ == "__main__":
    main()
