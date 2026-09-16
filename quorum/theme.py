"""Palette, glyphes et animations — la planche S0 du design, en code.

La teinte est l'identité d'un bot ; seuls la clarté et le chroma changent d'un thème à
l'autre. Rien n'a de fond coloré derrière du texte, donc aucun risque de contraste.
"""

from __future__ import annotations

import math
import os
import re

# Les deux thèmes : mêmes teintes, clarté et chroma différents.
SOMBRE = (0.78, 0.105)
CLAIR = (0.46, 0.079)  # 0.079 et non 0.13 : au-delà, plus de la moitié de la roue sort du gamut

NEUTRES_SOMBRE = {
    "fond": "#0c0b0a",
    "panneau": "#141312",
    "cadre": "#2b2824",
    "faible": "#56514a",
    "dim": "#8a8378",
    "encre": "#d9d3c8",
}
NEUTRES_CLAIR = {
    "fond": "#faf8f4",
    "panneau": "#f1eee7",
    "cadre": "#d9d3c8",
    "faible": "#a19a8e",
    "dim": "#6c665d",
    "encre": "#22201d",
}

# La palette courante. Elle est modifiée **sur place** : tous les modules la partagent,
# et un changement de thème n'oblige personne à se réimporter.
N: dict[str, str] = {}
_sombre = True


def est_sombre() -> bool:
    return _sombre


def appliquer_theme(sombre: bool) -> None:
    """Bascule la palette pour toute l'application, sans réimport."""
    global _sombre
    _sombre = sombre
    N.clear()
    N.update(NEUTRES_SOMBRE if sombre else NEUTRES_CLAIR)


def theme_du_terminal() -> bool:
    """Devine si le terminal est sombre.

    Il n'y a pas d'interrogation fiable depuis Textual : on lit COLORFGBG quand le terminal
    le pose (`15;0` = encre claire sur fond sombre), et à défaut on suppose sombre — c'est
    le cas le plus fréquent, et le thème reste choisissable à la main.
    """
    couleurs = os.environ.get("COLORFGBG", "")
    if ";" in couleurs:
        fond = couleurs.rsplit(";", 1)[-1].strip()
        if fond.isdigit():
            return int(fond) < 8
    return True


# Les teintes que l'interface se réserve, hors identité des bots.
ATTENTION = "#e8b563"
CLIQUABLE = "#a8c76a"
VERT = "#7fbf6a"
ROUGE = "#d96b6b"

# Deux bandes de teintes ne sont jamais données à un bot.
BANDE_ATTENTION = (45, 75)
BANDE_CLIQUABLE = (90, 120)

# Un glyphe, un mot. Le glyphe ne porte jamais l'état tout seul : le mot est toujours écrit.
ETATS = {
    "repos": ("·", "au repos"),
    "pense": ("◐", "pense"),
    "execute": ("▸", "exécute"),
    "demande": ("◆", "demande"),
    "fini": ("✓", "a fini"),
    "echec": ("✕", "échec"),
    "horsjeu": ("◌", "hors-jeu"),
}

# Les `kind` d'ACP, ramenés au mot court que le design affiche devant chaque outil.
FAMILLES = {
    "read": "fs", "edit": "fs", "delete": "fs", "move": "fs", "search": "fs",
    "execute": "shell", "fetch": "net", "think": "pense",
}

# Trois mouvements, pas un de plus. Le braille est de largeur franche : c'est le plus sûr.
ANIM_PENSE = "◜◠◝◞◡◟"
ANIM_TRAVAILLE = "⡿⡀⡄⡆⡇⡏⡟⡿⡟⡏⡇⡆⡄"


def oklch_vers_srgb(clarte: float, chroma: float, teinte: float) -> tuple[float, float, float]:
    """Convertit oklch en sRGB non borné : des composantes hors [0, 1] signalent le hors-gamut."""
    angle = math.radians(teinte)
    a, b = chroma * math.cos(angle), chroma * math.sin(angle)
    l = (clarte + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (clarte - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (clarte - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lineaire = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )
    return tuple(_gamma(c) for c in lineaire)


def _gamma(c: float) -> float:
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * abs(c) ** (1 / 2.4) * (1 if c > 0 else -1) - 0.055


def dans_le_gamut(clarte: float, chroma: float, teinte: float) -> bool:
    """Vrai si la couleur tient dans sRGB sans écrêtage."""
    return all(-0.0005 <= c <= 1.0005 for c in oklch_vers_srgb(clarte, chroma, teinte))


def hex_de(clarte: float, chroma: float, teinte: float) -> str:
    """Rend un #rrggbb, en réduisant le chroma par pas jusqu'à rentrer dans le gamut."""
    while chroma > 0 and not dans_le_gamut(clarte, chroma, teinte):
        chroma -= 0.005
    composantes = oklch_vers_srgb(clarte, max(chroma, 0.0), teinte)
    return "#" + "".join(f"{round(min(max(c, 0.0), 1.0) * 255):02x}" for c in composantes)


def couleur_bot(teinte: float, sombre: bool | None = None) -> str:
    """La couleur d'un bot dans le thème courant, à partir de sa seule teinte."""
    clarte, chroma = SOMBRE if (_sombre if sombre is None else sombre) else CLAIR
    return hex_de(clarte, chroma, teinte)


def teinte_reservee(teinte: float) -> bool:
    """Vrai si la teinte appartient à une bande réservée à l'interface."""
    return any(bas <= teinte % 360 <= haut for bas, haut in (BANDE_ATTENTION, BANDE_CLIQUABLE))


def regle(titre: str, largeur: int, note: str = ""):
    """Le seul marqueur de libellé du design : ─┤ TITRE ├── suivi d'un filet.

    La grille n'a pas d'interlettrage : un libellé espacé doublerait sa largeur en vraies
    espaces. Tous les titres passent par ici. Le filet est discret, le **titre** ne l'est
    pas : c'est un repère de lecture, pas de la décoration.
    """
    from rich.text import Text

    texte = Text()
    texte.append("─┤ ", style=N["cadre"])
    texte.append(titre, style=f"bold {N['encre']}")
    texte.append(" ├─", style=N["cadre"])
    reste = largeur - len(titre) - 6
    if note:
        texte.append(f"  {note} ", style=N["dim"])
        reste -= len(note) + 3
    texte.append("─" * max(2, reste) + "\n", style=N["cadre"])
    return texte


appliquer_theme(True)


if __name__ == "__main__":
    # Le thème sombre tient tel quel sur toute la roue.
    hors_sombre = [h for h in range(360) if not dans_le_gamut(*SOMBRE, h)]
    assert not hors_sombre, f"{len(hors_sombre)} teintes hors gamut en thème sombre"

    # Le thème clair déborde de 0.001 sur la bande cyan (h 193–207) : le maximum réel à
    # cette clarté est 0.078. C'est l'écrêtage de hex_de qui rattrape, et il est invisible.
    hors_clair = [h for h in range(360) if not dans_le_gamut(*CLAIR, h)]
    assert set(hors_clair) <= set(range(190, 212)), f"débordement inattendu : {hors_clair}"

    # 0.13 à la même clarté, lui, perd plus de la moitié de la roue — d'où la correction.
    hors_13 = [h for h in range(360) if not dans_le_gamut(CLAIR[0], 0.13, h)]
    assert len(hors_13) > 180, f"seulement {len(hors_13)} teintes hors gamut à 0.13"

    # Ce qui compte vraiment : aucune couleur rendue n'est hors gamut, dans les deux thèmes.
    for h in range(360):
        for sombre in (True, False):
            couleur = couleur_bot(h, sombre)
            assert re.fullmatch(r"#[0-9a-f]{6}", couleur), couleur

    for nom, teinte in (("sonar", 250), ("forge", 150), ("lex", 305), ("audit", 18), ("pico", 195)):
        assert not teinte_reservee(teinte), f"{nom} pique une bande réservée"
        print(f"{nom:6} h{teinte:4}  sombre {couleur_bot(teinte)}  clair {couleur_bot(teinte, False)}")

    appliquer_theme(False)
    assert N["fond"] == NEUTRES_CLAIR["fond"] and not est_sombre()
    appliquer_theme(True)
    assert N["fond"] == NEUTRES_SOMBRE["fond"] and est_sombre()
    os.environ["COLORFGBG"] = "0;15"
    assert not theme_du_terminal(), "fond clair annoncé par le terminal"
    os.environ["COLORFGBG"] = "15;0"
    assert theme_du_terminal()
    del os.environ["COLORFGBG"]

    print(f"clair : {len(hors_clair)} teintes écrêtées de 0.001 ; à 0.13 il en manquerait {len(hors_13)}")
