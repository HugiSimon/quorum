"""Écrire un bot depuis la fiche, le relire, et retomber sur la même chose."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.bot import (  # noqa: E402
    Bot,
    Regle,
    charger,
    ecrire_bot,
    lancement,
    lire_regles,
    pouvoir,
)


def main() -> None:
    regles = [
        Regle("shell:*", "deny"),
        Regle("shell:git *", "allow"),
        Regle("shell:rm *", "ask_user"),
        Regle("fs:écrit", "ask_user"),
        Regle("net:fetch", "deny"),
        Regle("mcp:vault", "deny"),
        Regle("tout le reste", "ask_user"),
    ]
    bot = Bot(
        nom="essai", role="exécution", teinte=150, dossier=Path("."),
        commande="gemini", args=["--acp"], env={"NO_PROXY": "localhost"},
        modele="", fournisseur="gemini", dossier_de_travail="copie",
    )

    with tempfile.TemporaryDirectory() as tmp:
        dossier = Path(tmp) / "essai"
        ecrire_bot(dossier, bot, "Tu exécutes.", regles)

        relu = charger(dossier)
        assert relu.nom == "essai" and relu.teinte == 150, relu
        assert relu.args == ["--acp"] and relu.env == {"NO_PROXY": "localhost"}
        assert relu.fournisseur == "gemini" and relu.copie_isolee, relu
        assert (dossier / "system.md").read_text().strip() == "Tu exécutes."

        # L'aller-retour par le disque ne perd ni un motif ni une décision, ni l'ordre.
        rendues = lire_regles(dossier)
        assert [(r.motif, r.decision) for r in rendues] == [
            (r.motif, r.decision) for r in regles
        ], [(r.motif, r.decision) for r in rendues]

        # Et le moteur de permissions reçoit bien le fichier.
        _, args, _ = lancement(relu)
        assert "--policy" in args and str(dossier / "policy.toml") in args, args

        niveau, phrase = pouvoir(regles)
        assert "lance des commandes" in phrase and "ne modifie aucun fichier" in phrase, phrase
        assert "3 garde-fous, 3 interdits" in phrase, phrase
        assert 1 <= niveau <= 7, niveau

        # Un bot sans aucune règle autorisée est au plus bas.
        bas, _ = pouvoir([Regle("tout le reste", "ask_user")])
        assert bas < niveau, (bas, niveau)
    print("test_fiche : écriture ok · relecture ok · ordre des règles ok · jauge ok")


if __name__ == "__main__":
    main()
