#!/usr/bin/env python3
"""Génère le site statique « Marchés à renouveler » à partir des DECP consolidées.

Source : Données essentielles de la commande publique consolidées (data.gouv.fr),
Licence Ouverte 2.0. Le script inspecte le schéma du fichier, écrit un rapport
(data/schema_report.json) puis produit une page par métier et par département.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys

import duckdb

from config import DEPARTEMENTS, METIERS, SITE

# Colonnes attendues -> noms candidats, du plus probable au moins probable.
CANDIDATES = {
    "uid": ["uid"],
    "objet": ["objet"],
    "nature": ["nature"],
    "cpv": ["codeCPV", "cpv", "code_cpv"],
    "montant": ["montant"],
    "date_notif": ["dateNotification", "date_notification"],
    "duree": ["dureeMois", "duree_mois"],
    "acheteur_nom": ["acheteur_nom", "acheteur.nom"],
    "acheteur_dep": ["acheteur_departement_code", "acheteur_departement"],
    "tit_nom": ["titulaire_nom", "titulaire_denominationSociale", "titulaire.denominationSociale"],
    "tit_id": ["titulaire_id", "titulaire.id"],
    "tit_cat": ["titulaire_categorie"],
    "actuel": ["donneesActuelles", "donnees_actuelles"],
}
REQUIRED = ["objet", "cpv", "date_notif", "acheteur_nom", "acheteur_dep"]
FUZZY = {
    "acheteur_dep": ["acheteur", "departement", "code"],
    "tit_nom": ["titulaire", "nom"],
    "tit_cat": ["titulaire", "categorie"],
}


def resolve_columns(columns):
    lower = {c.lower(): c for c in columns}
    mapping = {}
    for key, names in CANDIDATES.items():
        for n in names:
            if n.lower() in lower:
                mapping[key] = lower[n.lower()]
                break
        if key not in mapping and key in FUZZY:
            for c in columns:
                if all(tok in c.lower() for tok in FUZZY[key]):
                    mapping[key] = c
                    break
    return mapping


def q(col):
    return '"' + col.replace('"', '""') + '"'


def esc(v):
    return html.escape("" if v is None else str(v))


def euro(v):
    if v is None:
        return "non renseigné"
    return f"{v:,.0f}".replace(",", " ") + " €"


def fr_date(d):
    return d.strftime("%d/%m/%Y") if d else "non renseignée"


MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


def fr_month(d):
    return f"{MOIS[d.month - 1]} {d.year}" if d else "non estimé"


def de(n):
    return ("d'" if n[0].lower() in "aeiouyéèêàh" else "de ") + n


def slugify(s):
    s = s.lower()
    for a, b in (("éèêë", "e"), ("àâä", "a"), ("îï", "i"), ("ôö", "o"), ("ùûü", "u"), ("ç", "c"), ("'’ ", "-")):
        for ch in a:
            s = s.replace(ch, b)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "", s)).strip("-")


def page(title, description, body, depth, canonical):
    root = "../" * depth
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(SITE['url'].rstrip('/') + '/' + canonical)}">
<link rel="stylesheet" href="{root}style.css">
</head>
<body>
<header><a class="brand" href="{root}index.html">{esc(SITE['name'])}</a>
<nav><a href="{root}methode.html">Méthode et limites</a> <a href="{root}mentions-legales.html">Mentions légales</a></nav></header>
<main>
{body}
</main>
<footer>Source : Données essentielles de la commande publique consolidées, data.gouv.fr, Licence Ouverte 2.0.
Dernière génération : {fr_date(dt.date.today())}. Les échéances sont des estimations, voir <a href="{root}methode.html">la méthode</a>.</footer>
</body>
</html>
"""


def table(headers, rows):
    if not rows:
        return "<p class=\"empty\">Aucune ligne dans les données publiées.</p>"
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class=\"scroll\"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>"


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--out", default="public")
    ap.add_argument("--report", default="data/schema_report.json")
    args = ap.parse_args()

    con = duckdb.connect()
    src = f"read_parquet('{args.parquet}')"
    desc = con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()
    columns = [r[0] for r in desc]
    mapping = resolve_columns(columns)
    total = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    sample = con.execute(f"SELECT * FROM {src} LIMIT 3").fetchall()
    missing = [k for k in REQUIRED if k not in mapping]
    report = {
        "genere_le": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lignes": total,
        "colonnes": [{"nom": r[0], "type": r[1]} for r in desc],
        "correspondance": mapping,
        "colonnes_requises_manquantes": missing,
        "echantillon": [[None if v is None else str(v)[:120] for v in row] for row in sample],
    }
    write(args.report, json.dumps(report, ensure_ascii=False, indent=1))
    if missing:
        print("Colonnes requises introuvables :", missing, file=sys.stderr)
        sys.exit(2)

    m = mapping

    def col(key, cast=None, default="NULL"):
        if key not in m:
            return default
        return f"TRY_CAST({q(m[key])} AS {cast})" if cast else q(m[key])

    where_actuel = f"WHERE {col('actuel', 'BOOLEAN')} IS NOT FALSE" if "actuel" in m else ""
    group_key = col("uid", "VARCHAR") if "uid" in m else (
        f"concat_ws('|', {col('acheteur_nom', 'VARCHAR')}, {col('objet', 'VARCHAR')}, {col('date_notif', 'VARCHAR')})")
    con.execute(f"""
        CREATE TABLE marches AS
        SELECT {group_key} AS cle,
               any_value({col('objet', 'VARCHAR')}) AS objet,
               any_value({col('nature', 'VARCHAR')}) AS nature,
               any_value(replace({col('cpv', 'VARCHAR')}, '-', '')) AS cpv,
               max({col('montant', 'DOUBLE')}) AS montant,
               max({col('date_notif', 'DATE')}) AS date_notif,
               max({col('duree', 'INTEGER')}) AS duree,
               any_value({col('acheteur_nom', 'VARCHAR')}) AS acheteur,
               any_value(upper(trim({col('acheteur_dep', 'VARCHAR')}))) AS dep,
               string_agg(DISTINCT {col('tit_nom', 'VARCHAR')}, ' ; ') AS titulaires,
               any_value({col('tit_cat', 'VARCHAR')}) AS categorie
        FROM {src} {where_actuel}
        GROUP BY 1
    """)
    con.execute("""
        ALTER TABLE marches ADD COLUMN fin DATE;
        UPDATE marches SET fin = date_notif + to_months(duree)
        WHERE date_notif IS NOT NULL AND duree BETWEEN 1 AND 120;
    """)

    today = dt.date.today()
    horizon = today + dt.timedelta(days=548)
    out = args.out
    urls = ["index.html", "methode.html", "mentions-legales.html"]
    index_blocks = []
    stats_global = {"pages": 0, "echeances": 0}

    for met in METIERS:
        cond = " OR ".join(f"cpv LIKE '{p}%'" for p in met["cpv"])
        rows = con.execute(f"""
            SELECT dep, objet, acheteur, titulaires, montant, date_notif, duree, fin, categorie
            FROM marches WHERE ({cond}) AND dep IS NOT NULL AND date_notif >= DATE '2019-01-01'
              AND date_notif <= current_date
            ORDER BY date_notif DESC
        """).fetchall()
        by_dep = {}
        for r in rows:
            if r[0] in DEPARTEMENTS:
                by_dep.setdefault(r[0], []).append(r)
        dep_links = []
        for dep in sorted(by_dep):
            lst = by_dep[dep]
            nom = DEPARTEMENTS[dep]
            slug = f"{dep.lower()}-{slugify(nom)}"
            rel = f"{met['slug']}/{slug}.html"
            a_venir = sorted([r for r in lst if r[7] and today <= r[7] <= horizon], key=lambda r: r[7])
            montants = sorted(r[4] for r in lst if r[4] and r[4] > 0)
            mediane = montants[len(montants) // 2] if montants else None
            tit_count = {}
            for r in lst:
                for t in (r[3] or "").split(" ; "):
                    if t.strip():
                        tit_count[t.strip()] = tit_count.get(t.strip(), 0) + 1
            top_tit = sorted(tit_count.items(), key=lambda x: -x[1])[:5]
            free = a_venir[:SITE["lignes_gratuites"]]
            reste = len(a_venir) - len(free)
            t1 = table(
                ["Objet", "Acheteur", "Titulaire actuel", "Montant", "Notifié le", "Durée", "Échéance estimée"],
                [[esc(r[1]), esc(r[2]), esc(r[3] or "non renseigné"), euro(r[4]), fr_date(r[5]),
                  f"{r[6]} mois", fr_month(r[7])] for r in free])
            pay = SITE["liens_paiement"].get(met["slug"])
            if reste > 0 and pay:
                more = (f"<p class=\"more\">{reste} autres marchés arrivent à échéance dans ce département. "
                        f"<a class=\"btn\" href=\"{esc(pay)}\">Obtenir la liste complète ({esc(SITE['prix'])})</a></p>")
            elif reste > 0:
                more = (f"<p class=\"more\">{reste} autres marchés arrivent à échéance dans ce département. "
                        "La liste complète est en préparation.</p>")
            else:
                more = ""
            t2 = table(
                ["Objet", "Acheteur", "Titulaire", "Montant", "Notifié le"],
                [[esc(r[1]), esc(r[2]), esc(r[3] or "non renseigné"), euro(r[4]), fr_date(r[5])] for r in lst[:20]])
            top = "".join(f"<li>{esc(n)} : {c} marché{'s' if c > 1 else ''}</li>" for n, c in top_tit)
            title = f"Marchés publics {met['nom_court']} en {nom} ({dep}) : attributions et échéances"
            descr = (f"{len(lst)} marchés publics {de(met['nom_court'])} publiés en {nom} depuis 2019, "
                     f"dont {len(a_venir)} arrivent à échéance dans les 18 mois.")
            body = f"""<p class="crumb"><a href="../index.html">Accueil</a> › <a href="index.html">{esc(met['nom'])}</a> › {esc(nom)}</p>
<h1>Marchés publics {esc(de(met['nom_court']))} en {esc(nom)} ({esc(dep)})</h1>
<p class="lead">{len(lst)} marchés publiés depuis 2019. {len(a_venir)} arrivent à échéance dans les 18 mois.
Montant médian : {euro(mediane)}.</p>
<h2>Marchés qui arrivent à échéance</h2>
<p>Un marché qui se termine est en général remis en concurrence quelques mois avant. L'échéance est estimée à partir de la date de notification et de la durée publiée.</p>
{t1}
{more}
<h2>Entreprises les plus souvent titulaires</h2>
{('<ul>' + top + '</ul>') if top else '<p class="empty">Titulaires non renseignés.</p>'}
<h2>Derniers marchés attribués</h2>
{t2}
"""
            write(os.path.join(out, rel), page(title, descr, body, 1, rel))
            urls.append(rel)
            dep_links.append((dep, nom, slug, len(lst), len(a_venir)))
            stats_global["pages"] += 1
            stats_global["echeances"] += len(a_venir)
        lis = "".join(
            f"<li><a href=\"{s}.html\">{esc(n)} ({esc(d)})</a> <span>{c} marchés, {a} échéances</span></li>"
            for d, n, s, c, a in dep_links)
        body = f"""<p class="crumb"><a href="../index.html">Accueil</a> › {esc(met['nom'])}</p>
<h1>Marchés publics : {esc(met['nom'])}</h1>
<p class="lead">{esc(met['intro'])}</p>
<ul class="deps">{lis}</ul>"""
        rel = f"{met['slug']}/index.html"
        write(os.path.join(out, rel), page(f"Marchés publics {met['nom_court']} par département",
                                           met["intro"], body, 1, rel))
        urls.append(rel)
        index_blocks.append(
            f"<li><a href=\"{met['slug']}/index.html\">{esc(met['nom'])}</a> "
            f"<span>{len(dep_links)} départements, {sum(x[4] for x in dep_links)} échéances à venir</span></li>")

    body = f"""<h1>{esc(SITE['name'])}</h1>
<p class="lead">{esc(SITE['baseline'])}</p>
<p>Choisissez un métier, puis un département. Chaque page liste les marchés publics attribués, leurs titulaires et ceux qui arrivent à échéance dans les 18 mois.</p>
<ul class="deps">{''.join(index_blocks)}</ul>"""
    write(os.path.join(out, "index.html"), page(SITE["name"] + " : " + SITE["baseline"], SITE["baseline"], body, 0, "index.html"))

    methode = """<h1>Méthode et limites</h1>
<p>Les données viennent des « données essentielles de la commande publique » que les acheteurs publics doivent publier, consolidées chaque jour sur data.gouv.fr sous Licence Ouverte 2.0.</p>
<h2>Comment l'échéance est estimée</h2>
<p>Échéance estimée = date de notification + durée en mois publiée par l'acheteur. Les reconductions, avenants et résiliations ne sont pas toujours publiés : l'échéance réelle peut être différente.</p>
<h2>Ce que ces données ne garantissent pas</h2>
<ul><li>Tous les acheteurs ne publient pas leurs données : des marchés manquent.</li>
<li>Montants et durées sont saisis par les acheteurs et contiennent des erreurs.</li>
<li>Rien n'indique qu'un marché sera effectivement relancé, ni à quelle date.</li></ul>
<h2>Classement par métier</h2>
<p>Les marchés sont classés d'après leur code CPV principal. Le département est celui de l'acheteur.</p>
<h2>Rectification</h2>
<p>Une information vous concernant est inexacte ? Écrivez à l'adresse indiquée dans les mentions légales : elle sera corrigée ou retirée.</p>"""
    write(os.path.join(out, "methode.html"), page("Méthode et limites des données", "D'où viennent les données et ce qu'elles ne garantissent pas.", methode, 0, "methode.html"))

    ed = SITE["editeur"]
    mentions = f"""<h1>Mentions légales</h1>
<p>Éditeur : {esc(ed['nom'])}, {esc(ed['statut'])}, SIRET {esc(ed['siret'])}, {esc(ed['adresse'])}. Contact : {esc(ed['contact'])}.</p>
<p>Hébergeur : GitHub Inc., 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis.</p>
<p>Ce site ne dépose aucun cookie et ne collecte aucune donnée sur ses visiteurs.</p>
<p>Données : DECP consolidées, data.gouv.fr, Licence Ouverte 2.0. Les noms d'entreprises titulaires sont des données publiques publiées par les acheteurs.</p>"""
    write(os.path.join(out, "mentions-legales.html"), page("Mentions légales", "Mentions légales du site.", mentions, 0, "mentions-legales.html"))

    base = SITE["url"].rstrip("/")
    sm = "".join(f"<url><loc>{esc(base + '/' + u)}</loc></url>" for u in urls)
    write(os.path.join(out, "sitemap.xml"), f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
    write(os.path.join(out, "robots.txt"), f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
    with open(os.path.join(os.path.dirname(__file__), "style.css"), encoding="utf-8") as f:
        write(os.path.join(out, "style.css"), f.read())
    report["site"] = stats_global
    write(args.report, json.dumps(report, ensure_ascii=False, indent=1))
    print("Pages département :", stats_global["pages"], "| échéances :", stats_global["echeances"])


if __name__ == "__main__":
    main()
