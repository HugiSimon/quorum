"""Les rounds bornés, le refus commenté et la coupure sur radotage."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from atelier import attendre, monter_projet, saisie_prete  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.room import Transcript, charger_salle  # noqa: E402


def lignes_du_fil(app) -> str:
    return "\n".join(getattr(bloc, "texte_brut", "") for bloc in app.query("#fil > Static"))


async def ouvrir(racine: Path, max_rounds: int, relais: bool):
    monter_projet(racine, max_rounds=max_rounds, relais=relais)
    salle = charger_salle(racine, "essai")
    return Quorum(salle, bots.charger_tous(racine / "bots")), salle


async def scenario_budget() -> None:
    """Un bot en interpelle un autre : round suivant, puis le budget rend la main."""
    with tempfile.TemporaryDirectory() as tmp:
        app, salle = await ouvrir(Path(tmp), max_rounds=1, relais=True)
        async with app.run_test() as pilot:
            un, deux = app.participants["faux1"], app.participants["faux2"]
            await attendre(pilot, lambda: un.pret and deux.pret, "les sessions")
            await saisie_prete(pilot, app)

            app.query_one("#message", Input).value = "@faux1 RELANCE le débat"
            await pilot.press("enter")
            await attendre(pilot, lambda: app.conversation.done(), "la conversation s'arrête", 1500)

            fil = lignes_du_fil(app)
            assert "round 2 · @faux2" in fil, fil
            assert "budget d'enchaînement épuisé (1)" in fil, fil
            auteurs = [e.auteur for e in Transcript(salle.racine / "transcript.jsonl").entrees]
            assert auteurs == ["toi", "faux1", "faux2"], auteurs


async def scenario_radotage() -> None:
    """Un bot qui se répète coupe le round, même si le budget reste ouvert."""
    with tempfile.TemporaryDirectory() as tmp:
        app, _ = await ouvrir(Path(tmp), max_rounds=6, relais=True)
        async with app.run_test() as pilot:
            un, deux = app.participants["faux1"], app.participants["faux2"]
            await attendre(pilot, lambda: un.pret and deux.pret, "les sessions")
            await saisie_prete(pilot, app)

            app.query_one("#message", Input).value = "@faux1 RELANCE le débat"
            await pilot.press("enter")
            await attendre(pilot, lambda: app.conversation.done(), "la conversation s'arrête", 2000)

            fil = lignes_du_fil(app)
            assert "se répète — round coupé" in fil, fil
            assert "round 3" in fil and "round 6" not in fil, fil
            assert un.radote, un.empreintes


async def scenario_refus_commente() -> None:
    """Un refus n'arrête pas le tour : il devient une consigne, et le fil le garde."""
    with tempfile.TemporaryDirectory() as tmp:
        app, salle = await ouvrir(Path(tmp), max_rounds=0, relais=False)
        async with app.run_test() as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)

            app.query_one("#message", Input).value = "@faux1 PERM supprime .venv"
            await pilot.press("enter")
            await attendre(pilot, lambda: un.panneau is not None, "l'autorisation")
            premiere = un.bulle

            un.panneau.focus()
            await pilot.press("r")
            await attendre(pilot, lambda: bool(app.query("#refus")), "la zone de commentaire")
            app.query_one("#refus", Input).value = "ne supprime pas .venv, il est compilé à la main"
            await pilot.press("enter")

            await attendre(pilot, lambda: un.bulle is not premiere, "le rebond du bot", 1500)
            await attendre(pilot, lambda: un.tour.done(), "la fin du tour", 1500)

            assert "propose" in un.bulle.corps, un.bulle.corps
            assert "« ne supprime pas .venv" in lignes_du_fil(app)

            entrees = Transcript(salle.racine / "transcript.jsonl").entrees
            genres = [(e.genre, e.auteur) for e in entrees]
            assert ("refus", "toi") in genres, genres
            assert genres[-1] == ("bot", "faux1"), genres
            # Le commentaire doit être lisible par les autres bots, donc dans le fil.
            assert any("compilé à la main" in e.texte for e in entrees if e.genre == "refus")


async def scenario_interruption() -> None:
    """^C referme les blocs, garde ce qui a été dit, et coupe l'enchaînement."""
    with tempfile.TemporaryDirectory() as tmp:
        app, salle = await ouvrir(Path(tmp), max_rounds=5, relais=True)
        async with app.run_test() as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)

            app.query_one("#message", Input).value = "@faux1 PERM supprime .venv"
            await pilot.press("enter")
            await attendre(pilot, lambda: un.panneau is not None, "l'autorisation en vol")

            app.action_interrompre()
            await attendre(pilot, lambda: un.tour.done(), "la fin du tour interrompu", 1500)

            # Le bloc se referme : il n'anime pas indéfiniment.
            assert un.etat == "horsjeu" and un.bulle.etat == "horsjeu", un.etat
            assert un.panneau is None or un.panneau.decision == "annulée"
            assert app.interrompu is True
            await attendre(pilot, lambda: app.conversation.done(), "l'enchaînement coupé", 1500)

            genres = [(e.genre, e.auteur) for e in Transcript(salle.racine / "transcript.jsonl").entrees]
            assert ("systeme", "toi") in genres, genres
            # Et le tour suivant repart normalement.
            ancienne = app.conversation
            app.query_one("#message", Input).value = "@faux1 et maintenant ?"
            await pilot.press("enter")
            await attendre(pilot,
                           lambda: app.conversation is not ancienne and app.conversation.done(),
                           "le tour d'après", 1500)
            assert un.bulle.corps.strip(), un.bulle.corps


async def main() -> None:
    await scenario_budget()
    await scenario_radotage()
    await scenario_refus_commente()
    await scenario_interruption()
    print("test_rounds : budget ok · radotage ok · refus commenté ok · interruption ok")


if __name__ == "__main__":
    asyncio.run(main())
