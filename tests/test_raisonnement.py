"""S7 : les jalons, les fichiers touchés, et le sort d'une sortie qui tarde ou ne vient pas."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input  # noqa: E402

from atelier import attendre, monter_projet, saisie_prete  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import EcranRaisonnement, Quorum  # noqa: E402
from quorum.room import charger_salle  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=0)
        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test(size=(100, 40)) as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)

            app.query_one("#message", Input).value = "@faux1 PERM supprime .venv"
            await pilot.press("enter")
            await attendre(pilot, lambda: un.panneau is not None, "l'autorisation")

            # Deux jalons titrés, tirés d'un seul bloc de pensée.
            assert [j["titre"] for j in un.bulle.jalons] == [
                "Reading The Sources", "Executing Shell Commands"
            ], un.bulle.jalons
            assert un.bulle.jalons[0]["corps"] == "I am looking at the notes first."

            # `locations` alimente le panneau des fichiers touchés.
            assert un.fichiers == {"/tmp/notes.md": "lu"}, un.fichiers

            # ^R ouvre le raisonnement du dernier bot actif — au clavier, sans souris.
            assert un.bulle.proprietaire == "faux1"
            assert app.dernier_actif() == "faux1", app.dernier_actif()
            app.action_raisonnement()
            await pilot.pause(0.25)
            ecran = app.screen
            assert isinstance(ecran, EcranRaisonnement), ecran
            rendu = ecran.dernier_rendu.plain
            for attendu in ("JALONS", "Reading The Sources", "Executing Shell Commands",
                            "FICHIERS TOUCHÉS", "/tmp/notes.md", "CHRONO", "JETONS"):
                assert attendu in rendu, f"{attendu} absent de l'écran S7"
            # Le dernier jalon est ouvert, les autres repliés.
            assert "▾ Executing Shell Commands" in rendu and "┊ Reading The Sources" in rendu

            await pilot.press("escape")
            await pilot.pause(0.25)

            # Le chemin complet de la sortie : le faux agent l'écrit dans son journal, le
            # suiveur la retrouve et la raccorde à la bonne ligne d'outil. Ce test attrape
            # aussi le piège de l'inode : vider le journal après le lancement de l'agent
            # ferait disparaître la sortie pour toujours.
            assert un.journal is not None, "un bot gemini doit avoir un adaptateur de sorties"
            shell = next(o for o in un.bulle.outils if o["famille"] == "shell")
            assert shell["attend_sortie"] is False and shell["fin"] is None, shell

            un.panneau.choisir(un.panneau.options[0])
            await attendre(pilot, lambda: un.tour.done(), "la fin du tour")
            await attendre(pilot, lambda: shell["sortie"] is not None, "la sortie du journal", 1500)

            assert shell["sortie"] == "        2 donnees.txt", repr(shell["sortie"])
            assert "sortie +" in un.bulle.rendu().plain
            assert un.journal.chemin.exists(), "le journal doit rester sur disque, pas être effacé"

            # Et une sortie qui ne viendra jamais est annoncée, pas animée à l'infini.
            lecture = next(o for o in un.bulle.outils if o["famille"] == "fs")
            lecture["fin"] = lecture["debut"] + 0.2
            lecture["attend_sortie"] = True
            from quorum import telemetry
            garde, telemetry.GRACE = telemetry.GRACE, 0.05
            try:
                await app.clore_sorties(un.bulle)
            finally:
                telemetry.GRACE = garde
            assert lecture["sortie_perdue"] and not lecture["attend_sortie"], lecture
            assert "sortie non parvenue" in un.bulle.rendu().plain

            # Et si elle finit par venir malgré tout, l'interface se dédit.
            app.sur_sortie(un, "call_7", "contenu tardif")
            assert not lecture["sortie_perdue"] and lecture["sortie"] == "contenu tardif", lecture
            assert "sortie non parvenue" not in un.bulle.rendu().plain

            # Une fois le tour fini, le fil ne garde que le message et un compte discret.
            un.bulle.etat = "fini"
            fini = un.bulle.rendu().plain
            assert "Reading The Sources" not in fini, fini
            assert "ouvrir le raisonnement" not in fini, fini
            assert "2 outils" in fini, fini
    print("test_raisonnement : jalons ok · fichiers ok · écran S7 ok · sortie décalée et perdue ok")


if __name__ == "__main__":
    asyncio.run(main())
