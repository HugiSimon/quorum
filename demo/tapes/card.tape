# A bot is a folder, and this is the sheet that writes it: role, model, and the permission
# table — first matching rule wins. One still is enough; nothing here moves.

Output "demo/out/frames-card/"
Source demo/tapes/common.tape
Set Width 1280
Set Height 1120

Hide
Type `export HOME="$QUORUM_REPO/demo/root"`
Enter
Type `cd "$HOME/dev/atlas" && clear`
Enter
Type `"$QUORUM_REPO/.venv/bin/quorum"`
Enter
Sleep 2500ms
Tab
Sleep 400ms
Down
Sleep 400ms
Enter
Sleep 2500ms
Show

Sleep 2s

Hide
Escape
Sleep 300ms
Ctrl+Q
Sleep 500ms
