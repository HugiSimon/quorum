"""Le chargement d'un bot : les variables absentes ne doivent jamais atteindre le process."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quorum.bot import charger, lancement, lire_env  # noqa: E402

BOT = """
nom = "essai"
role = "essai"
teinte = 150
commande = "gemini"
args = ["--acp"]

[env]
HTTPS_PROXY = "${HTTPS_PROXY}"
NODE_EXTRA_CA_CERTS = "${CA_DU_PROJET}"
VIDE = ""
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dossier = Path(tmp) / "essai"
        dossier.mkdir()
        (dossier / "bot.toml").write_text(BOT, encoding="utf-8")
        (dossier / "system.md").write_text("un rôle", encoding="utf-8")
        (dossier / "policy.toml").write_text("", encoding="utf-8")
        (Path(tmp) / ".env").write_text("CA_DU_PROJET=/tmp/ca.pem\n", encoding="utf-8")

        os.environ.pop("HTTPS_PROXY", None)
        bot = charger(dossier)
        commande, args, env = lancement(bot, lire_env(Path(tmp) / ".env"))

        assert "HTTPS_PROXY" not in env, "une variable non résolue ne doit pas être posée"
        assert "VIDE" not in env, "une variable vide ne doit pas être posée"
        assert env["NODE_EXTRA_CA_CERTS"] == "/tmp/ca.pem", env.get("NODE_EXTRA_CA_CERTS")
        assert env["GEMINI_SYSTEM_MD"].endswith("system.md")
        assert "--policy" in args and "--approval-mode" in args, args
        assert args[args.index("-e") + 1] == "aucune", "aucune extension personnelle héritée"
        assert commande == "gemini"

        # Le fournisseur est déduit de la commande quand bot.toml se tait.
        assert bot.fournisseur == "gemini", bot.fournisseur

        # Un bot qui n'est pas Gemini garde exactement ce que son bot.toml déclare.
        autre = bot.__class__(**{**bot.__dict__, "commande": "npx", "fournisseur": "autre"})
        commande_autre, args_autre, env_autre = lancement(autre)
        assert args_autre == ["--acp"], args_autre
        assert "GEMINI_SYSTEM_MD" not in env_autre and commande_autre == "npx"
    print("test_bot : variables absentes écartées · leviers gemini posés · autre fournisseur intact")


if __name__ == "__main__":
    main()
