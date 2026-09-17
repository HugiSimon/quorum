#!/bin/sh
# Rebuilds the set and re-shoots every recording. Run it from the repository root.
#
#   demo/render.sh            every tape
#   demo/render.sh home       one of them
#
# vhs captures the frames and then fails to assemble them: version 0.12, the newest there
# is, still calls ffmpeg with `-vsync`, which ffmpeg 9 removed — and it reports success
# either way. So each tape writes a frame directory, and the encode happens here, where we
# can also keep the files small enough for a README.
set -e

cd "$(dirname "$0")/.."
QUORUM_REPO="$(pwd)"
export QUORUM_REPO
PATH="$QUORUM_REPO/demo/bin:$PATH"
export PATH

command -v vhs >/dev/null || { echo "vhs is missing — brew install vhs"; exit 1; }

STILLS="card"

mkdir -p docs demo/out
python3 demo/build.py
python3 demo/logo.py

for tape in ${*:-home room reasoning card}; do
    path="demo/tapes/$tape.tape"
    [ -f "$path" ] || { echo "no tape called $tape"; exit 1; }
    frames="demo/out/frames-$tape"
    rm -rf "$frames"

    echo "shooting $tape"
    vhs "$path" >"demo/out/$tape.log" 2>&1
    count=$(ls "$frames" 2>/dev/null | wc -l | tr -d ' ')
    [ "$count" -gt 2 ] || { echo "  no frame came out — see demo/out/$tape.log"; exit 1; }

    # A still needs no encoding at all: the last text frame is the picture.
    case " $STILLS " in
        *" $tape "*)
            last=$(ls "$frames"/frame-text-*.png | tail -1)
            cp "$last" "docs/$tape.png"
            rm -rf "$frames" "demo/out/$tape.log"
            echo "  docs/$tape.png · $(du -h "docs/$tape.png" | cut -f1 | tr -d ' ')"
            continue
            ;;
    esac

    # The text layer only. vhs also renders the terminal's own cursor as a second layer,
    # and because it blinks, every single frame differs from the one before — which is
    # forty times the file for something the app already draws itself.
    # Sixty-four colours and no dithering: this is flat areas of six hues on one
    # background, dithering would only add noise for the encoder to carry.
    ffmpeg -y -loglevel error \
        -framerate 24 -i "$frames/frame-text-%05d.png" \
        -filter_complex "fps=15,split[a][b];[a]palettegen=max_colors=64[p];\
[b][p]paletteuse=dither=none" \
        -loop 0 "docs/$tape.gif"
    rm -rf "$frames" "demo/out/$tape.log"

    size=$(du -h "docs/$tape.gif" | cut -f1 | tr -d ' ')
    echo "  docs/$tape.gif · $size · $((count / 2)) frames"
done
