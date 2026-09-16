"""Client ACP : JSON-RPC 2.0 sur stdio, bidirectionnel.

Trois formes de message et une seule règle de tri : un `id` **et** une `method` est une
requête de l'agent, un `id` seul est la réponse à l'une des nôtres, ni l'un ni l'autre une
notification. Les identifiants de l'agent commencent à 0 : on teste toujours `is not None`,
jamais la véracité de l'id.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable

# Un rejeu d'historique (session/load) arrive sur une seule ligne : la limite de 64 Kio
# d'asyncio est très en dessous du besoin.
LIMITE_LIGNE = 4 * 1024 * 1024


class AcpErreur(Exception):
    """Erreur portée par le champ `error` d'une réponse, ou panne du transport."""


class ClientAcp:
    """Un process agent, sa boucle de lecture et ses requêtes en vol.

    `sur_notification(message)` est appelé **dans** la boucle de lecture : il doit rendre
    la main tout de suite. `sur_autorisation(params)` est une coroutine qui peut attendre
    une décision humaine aussi longtemps qu'il faut — elle tourne dans sa propre tâche.
    """

    def __init__(
        self,
        sur_notification: Callable[[dict], None],
        sur_autorisation: Callable[[dict], Awaitable[dict]],
        sur_ecriture: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self.sur_notification = sur_notification
        self.sur_autorisation = sur_autorisation
        # L'agent demande au client d'écrire : c'est une frontière de confiance, pas un
        # détail de transport. L'application peut s'y interposer.
        self.sur_ecriture = sur_ecriture
        self.capacites: dict[str, Any] = {}
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr = None
        self._lecteur: asyncio.Task | None = None
        self._dernier_id = 0
        self._attentes: dict[int, asyncio.Future] = {}
        self._taches: dict[Any, asyncio.Task] = {}
        self._autorisations: set[Any] = set()

    # ── cycle de vie ────────────────────────────────────────────────────────────────

    async def demarrer(
        self,
        commande: str,
        args: list[str],
        env: dict[str, str],
        cwd: Path,
        journal_stderr: Path,
    ) -> None:
        """Lance le process agent, stderr détourné vers un fichier (le terminal est pris)."""
        journal_stderr.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = journal_stderr.open("ab", buffering=0)
        self._proc = await asyncio.create_subprocess_exec(
            commande,
            *args,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr,
            limit=LIMITE_LIGNE,
            # `gemini --acp` n'est qu'un lanceur : il ouvre un second process node qui lui
            # survit. Un groupe à part permet de fermer les deux d'un seul geste.
            start_new_session=True,
        )
        self._lecteur = asyncio.create_task(self._boucle_lecture())

    async def fermer(self, delai_propre: float = 4.0) -> None:
        """Termine le process sans laisser d'orphelin — mais en lui laissant sa sortie propre.

        La fermeture de stdin est le seul « au revoir » du protocole : c'est elle qui permet
        à l'agent d'enregistrer sa session sur disque, donc à `session/load` de la retrouver
        au lancement suivant. Un SIGTERM immédiat la perd. Le signal ne vient qu'après.
        """
        if self._proc is not None and self._proc.returncode is None:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), delai_propre)
            except asyncio.TimeoutError:
                self._signaler(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self._proc.wait(), 3)
                except asyncio.TimeoutError:
                    self._signaler(signal.SIGKILL)
        if self._lecteur is not None:
            self._lecteur.cancel()
        if self._stderr is not None:
            self._stderr.close()

    def _signaler(self, sig: int) -> None:
        """Vise le groupe du process, pas seulement le lanceur, pour ne laisser aucun enfant."""
        assert self._proc is not None
        try:
            os.killpg(os.getpgid(self._proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            self._proc.send_signal(sig)

    @property
    def vivant(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ── les quatre appels du protocole ──────────────────────────────────────────────

    async def initialiser(self) -> dict:
        """Annonce ce que le client sait faire ; la réponse donne les capacités de l'agent."""
        self.capacites = await self.requete(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            },
        )
        return self.capacites

    async def nouvelle_session(self, cwd: Path, serveurs_mcp: list[dict] | None = None) -> dict:
        """Ouvre une session. La réponse porte sessionId, les modes et la liste des modèles."""
        return await self.requete(
            "session/new", {"cwd": str(cwd), "mcpServers": serveurs_mcp or []}
        )

    async def charger_session(
        self, session_id: str, cwd: Path, serveurs_mcp: list[dict] | None = None
    ) -> dict:
        """Reprend une session d'un process à l'autre : l'agent rejoue tout en notifications."""
        return await self.requete(
            "session/load",
            {"sessionId": session_id, "cwd": str(cwd), "mcpServers": serveurs_mcp or []},
        )

    async def prompt(self, session_id: str, texte: str) -> dict:
        """Bloque jusqu'à la fin du tour et rend {stopReason, _meta.quota}."""
        return await self.requete(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": texte}]},
        )

    def annuler(self, session_id: str) -> None:
        """Annule le tour, puis résout à la main les autorisations en vol.

        L'agent ne les résout pas de lui-même : sans cette réponse `cancelled` le tour
        reste ouvert indéfiniment. Les deux gestes ne se séparent jamais.
        """
        self.notifier("session/cancel", {"sessionId": session_id})
        for rid in list(self._autorisations):
            self._autorisations.discard(rid)
            tache = self._taches.get(rid)
            if tache is not None:
                tache.cancel()
            self._envoyer({"jsonrpc": "2.0", "id": rid, "result": {"outcome": {"outcome": "cancelled"}}})

    # ── transport ───────────────────────────────────────────────────────────────────

    async def requete(self, methode: str, params: dict) -> Any:
        """Envoie une requête et attend sa réponse."""
        self._dernier_id += 1
        rid = self._dernier_id
        attente = asyncio.get_running_loop().create_future()
        self._attentes[rid] = attente
        self._envoyer({"jsonrpc": "2.0", "id": rid, "method": methode, "params": params})
        return await attente

    def notifier(self, methode: str, params: dict) -> None:
        """Envoie une notification : aucun id, aucune réponse attendue."""
        self._envoyer({"jsonrpc": "2.0", "method": methode, "params": params})

    def _envoyer(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise AcpErreur("agent non démarré")
        self._proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())

    async def _boucle_lecture(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            ligne = await self._proc.stdout.readline()
            if not ligne:
                break
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                message = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            self._trier(message)
        self._rompre_attentes(AcpErreur("le process agent s'est arrêté"))

    def _trier(self, message: dict) -> None:
        rid = message.get("id")
        if rid is not None and "method" in message:
            self._taches[rid] = asyncio.create_task(
                self._servir(rid, message["method"], message.get("params") or {})
            )
        elif rid is not None:
            attente = self._attentes.pop(rid, None)
            if attente is None or attente.done():
                return
            if "error" in message:
                attente.set_exception(AcpErreur(json.dumps(message["error"], ensure_ascii=False)))
            else:
                attente.set_result(message.get("result"))
        else:
            self.sur_notification(message)

    async def _servir(self, rid: Any, methode: str, params: dict) -> None:
        """Traite une requête de l'agent, dans sa propre tâche.

        La boucle de lecture ne doit jamais attendre ici : une autorisation peut rester
        en suspens le temps qu'un humain se décide, et l'agent continue d'émettre pendant
        ce temps.
        """
        try:
            if methode == "session/request_permission":
                self._autorisations.add(rid)
                resultat = {"outcome": await self.sur_autorisation(params)}
            elif methode == "fs/read_text_file":
                resultat = {"content": _lire_fichier(params)}
            elif methode == "fs/write_text_file":
                if self.sur_ecriture is not None:
                    await self.sur_ecriture(params)
                else:
                    ecrire_fichier(params)
                resultat = None
            else:
                raise AcpErreur(f"méthode inconnue : {methode}")
        except asyncio.CancelledError:
            return  # annuler() a déjà répondu « cancelled » à cette demande
        except Exception as erreur:
            self._envoyer(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(erreur)}}
            )
        else:
            self._envoyer({"jsonrpc": "2.0", "id": rid, "result": resultat})
        finally:
            self._autorisations.discard(rid)
            self._taches.pop(rid, None)

    def _rompre_attentes(self, erreur: Exception) -> None:
        for attente in self._attentes.values():
            if not attente.done():
                attente.set_exception(erreur)
        self._attentes.clear()


def ecrire_fichier(params: dict) -> None:
    """Écrit ce que l'agent demande. Les vérifications sont faites avant d'arriver ici."""
    chemin = Path(params["path"])
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(params.get("content", ""), encoding="utf-8")


def _lire_fichier(params: dict) -> str:
    """Lit un fichier pour l'agent, en respectant la fenêtre `line`/`limit` s'il en demande une."""
    texte = Path(params["path"]).read_text(encoding="utf-8")
    debut, limite = params.get("line"), params.get("limit")
    if debut is None and limite is None:
        return texte
    lignes = texte.splitlines(keepends=True)[max((debut or 1) - 1, 0) :]
    if limite is not None:
        lignes = lignes[:limite]
    return "".join(lignes)
