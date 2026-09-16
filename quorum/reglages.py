"""Les réglages globaux : un fichier JSON, et des valeurs qui ont toutes un effet.

Tout est écrit dans ~/.config/quorum/ (ou dans QUORUM_CONFIG), et reste éditable à la main.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAUTS = {
    "theme": "suit le terminal",
    "raisonnement": "replié",
    "densite": "automatique",
    "demander_hors_dossier": True,
    "conserver_les_sorties": True,
}


def dossier() -> Path:
    return Path(os.environ.get("QUORUM_CONFIG", Path.home() / ".config" / "quorum"))


def lire() -> dict:
    fichier = dossier() / "settings.json"
    if not fichier.exists():
        return dict(DEFAUTS)
    try:
        return {**DEFAUTS, **json.loads(fichier.read_text(encoding="utf-8"))}
    except ValueError:
        return dict(DEFAUTS)


def ecrire(valeurs: dict) -> Path:
    fichier = dossier() / "settings.json"
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(
        json.dumps({c: valeurs.get(c, d) for c, d in DEFAUTS.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return fichier


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["QUORUM_CONFIG"] = tmp
        assert lire() == DEFAUTS
        chemin = ecrire({**DEFAUTS, "raisonnement": "déplié", "inconnu": 1})
        relu = lire()
        assert relu["raisonnement"] == "déplié" and "inconnu" not in relu, relu
        chemin.write_text("pas du json", encoding="utf-8")
        assert lire() == DEFAUTS, "un fichier abîmé ne doit pas empêcher de démarrer"
    print("reglages : défauts ok · écriture ok · fichier abîmé toléré ok")
