"""Outils communs aux vérifications : un projet jetable et une attente bornée."""

import sys
from pathlib import Path

FAUX = Path(__file__).with_name("fake_agent.py")


def monter_projet(
    racine: Path, max_rounds: int = 3, relais: bool = False, copie: bool = False
) -> None:
    """Deux bots servis par le faux agent, dans une salle jetable.

    `relais` donne à chacun le nom de l'autre : c'est ce qui lui permet d'interpeller.
    """
    voisins = {"faux1": "faux2", "faux2": "faux1"}
    for nom, voisin in voisins.items():
        dossier = racine / "bots" / nom
        dossier.mkdir(parents=True)
        args = f'"{FAUX}", "{nom}"' + (f', "{voisin}"' if relais else "")
        (dossier / "bot.toml").write_text(
            f'nom = "{nom}"\nrole = "essai"\nteinte = {150 if nom == "faux1" else 250}\n'
            f'commande = "{sys.executable}"\nargs = [{args}]\n'
            f'fournisseur = "gemini"\n'
            + ('dossier = "copie"\n' if copie and nom == "faux1" else ""),
            encoding="utf-8",
        )
    salle = racine / "rooms" / "essai"
    salle.mkdir(parents=True)
    (salle / "room.toml").write_text(
        f'nom = "essai"\ndossier = "."\nmembres = ["faux1", "faux2"]\n'
        f"max_rounds = {max_rounds}\n",
        encoding="utf-8",
    )


async def saisie_prete(pilot, app, quoi: str = "la saisie") -> None:
    """Un bot « prêt » l'est avant que l'écran n'accepte les touches : on attend le focus."""
    await attendre(pilot, lambda: app.query("#message")
                   and app.query_one("#message").has_focus, quoi)


async def attendre(pilot, condition, quoi: str, tours: int = 600) -> None:
    for _ in range(tours):
        if condition():
            return
        await pilot.pause(0.02)
    raise AssertionError(f"jamais arrivé : {quoi}")
