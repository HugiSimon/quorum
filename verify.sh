#!/bin/sh
# Every check of the project, in the order they appeared.
set -e
for m in theme room settings; do uv run python quorum/$m.py | tail -1; done
for t in acp bot bot_card telemetry room_app rounds thinking resume bot_screen room_screen home extremes; do
  uv run python tests/test_$t.py
done
echo "— all green"
