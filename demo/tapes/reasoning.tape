# Watching a bot work: the milestones it emits, the commands, the output that lands late,
# the files it has touched. ^R from the thread, esc to come back to the same place.

Output "demo/out/frames-reasoning/"
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

Sleep 1s
Type@30ms "@scout where do we still accept an expired token?"
Sleep 400ms
Enter
Sleep 2500ms

# Into the reasoning while it is still running.
Ctrl+R
Sleep 8s

Escape
Sleep 2s

Hide
Ctrl+Q
Sleep 500ms
