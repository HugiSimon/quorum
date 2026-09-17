"""Writes the mark, in both themes, from the palette the application itself uses.

An open ring and a bead sitting in the gap: the ring is short of closing, the bead is the
member that completes it. Drawn once here rather than kept as two files somebody has to
remember to edit — change a neutral in theme.py and the mark follows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.theme import ATTENTION, NEUTRALS_DARK, NEUTRALS_LIGHT  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"

# The geometry, as it was chosen: a 70° gap centred on the lower-right diagonal, the bead on
# the ring's own radius. Left alone on purpose — at small sizes the bead closes up against
# the ring and the mark reads as a Q, which is the other thing it is meant to be.
MARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" \
fill="none" role="img" aria-label="Quorum">
  <path d="M35.47 51.70 A20 20 0 1 1 51.70 35.47" stroke="{ink}" stroke-width="8"/>
  <circle cx="46.14" cy="46.14" r="6" fill="{dot}"/>
</svg>
"""


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    for suffix, neutrals in (("dark", NEUTRALS_DARK), ("light", NEUTRALS_LIGHT)):
        (DOCS / f"logo-{suffix}.svg").write_text(
            MARK.format(ink=neutrals["ink"], dot=ATTENTION), encoding="utf-8"
        )
    # One flat version for anywhere a single colour is all there is: a favicon, a sticker,
    # somebody's dark slide.
    (DOCS / "logo-mono.svg").write_text(
        MARK.format(ink="currentColor", dot="currentColor"), encoding="utf-8"
    )
    print(f"logo written: {', '.join(p.name for p in sorted(DOCS.glob('logo-*.svg')))}")


if __name__ == "__main__":
    main()
