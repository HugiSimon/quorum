"""Vérifie le client ACP contre un faux agent : le round-trip, l'id 0, l'annulation."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.acp import ClientAcp  # noqa: E402

FAUX = Path(__file__).with_name("fake_agent.py")


async def _client(sur_autorisation, notifs, dossier):
    client = ClientAcp(lambda m: notifs.append(m), sur_autorisation)
    await client.demarrer(sys.executable, [str(FAUX)], {}, Path.cwd(), dossier / "agent.stderr.log")
    await client.initialiser()
    session = await client.nouvelle_session(Path.cwd())
    return client, session


async def scenario_nominal(dossier: Path) -> None:
    """Un tour complet : pensée, outil, autorisation accordée, fin propre."""
    notifs, demandes = [], []

    async def autoriser(params):
        demandes.append(params)
        return {"outcome": "selected", "optionId": params["options"][0]["optionId"]}

    client, session = await _client(autoriser, notifs, dossier)
    assert session["sessionId"], "la session doit avoir un identifiant"
    assert session["models"]["availableModels"], "la liste des modèles doit venir de la session"

    resultat = await client.prompt(session["sessionId"], "PERM supprime .venv")
    assert resultat["stopReason"] == "end_turn", resultat
    assert resultat["_meta"]["outcome"]["optionId"] == "proceed_always", resultat

    assert len(demandes) == 1, demandes
    options = demandes[0]["options"]
    assert [o["kind"] for o in options] == ["allow_always", "allow_once", "reject_once"], \
        "le client transmet les options dans l'ordre reçu : c'est l'affichage qui range"

    genres = [n["params"]["update"]["sessionUpdate"] for n in notifs]
    assert "agent_thought_chunk" in genres and "tool_call" in genres, genres
    await client.fermer()


async def scenario_annulation(dossier: Path) -> None:
    """^C pendant une autorisation en vol : le tour se ferme et la demande est résolue."""
    notifs = []
    arrivee = asyncio.Event()

    async def autoriser(params):
        arrivee.set()
        await asyncio.Event().wait()  # personne ne répond jamais

    client, session = await _client(autoriser, notifs, dossier)
    tour = asyncio.create_task(client.prompt(session["sessionId"], "PERM supprime .venv"))
    await asyncio.wait_for(arrivee.wait(), 5)

    client.annuler(session["sessionId"])
    resultat = await asyncio.wait_for(tour, 5)
    assert resultat["stopReason"] == "cancelled", resultat

    for _ in range(50):  # la réponse « cancelled » remonte au faux agent
        await asyncio.sleep(0.02)
        echos = [n["params"]["update"] for n in notifs
                 if n["params"]["update"]["sessionUpdate"] == "_reponse_autorisation"]
        if echos:
            break
    assert echos, "l'autorisation en vol n'a pas été résolue : le tour serait resté ouvert"
    assert echos[0]["outcome"] == {"outcome": "cancelled"}, echos
    await client.fermer()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        await scenario_nominal(Path(tmp))
        await scenario_annulation(Path(tmp))
    print("test_acp : nominal ok · id 0 ok · annulation ok")


if __name__ == "__main__":
    asyncio.run(main())
