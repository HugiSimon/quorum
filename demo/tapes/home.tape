# The home sheet: five rooms across three projects, five bots, two providers.
# Shot on the set built by demo/build.py, with HOME pointed at it.

Output "demo/out/frames-home/"
Source demo/tapes/common.tape
Set Width 1280
Set Height 560

Hide
Type `export HOME="$QUORUM_REPO/demo/root"`
Enter
Type `cd "$HOME/dev/atlas" && clear`
Enter
Type `"$QUORUM_REPO/.venv/bin/quorum"`
Enter
Sleep 2s
Show

Sleep 900ms
Down@620ms 3
Sleep 700ms
Tab
Sleep 700ms
Down@560ms 4
Sleep 900ms
Tab
Sleep 500ms
Up@520ms 3
Sleep 800ms
Enter
Sleep 2600ms

Hide
Ctrl+Q
Sleep 500ms
