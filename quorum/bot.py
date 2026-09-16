"""Un bot est un dossier : bot.toml, system.md, policy.toml, settings.json.

Les quatre leviers de configuration passent tous par le process — le `cwd` reste celui du
travail. Rien de spécifique à un environnement n'est écrit ici : ce qui varie d'une machine
à l'autre (certificat, proxy) arrive par le bloc `env` du bot, en `${VAR}`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from string import Template


# Le motif est ce que l'utilisateur écrit dans la fiche ; la règle TOML est ce que le moteur
# de permissions lit. La table fait la traduction dans les deux sens.
MOTIFS = (
    ("shell:", {"toolName": "run_shell_command"}, "commandPrefix"),
    ("fs:lit", {"toolName": "read_file"}, None),
    ("fs:écrit", {"toolName": "write_file"}, None),
    ("fs:remplace", {"toolName": "replace"}, None),
    ("fs:liste", {"toolName": "list_directory"}, None),
    ("fs:cherche", {"toolName": "search_file_content"}, None),
    ("net:fetch", {"toolName": "web_fetch"}, None),
    ("net:cherche", {"toolName": "google_web_search"}, None),
    ("mcp:", {}, "mcpName"),
)

DECISIONS = {
    "allow": ("✓ autorisé", "toujours"),
    "ask_user": ("◆ me demander", "à chaque fois"),
    "deny": ("✕ interdit", "sans exception"),
}


@dataclass
class Regle:
    """Une ligne de la table des permissions : un motif, une décision. L'ordre fait la priorité."""

    motif: str
    decision: str = "ask_user"

    @property
    def libelles(self) -> tuple[str, str]:
        return DECISIONS.get(self.decision, ("?", "?"))


@dataclass(frozen=True)
class Bot:
    nom: str
    role: str
    teinte: int
    dossier: Path
    commande: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    modele: str | None = None
    fournisseur: str = "autre"
    # "commun" : tous les bots partagent le dossier de la salle. "copie" : ce bot travaille
    # dans son propre worktree git — utile dès qu'il écrit.
    dossier_de_travail: str = "commun"
    # Les trois capacités de la fiche : chacune a un effet réel sur la conversation.
    peut_interpeller: bool = True
    raisonnement_visible: bool = True
    parle_sans_etre_appele: bool = True

    @property
    def mention(self) -> str:
        return f"@{self.nom}"

    @property
    def copie_isolee(self) -> bool:
        return self.dossier_de_travail == "copie"


def lire_env(fichier: Path) -> dict[str, str]:
    """Lit un .env non versionné : une variable par ligne, # en commentaire."""
    valeurs: dict[str, str] = {}
    if not fichier.exists():
        return valeurs
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        valeurs[cle.strip()] = valeur.strip().strip("'\"")
    return valeurs


def charger(dossier: Path) -> Bot:
    """Construit un Bot depuis son dossier. Le nom du dossier fait foi si bot.toml se tait."""
    conf = tomllib.loads((dossier / "bot.toml").read_text(encoding="utf-8"))
    return Bot(
        nom=conf.get("nom", dossier.name),
        role=conf.get("role", ""),
        teinte=int(conf.get("teinte", 0)),
        dossier=dossier,
        commande=conf["commande"],
        args=list(conf.get("args", [])),
        env=dict(conf.get("env", {})),
        modele=conf.get("modele") or None,
        dossier_de_travail=conf.get("dossier", "commun"),
        peut_interpeller=bool(conf.get("peut_interpeller", True)),
        raisonnement_visible=bool(conf.get("raisonnement_visible", True)),
        parle_sans_etre_appele=bool(conf.get("parle_sans_etre_appele", True)),
        fournisseur=conf.get(
            "fournisseur", "gemini" if "gemini" in conf["commande"] else "autre"
        ),
    )


def charger_tous(racine: Path) -> dict[str, Bot]:
    """Tous les bots d'un dossier bots/, indexés par nom. Aucun dossier = aucun bot."""
    bots: dict[str, Bot] = {}
    if not racine.exists():
        return bots
    for dossier in sorted(p for p in racine.iterdir() if (p / "bot.toml").exists()):
        bot = charger(dossier)
        bots[bot.nom] = bot
    return bots


def _toml_valeur(valeur) -> str:
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, int):
        return str(valeur)
    return '"' + str(valeur).replace("\\", "\\\\").replace('"', '\\"') + '"'


def ecrire_toml(tables: list[tuple[str, dict]], entete: str = "") -> str:
    """Écrit le TOML dont on a besoin : des tables plates, rien de plus.

    C'est le seul TOML que l'application produit et sa forme est fixe — ça ne vaut pas une
    dépendance de plus.
    """
    morceaux = [f"# {ligne}" for ligne in entete.splitlines() if ligne] + [""] if entete else []
    for nom, champs in tables:
        morceaux.append(f"[{nom}]" if not nom.startswith("[") else nom)
        for cle, valeur in champs.items():
            if valeur is not None and valeur != "":
                morceaux.append(f"{cle} = {_toml_valeur(valeur)}")
        morceaux.append("")
    return "\n".join(morceaux).rstrip() + "\n"


def regle_vers_toml(regle: Regle, priorite: int) -> dict:
    """Traduit un motif de la fiche en champs que le moteur de permissions comprend."""
    champs: dict = {}
    for prefixe, fixes, champ_libre in MOTIFS:
        if not regle.motif.startswith(prefixe):
            continue
        champs.update(fixes)
        reste = regle.motif[len(prefixe):].strip().rstrip("*").strip()
        if champ_libre and reste:
            champs[champ_libre] = reste
        break
    champs["decision"] = regle.decision
    champs["priority"] = priorite
    return champs


def toml_vers_motif(champs: dict) -> str:
    """Reconstruit le motif affichable d'une règle lue sur disque."""
    for prefixe, fixes, champ_libre in MOTIFS:
        if fixes and all(champs.get(c) == v for c, v in fixes.items()):
            if not champ_libre:
                return prefixe
            reste = champs.get(champ_libre)
            return f"{prefixe}{reste} *" if reste else f"{prefixe}*"
        if not fixes and champ_libre and champs.get(champ_libre):
            return f"{prefixe}{champs[champ_libre]}"
    return "tout le reste"


def lire_regles(dossier: Path) -> list[Regle]:
    """Les règles d'un bot, dans l'ordre de priorité décroissante — la première gagne."""
    fichier = dossier / "policy.toml"
    if not fichier.exists():
        return [Regle("tout le reste", "ask_user")]
    brut = tomllib.loads(fichier.read_text(encoding="utf-8")).get("rules", [])
    brut.sort(key=lambda r: -int(r.get("priority", 0)))
    return [Regle(toml_vers_motif(r), r.get("decision", "ask_user")) for r in brut]


def pouvoir(regles: list[Regle]) -> tuple[int, str]:
    """La jauge de la fiche : ce que les règles autorisent vraiment, sans rien promettre de plus."""
    autorise = [r.motif for r in regles if r.decision == "allow"]
    interdits = sum(1 for r in regles if r.decision == "deny")
    garde_fous = sum(1 for r in regles if r.decision == "ask_user")
    ecrit = any(m.startswith(("fs:écrit", "fs:remplace")) for m in autorise)
    lance = any(m.startswith("shell") for m in autorise)
    sort = any(m.startswith("net") for m in autorise)
    niveau = 1 + 2 * ecrit + 2 * lance + 2 * sort
    phrases = []
    phrases.append("peut modifier des fichiers" if ecrit else "ne modifie aucun fichier")
    phrases.append("lance des commandes" if lance else "ne lance aucune commande")
    phrases.append("sort sur le réseau" if sort else "ne sort pas sur le réseau")
    return niveau, (
        " ; ".join(phrases)
        + f". {garde_fous} garde-fou{'s' if garde_fous > 1 else ''}, "
        + f"{interdits} interdit{'s' if interdits > 1 else ''}."
    )


def ecrire_bot(dossier: Path, bot: Bot, role: str, regles: list[Regle]) -> None:
    """Écrit les trois fichiers d'un bot. Un bot reste un dossier, éditable à la main."""
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "system.md").write_text(role.rstrip() + "\n", encoding="utf-8")

    lignes = ["# écrit par quorum · reste éditable à la main", ""]
    for cle, valeur in (
        ("nom", bot.nom), ("role", bot.role), ("teinte", bot.teinte),
        ("commande", bot.commande), ("modele", bot.modele or ""),
        ("fournisseur", bot.fournisseur), ("dossier", bot.dossier_de_travail),
        ("peut_interpeller", bot.peut_interpeller),
        ("raisonnement_visible", bot.raisonnement_visible),
        ("parle_sans_etre_appele", bot.parle_sans_etre_appele),
    ):
        lignes.append(f"{cle} = {_toml_valeur(valeur)}")
    lignes.append("args = [" + ", ".join(_toml_valeur(a) for a in bot.args) + "]")
    lignes += ["", "[env]"] + [f"{c} = {_toml_valeur(v)}" for c, v in bot.env.items()]
    (dossier / "bot.toml").write_text("\n".join(lignes).rstrip() + "\n", encoding="utf-8")

    (dossier / "policy.toml").write_text(
        ecrire_toml(
            [("[[rules]]", regle_vers_toml(regle, 100 - index * 5))
             for index, regle in enumerate(regles)],
            entete="première règle qui gagne, priorité décroissante",
        ),
        encoding="utf-8",
    )


def lancement(
    bot: Bot,
    env_projet: dict[str, str] | None = None,
    telemetrie: Path | None = None,
) -> tuple[str, list[str], dict[str, str]]:
    """Rend le triplet (commande, args, env) prêt pour asyncio.

    Les leviers propres à Gemini ne sont posés que si la commande en est une : un bot
    Claude Code ou Codex garde exactement ce que son bot.toml déclare.

    `telemetrie` est le seul chemin par lequel la sortie des commandes remonte : l'agent
    ne l'envoie pas dans le flux. Sans ce journal, le fil montre les commandes sans ce
    qu'elles ont répondu.
    """
    substitutions = {**os.environ, **(env_projet or {})}
    env = dict(os.environ)
    for cle, valeur in bot.env.items():
        resolu = Template(valeur).safe_substitute(substitutions)
        # Non résolue ou vide veut dire « ne la pose pas » : un HTTPS_PROXY vide, ou resté
        # en ${HTTPS_PROXY}, suffit à faire tomber l'agent au démarrage.
        if not resolu or "${" in resolu:
            env.pop(cle, None)
        else:
            env[cle] = resolu

    args = list(bot.args)
    if bot.fournisseur == "gemini":
        if (bot.dossier / "system.md").exists():
            env["GEMINI_SYSTEM_MD"] = str(bot.dossier / "system.md")
        if (bot.dossier / "settings.json").exists():
            env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(bot.dossier / "settings.json")
        if (bot.dossier / "policy.toml").exists():
            args += ["--policy", str(bot.dossier / "policy.toml")]
        # Sans ce mode, l'agent décide seul et aucune autorisation ne remonte.
        args += ["--approval-mode", "default"]
        # Un bot n'hérite pas des extensions personnelles de la machine : ses outils lui
        # viennent de sa policy et des MCP passés à session/new. Un nom qui n'existe pas
        # suffit à n'en charger aucune. (Les serveurs A2A des réglages utilisateur, eux,
        # se chargent encore : pas de levier trouvé.)
        args += ["-e", "aucune"]
        if bot.modele:
            args += ["-m", bot.modele]
        if telemetrie is not None:
            telemetrie.parent.mkdir(parents=True, exist_ok=True)
            env["GEMINI_TELEMETRY_ENABLED"] = "true"
            env["GEMINI_TELEMETRY_TARGET"] = "local"
            env["GEMINI_TELEMETRY_OUTFILE"] = str(telemetrie)
    return bot.commande, args, env
