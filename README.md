# Marchés à renouveler

Site statique généré chaque jour à partir des données essentielles de la commande publique
(DECP consolidées, data.gouv.fr, Licence Ouverte 2.0).

- `scripts/config.py` : nom du site, métiers (codes CPV), prix, liens de paiement, éditeur.
- `scripts/build.py` : lit le fichier parquet, écrit `data/schema_report.json`, génère `public/`.
- `.github/workflows/build.yml` : téléchargement, génération et publication sur GitHub Pages, tous les jours.

Essai local : `python scripts/build.py --parquet decp.parquet --out public`
