# Quorum

Une salle, plusieurs agents, un seul fil.

Quorum est une application de terminal où l'on discute avec plusieurs agents IA à la fois,
dans une conversation unique, comme une messagerie d'équipe. Chaque bot a son rôle, son
modèle, ses outils et ses permissions. Ils travaillent vraiment — ils lisent des fichiers,
lancent des commandes, appellent des serveurs MCP — et peuvent s'interpeller entre eux avec
`@nom`. Les demandes d'autorisation remontent à l'utilisateur, qui accepte, refuse, ou
refuse en expliquant pourquoi.

```
◈ atlas / refonte-auth · tour 12                              ◐2 ▸1 ·2

  ▌ toi · 14:02
    @sonar regarde comment on valide les tokens, et @audit dis-moi si la
    rotation des clés est correcte. Ne touchez à rien pour l'instant.

  ▌ @sonar  recherche · 14:02  ◐ pense · 0:18
    ▸ shell · ls src/auth/                    ✓ 0.2s · sortie +1.4s
        jwt.py  legacy/  tokens.py  __init__.py
    ▸ fs · lit src/auth/jwt.py                ✓ 0.4s
    ┊ ouvrir le raisonnement ⏎ · 3 jalons · 2 outils · 0:18

  ◆ @forge demande une autorisation                            ⇥ suivante
    rm -rf .venv && uv sync
    1 ✓  Allow
    2 ✓✓ Allow for this session
    3 ✕  Reject
    1-9 décider · r refuser en expliquant · esc revenir à la saisie

◇ écris pendant qu'ils travaillent…▏
◐@sonar  ◆@forge  ·@audit                    ^C interrompre · 0:18
```

## Installer

```sh
git clone <ce dépôt> && cd quorum
uv sync
```

Il faut au moins un agent qui parle **ACP** (Agent Client Protocol) :

| Fournisseur | Commande |
|---|---|
| Gemini CLI | `gemini --acp` |
| Claude Code | `npx @zed-industries/claude-code-acp` |
| Codex | `npx @zed-industries/codex-acp` |

Quorum n'est lié à aucun d'eux : un bot déclare simplement la commande à lancer.

## Lancer

```sh
uv run quorum          # l'accueil : reprendre une salle, en créer une, gérer les bots
uv run quorum demo     # ouvrir directement une salle
```

**Dans l'accueil** — `↑↓` parcourir · `⏎` ouvrir · `n` nouvelle salle · `b` nouveau bot ·
`,` réglages · `^Q` quitter.

**Dans une salle** — `⏎` envoyer · `1`…`9` décider d'une autorisation · `r` refuser en
expliquant · `^C` interrompre le tour · `^R` voir travailler le dernier bot actif ·
`^B` sa fiche · `^O` composer la salle · `^G` réglages · `fin` suivre le flux ·
`^Q` revenir à l'accueil.

**Dans une fiche** — `⇥` champ suivant · `^S` enregistrer · `esc` abandonner. Dans la table
des permissions : `a` ajouter · `e` modifier le motif · `d` changer la décision · `x`
retirer · `⇧↑↓` réordonner.

## Un bot est un dossier

```
bots/forge/
  bot.toml       nom, rôle, teinte, commande, modèle, capacités
  system.md      son rôle — remplace le prompt système de l'agent
  policy.toml    ses permissions — première règle qui gagne
  settings.json  ses réglages — isole le bot des MCP personnels de la machine
```

Tout est éditable à la main ; la fiche (`^B`) écrit exactement ces fichiers.

Ce qui dépend de la machine (certificat d'entreprise, proxy) passe par le bloc `env` du bot
en `${VARIABLE}`, résolu depuis un `.env` non versionné. Voir `.env.example`.

## Une salle est un dossier

```
rooms/demo/
  room.toml          membres, dossier de travail, budget d'enchaînement — écrit par un humain
  transcript.jsonl   le fil, source de vérité — non versionné
  state.json         sessions et index de lecture par bot — écrit par la machine
```

**Le transcript fait foi.** Les sessions des agents ne sont que leur mémoire privée : une
salle dont les sessions sont mortes reste lisible, et les bots repartent du fil.

## Ce qui est mesuré, et ce qui ne marche pas

Ces points viennent d'essais sur le vrai protocole, pas de la documentation.

- **La sortie des commandes n'est pas dans le flux ACP.** Ni elle, ni le contenu des fichiers
  lus. Pour Gemini, on la récupère dans le journal de télémétrie local — elle arrive **~5 s
  après** la fin de l'outil, d'un bloc. L'interface tient ce décalage (`✓ 0.6s · sortie en
  route`) et le dit quand elle ne vient jamais. Sans ce journal, ou chez un autre
  fournisseur, le fil montre les commandes sans leurs réponses.
- **`session/load` ne reprend rien avec Gemini CLI 0.59.** L'agent annonce pourtant
  `loadSession: true`, mais répond « No previous sessions found for this project » : la
  session n'est jamais écrite sur disque. Le code de reprise est là et testé ; en attendant,
  c'est le transcript qui porte la mémoire — et ça marche.
- **Un bot hérite des réglages personnels de la machine.** `settings.json` avec
  `{"mcp": {"allowed": []}}` coupe les serveurs MCP, et `-e` sans extension valide coupe les
  extensions. Les serveurs A2A des réglages utilisateur, eux, se chargent encore.
- **Le décompte de jetons n'existe qu'à la fin d'un tour.** Rien à afficher pendant.
- **Les pensées des agents arrivent en anglais**, en blocs titrés. Le fil n'en garde que les
  titres ; le corps s'ouvre dans l'écran de raisonnement.

## Vérifier

```sh
./verifier.sh
```

Douze vérifications, sans réseau ni modèle : un faux agent ACP scripté joue les scénarios
(autorisation, refus commenté, annulation, rounds, reprise, sorties en décalé). Pas de
dépendance de test — des `assert` et un `__main__`.

## Choix

Python et [Textual](https://textual.textualize.io/), gérés par `uv`. Une seule dépendance.
Pas de base de données : des fichiers. Le client ACP fait environ 250 lignes et ne dépend de
rien. Tout le français dans l'interface, les identifiants en anglais.
