# The hero: two bots working at once in one thread, a permission that comes up to you,
# a refusal that carries a reason, and a mention that hands the work over.

Output "demo/out/frames-room/"
Source demo/tapes/common.tape
Set Width 1280
Set Height 780

Hide
Type `export HOME="$QUORUM_REPO/demo/root"`
Enter
Type `cd "$HOME/dev/atlas" && clear`
Enter
Type `"$QUORUM_REPO/.venv/bin/quorum" pair`
Enter
Sleep 3s
Show

Sleep 1500ms
Type@30ms "@scout where do we still accept an expired token? @forge run the auth tests."
Sleep 600ms
Enter

# Both start at once: two bubbles fill side by side, each in its own colour.
Sleep 9s

# The permission is up. Refuse it, and say why.
Type "r"
Sleep 900ms
Type@35ms "not in this repo — uv sync --reinstall does it in place"
Sleep 700ms
Enter
Sleep 4s

# The hand-over: scout finds the gap and calls forge out by name.
Type@30ms "@scout does anything actually test /v1/refresh?"
Sleep 500ms
Enter
Sleep 9s

Hide
Ctrl+Q
Sleep 500ms
