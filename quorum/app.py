"""L'application : une salle, plusieurs bots, un seul fil.

Le transcript fait foi. Une bulle est créée quand un bot commence à parler — avant d'avoir
du contenu — et se remplit sur place ; plusieurs peuvent se remplir en même temps, et un bot
n'insère jamais de ligne dans le bloc d'un autre.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static

from . import bot as bots
from .ecrans import Accueil, EcranFicheBot, EcranReglages, EcranSalle
from .acp import AcpErreur, ClientAcp, ecrire_fichier
from .room import (
    Entree,
    archiver,
    ecrire_etat,
    lire_etat,
    resume_du_fil,
    decouper_pensee,
    Salle,
    Transcript,
    charger_salle,
    destinataires,
    empreinte,
    prompt_pour,
    relances,
)
from . import reglages as config
from . import telemetry
from .telemetry import SortiesGemini, identifiant_court
from .theme import (
    ANIM_PENSE,
    appliquer_theme,
    ANIM_TRAVAILLE,
    ATTENTION,
    CLIQUABLE,
    ETATS,
    FAMILLES,
    N,
    ROUGE,
    VERT,
    couleur_bot,
    regle,
    theme_du_terminal,
)

VIVANTS = ("pense", "execute", "demande")

# Au-delà, on considère la mémoire perdue plutôt que de laisser la salle fermée.
ATTENTE_REPRISE = 30.0

# Le `kind` est la seule chose stable : il décide de la couleur et de la place, jamais du
# libellé. L'agent réel envoie allow_always en premier — laisser cet ordre mettrait la
# permission la plus large sous la touche 1.
ORDRE_DES_CHOIX = {"allow_once": 0, "allow_always": 1, "reject_once": 2, "reject_always": 3}


def _duree(secondes: float) -> str:
    return f"{int(secondes) // 60}:{int(secondes) % 60:02d}"


class Participant:
    """Un membre de la salle : son bot, son process, sa session, sa bulle du moment."""

    def __init__(self, bot: bots.Bot) -> None:
        self.bot = bot
        self.couleur = couleur_bot(bot.teinte)
        self.client: ClientAcp | None = None
        self.session: dict = {}
        self.etat = "horsjeu"
        self.bulle: Bulle | None = None
        # Les dernières bulles du bot : une sortie arrive plusieurs secondes après, parfois
        # une fois qu'il a déjà rebondi dans un nouveau bloc.
        self.bulles: list[Bulle] = []
        self.panneau: PanneauAutorisation | None = None
        self.tour: asyncio.Task | None = None
        self.vu = 0
        self.empreintes: list[str] = []
        self.refus: str | None = None  # commentaire à transmettre au tour suivant
        self.journal: SortiesGemini | None = None
        self.demarrage = "en file"
        self.memoire_expiree = False
        self.derniere_activite = 0.0
        self.dossier: Path | None = None
        self.fichiers: dict[str, str] = {}
        self.chrono: list[tuple[float, str]] = []
        self.debut_tour = 0.0

    @property
    def radote(self) -> bool:
        """Deux messages de suite identiques : le bot tourne en rond, on coupe."""
        return len(self.empreintes) >= 2 and self.empreintes[-1] == self.empreintes[-2]

    @property
    def nom(self) -> str:
        return self.bot.nom

    @property
    def pret(self) -> bool:
        return bool(self.session) and self.client is not None and self.client.vivant


class Bulle(Static):
    """Un bloc d'un auteur : en-tête, outils, corps. Le même gabarit pour tout le monde."""

    can_focus = True

    def __init__(
        self,
        auteur: str,
        role: str,
        couleur: str,
        heure: str,
        etat: str | None = None,
        debut: float | None = None,
    ) -> None:
        super().__init__()
        self.auteur, self.role, self.couleur, self.heure = auteur, role, couleur, heure
        self.etat = etat
        self.debut = debut
        self.corps = ""
        self.outils: list[dict] = []
        self.jalons: list[dict] = []
        self.proprietaire: str | None = None
        self.raisonnement_visible = True
        self.raisonnement = "replié"
        self.compacte = False
        self.phase = 0

    def rendu(self) -> Text:
        texte = Text()
        texte.append("▌ ", style=self.couleur)
        texte.append(self.auteur, style=f"bold {self.couleur}")
        if self.role and not self.compacte:
            texte.append(f"  {self.role}", style=N["dim"])
        texte.append(f" · {self.heure_relative if self.compacte else self.heure}", style=N["faible"])

        if self.etat is not None:
            glyphe, mot = ETATS[self.etat]
            if self.etat == "pense":
                glyphe = ANIM_PENSE[self.phase % len(ANIM_PENSE)]
            elif self.etat == "execute":
                glyphe = ANIM_TRAVAILLE[self.phase % len(ANIM_TRAVAILLE)]
            texte.append(f"  {glyphe} {mot}", style=self.couleur)
            if self.etat in VIVANTS and self.debut is not None:
                texte.append(f" · {_duree(time.monotonic() - self.debut)}", style=N["faible"])
        texte.append("\n")

        for outil in self.outils:
            texte.append_text(self.ligne_outil(outil))

        for ligne in self.corps.rstrip().splitlines():
            texte.append(f"  {ligne}\n", style=N["encre"])

        if self.etat is not None and self.raisonnement_visible and self.jalons:
            montres = (
                [] if self.compacte
                else self.jalons if self.raisonnement == "déplié"
                else self.jalons[-1:] if self.raisonnement == "dernière ligne"
                else []
            )
            for jalon in montres:
                texte.append("  ┊ ", style=self.couleur)
                texte.append(f"{jalon['titre']}\n", style=N["dim"])

        if self.etat is not None and self.raisonnement_visible and (self.jalons or self.outils):
            texte.append("  ┊ ouvrir le raisonnement ⏎", style=CLIQUABLE)
            details = f" · {len(self.jalons)} jalons · {len(self.outils)} outils"
            if self.debut is not None:
                details += f" · {_duree(time.monotonic() - self.debut)}"
            texte.append(f"{details}\n", style=N["faible"])
        return texte

    def ligne_outil(self, outil: dict) -> Text:
        """Une ligne d'outil : famille · titre · durée, et le sort de sa sortie.

        La sortie n'arrive pas avec la fin de l'outil : une à deux secondes plus tard, par
        le journal local. « sortie en route » est l'état normal, pas une anomalie.
        """
        braille = ANIM_TRAVAILLE[self.phase % len(ANIM_TRAVAILLE)]
        texte = Text()
        texte.append("  ▸ ", style=self.couleur)
        if outil.get("famille"):
            texte.append(f"{outil['famille']} · ", style=N["faible"])
        texte.append(outil["titre"], style=N["dim"])
        if not outil.get("fin"):
            texte.append(f"  {braille}\n", style=N["faible"])
            return texte

        texte.append(f"  ✓ {outil['fin'] - outil['debut']:.1f}s", style=N["faible"])
        if outil.get("sortie") is not None:
            retard = outil.get("arrivee", outil["fin"]) - outil["fin"]
            texte.append(f" · sortie +{retard:.1f}s\n", style=N["faible"])
            for ligne in str(outil["sortie"]).rstrip().splitlines()[:12]:
                texte.append(f"      {ligne}\n", style=N["dim"])
        elif outil.get("attend_sortie"):
            texte.append(f" · sortie en route {braille}\n", style=N["faible"])
        elif outil.get("sortie_perdue"):
            texte.append(" · sortie non parvenue\n", style=N["faible"])
        else:
            texte.append("\n")
        return texte

    def rafraichir(self) -> None:
        self.update(self.rendu())

    def on_click(self) -> None:
        self.focus()

    def on_key(self, evenement) -> None:
        if evenement.key == "enter" and self.ouvrable:
            evenement.stop()
            self.app.ouvrir_raisonnement(self.proprietaire)

    @property
    def ouvrable(self) -> bool:
        return self.proprietaire is not None and bool(self.jalons or self.outils)

    @property
    def heure_relative(self) -> str:
        """Sous 100 colonnes l'horodatage passe en relatif : deux caractères au lieu de cinq."""
        if self.debut is None:
            return self.heure
        minutes = int((time.monotonic() - self.debut) // 60)
        return "maintenant" if minutes < 1 else f"il y a {minutes} min"

    @property
    def texte_brut(self) -> str:
        return f"{self.auteur} {self.corps}"

    def titres_outils(self) -> list[str]:
        return [f"{o.get('famille', '')} {o['titre']}".strip() for o in self.outils]


class PanneauAutorisation(Static):
    """Les choix viennent de l'agent : libellés tels quels, place imposée par le design.

    Rien n'est écrit en dur : le nombre d'options varie d'un outil à l'autre et d'un
    fournisseur à l'autre. Plusieurs bots peuvent en avoir un chacun — ⇥ passe au suivant.
    """

    can_focus = True

    def __init__(self, participant: Participant, params: dict, reponse: asyncio.Future) -> None:
        super().__init__()
        self.participant = participant
        self.options = sorted(
            params.get("options", []),
            key=lambda o: ORDRE_DES_CHOIX.get(str(o.get("kind", "")), 4),
        )
        self.appel = params.get("toolCall", {})
        self.reponse = reponse
        self.decision: str | None = None
        self.phase = 0

    def on_mount(self) -> None:
        self.rafraichir()

    def rafraichir(self) -> None:
        clignote = "◆" if self.decision or self.phase % 2 == 0 else " "
        texte = Text()
        texte.append(f"  {clignote} ", style=ATTENTION)
        texte.append(f"@{self.participant.nom}", style=f"bold {self.participant.couleur}")
        texte.append(" demande une autorisation", style=ATTENTION)
        texte.append("  ⇥ suivante\n" if not self.has_focus else "\n", style=N["faible"])

        titre = self.appel.get("title") or self.appel.get("toolCallId", "")
        if titre:
            texte.append(f"    {titre}\n", style=N["encre"])

        if self.decision:
            texte.append(f"    → {self.decision}\n", style=N["dim"])
        elif self.app.compacte:
            texte.append("    ")
            for i, option in enumerate(self.options, 1):
                genre = str(option.get("kind", ""))
                refus = genre.startswith("reject")
                marque = ("✕" if refus else "✓") * (2 if genre.endswith("always") else 1)
                verbe = str(option.get("name", option.get("optionId", ""))).split()[0].lower()
                texte.append(f"{i} ", style=CLIQUABLE)
                texte.append(f"{marque}{verbe}", style=ROUGE if refus else VERT)
                texte.append(" · ", style=N["faible"])
            texte.append("r commenter\n", style=N["faible"])
        else:
            for i, option in enumerate(self.options, 1):
                genre = str(option.get("kind", ""))
                refus = genre.startswith("reject")
                marque = ("✕" if refus else "✓") * (2 if genre.endswith("always") else 1)
                texte.append(f"    {i} ", style=CLIQUABLE)
                texte.append(f"{marque} ", style=ROUGE if refus else VERT)
                texte.append(f"{option.get('name', option.get('optionId'))}\n", style=N["encre"])
            if self.has_focus:
                texte.append(
                    "    1-9 décider · r refuser en expliquant · esc revenir à la saisie\n",
                    style=N["faible"],
                )
        self.update(texte)

    def rendre_la_saisie(self) -> None:
        """Redonne le focus à la saisie, si elle est encore là."""
        saisies = self.screen.query("#message")
        if saisies:
            saisies.first(Input).focus()

    def on_focus(self) -> None:
        self.rafraichir()

    def on_blur(self) -> None:
        self.rafraichir()

    def on_key(self, evenement) -> None:
        if self.decision is not None:
            return
        if evenement.key == "escape":
            self.rendre_la_saisie()
            evenement.stop()
        elif evenement.key == "r":
            evenement.stop()
            refus = next(
                (o for o in self.options if str(o.get("kind", "")) == "reject_once"), None
            )
            if refus is not None:
                self.app.ouvrir_refus(self, refus)
        elif evenement.key.isdigit() and 1 <= int(evenement.key) <= len(self.options):
            self.choisir(self.options[int(evenement.key) - 1])
            evenement.stop()

    def choisir(self, option: dict) -> None:
        self.decision = option.get("name", option.get("optionId"))
        if not self.reponse.done():
            self.reponse.set_result({"outcome": "selected", "optionId": option["optionId"]})
        self.rafraichir()
        self.rendre_la_saisie()

    def refuser(self, option: dict, commentaire: str) -> None:
        """Refuse, puis fait suivre le commentaire au bot par un prompt — et au fil par une ligne."""
        self.participant.refus = commentaire or None
        if commentaire:
            self.app.tracer_refus(self.participant, commentaire)
        self.choisir(option)

    def abandonner(self) -> None:
        """L'annulation a déjà répondu à l'agent : ici on garde la trace et on rend la saisie.

        Sans ce dernier geste, le focus reste sur un panneau mort et plus rien ne s'écrit.
        """
        if self.decision is None:
            self.decision = "annulée"
            self.rafraichir()
        self.rendre_la_saisie()


class ZoneRefus(Vertical):
    """Le commentaire de refus part au bot, et se pose aussi dans le fil.

    La réponse d'autorisation n'a aucun champ texte : le commentaire ne peut pas passer par
    là. Il part en prompt juste après, et le fil en garde la trace pour les autres bots.
    """

    def __init__(self, panneau: "PanneauAutorisation", option: dict) -> None:
        super().__init__()
        self.panneau, self.option = panneau, option

    def compose(self) -> ComposeResult:
        entete = Static()
        entete.update(
            Text.assemble(
                ("  ✕ ", ROUGE),
                (f"refuser la demande de @{self.panneau.participant.nom}", N["encre"]),
                (" — dis-lui pourquoi (facultatif)\n", N["dim"]),
                ("    ⏎ refuser et envoyer · esc revenir aux choix", N["faible"]),
            )
        )
        yield entete
        yield Input(placeholder="…", id="refus")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, evenement: Input.Submitted) -> None:
        evenement.stop()
        self.panneau.refuser(self.option, evenement.value.strip())
        self.remove()

    def on_key(self, evenement) -> None:
        if evenement.key == "escape":
            evenement.stop()
            self.panneau.focus()
            self.remove()



class PanneauDemarrage(Static):
    """S3 : le fil s'affiche tout de suite, les membres se lèvent un par un."""

    def __init__(self, participants: dict) -> None:
        super().__init__()
        self.participants = participants
        self.phase = 0

    def rafraichir(self) -> None:
        self.phase += 1
        texte = Text()
        texte.append_text(regle("DÉMARRAGE", 60))
        for participant in self.participants.values():
            marque = {
                "en file": ("·", N["faible"]),
                "connexion": (ANIM_PENSE[self.phase % len(ANIM_PENSE)], participant.couleur),
                "session": (ANIM_PENSE[self.phase % len(ANIM_PENSE)], participant.couleur),
                "repris": ("✓", VERT),
                "neuf": ("✓", VERT),
                "évincé": ("◌", N["faible"]),
                "échec": ("✕", ROUGE),
            }.get(participant.demarrage, ("·", N["faible"]))
            texte.append("  ▌ ", style=participant.couleur)
            texte.append(f"@{participant.nom}", style=f"bold {participant.couleur}")
            detail = {
                "en file": "en file",
                "connexion": "connexion au fournisseur",
                "session": "ouverture de la session",
                "repris": "contexte repris",
                "neuf": "session neuve",
                "évincé": "mémoire rendue",
                "échec": "n'a pas pu démarrer",
            }.get(participant.demarrage, participant.demarrage)
            texte.append(f"  {detail} ", style=N["dim"])
            texte.append(f"{marque[0]}\n", style=marque[1])
        self.update(texte)
        self.texte_brut = texte.plain


class PanneauExpiration(Static):
    """S10 : les bots ont perdu leur mémoire de travail ; le fil, lui, est intact."""

    can_focus = True

    CHOIX = ("relire le fil et repartir de là", "résumer le fil pour eux", "archiver")

    def __init__(self, noms: list[str], messages: int) -> None:
        super().__init__()
        self.noms, self.messages = noms, messages

    def on_mount(self) -> None:
        texte = Text()
        texte.append("  ◌ mémoire expirée · ", style=ATTENTION)
        texte.append(", ".join(f"@{n}" for n in self.noms), style=N["encre"])
        texte.append(
            f"\n    le fil est intact ({self.messages} messages), leur mémoire de travail non.\n",
            style=N["dim"],
        )
        for i, choix in enumerate(self.CHOIX, 1):
            texte.append(f"    {i} ", style=CLIQUABLE)
            texte.append(f"{choix}\n", style=N["encre"])
        texte.append("    esc revenir à la saisie — le fil reste lisible\n", style=N["faible"])
        self.update(texte)
        self.texte_brut = texte.plain
        self.focus()

    def on_key(self, evenement) -> None:
        if evenement.key == "escape":
            evenement.stop()
            saisies = self.screen.query("#message")
            if saisies:
                saisies.first(Input).focus()
        elif evenement.key in ("1", "2", "3"):
            evenement.stop()
            self.app.trancher_expiration(int(evenement.key), self.noms)
            self.remove()


class EcranRaisonnement(Screen):
    """S7 : le travail d'un bot, en direct et en plein écran.

    Les jalons et les outils se mêlent dans l'ordre où ils sont arrivés. Rien sur les
    jetons : le décompte n'existe qu'à la fin du tour.
    """

    CSS = """
    EcranRaisonnement { background: $fond; }
    #detail { padding: 1 2; }
    #pied { height: 1; padding: 0 2; color: $dim; background: $panneau; }
    """

    BINDINGS = [
        Binding("escape", "fermer", "revenir au fil", priority=True),
        Binding("tab", "suivant", "raisonnement suivant", priority=True),
    ]

    def __init__(self, nom: str) -> None:
        super().__init__()
        self.nom = nom
        self.dernier_rendu = Text()

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(id="detail")
        yield Static(id="pied")

    def on_mount(self) -> None:
        self.set_interval(0.16, self.rafraichir)
        self.rafraichir()

    def action_fermer(self) -> None:
        self.app.pop_screen()

    def action_suivant(self) -> None:
        noms = [n for n, p in self.app.participants.items() if p.bulle is not None]
        if len(noms) > 1:
            self.nom = noms[(noms.index(self.nom) + 1) % len(noms)] if self.nom in noms else noms[0]
            self.rafraichir()

    def rafraichir(self) -> None:
        participant = self.app.participants[self.nom]
        bulle = participant.bulle
        largeur = max(40, self.size.width - 4)
        texte = Text()

        glyphe, mot = ETATS[participant.etat]
        texte.append("▌ ", style=participant.couleur)
        texte.append(f"@{participant.nom}", style=f"bold {participant.couleur}")
        texte.append(" · raisonnement", style=N["dim"])
        if bulle is not None:
            texte.append(
                f"    {glyphe} {mot} · {_duree(time.monotonic() - participant.debut_tour)}"
                f" · {len(bulle.outils)} outils\n\n",
                style=N["faible"],
            )
        else:
            texte.append("    rien en cours\n\n", style=N["faible"])

        if bulle is not None:
            texte.append_text(
                regle("JALONS", largeur, "titres émis par l'agent · anglais, par blocs entiers")
            )
            texte.append_text(self.entrelacer(bulle, participant, largeur))
            texte.append("\n")

            texte.append_text(regle("FICHIERS TOUCHÉS", largeur))
            if participant.fichiers:
                for chemin, action in participant.fichiers.items():
                    texte.append(f"  {chemin}", style=N["encre"])
                    texte.append(f"  {action}\n", style=N["faible"])
            else:
                texte.append("  aucun pour l'instant\n", style=N["faible"])
            texte.append("\n")

            texte.append_text(regle("CHRONO", largeur))
            for instant, quoi in participant.chrono[-12:]:
                texte.append(f"  {_duree(instant)} ", style=N["faible"])
                texte.append(f"{quoi}\n", style=N["dim"])
            texte.append("\n")

        texte.append_text(regle("AUTRES BOTS", largeur))
        for autre in self.app.participants.values():
            if autre.nom == self.nom:
                continue
            signe, sens = ETATS[autre.etat]
            texte.append(f"  {signe} ", style=autre.couleur)
            texte.append(f"@{autre.nom} {sens}\n", style=N["dim"])
        texte.append("\n")
        texte.append_text(
            regle("JETONS", largeur, "le décompte n'existe qu'à la fin du tour")
        )
        self.dernier_rendu = texte
        self.query_one("#detail", Static).update(texte)

        pied = Text()
        pied.append("esc revenir au fil · ⇥ raisonnement suivant", style=N["faible"])
        pied.append(f"    ^C arrêter @{self.nom} seul", style=N["faible"])
        self.query_one("#pied", Static).update(pied)

    def entrelacer(self, bulle: Bulle, participant: Participant, largeur: int) -> Text:
        """Jalons et outils dans l'ordre d'arrivée — le dernier jalon s'ouvre, les autres non."""
        evenements: list[tuple[float, str, object]] = [
            (jalon["t"], "jalon", jalon) for jalon in bulle.jalons
        ]
        evenements += [
            (outil["debut"] - participant.debut_tour, "outil", outil) for outil in bulle.outils
        ]
        evenements.sort(key=lambda e: e[0])
        dernier = bulle.jalons[-1] if bulle.jalons else None

        texte = Text()
        for instant, genre, objet in evenements:
            if genre == "outil":
                texte.append_text(bulle.ligne_outil(objet))
                continue
            ouvert = objet is dernier
            texte.append("  ▾ " if ouvert else "  ┊ ", style=participant.couleur)
            texte.append(objet["titre"], style=N["encre"] if ouvert else N["dim"])
            texte.append(f"  {_duree(instant)}\n", style=N["faible"])
            if ouvert and objet["corps"]:
                for ligne in objet["corps"].splitlines():
                    texte.append(f"      {ligne}\n", style=N["dim"])
        if participant.etat == "pense" and bulle.corps == "":
            texte.append(
                f"  {ANIM_TRAVAILLE[bulle.phase % len(ANIM_TRAVAILLE)]} rédige sa réponse…\n",
                style=N["faible"],
            )
        return texte


class Quorum(App):
    CSS = """
    Screen { background: $fond; color: $encre; }
    #entete { height: 1; padding: 0 2; color: $dim; background: $panneau; }
    #relecture { height: 1; padding: 0 2; color: $attention; }
    #milieu { height: 1fr; }
    #fil { width: 1fr; padding: 1 2; }
    #cote { width: 46; padding: 1 2; background: $panneau; }
    #fil > Static { margin-bottom: 1; }
    #fil.compacte > Static { margin-bottom: 0; }
    #bandeau { height: 1; padding: 0 2; color: $dim; background: $panneau; }
    #saisie { height: 1; }
    #chevron { width: 2; padding: 0 0 0 2; color: $cliquable; }
    Input { border: none; background: $fond; padding: 0; height: 1; }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrompre", "interrompre le tour", priority=True),
        Binding("ctrl+q", "quit", "quitter", priority=True),
        Binding("end", "suivre", "suivre le flux", priority=True),
        Binding("ctrl+r", "raisonnement", "raisonnement du dernier bot actif", priority=True),
        Binding("ctrl+b", "fiche", "fiche du bot", priority=True),
        Binding("ctrl+o", "composer", "composer la salle", priority=True),
        Binding("ctrl+g", "reglages", "réglages", priority=True),
    ]

    def __init__(self, salle: Salle, bots_connus: dict[str, bots.Bot]) -> None:
        super().__init__()
        # Le thème d'abord : la couleur d'un participant est calculée à sa création.
        self.reglages = config.lire()
        appliquer_theme(self.theme_voulu())
        self.salle = salle
        self.transcript = Transcript(salle.racine / "transcript.jsonl")
        self.participants = {
            nom: Participant(bots_connus[nom]) for nom in salle.membres if nom in bots_connus
        }
        self.manquants = [nom for nom in salle.membres if nom not in bots_connus]
        self.non_lus = 0
        self.debut_tour = 0.0
        self.conversation: asyncio.Task | None = None
        self.interrompu = False
        self.etat_salle = lire_etat(salle)
        self.demarrage: PanneauDemarrage | None = None

    def theme_voulu(self) -> bool:
        choix = self.reglages.get("theme", "suit le terminal")
        return theme_du_terminal() if choix == "suit le terminal" else choix == "sombre"

    def get_css_variables(self) -> dict[str, str]:
        """La palette passe dans les variables CSS : un seul endroit à basculer."""
        return {
            **super().get_css_variables(),
            "fond": N["fond"], "panneau": N["panneau"], "cadre": N["cadre"],
            "encre": N["encre"], "dim": N["dim"], "faible": N["faible"],
            "attention": ATTENTION, "cliquable": CLIQUABLE,
        }

    def compose(self) -> ComposeResult:
        yield Static(id="entete")
        yield Static(id="relecture")
        with Horizontal(id="milieu"):
            yield VerticalScroll(id="fil")
            yield Static(id="cote")
        yield Static(id="bandeau")
        with Horizontal(id="saisie"):
            yield Static("◇", id="chevron")
            yield Input(placeholder="écris pendant qu'ils travaillent…", id="message")

    async def on_mount(self) -> None:
        self.query_one("#relecture", Static).display = False
        self.query_one("#entete", Static).update(
            Text(f"◈ quorum · {self.salle.nom} · {self.salle.dossier}", style=N["dim"])
        )
        for entree in self.transcript.entrees:
            await self.ajouter(self.bulle_passee(entree))
        if self.transcript.entrees:
            await self.ajouter(
                self.ligne(f"{len(self.transcript.entrees)} messages restaurés", N["faible"])
            )
        for nom in self.manquants:
            await self.ajouter(self.ligne(f"✕ aucun bot nommé « {nom} » dans bots/", ROUGE))
        self.demarrage = PanneauDemarrage(self.participants)
        await self.ajouter(self.demarrage)
        self.set_interval(0.16, self.battre)
        if self.salle.eviction_minutes:
            self.set_interval(30.0, self.evincer_inactifs)
        self.run_worker(self.demarrer_tous())

    # ── affichage ───────────────────────────────────────────────────────────────────

    def ligne(self, texte: str, couleur: str) -> Static:
        """Une ligne d'annonce dans le fil. `texte_brut` la rend relisible sans le rendu Rich."""
        bloc = Static()
        bloc.texte_brut = texte
        bloc.update(Text(f"  {texte}", style=couleur))
        return bloc

    def bulle_passee(self, entree: Entree) -> Static:
        """Une entrée relue du disque : figée, sans chrono, avec le compte d'outils."""
        if entree.genre == "systeme":
            return self.ligne(f"^C {entree.texte} · {entree.heure}", N["faible"])
        if entree.genre == "utilisateur":
            bulle = Bulle(entree.auteur, "", N["encre"], entree.heure)
        else:
            participant = self.participants.get(entree.auteur)
            couleur = participant.couleur if participant else N["dim"]
            role = participant.bot.role if participant else ""
            bulle = Bulle(f"@{entree.auteur}", role, couleur, entree.heure, etat="fini")
            bulle.proprietaire = entree.auteur if participant else None
        bulle.corps = entree.texte
        if entree.outils:
            bulle.corps = f"({len(entree.outils)} outils) " + bulle.corps
        bulle.rafraichir()
        return bulle

    async def ajouter(self, widget: Static) -> None:
        """Monte un bloc au bas du fil, et ne suit le flux que si on y est déjà."""
        fil = self.query_one("#fil", VerticalScroll)
        en_bas = fil.scroll_offset.y >= fil.max_scroll_y - 1
        await fil.mount(widget)
        if en_bas:
            fil.scroll_end(animate=False)
        else:
            self.non_lus += 1

    @property
    def compacte(self) -> bool:
        """Sous 100 colonnes, tout est raccourci — jamais retiré."""
        densite = self.reglages.get("densite", "automatique")
        if densite == "compacte":
            return True
        return densite == "automatique" and self.size.width < 100

    def battre(self) -> None:
        """Un seul battement pour toute l'interface : trois mouvements, pas un de plus."""
        if self.demarrage is not None:
            self.demarrage.rafraichir()
            if all(p.demarrage in ("repris", "neuf", "échec") for p in self.participants.values()):
                self.demarrage = None
        for participant in self.participants.values():
            if participant.bulle is not None and participant.bulle.etat in VIVANTS:
                participant.bulle.phase += 1
                participant.bulle.rafraichir()
            if participant.panneau is not None and participant.panneau.decision is None:
                participant.panneau.phase += 1
                if not participant.panneau.has_focus:
                    participant.panneau.rafraichir()

        # Le fil n'est pas toujours là : un écran de raisonnement peut être au-dessus, et
        # à la fermeture les widgets partent avant le minuteur.
        fils, relectures = self.query("#fil"), self.query("#relecture")
        if not fils or not relectures:
            return
        fil = fils.first(VerticalScroll)
        fil.set_class(self.compacte, "compacte")
        for bulle in self.query(Bulle):
            if bulle.compacte != self.compacte:
                bulle.compacte = self.compacte
                bulle.rafraichir()
        self.peindre_cote()
        if fil.scroll_offset.y >= fil.max_scroll_y - 1:
            self.non_lus = 0
        bandeau_relecture = relectures.first(Static)
        if self.non_lus:
            bandeau_relecture.display = True
            bandeau_relecture.update(
                Text(
                    f"↑ tu relis plus haut — le fil ne bougera pas    "
                    f"{self.non_lus} nouveaux messages ↓ fin",
                    style=ATTENTION,
                )
            )
        else:
            bandeau_relecture.display = False
        self.peindre_bandeau()

    def peindre_cote(self) -> None:
        """Au-delà de 160 colonnes les marges deviennent utiles : la salle et le bot actif."""
        cotes = self.query("#cote")
        if not cotes:
            return
        cote = cotes.first(Static)
        cote.display = self.size.width >= 160
        if not cote.display:
            return
        texte = Text()
        texte.append_text(regle("DANS CETTE SALLE", 42))
        for participant in self.participants.values():
            glyphe, mot = ETATS[participant.etat]
            texte.append(f"  {glyphe} ", style=participant.couleur)
            texte.append(f"@{participant.nom:<10}", style=participant.couleur)
            texte.append(f"{mot}\n", style=N["dim"])
        texte.append("\n")

        actif = next(
            (p for p in self.participants.values()
             if p.bulle is not None and p.etat in VIVANTS), None
        )
        if actif is None:
            texte.append_text(regle("RAISONNEMENT", 42))
            texte.append("  personne ne travaille\n", style=N["faible"])
        else:
            texte.append_text(regle(f"@{actif.nom}", 42, "en direct"))
            for jalon in actif.bulle.jalons[-4:]:
                texte.append("  ┊ ", style=actif.couleur)
                texte.append(f"{jalon['titre'][:36]}\n", style=N["dim"])
            for outil in actif.bulle.outils[-4:]:
                texte.append("  ▸ ", style=actif.couleur)
                texte.append(f"{outil['titre'][:34]}", style=N["dim"])
                texte.append(" ✓\n" if outil["fin"] else " ◆\n", style=N["faible"])
        cote.update(texte)

    def peindre_bandeau(self) -> None:
        """Un glyphe et un nom par membre, et à droite ce que coûte l'interruption."""
        gauche = Text()
        for participant in self.participants.values():
            glyphe, _ = ETATS[participant.etat]
            if participant.etat == "pense":
                glyphe = ANIM_PENSE[(participant.bulle.phase if participant.bulle else 0) % len(ANIM_PENSE)]
            gauche.append(f"{glyphe}", style=participant.couleur)
            gauche.append(f"@{participant.nom}  ", style=N["dim"])

        actifs = [p for p in self.participants.values() if p.tour is not None and not p.tour.done()]
        droite = (
            f"^C interrompre · {_duree(time.monotonic() - self.debut_tour)}"
            if actifs
            else "^C interrompre · ^Q quitter"
        )
        remplissage = max(1, self.size.width - 4 - gauche.cell_len - len(droite))
        gauche.append(" " * remplissage)
        gauche.append(droite, style=N["faible"])
        bandeaux = self.query("#bandeau")
        if bandeaux:
            bandeaux.first(Static).update(gauche)

    # ── agents ──────────────────────────────────────────────────────────────────────

    async def demarrer_tous(self) -> None:
        """Les membres se lèvent en parallèle ; la saisie s'ouvre au premier prêt."""
        await asyncio.gather(*(self.demarrer(p) for p in self.participants.values()))
        perdus = [p.nom for p in self.participants.values() if p.memoire_expiree]
        if perdus and self.transcript.entrees:
            await self.ajouter(PanneauExpiration(perdus, len(self.transcript.entrees)))

    def ouvrir_la_saisie(self) -> None:
        """La saisie s'ouvre dès le premier bot prêt — pas quand tout le monde est levé."""
        saisies = self.query("#message")
        if saisies and not saisies.first(Input).has_focus:
            saisies.first(Input).focus()

    async def dossier_de(self, participant: Participant) -> Path:
        """Le dossier de travail d'un bot : commun, ou sa propre copie s'il écrit.

        Hors dépôt git il n'y a pas de worktree possible : on partage le dossier et on le
        dit, plutôt que de faire croire à une isolation qui n'existe pas.
        """
        if not participant.bot.copie_isolee:
            return self.salle.dossier
        copie = self.salle.racine.parents[1] / "runtime" / self.salle.nom / "copies" / participant.nom
        if copie.exists():
            return copie
        copie.parent.mkdir(parents=True, exist_ok=True)
        processus = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "--detach", str(copie),
            cwd=str(self.salle.dossier),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, erreur = await processus.communicate()
        if processus.returncode == 0:
            await self.ajouter(
                self.ligne(f"· @{participant.nom} travaille dans sa copie {copie.name}", N["faible"])
            )
            return copie
        await self.ajouter(
            self.ligne(
                f"· @{participant.nom} : pas de copie isolée possible ici "
                f"({erreur.decode(errors='replace').strip().splitlines()[-1:] or ['hors dépôt git']}"
                f") — dossier commun et politique stricte",
                ATTENTION,
            )
        )
        return self.salle.dossier

    async def demarrer(self, participant: Participant) -> None:
        participant.demarrage = "connexion"
        participant.dossier = await self.dossier_de(participant)
        env_projet = bots.lire_env(self.salle.racine.parents[1] / ".env")
        atelier = self.salle.racine.parents[1] / "runtime" / self.salle.nom
        journal_telemetrie = getattr(self, "journal_telemetrie", None) or (
            atelier / f"{participant.nom}.telemetry.jsonl"
        )
        sorties = (
            self.salle.conserver_les_sorties and participant.bot.fournisseur == "gemini"
        )
        if not sorties:
            journal_telemetrie = None
        else:
            # Impérativement avant le lancement : supprimer le fichier ensuite laisserait
            # l'agent écrire dans un inode effacé, et aucune sortie ne remonterait jamais.
            journal_telemetrie.parent.mkdir(parents=True, exist_ok=True)
            journal_telemetrie.unlink(missing_ok=True)
        commande, args, env = bots.lancement(participant.bot, env_projet, journal_telemetrie)
        participant.client = ClientAcp(
            lambda message, p=participant: self.sur_notification(p, message),
            lambda params, p=participant: self.sur_autorisation(p, params),
            lambda params, p=participant: self.sur_ecriture(p, params),
        )
        journal = atelier / f"{participant.nom}.stderr.log"
        try:
            await participant.client.demarrer(commande, args, env, participant.dossier, journal)
            capacites = await participant.client.initialiser()
            participant.demarrage = "session"
            await self.ouvrir_session(participant, capacites)
        except (OSError, AcpErreur) as erreur:
            participant.etat = "echec"
            participant.demarrage = "échec"
            await self.ajouter(
                self.ligne(f"✕ @{participant.nom} n'a pas pu démarrer — {erreur}", ROUGE)
            )
            return
        participant.etat = "repos"
        participant.derniere_activite = time.monotonic()
        self.ouvrir_la_saisie()
        if journal_telemetrie is not None:
            participant.journal = SortiesGemini(
                journal_telemetrie,
                lambda identifiant, sortie, p=participant: self.sur_sortie(p, identifiant, sortie),
            )
            participant.journal.demarrer()

    async def ouvrir_session(self, participant: Participant, capacites: dict) -> None:
        """Reprend la session du bot si elle existe encore, sinon en ouvre une neuve.

        `session/load` rejoue tout l'historique en notifications : le contexte privé du bot
        se reconstruit sans rien envoyer. Si la session a expiré, on repart à zéro et le fil
        le dit — c'est l'état S10.
        """
        ancienne = self.etat_salle["sessions"].get(participant.nom)
        sait_charger = bool((capacites.get("agentCapabilities") or {}).get("loadSession"))
        if ancienne and sait_charger:
            try:
                # Un agent qui ignore session/load laisserait la salle fermée pour toujours.
                reprise = await asyncio.wait_for(
                    participant.client.charger_session(ancienne, participant.dossier), ATTENTE_REPRISE
                )
                participant.session = {"sessionId": ancienne, **(reprise or {})}
                participant.vu = int(self.etat_salle["vu"].get(participant.nom, 0))
                participant.demarrage = "repris"
                return
            except (AcpErreur, asyncio.TimeoutError):
                participant.memoire_expiree = True

        participant.session = await participant.client.nouvelle_session(participant.dossier)
        participant.vu = 0
        participant.demarrage = "neuf"
        self.etat_salle["sessions"][participant.nom] = participant.session["sessionId"]
        self.etat_salle["vu"][participant.nom] = 0
        ecrire_etat(self.salle, self.etat_salle)

    async def assurer_pret(self, participant: Participant) -> bool:
        """Réveille un bot évincé avant son tour ; ne fait rien s'il est déjà là."""
        if participant.pret:
            return True
        if participant.client is not None:
            await participant.client.fermer()
        participant.client = None
        participant.session = {}
        await self.demarrer(participant)
        return participant.pret

    def evincer_inactifs(self) -> None:
        """Un bot inactif rend sa mémoire vive ; sa session reste sur disque."""
        seuil = self.salle.eviction_minutes * 60
        for participant in self.participants.values():
            occupe = participant.tour is not None and not participant.tour.done()
            if occupe or not participant.pret or not participant.derniere_activite:
                continue
            if time.monotonic() - participant.derniere_activite > seuil:
                self.run_worker(self.evincer(participant))

    async def evincer(self, participant: Participant) -> None:
        if participant.journal is not None:
            participant.journal.arreter()
            participant.journal = None
        if participant.client is not None:
            await participant.client.fermer()
        participant.client = None
        participant.session = {}
        participant.etat = "horsjeu"
        participant.demarrage = "évincé"
        await self.ajouter(
            self.ligne(f"◌ @{participant.nom} évincé — son prochain tour le réveille", N["faible"])
        )

    def trancher_expiration(self, choix: int, noms: list[str]) -> None:
        """Les trois issues de S10 : repartir du fil, le faire résumer, ou l'archiver."""
        if choix == 1:
            self.bandeau_court("· les bots repartent du fil complet")
        elif choix == 2:
            self.conversation = asyncio.create_task(self.faire_resumer(noms))
        elif choix == 3:
            self.run_worker(self.archiver_le_fil())

    async def faire_resumer(self, noms: list[str]) -> None:
        """Un résumé écrit par les bots eux-mêmes, puis le fil repart de là."""
        cibles = [self.participants[n] for n in noms if self.participants[n].pret]
        if not cibles:
            return
        for participant in cibles:
            participant.bulle = self.nouvelle_bulle(participant)
            await self.ajouter(participant.bulle)
        texte = resume_du_fil(self.transcript.entrees)
        for participant in cibles:
            participant.tour = asyncio.create_task(self.parler(participant, texte))
        await asyncio.gather(*(p.tour for p in cibles), return_exceptions=True)

    async def archiver_le_fil(self) -> None:
        cible = archiver(self.salle)
        self.transcript = Transcript(self.salle.racine / "transcript.jsonl")
        self.etat_salle = lire_etat(self.salle)
        for participant in self.participants.values():
            participant.vu = 0
            participant.memoire_expiree = False
        await self.ajouter(
            self.ligne(f"· fil archivé dans {cible.name if cible else '—'}", N["faible"])
        )

    def sur_sortie(self, participant: Participant, identifiant: str, sortie: str) -> None:
        """Raccorde une sortie du journal à la ligne d'outil qui l'attend."""
        for bulle in reversed(participant.bulles[-5:]):
            for outil in bulle.outils:
                if identifiant_court(str(outil["id"])) == identifiant and outil["sortie"] is None:
                    outil["sortie"] = sortie
                    outil["arrivee"] = time.monotonic()
                    outil["attend_sortie"] = False
                    outil["sortie_perdue"] = False  # elle a fini par venir : on se dédit
                    if not outil["fin"]:
                        outil["fin"] = outil["arrivee"]
                    bulle.rafraichir()
                    return

    def sur_notification(self, participant: Participant, message: dict) -> None:
        """Appelé depuis la boucle de lecture d'un agent : rapide, et rien qui attende."""
        if message.get("method") != "session/update" or participant.bulle is None:
            return
        params = message.get("params") or {}
        maj = params.get("update", params)
        genre = maj.get("sessionUpdate")
        bulle = participant.bulle

        if genre == "agent_thought_chunk":
            participant.etat = bulle.etat = "pense"
            self.noter_pensee(participant, (maj.get("content") or {}).get("text", ""))
        elif genre == "agent_message_chunk":
            participant.etat = bulle.etat = "pense"
            bulle.corps += (maj.get("content") or {}).get("text", "")
        elif genre == "tool_call":
            participant.etat = bulle.etat = "execute"
            genre_outil = str(maj.get("kind", "other"))
            bulle.outils.append({
                "id": maj.get("toolCallId"),
                "famille": FAMILLES.get(genre_outil, "outil"),
                "titre": maj.get("title") or genre_outil,
                "debut": time.monotonic(),
                "fin": None,
                "sortie": None,
                "attend_sortie": False,
                "sortie_perdue": False,
            })
            self.noter_fichiers(participant, genre_outil, maj.get("locations") or [])
            participant.chrono.append(
                (time.monotonic() - participant.debut_tour, maj.get("title") or genre_outil)
            )
        elif genre == "tool_call_update":
            self.noter_fichiers(participant, str(maj.get("kind", "other")), maj.get("locations") or [])
            for outil in bulle.outils:
                fini = maj.get("status") in ("completed", "failed")
                if outil["id"] == maj.get("toolCallId") and fini and not outil["fin"]:
                    outil["fin"] = time.monotonic()
                    outil["attend_sortie"] = participant.journal is not None
            if all(o["fin"] for o in bulle.outils):
                participant.etat = bulle.etat = "pense"
        bulle.rafraichir()

    def noter_pensee(self, participant: Participant, texte: str) -> None:
        """Les jalons s'empilent ; un bloc sans titre prolonge le corps du jalon en cours."""
        bulle = participant.bulle
        assert bulle is not None
        suite, nouveaux = decouper_pensee(texte)
        if suite and bulle.jalons:
            bulle.jalons[-1]["corps"] += ("\n" if bulle.jalons[-1]["corps"] else "") + suite
        for titre, corps in nouveaux:
            bulle.jalons.append({"titre": titre, "corps": corps,
                                 "t": time.monotonic() - participant.debut_tour})
            participant.chrono.append((bulle.jalons[-1]["t"], f"pense · {titre}"))

    def noter_fichiers(self, participant: Participant, genre: str, endroits: list[dict]) -> None:
        """`locations` est rempli pour les lectures et les écritures : le panneau reste à jour."""
        action = {"read": "lu", "edit": "modifié", "delete": "supprimé", "move": "déplacé"}
        for endroit in endroits:
            chemin = endroit.get("path")
            if chemin:
                participant.fichiers[chemin] = action.get(genre, genre)

    async def sur_ecriture(self, participant: Participant, params: dict) -> None:
        """Garde-fou : une écriture hors du dossier de la salle passe par une autorisation.

        L'agent demande au client d'écrire ; sans ce contrôle, il écrit n'importe où sur le
        disque sans que rien ne remonte. Le réglage peut l'ôter, mais il est posé par défaut.
        """
        chemin = Path(params["path"]).resolve()
        racine = (participant.dossier or self.salle.dossier).resolve()
        dehors = not chemin.is_relative_to(racine)
        if dehors and self.reglages.get("demander_hors_dossier", True):
            issue = await self.sur_autorisation(participant, {
                "toolCall": {"title": f"écrire hors du dossier de la salle : {chemin}"},
                "options": [
                    {"optionId": "oui", "name": "Écrire ce fichier", "kind": "allow_once"},
                    {"optionId": "non", "name": "Refuser", "kind": "reject_once"},
                ],
            })
            if issue.get("optionId") != "oui":
                raise AcpErreur("écriture hors du dossier refusée par l'utilisateur")
        ecrire_fichier(params)

    async def sur_autorisation(self, participant: Participant, params: dict) -> dict:
        """Monte le panneau et attend la décision — aussi longtemps qu'il faut.

        Au plus une demande en attente par bot ; plusieurs bots peuvent en avoir chacun une.
        Le panneau ne prend le focus que si aucun autre n'est en cours de décision.
        """
        participant.etat = "demande"
        if participant.bulle is not None:
            participant.bulle.etat = "demande"
            participant.bulle.rafraichir()
        reponse = asyncio.get_running_loop().create_future()
        panneau = PanneauAutorisation(participant, params, reponse)
        participant.panneau = panneau
        await self.ajouter(panneau)
        if not any(
            p.panneau is not None and p.panneau.has_focus for p in self.participants.values()
        ):
            panneau.focus()
        try:
            return await reponse
        finally:
            participant.panneau = None

    # ── tours ───────────────────────────────────────────────────────────────────────

    async def on_input_submitted(self, evenement: Input.Submitted) -> None:
        if evenement.input.id != "message":
            return
        texte = evenement.value.strip()
        if not texte:
            return
        joignables = [p for p in self.participants.values() if p.etat != "echec"]
        if not joignables:
            self.bandeau_court("aucun bot joignable")
            return
        evenement.input.value = ""
        self.transcript.ajouter("utilisateur", "toi", texte)
        await self.ajouter(self.bulle_passee(self.transcript.entrees[-1]))

        noms = [q.nom for q in joignables]
        vises = destinataires(texte, noms)
        spontane = vises == noms and "@" not in texte
        cibles = [
            p for p in joignables
            if p.nom in vises and (not spontane or p.bot.parle_sans_etre_appele)
        ]
        occupes = [p for p in cibles if p.tour is not None and not p.tour.done()]
        if occupes:
            self.bandeau_court(
                "· " + ", ".join(f"@{p.nom}" for p in occupes)
                + " travaille encore — le message est dans le fil, il le verra"
            )
            cibles = [p for p in cibles if p not in occupes]
        if cibles:
            self.debut_tour = time.monotonic()
            self.interrompu = False
            self.conversation = asyncio.create_task(self.mener(cibles))

    def bandeau_court(self, texte: str) -> None:
        bandeaux = self.query("#bandeau")
        if bandeaux:
            bandeaux.first(Static).update(Text(f"  {texte}", style=N["dim"]))

    # ── rounds ──────────────────────────────────────────────────────────────────────

    async def mener(self, cibles: list[Participant]) -> None:
        """Un round par vague : parallèle dedans, séquentiel entre les vagues, et borné.

        Le budget de la salle est la vraie limite ; la coupure sur radotage n'est qu'un
        garde-fou de confort.
        """
        await self.jouer_round(cibles)
        for enchainement in range(self.salle.max_rounds):
            if self.interrompu:
                return
            if self.salle.couper_si_repetition:
                radoteurs = [p.nom for p in cibles if p.radote]
                if radoteurs:
                    await self.ajouter(
                        self.ligne("↳ " + ", ".join(f"@{n}" for n in radoteurs)
                                   + " se répète — round coupé", ATTENTION)
                    )
                    return
            suivants = self.suite(cibles)
            if not suivants:
                return
            await self.ajouter(
                self.ligne(
                    f"↳ round {enchainement + 2} · " + ", ".join(f"@{p.nom}" for p in suivants),
                    N["faible"],
                )
            )
            cibles = suivants
            await self.jouer_round(cibles)
        if self.suite(cibles):
            await self.ajouter(
                self.ligne(
                    f"↳ budget d'enchaînement épuisé ({self.salle.max_rounds}) — à toi la main",
                    ATTENTION,
                )
            )

    def suite(self, precedents: list[Participant]) -> list[Participant]:
        """Les bots interpellés par ceux qui viennent de parler, et qui peuvent répondre."""
        vises: list[Participant] = []
        membres = [p.nom for p in self.participants.values()]
        for parlant in precedents:
            if parlant.bulle is None or not parlant.bot.peut_interpeller:
                continue
            for nom in relances(parlant.bulle.corps, membres, sauf=parlant.nom):
                candidat = self.participants[nom]
                if candidat.etat != "echec" and candidat not in vises:
                    vises.append(candidat)
        return vises

    async def jouer_round(self, cibles: list[Participant]) -> None:
        """Les bots d'une même vague répondent en parallèle, chacun dans son bloc."""
        for participant in cibles:
            participant.bulle = self.nouvelle_bulle(participant)
            await self.ajouter(participant.bulle)
        for participant in cibles:
            participant.tour = asyncio.create_task(self.parler(participant))
        await asyncio.gather(*(p.tour for p in cibles), return_exceptions=True)

    def nouvelle_bulle(self, participant: Participant) -> Bulle:
        participant.etat = "pense"
        participant.debut_tour = time.monotonic()
        participant.chrono = []
        bulle = Bulle(
            f"@{participant.nom}",
            participant.bot.role,
            participant.couleur,
            time.strftime("%H:%M"),
            etat="pense",
            debut=time.monotonic(),
        )
        bulle.proprietaire = participant.nom
        participant.bulles.append(bulle)
        bulle.raisonnement_visible = participant.bot.raisonnement_visible
        bulle.raisonnement = self.reglages.get("raisonnement", "replié")
        return bulle

    async def parler(self, participant: Participant, impose: str | None = None) -> None:
        """Un tour, et le rebond du refus : le refus n'arrête pas le bot, il l'oriente."""
        if not await self.assurer_pret(participant):
            await self.echouer(participant, "n'a pas pu être relancé")
            return
        texte = impose or prompt_pour(participant.nom, self.transcript.entrees, participant.vu)
        while True:
            participant.vu = len(self.transcript.entrees)
            bulle = participant.bulle
            assert bulle is not None and participant.client is not None
            try:
                resultat = await participant.client.prompt(participant.session["sessionId"], texte)
            except AcpErreur as erreur:
                await self.echouer(participant, str(erreur))
                return
            arret = resultat.get("stopReason", "")
            participant.etat = bulle.etat = "horsjeu" if arret == "cancelled" else "fini"
            bulle.rafraichir()
            if bulle.corps.strip():
                self.transcript.ajouter(
                    "bot", participant.nom, bulle.corps.strip(), bulle.titres_outils()
                )
                participant.empreintes.append(empreinte(bulle.corps))
            participant.vu = len(self.transcript.entrees)
            participant.derniere_activite = time.monotonic()
            self.etat_salle["vu"][participant.nom] = participant.vu
            ecrire_etat(self.salle, self.etat_salle)

            # Le chemin qui marche : le tour se termine proprement, puis le commentaire part
            # en prompt. Pas de rebond après une annulation.
            if bulle.outils:
                asyncio.create_task(self.clore_sorties(bulle))
            if participant.refus is None or arret == "cancelled":
                return
            texte = f"[refus de l'utilisateur] {participant.refus}"
            participant.refus = None
            participant.bulle = self.nouvelle_bulle(participant)
            await self.ajouter(participant.bulle)

    async def clore_sorties(self, bulle: Bulle) -> None:
        """Une sortie arrive avec la requête modèle suivante : s'il n'y en a pas, elle ne
        viendra jamais. Passé le délai de grâce, on l'annonce au lieu d'animer dans le vide."""
        await asyncio.sleep(telemetry.GRACE)
        change = False
        for outil in bulle.outils:
            if outil.get("attend_sortie") and outil["sortie"] is None:
                outil["attend_sortie"] = False
                outil["sortie_perdue"] = True
                change = True
        if change:
            bulle.rafraichir()

    async def echouer(self, participant: Participant, message: str) -> None:
        """Un échec tient sur une ligne, jamais en fenêtre modale — et la salle continue."""
        participant.etat = "echec"
        if participant.bulle is not None:
            participant.bulle.etat = "echec"
            participant.bulle.rafraichir()
        await self.ajouter(self.ligne(f"✕ @{participant.nom} — {message}", ROUGE))
        await self.ajouter(self.ligne("les autres bots continuent", N["faible"]))

    # ── refus commenté ──────────────────────────────────────────────────────────────

    def ouvrir_refus(self, panneau: PanneauAutorisation, option: dict) -> None:
        """Ouvre la zone de commentaire juste sous le panneau, comme le montre S9."""
        self.query_one("#fil", VerticalScroll).mount(ZoneRefus(panneau, option), after=panneau)

    def tracer_refus(self, participant: Participant, commentaire: str) -> None:
        """Le refus entre dans le fil : sans ça les autres bots ne comprennent pas le virage."""
        self.transcript.ajouter(
            "refus", "toi", f"refus de la demande de @{participant.nom} : « {commentaire} »"
        )
        self.run_worker(
            self.ajouter(self.ligne(f"✕ refusé · toi · « {commentaire} »", ROUGE)), exclusive=False
        )

    def action_interrompre(self) -> None:
        """^C : annuler chaque tour en cours et résoudre les autorisations en vol.

        On n'annule pas la tâche de conversation : elle porte le code qui referme les
        bulles et conserve ce qui a déjà été dit. On lui demande de s'arrêter après le
        round en cours, et les tours se terminent d'eux-mêmes sur `stopReason: cancelled`.
        """
        self.interrompu = True
        coupes = []
        for participant in self.participants.values():
            if participant.tour is not None and not participant.tour.done():
                participant.client.annuler(participant.session["sessionId"])
                coupes.append(participant.nom)
            if participant.panneau is not None:
                participant.panneau.abandonner()
        if coupes:
            self.transcript.ajouter(
                "systeme", "toi", "tour interrompu · " + ", ".join(f"@{n}" for n in coupes)
            )

    def ouvrir_raisonnement(self, nom: str | None) -> None:
        if nom in self.participants and self.participants[nom].bot.raisonnement_visible:
            self.push_screen(EcranRaisonnement(nom))

    def modeles_connus(self) -> list[str]:
        """La liste des modèles vient des sessions ouvertes — jamais d'un nom écrit en dur."""
        for participant in self.participants.values():
            modeles = (participant.session.get("models") or {}).get("availableModels") or []
            if modeles:
                return [m.get("modelId", "") for m in modeles if m.get("modelId")]
        return []

    def action_fiche(self) -> None:
        """La fiche du bot dont la bulle a le focus, sinon un bot neuf."""
        vise = next(
            (b.proprietaire for b in self.query(Bulle) if b.has_focus and b.proprietaire), None
        )
        prises = {p.bot.teinte for p in self.participants.values() if p.nom != vise}
        self.push_screen(
            EcranFicheBot(self.salle.racine.parents[1], vise, self.modeles_connus(), prises),
            self.fiche_enregistree,
        )

    def fiche_enregistree(self, nom: str | None) -> None:
        """Un bot modifié est évincé : son prochain tour le relance avec sa nouvelle fiche."""
        if not nom:
            return
        dossier = self.salle.racine.parents[1] / "bots" / nom
        participant = self.participants.get(nom)
        if participant is None:
            self.run_worker(
                self.ajouter(self.ligne(f"· @{nom} enregistré — ajoute-le à la salle pour l'entendre",
                                        N["faible"]))
            )
            return
        participant.bot = bots.charger(dossier)
        participant.couleur = couleur_bot(participant.bot.teinte)
        self.run_worker(self.evincer(participant))

    def action_composer(self) -> None:
        racine = self.salle.racine.parents[1]
        self.push_screen(
            EcranSalle(racine, self.salle, bots.charger_tous(racine / "bots")), self.salle_composee
        )

    def salle_composee(self, nom: str | None) -> None:
        """Le room.toml est écrit ; la composition prend effet à la prochaine ouverture."""
        if nom:
            self.run_worker(self.ajouter(self.ligne(
                f"· salle « {nom} » enregistrée — rouvre-la pour que la composition s'applique",
                N["faible"],
            )))

    def action_reglages(self) -> None:
        racine = self.salle.racine.parents[1]
        self.push_screen(
            EcranReglages(self.reglages, bots.charger_tous(racine / "bots"), self.modeles_connus()),
            self.reglages_changes,
        )

    def reglages_changes(self, valeurs: dict | None) -> None:
        """Ce qui se voit tout de suite s'applique tout de suite."""
        if not valeurs:
            return
        self.reglages = valeurs
        appliquer_theme(self.theme_voulu())
        self.refresh_css()
        for participant in self.participants.values():
            participant.couleur = couleur_bot(participant.bot.teinte)
            if participant.bulle is not None:
                participant.bulle.couleur = participant.couleur
                participant.bulle.raisonnement = valeurs["raisonnement"]
                participant.bulle.rafraichir()
        self.peindre_bandeau()

    def action_raisonnement(self) -> None:
        """Le raisonnement du dernier bot qui a parlé — sans aller chercher sa bulle."""
        vivants = [p for p in self.participants.values() if p.bulle is not None]
        if vivants:
            self.ouvrir_raisonnement(max(vivants, key=lambda p: p.debut_tour).nom)

    def action_suivre(self) -> None:
        self.query_one("#fil", VerticalScroll).scroll_end(animate=False)
        self.non_lus = 0

    async def on_unmount(self) -> None:
        """En sortant, aucun process ne survit à l'application."""
        for participant in self.participants.values():
            if participant.journal is not None:
                participant.journal.arreter()
            if participant.client is not None:
                await participant.client.fermer()


def main() -> None:
    """L'accueil, puis une salle, puis l'accueil : ^Q revient, ^Q à l'accueil quitte."""
    racine = Path(__file__).resolve().parents[1]
    demande = sys.argv[1] if len(sys.argv) > 1 else None
    while True:
        nom = demande or Accueil(racine).run()
        demande = None
        if not nom:
            return
        chemin = racine / "rooms" / nom / "room.toml"
        if not chemin.exists():
            print(f"aucune salle nommée « {nom} » dans {racine / 'rooms'}")
            return
        salle = charger_salle(racine, nom)
        Quorum(salle, bots.charger_tous(racine / "bots")).run()


if __name__ == "__main__":
    main()
