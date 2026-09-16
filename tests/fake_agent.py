"""Faux agent ACP : juste assez de protocole pour éprouver le client, sans réseau ni modèle.

Il émet volontairement sa demande d'autorisation avec l'**id 0** : c'est le piège que le
client doit passer.
"""

import json
import os
import sys

NOM = sys.argv[1] if len(sys.argv) > 1 else "faux"
RELAI = sys.argv[2] if len(sys.argv) > 2 else ""
SESSION = f"sess-{NOM}"


def replique(prompt: str) -> tuple[str, bool]:
    """Ce que le faux agent répond, et s'il réclame une autorisation avant.

    Les mots-clés du prompt pilotent le scénario : PERM demande une autorisation, RELANCE
    interpelle l'autre bot, RADOTE répond toujours la même chose.
    """
    if "[refus de l'utilisateur]" in prompt:
        return "Compris. Je propose `uv pip install -r requirements.txt` à la place.", False
    if "RADOTE" in prompt:
        return "je radote toujours pareil", False
    if RELAI and ("RELANCE" in prompt or "tu confirmes" in prompt):
        return f"@{RELAI} tu confirmes ?", False
    if "ECRIRE:" in prompt:
        return "écriture demandée", False
    if "PERM" in prompt:
        return f"c'est fait, {NOM}.", True
    return f"c'est fait, {NOM}.", False


JOURNAL = os.environ.get("GEMINI_TELEMETRY_OUTFILE")


def journaliser(nom_outil: str, id_appel: str, sortie: str) -> None:
    """Écrit la sortie d'un outil comme le vrai agent : dans le journal, pas dans le flux."""
    if not JOURNAL:
        return
    enregistrement = {
        "attributes": {
            "event.name": "gemini_cli.api_request",
            "request_text": json.dumps([{
                "role": "user",
                "parts": [{"functionResponse": {
                    "name": nom_outil, "id": id_appel,
                    "response": {"output": f"<untrusted_context>\nOutput:{sortie}\n"
                                           f"Process Group PGID: 1\n</untrusted_context>"},
                }}],
            }]),
        },
        "_body": "API request to faux-1.",
    }
    with open(JOURNAL, "a", encoding="utf-8") as fichier:
        fichier.write(json.dumps(enregistrement, indent=2) + "\n")


def envoyer(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def maj(contenu: dict) -> None:
    envoyer({"jsonrpc": "2.0", "method": "session/update",
             "params": {"sessionId": SESSION, "update": contenu}})


def main() -> None:
    tour = None  # id du session/prompt en cours
    reponse_prevue = f"c'est fait, {NOM}."
    while True:
        ligne = sys.stdin.readline()
        if not ligne:
            return
        ligne = ligne.strip()
        if not ligne:
            continue
        message = json.loads(ligne)
        mid, methode = message.get("id"), message.get("method")

        if methode == "initialize":
            envoyer({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True},
                "authMethods": [],
            }})
        elif methode == "session/new":
            envoyer({"jsonrpc": "2.0", "id": mid, "result": {
                "sessionId": SESSION,
                "modes": {"currentModeId": "default", "availableModes": [{"id": "default"}]},
                "models": {"availableModels": [{"modelId": "faux-1", "name": "Faux 1"}]},
            }})
        elif methode == "session/load":
            demandee = (message.get("params") or {}).get("sessionId")
            if demandee == SESSION:
                envoyer({"jsonrpc": "2.0", "id": mid, "result": {}})
            else:
                envoyer({"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32602, "message": f"session inconnue : {demandee}"}})
        elif methode == "session/prompt":
            tour = mid
            recu = " ".join(
                bloc.get("text", "") for bloc in (message.get("params") or {}).get("prompt", [])
            )
            texte, demande = replique(recu)
            if "ECRIRE:" in recu:
                # L'agent demande au CLIENT d'écrire : c'est la frontière que garde l'appli.
                cible = recu.split("ECRIRE:", 1)[1].split()[0]
                envoyer({"jsonrpc": "2.0", "id": 7, "method": "fs/write_text_file",
                         "params": {"sessionId": SESSION, "path": cible,
                                    "content": "écrit par l'agent\n"}})
                continue
            if not demande:
                maj({"sessionUpdate": "agent_message_chunk",
                     "content": {"type": "text", "text": texte}})
                envoyer({"jsonrpc": "2.0", "id": tour, "result": {"stopReason": "end_turn"}})
                tour = None
                continue
            reponse_prevue = texte
            maj({"sessionUpdate": "agent_thought_chunk", "content": {
                "type": "text",
                "text": "**Reading The Sources**\nI am looking at the notes first.\n"
                        "**Executing Shell Commands**\nI am now proceeding with the execution.",
            }})
            maj({"sessionUpdate": "tool_call", "toolCallId": "run_shell_command__call_0",
                 "title": "rm -rf .venv && uv sync", "kind": "execute", "status": "pending",
                 "locations": []})
            maj({"sessionUpdate": "tool_call", "toolCallId": "read_file__call_7",
                 "title": "lit notes.md", "kind": "read",
                 "locations": [{"path": "/tmp/notes.md"}]})
            maj({"sessionUpdate": "tool_call_update", "toolCallId": "read_file__call_7",
                 "status": "completed", "kind": "read"})
            envoyer({"jsonrpc": "2.0", "id": 0, "method": "session/request_permission", "params": {
                "sessionId": SESSION,
                "toolCall": {"toolCallId": "run_shell_command__call_0",
                             "title": "rm -rf .venv && uv sync"},
                # Ordre volontairement celui du vrai agent : la permission la plus large
                # arrive en premier.
                "options": [
                    {"optionId": "proceed_always", "name": "Allow for this session",
                     "kind": "allow_always"},
                    {"optionId": "proceed_once", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "cancel", "name": "Reject", "kind": "reject_once"},
                ],
            }})
        elif methode == "session/cancel":
            if tour is not None:
                envoyer({"jsonrpc": "2.0", "id": tour, "result": {"stopReason": "cancelled"}})
                tour = None
        elif mid == 7 and methode is None:
            refus = "error" in message
            maj({"sessionUpdate": "agent_message_chunk", "content": {
                "type": "text",
                "text": "écriture refusée" if refus else "écriture faite"}})
            if tour is not None:
                envoyer({"jsonrpc": "2.0", "id": tour, "result": {"stopReason": "end_turn"}})
                tour = None
        elif mid is not None and methode is None:
            issue = (message.get("result") or {}).get("outcome")
            if tour is None:
                # Réponse arrivée après l'annulation : on la renvoie pour que le test la voie.
                maj({"sessionUpdate": "_reponse_autorisation", "outcome": issue})
            else:
                maj({"sessionUpdate": "tool_call_update", "kind": "execute",
                     "toolCallId": "run_shell_command__call_0", "status": "completed"})
                journaliser("run_shell_command", "call_0", "        2 donnees.txt")
                maj({"sessionUpdate": "agent_message_chunk",
                     "content": {"type": "text", "text": reponse_prevue}})
                envoyer({"jsonrpc": "2.0", "id": tour,
                         "result": {"stopReason": "end_turn", "_meta": {"outcome": issue}}})
                tour = None


if __name__ == "__main__":
    main()
