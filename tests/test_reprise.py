"""T5 : reprise de session, mémoire expirée, éviction et réveil, copie isolée."""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from atelier import attendre, monter_projet, saisie_prete  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import PanneauExpiration, Quorum  # noqa: E402
from quorum.room import Transcript, charger_salle  # noqa: E402


async def un_tour(racine: Path) -> None:
    """Une salle qui a vécu : un message, deux réponses, un état sur disque."""
    app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
    async with app.run_test() as pilot:
        await attendre(pilot, lambda: all(p.pret for p in app.participants.values()), "sessions")
        await saisie_prete(pilot, app)
        app.query_one("#message", Input).value = "bonjour"
        await pilot.press("enter")
        await attendre(pilot, lambda: app.conversation.done(), "le tour", 1500)


async def scenario_expiration() -> None:
    """Une session perdue ne doit pas emporter le fil : S10 propose ses trois issues."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=0)
        await un_tour(racine)

        salle = charger_salle(racine, "essai")
        etat = json.loads((salle.racine / "state.json").read_text())
        assert etat["sessions"], etat
        etat["sessions"] = {nom: "sess-perdue" for nom in etat["sessions"]}
        (salle.racine / "state.json").write_text(json.dumps(etat))
        avant = len(Transcript(salle.racine / "transcript.jsonl").entrees)

        app = Quorum(salle, bots.charger_tous(racine / "bots"))
        async with app.run_test() as pilot:
            await attendre(pilot, lambda: bool(app.query(PanneauExpiration)), "le panneau S10")
            for participant in app.participants.values():
                assert participant.memoire_expiree, participant.nom
                assert participant.demarrage == "neuf", participant.demarrage
                assert participant.vu == 0, "mémoire perdue : le bot repart du fil entier"
            assert app.query_one(PanneauExpiration).texte_brut.count("\n") >= 4

            # Issue 3 : archiver. Le fil part de côté, rien n'est supprimé.
            app.query_one(PanneauExpiration).focus()
            await pilot.press("3")
            await attendre(pilot, lambda: bool(list(salle.racine.glob("transcript-*.jsonl"))),
                           "l'archive")
            archive = next(salle.racine.glob("transcript-*.jsonl"))
            assert len(Transcript(archive).entrees) == avant, archive
            assert app.transcript.entrees == [], "le fil repart vide"


async def scenario_resume() -> None:
    """Issue 2 : les bots écrivent eux-mêmes le résumé du fil qu'ils ont oublié."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=0)
        await un_tour(racine)

        salle = charger_salle(racine, "essai")
        etat = json.loads((salle.racine / "state.json").read_text())
        etat["sessions"] = {nom: "sess-perdue" for nom in etat["sessions"]}
        (salle.racine / "state.json").write_text(json.dumps(etat))

        app = Quorum(salle, bots.charger_tous(racine / "bots"))
        async with app.run_test() as pilot:
            await attendre(pilot, lambda: bool(app.query(PanneauExpiration)), "le panneau S10")
            app.query_one(PanneauExpiration).focus()
            await pilot.press("2")
            await attendre(pilot, lambda: app.conversation is not None and app.conversation.done(),
                           "le résumé", 1500)
            for participant in app.participants.values():
                assert participant.bulle is not None and participant.bulle.corps.strip()
                assert participant.vu > 0, "après le résumé, le fil repart de là"


async def scenario_eviction() -> None:
    """Un bot évincé rend sa mémoire vive, et son tour suivant le réveille."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=0)
        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test() as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)

            await app.evincer(un)
            assert not un.pret and un.demarrage == "évincé" and un.client is None

            app.query_one("#message", Input).value = "@faux1 te voilà réveillé ?"
            await pilot.press("enter")
            await attendre(pilot, lambda: app.conversation.done(), "le tour de réveil", 1500)
            assert un.pret, "le tour doit réveiller le bot évincé"
            assert un.demarrage == "repris", un.demarrage
            assert un.bulle.corps.strip(), un.bulle.corps


async def scenario_copie_isolee() -> None:
    """Dans un dépôt git, un bot qui écrit a sa copie ; hors dépôt, on partage et on le dit."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=0, copie=True)
        subprocess.run(["git", "init", "-q"], cwd=racine, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                        "commit", "-q", "--allow-empty", "-m", "depart"], cwd=racine, check=True)

        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test() as pilot:
            un, deux = app.participants["faux1"], app.participants["faux2"]
            await attendre(pilot, lambda: un.pret and deux.pret, "les sessions")
            await saisie_prete(pilot, app)
            assert un.dossier is not None and un.dossier.name == "faux1", un.dossier
            assert un.dossier.exists() and un.dossier != app.salle.dossier
            assert deux.dossier == app.salle.dossier, "un bot en lecture seule partage"

    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=0, copie=True)
        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test() as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)
            assert un.dossier == app.salle.dossier, "hors dépôt git, pas de copie possible"
            fil = "\n".join(getattr(b, "texte_brut", "") for b in app.query("#fil > Static"))
            assert "pas de copie isolée possible" in fil, fil


async def main() -> None:
    await scenario_expiration()
    await scenario_resume()
    await scenario_eviction()
    await scenario_copie_isolee()
    print("test_reprise : expiration ok · résumé ok · éviction et réveil ok · copie isolée ok")


if __name__ == "__main__":
    asyncio.run(main())
