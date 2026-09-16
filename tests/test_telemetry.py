"""L'adaptateur de sorties, sur un journal capturé : aucun agent, aucun réseau."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.telemetry import (  # noqa: E402
    SortiesGemini,
    identifiant_court,
    nettoyer,
    objets,
    sorties_de,
)

JOURNAL = Path(__file__).with_name("journal_gemini.json")


def main() -> None:
    texte = JOURNAL.read_text(encoding="utf-8")

    # Le journal n'est pas du JSONL : trois objets indentés, collés.
    trouves, consomme = objets(texte)
    assert len(trouves) == 3, len(trouves)
    assert consomme == len(texte.rstrip()) or consomme <= len(texte)

    # Seul l'enregistrement api_request porte une sortie.
    recolte = [s for objet in trouves for s in sorties_de(objet)]
    assert len(recolte) == 1, recolte
    nom, identifiant, sortie = recolte[0]
    assert (nom, identifiant) == ("run_shell_command", "call_8728"), recolte[0]
    assert sortie == "        2 donnees.txt", repr(sortie)

    # Le raccord ACP → journal.
    assert identifiant_court("run_shell_command__call_8728") == "call_8728"
    assert identifiant_court("call_8728") == "call_8728", "un id sans préfixe reste lui-même"

    # L'emballage part, l'alignement de la sortie reste.
    assert nettoyer("<untrusted_context>\nOutput: ok\n</untrusted_context>") == " ok"
    assert nettoyer("brut, sans emballage") == "brut, sans emballage"

    # Un objet incomplet est laissé pour le passage suivant, pas perdu.
    moitie = texte[: texte.index("api_request") + 5]
    partiels, reste = objets(moitie)
    assert len(partiels) == 1 and reste < len(moitie), (len(partiels), reste)

    # Et la classe qui suit le fichier ne signale chaque sortie qu'une fois.
    vues = []
    suiveur = SortiesGemini(JOURNAL, lambda i, s: vues.append((i, s)))
    suiveur.moissonner()
    suiveur.moissonner()
    assert vues == [("call_8728", "        2 donnees.txt")], vues
    print("test_telemetry : découpage ok · extraction ok · raccord ok · une seule fois ok")


if __name__ == "__main__":
    main()
