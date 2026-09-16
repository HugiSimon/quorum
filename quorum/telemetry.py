"""Adaptateur Gemini : récupérer la sortie des commandes, que le flux ACP n'envoie pas.

Ni la sortie des commandes ni le contenu des fichiers lus ne remontent avec la fin de
l'outil. Le seul endroit où ils existent est le journal de télémétrie local, et ils y
arrivent **avec la requête modèle suivante** — une à deux secondes après, d'un bloc.

Le journal n'est pas du JSONL malgré son extension : c'est une suite d'objets JSON indentés
collés les uns aux autres. On le décode donc en flux, objet par objet.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Callable, Iterator

EVENEMENT = "gemini_cli.api_request"
MOTIF_PGID = re.compile(r"(?m)^Process Group PGID: \d+$\n?")
DECODEUR = json.JSONDecoder()

# La sortie arrive avec la requête modèle suivante ; s'il n'y en a pas, elle ne viendra
# jamais. Mesuré à +5,5 s sur un `wc -l` — le « 1 à 2 s » annoncé est optimiste, d'où la marge.
GRACE = 8.0


def objets(texte: str) -> tuple[list[dict], int]:
    """Décode tous les objets JSON complets d'un texte, et rend où l'on s'est arrêté."""
    trouves: list[dict] = []
    i = 0
    while i < len(texte):
        while i < len(texte) and texte[i] in " \r\n\t":
            i += 1
        if i >= len(texte):
            break
        try:
            objet, j = DECODEUR.raw_decode(texte, i)
        except ValueError:
            break  # objet encore incomplet : on le reprendra au prochain passage
        trouves.append(objet)
        i = j
    return trouves, i


def sorties_de(objet: dict) -> Iterator[tuple[str, str, str]]:
    """Rend les (nom d'outil, identifiant d'appel, sortie) portés par un enregistrement."""
    attributs = objet.get("attributes") or {}
    if attributs.get("event.name") != EVENEMENT:
        return
    try:
        contenu = json.loads(attributs.get("request_text") or "")
    except ValueError:
        return
    for reponse in _reponses(contenu):
        sortie = (reponse.get("response") or {}).get("output")
        if reponse.get("id") and sortie is not None:
            yield reponse.get("name", ""), reponse["id"], nettoyer(str(sortie))


def _reponses(noeud) -> Iterator[dict]:
    if isinstance(noeud, dict):
        if isinstance(noeud.get("functionResponse"), dict):
            yield noeud["functionResponse"]
        for valeur in noeud.values():
            yield from _reponses(valeur)
    elif isinstance(noeud, list):
        for valeur in noeud:
            yield from _reponses(valeur)


def nettoyer(sortie: str) -> str:
    """Retire l'emballage que l'agent ajoute autour d'une sortie de commande.

    Les espaces qui suivent `Output:` appartiennent à la sortie (`wc -l` s'aligne) : on
    n'enlève que l'étiquette elle-même.
    """
    texte = sortie
    debut = texte.find("<untrusted_context>")
    if debut != -1:
        fin = texte.find("</untrusted_context>", debut)
        texte = texte[debut + len("<untrusted_context>") : fin if fin != -1 else None]
    texte = MOTIF_PGID.sub("", texte).strip("\n")
    if texte.startswith("Output:"):
        texte = texte[len("Output:") :]
    return texte.strip("\n")


class SortiesGemini:
    """Suit le journal d'un bot et signale chaque sortie dès qu'elle y apparaît."""

    def __init__(self, chemin: Path, sur_sortie: Callable[[str, str], None]) -> None:
        self.chemin = chemin
        self.sur_sortie = sur_sortie
        self._position = 0
        self._reste = ""
        self._tache: asyncio.Task | None = None
        self._vues: set[str] = set()

    def demarrer(self) -> None:
        self._tache = asyncio.create_task(self._suivre())

    def arreter(self) -> None:
        if self._tache is not None:
            self._tache.cancel()

    async def _suivre(self, intervalle: float = 0.5) -> None:
        while True:
            self.moissonner()
            await asyncio.sleep(intervalle)

    def moissonner(self) -> None:
        """Lit ce qui s'est ajouté au journal et remonte les sorties encore inconnues."""
        if not self.chemin.exists():
            return
        with self.chemin.open("r", encoding="utf-8", errors="replace") as fichier:
            fichier.seek(self._position)
            self._reste += fichier.read()
            self._position = fichier.tell()
        trouves, consomme = objets(self._reste)
        self._reste = self._reste[consomme:]
        for objet in trouves:
            for _, identifiant, sortie in sorties_de(objet):
                if identifiant not in self._vues:
                    self._vues.add(identifiant)
                    self.sur_sortie(identifiant, sortie)


def identifiant_court(tool_call_id: str) -> str:
    """Le `toolCallId` d'ACP vaut `<nom>__<id>` : c'est le `<id>` qui raccorde au journal."""
    return tool_call_id.rsplit("__", 1)[-1]
