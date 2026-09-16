#!/bin/sh
# Toutes les vérifications du projet, dans l'ordre où elles sont apparues.
set -e
for m in theme room reglages; do uv run python quorum/$m.py | tail -1; done
for t in acp bot fiche telemetry room_app rounds raisonnement reprise ecran_fiche ecran_salle accueil extremes; do
  uv run python tests/test_$t.py
done
echo "— tout au vert"
