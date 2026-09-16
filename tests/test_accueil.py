"""S1 et S2 : le vide qui explique, et la liste qui ouvre une salle."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from atelier import attendre, monter_projet  # noqa: E402
from quorum.ecrans import Accueil, EcranAccueil, EcranFicheBot  # noqa: E402
from quorum.room import Transcript  # noqa: E402


async def scenario_vide() -> None:
    """S2 : sans rien, l'accueil explique et ne propose qu'un geste."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        os.environ["QUORUM_CONFIG"] = str(racine / "config")
        app = Accueil(racine)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause(0.2)
            accueil = app.screen
            assert isinstance(accueil, EcranAccueil), accueil
            assert accueil.salles == [] and accueil.bots == {}
            texte = accueil.query_one("#corps").render().plain
            assert "créer mon premier bot" in texte, texte
            assert "Une salle réunit tes bots" in texte

            await pilot.press("b")
            await pilot.pause(0.2)
            assert isinstance(app.screen, EcranFicheBot), app.screen
            await pilot.press("escape")
            await pilot.pause(0.2)
        del os.environ["QUORUM_CONFIG"]


async def scenario_liste() -> None:
    """S1 : deux salles, la plus récente en tête, ⏎ rend son nom."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        os.environ["QUORUM_CONFIG"] = str(racine / "config")
        monter_projet(racine, max_rounds=0)
        (racine / "rooms" / "ancienne").mkdir(parents=True)
        (racine / "rooms" / "ancienne" / "room.toml").write_text(
            'nom = "ancienne"\ndossier = "."\nmembres = ["faux1"]\n', encoding="utf-8"
        )
        fil = Transcript(racine / "rooms" / "essai" / "transcript.jsonl")
        fil.ajouter("utilisateur", "toi", "bonjour")
        fil.ajouter("bot", "faux1", "salut")

        app = Accueil(racine)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.2)
            accueil = app.screen
            noms = [s["nom"] for s in accueil.salles]
            assert noms == ["essai", "ancienne"], noms  # la plus récemment écrite en tête
            assert accueil.salles[0]["messages"] == 2, accueil.salles[0]
            assert accueil.salles[1]["quand"] == "jamais ouverte", accueil.salles[1]

            texte = accueil.query_one("#corps").render().plain
            assert "@faux1" in texte and "@faux2" in texte, texte
            assert "SALLES" in texte and "BOTS" in texte

            await pilot.press("down")
            assert accueil.curseur["salles"] == 1

            # ⇥ passe aux bots, et ⏎ y ouvre la fiche — plus l'ouverture d'une salle.
            await pilot.press("tab")
            assert accueil.section == "bots"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, EcranFicheBot), app.screen
            assert app.screen.bot.nom == "faux1", app.screen.bot.nom
            await pilot.press("escape")
            await pilot.pause(0.2)

            await pilot.press("tab")
            assert accueil.section == "salles"
            await pilot.press("enter")
            await pilot.pause(0.1)
        assert app.return_value == "ancienne", app.return_value
        del os.environ["QUORUM_CONFIG"]


async def main() -> None:
    await scenario_vide()
    await scenario_liste()
    print("test_accueil : premier lancement ok · liste ok · ouverture ok")


if __name__ == "__main__":
    asyncio.run(main())
