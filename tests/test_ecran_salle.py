"""S12, les réglages, et le garde-fou qui empêche un agent d'écrire n'importe où."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from atelier import attendre, monter_projet, saisie_prete  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum import reglages as config  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.ecrans import EcranReglages, EcranSalle, ListeParticipants, Pas  # noqa: E402
from quorum.room import charger_salle  # noqa: E402


async def scenario_salle() -> None:
    """Composer une salle : un membre retiré, un budget changé, un room.toml relisible."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=3)
        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test(size=(110, 45)) as pilot:
            await attendre(pilot, lambda: all(p.pret for p in app.participants.values()), "sessions")
            await saisie_prete(pilot, app)
            app.action_composer()
            await pilot.pause(0.2)
            ecran = app.screen
            assert isinstance(ecran, EcranSalle), ecran

            liste = ecran.query_one(ListeParticipants)
            assert liste.choisis == ["faux1", "faux2"], liste.choisis
            liste.focus()
            await pilot.press("down")
            await pilot.press("space")
            assert liste.choisis == ["faux1"], liste.choisis

            enchainements = next(p for p in ecran.query(Pas) if p.etiquette == "enchaînements")
            enchainements.focus()
            await pilot.press("left")   # 3 → 2
            assert enchainements.valeur == "2", enchainements.valeur

            await pilot.press("ctrl+s")
            await attendre(pilot, lambda: not isinstance(app.screen, EcranSalle), "la fermeture")

        relue = charger_salle(racine, "essai")
        assert relue.membres == ["faux1"], relue.membres
        assert relue.max_rounds == 2 and relue.couper_si_repetition, relue


async def scenario_reglages() -> None:
    """Les réglages s'écrivent, et ce qui se voit s'applique tout de suite."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        os.environ["QUORUM_CONFIG"] = str(racine / "config")
        monter_projet(racine, max_rounds=0)
        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test(size=(110, 45)) as pilot:
            await attendre(pilot, lambda: all(p.pret for p in app.participants.values()), "sessions")
            await saisie_prete(pilot, app)
            assert app.reglages["raisonnement"] == "replié"
            app.action_reglages()
            await pilot.pause(0.2)
            ecran = app.screen
            assert isinstance(ecran, EcranReglages), ecran

            raisonnement = next(p for p in ecran.query(Pas) if p.etiquette == "raisonnement")
            raisonnement.focus()
            await pilot.press("right")
            assert raisonnement.valeur == "dernière ligne"
            hors = next(p for p in ecran.query(Pas) if p.etiquette == "hors dossier")
            hors.focus()
            await pilot.press("right")
            assert hors.valeur == "laisser faire"

            await pilot.press("ctrl+s")
            await attendre(pilot, lambda: not isinstance(app.screen, EcranReglages), "la fermeture")
            assert app.reglages["raisonnement"] == "dernière ligne", app.reglages
            assert app.reglages["demander_hors_dossier"] is False

        ecrit = json.loads((racine / "config" / "settings.json").read_text())
        assert ecrit["raisonnement"] == "dernière ligne" and not ecrit["demander_hors_dossier"]
        del os.environ["QUORUM_CONFIG"]


async def scenario_garde_fou() -> None:
    """Une écriture hors du dossier de la salle ne passe pas sans décision de l'utilisateur."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        os.environ["QUORUM_CONFIG"] = str(racine / "config")
        monter_projet(racine, max_rounds=0)
        dehors = Path(tempfile.mkdtemp()) / "vole.txt"
        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test() as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)
            assert app.reglages["demander_hors_dossier"] is True

            app.query_one("#message", Input).value = f"@faux1 ECRIRE:{dehors}"
            await pilot.press("enter")
            await attendre(pilot, lambda: un.panneau is not None, "l'autorisation d'écriture")
            assert "hors du dossier" in un.panneau.appel["title"], un.panneau.appel
            refus = next(o for o in un.panneau.options if o["kind"] == "reject_once")
            un.panneau.choisir(refus)
            await attendre(pilot, lambda: un.tour.done(), "la fin du tour")
            assert not dehors.exists(), "le fichier ne doit pas avoir été écrit"
            assert "refusée" in un.bulle.corps, un.bulle.corps

            # Une écriture dans le dossier de la salle passe sans rien demander.
            dedans = app.salle.dossier / "dedans.txt"
            app.query_one("#message", Input).value = f"@faux1 ECRIRE:{dedans}"
            await pilot.press("enter")
            await attendre(pilot, lambda: un.tour.done() and dedans.exists(), "l'écriture permise")
            assert dedans.read_text().startswith("écrit par l'agent")
        del os.environ["QUORUM_CONFIG"]


async def main() -> None:
    await scenario_salle()
    await scenario_reglages()
    await scenario_garde_fou()
    print("test_ecran_salle : composition ok · réglages ok · garde-fou d'écriture ok")


if __name__ == "__main__":
    asyncio.run(main())
