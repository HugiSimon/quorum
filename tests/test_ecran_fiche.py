"""S11 pilotée au clavier : créer un bot sans toucher un fichier, puis le relire."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input, TextArea  # noqa: E402

from atelier import attendre, monter_projet, saisie_prete  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.ecrans import EcranFicheBot, Pas, TableRegles, Teinte  # noqa: E402
from quorum.room import charger_salle  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        monter_projet(racine, max_rounds=0)
        app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
        async with app.run_test(size=(110, 45)) as pilot:
            await attendre(pilot, lambda: all(p.pret for p in app.participants.values()), "sessions")
            await saisie_prete(pilot, app)

            # La liste des modèles vient de la session, pas d'une constante.
            assert app.modeles_connus() == ["faux-1"], app.modeles_connus()

            app.action_fiche()
            await pilot.pause(0.2)
            ecran = app.screen
            assert isinstance(ecran, EcranFicheBot), ecran
            assert ecran.nouveau, "sans bulle sélectionnée, on ouvre un bot neuf"
            modele = next(p for p in ecran.query(Pas) if p.etiquette == "modèle")
            assert modele.valeurs == ["faux-1"], modele.valeurs

            ecran.query_one("#nom", Input).value = "veilleur"
            ecran.query_one("#role", Input).value = "veille"
            ecran.query_one("#prompt", TextArea).text = "Tu surveilles et tu alertes."

            # La teinte évite les bandes réservées à l'interface.
            teinte = ecran.query_one(Teinte)
            teinte.teinte = 40
            teinte.focus()
            await pilot.press("right")
            assert teinte.teinte not in range(45, 76), teinte.teinte

            # La table : une règle ajoutée, sa décision changée, l'ordre inversé.
            table = ecran.query_one(TableRegles)
            table.focus()
            depart = len(table.regles)
            await pilot.press("a")
            await attendre(pilot, lambda: bool(ecran.query("#motif")), "la saisie du motif")
            ecran.query_one("#motif", Input).value = "shell:git *"
            await pilot.press("enter")
            assert len(table.regles) == depart + 1, table.regles
            assert table.regles[0].motif == "shell:git *", table.regles

            table.focus()
            await pilot.press("d")
            assert table.regles[0].decision == "deny", table.regles[0]
            await pilot.press("d")  # deny → allow, le cycle boucle sur trois valeurs
            assert table.regles[0].decision == "allow", table.regles[0]

            await pilot.press("shift+down")
            assert table.regles[1].motif == "shell:git *", table.regles
            assert table.curseur == 1

            # La jauge dit ce que les règles permettent, et le prévient honnêtement.
            assert "lecture seule" in table.texte_brut, "la table prévient de ce qu'elle ne couvre pas"

            await pilot.press("ctrl+s")
            await attendre(pilot, lambda: not isinstance(app.screen, EcranFicheBot), "la fermeture")

        # Les trois fichiers sont là, et se relisent.
        dossier = racine / "bots" / "veilleur"
        assert {f.name for f in dossier.iterdir()} == {"bot.toml", "system.md", "policy.toml"}
        relu = bots.charger(dossier)
        assert relu.nom == "veilleur" and relu.role == "veille"
        assert relu.teinte == teinte.teinte and not relu.copie_isolee
        assert "Tu surveilles" in (dossier / "system.md").read_text()
        motifs = [(r.motif, r.decision) for r in bots.lire_regles(dossier)]
        assert ("shell:git *", "allow") in motifs, motifs
        assert "veilleur" in bots.charger_tous(racine / "bots")
    print("test_ecran_fiche : modèles de la session ok · teinte ok · table ok · trois fichiers ok")


if __name__ == "__main__":
    asyncio.run(main())
