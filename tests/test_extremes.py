"""S13 : 80×24, 200 colonnes, et le thème clair. Rien n'est retiré, tout est raccourci."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.widgets import Input, Static  # noqa: E402

from atelier import attendre, monter_projet, saisie_prete  # noqa: E402
from quorum import bot as bots  # noqa: E402
from quorum import reglages as config  # noqa: E402
from quorum.app import Quorum  # noqa: E402
from quorum.room import charger_salle  # noqa: E402
from quorum.theme import N, NEUTRES_CLAIR, NEUTRES_SOMBRE, couleur_bot  # noqa: E402


async def ouvrir(racine: Path, taille):
    monter_projet(racine, max_rounds=0)
    app = Quorum(charger_salle(racine, "essai"), bots.charger_tous(racine / "bots"))
    return app, app.run_test(size=taille)


async def scenario_exigu() -> None:
    """80×24 : le rôle disparaît, l'heure passe en relatif, les choix se réduisent au verbe."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        app, contexte = await ouvrir(racine, (80, 24))
        async with contexte as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)
            assert app.compacte, app.size.width

            app.query_one("#message", Input).value = "@faux1 PERM supprime .venv"
            await pilot.press("enter")
            await attendre(pilot, lambda: un.panneau is not None, "l'autorisation")
            await pilot.pause(0.25)

            rendu = un.bulle.rendu().plain
            assert un.bulle.compacte, "la bulle doit se raccourcir"
            assert "essai" not in rendu, f"le rôle doit disparaître : {rendu}"
            assert "maintenant" in rendu, f"horodatage relatif attendu : {rendu}"
            assert "@faux1" in rendu, "le nom, lui, ne part jamais"

            choix = un.panneau.render().plain
            assert "1 ✓allow" in choix and "3 ✕reject" in choix, choix
            assert "r commenter" in choix, choix
            assert choix.count("\n") <= 3, f"tout doit tenir serré : {choix!r}"

            assert not app.query_one("#cote", Static).display, "pas de marge à 80 colonnes"
            un.panneau.choisir(un.panneau.options[0])
            await attendre(pilot, lambda: un.tour.done(), "la fin du tour")


async def scenario_immense() -> None:
    """200 colonnes : les marges deviennent utiles, le fil ne s'étale pas."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        app, contexte = await ouvrir(racine, (200, 48))
        async with contexte as pilot:
            un = app.participants["faux1"]
            await attendre(pilot, lambda: un.pret, "la session")
            await saisie_prete(pilot, app)
            await pilot.pause(0.3)
            assert not app.compacte

            cote = app.query_one("#cote", Static)
            assert cote.display, "au-delà de 160 colonnes, le panneau latéral s'ouvre"
            texte = cote.render().plain
            assert "DANS CETTE SALLE" in texte and "@faux1" in texte and "@faux2" in texte, texte
            assert "personne ne travaille" in texte, texte


async def scenario_theme_clair() -> None:
    """Même teinte, autre clarté : l'identité du bot ne change pas de couleur, juste de ton."""
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        os.environ["QUORUM_CONFIG"] = str(racine / "config")
        config.ecrire({**config.DEFAUTS, "theme": "clair"})
        app, contexte = await ouvrir(racine, (120, 30))
        async with contexte as pilot:
            await attendre(pilot, lambda: all(p.pret for p in app.participants.values()), "sessions")
            await saisie_prete(pilot, app)
            assert N["fond"] == NEUTRES_CLAIR["fond"], N["fond"]
            assert app.get_css_variables()["encre"] == NEUTRES_CLAIR["encre"]

            faux1 = app.participants["faux1"]
            assert faux1.couleur == couleur_bot(faux1.bot.teinte, sombre=False)
            assert faux1.couleur != couleur_bot(faux1.bot.teinte, sombre=True)

            # Et on rebascule à chaud, sans redémarrer.
            app.reglages_changes({**app.reglages, "theme": "sombre"})
            await pilot.pause(0.2)
            assert N["fond"] == NEUTRES_SOMBRE["fond"], N["fond"]
            assert app.participants["faux1"].couleur == couleur_bot(faux1.bot.teinte, sombre=True)
        del os.environ["QUORUM_CONFIG"]


async def main() -> None:
    await scenario_exigu()
    await scenario_immense()
    await scenario_theme_clair()
    print("test_extremes : 80×24 ok · 200 colonnes ok · thème clair et bascule à chaud ok")


if __name__ == "__main__":
    asyncio.run(main())
