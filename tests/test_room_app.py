"""La salle de bout en bout : deux bots en parallèle, deux autorisations, un fil qui survit."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.widgets import Input  # noqa: E402

from quorum import bot as bots  # noqa: E402
from quorum.app import Bulle, Quorum  # noqa: E402
from quorum.room import Transcript, charger_salle  # noqa: E402

from atelier import attendre, monter_projet, saisie_prete  # noqa: E402

async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine)
        salle = charger_salle(racine, "essai")
        connus = bots.charger_tous(racine / "bots")

        app = Quorum(salle, connus)
        app.transcript = Transcript(salle.racine / "transcript.jsonl")
        async with app.run_test() as pilot:
            un, deux = app.participants["faux1"], app.participants["faux2"]
            await attendre(pilot, lambda: un.pret and deux.pret, "les deux sessions s'ouvrent")
            await saisie_prete(pilot, app)

            # Une mention explicite ne réveille qu'un bot.
            app.query_one(Input).value = "@faux1 PERM supprime .venv"
            await pilot.press("enter")
            await attendre(pilot, lambda: un.panneau is not None, "l'autorisation de faux1")
            assert deux.tour is None, "une mention explicite ne doit réveiller personne d'autre"
            assert [o["kind"] for o in un.panneau.options] == \
                ["allow_once", "allow_always", "reject_once"], \
                "la touche 1 ne doit jamais tomber sur la permission la plus large"
            assert un.panneau.options[0]["name"] == "Allow", "le libellé de l'agent, tel quel"
            assert un.bulle.outils and un.bulle.outils[0]["titre"].startswith("rm -rf")
            assert un.bulle.outils[0]["famille"] == "shell", un.bulle.outils[0]
            un.panneau.choisir(un.panneau.options[0])
            await attendre(pilot, lambda: un.tour.done(), "le tour de faux1 se termine")

            # Sans mention, tout le monde répond — et en parallèle.
            app.query_one(Input).value = "PERM et vous en pensez quoi ?"
            await pilot.press("enter")
            await attendre(
                pilot,
                lambda: un.panneau is not None and deux.panneau is not None,
                "deux autorisations en attente en même temps",
            )
            assert un.bulle is not deux.bulle, "deux flux, deux blocs"
            focus = [p.nom for p in app.participants.values() if p.panneau.has_focus]
            assert len(focus) == 1, f"un seul panneau décide à la fois, pas {focus}"

            for participant in (un, deux):
                participant.panneau.choisir(participant.panneau.options[0])
            await attendre(pilot, lambda: un.tour.done() and deux.tour.done(), "les deux tours")

            assert "faux1" in un.bulle.corps and "faux2" not in un.bulle.corps, un.bulle.corps
            assert "faux2" in deux.bulle.corps and "faux1" not in deux.bulle.corps, deux.bulle.corps

        # Le fil a survécu à la fermeture, et se relit sans aucun agent.
        relu = Transcript(salle.racine / "transcript.jsonl")
        genres = [(e.genre, e.auteur) for e in relu.entrees]
        assert genres == [
            ("utilisateur", "toi"), ("bot", "faux1"),
            ("utilisateur", "toi"), ("bot", "faux1"), ("bot", "faux2"),
        ], genres

        froid = Quorum(charger_salle(racine, "essai"), connus)
        async with froid.run_test() as pilot:
            await attendre(pilot, lambda: all(p.pret for p in froid.participants.values()),
                           "les sessions reprises")
            await saisie_prete(pilot, froid)
            bulles = froid.query_one("#fil").query(Bulle)
            assert len(bulles) >= 5, f"{len(bulles)} bulles restaurées"
            # L'index vu survit au redémarrage : un bot ne se fait pas relire ce qu'il sait.
            # Il diffère d'un bot à l'autre, et c'est juste — faux1 a fini son tour avant
            # que faux2 n'écrive, donc il n'a pas encore vu son message.
            for participant in froid.participants.values():
                assert participant.demarrage == "repris", participant.demarrage
                assert participant.vu == froid.etat_salle["vu"][participant.nom] > 0, (
                    participant.nom, participant.vu, froid.etat_salle["vu"]
                )
                assert not participant.memoire_expiree
            assert not froid.query(".expiration"), "rien n'a expiré : pas de panneau S10"
    print("test_room_app : mention ok · parallèle ok · 2 autorisations ok · fil persistant ok")


if __name__ == "__main__":
    asyncio.run(main())
