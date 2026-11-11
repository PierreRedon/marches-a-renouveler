#!/usr/bin/env python3
"""Génère le site statique « Marchés à renouveler » à partir des DECP consolidées.

Source : Données essentielles de la commande publique consolidées (data.gouv.fr),
Licence Ouverte 2.0. Le script inspecte le schéma du fichier, écrit un rapport
(data/schema_report.json) puis produit une page par métier et par département,
une page par département et les pages d'accueil.
"""
import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import shutil
import sys

import duckdb

from config import DEPARTEMENTS, METIERS, SITE

CANDIDATES = {
    "uid": ["uid"],
    "objet": ["objet"],
    "cpv": ["codeCPV", "cpv", "code_cpv"],
    "montant": ["montant"],
    "montant_anomalie": ["montant_anomalie"],
    "date_notif": ["dateNotification", "date_notification"],
    "duree": ["dureeMois", "duree_mois"],
    "acheteur_nom": ["acheteur_nom", "acheteur.nom"],
    "acheteur_dep": ["acheteur_departement_code", "acheteur_departement"],
    "region": ["acheteur_region_nom"],
    "tit_nom": ["titulaire_nom", "titulaire_denominationSociale", "titulaire.denominationSociale"],
    "tit_cat": ["titulaire_categorie"],
    "actuel": ["donneesActuelles", "donnees_actuelles"],
}
REQUIRED = ["objet", "cpv", "date_notif", "acheteur_nom", "acheteur_dep"]
FUZZY = {
    "acheteur_dep": ["acheteur", "departement", "code"],
    "tit_nom": ["titulaire", "nom"],
    "tit_cat": ["titulaire", "categorie"],
}
MIN_MARCHES = 3  # pas de page pour moins de 3 marchés : trop peu d'information utile
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
TODAY = dt.date.today()
BASE = SITE["url"].rstrip("/")
NAV_PACK = ""  # lien « Le pack » du menu, renseigné seulement si la vente est active
FILTER_JS = ("var v=this.value.toLowerCase();document.querySelectorAll('.dep')"
             ".forEach(function(a){a.hidden=a.dataset.n.indexOf(v)<0})")


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


def nb(n):
    return f"{n:,}".replace(",", " ")


def euro(v):
    if v is None or v <= 0:
        return '<span class="na">non publié</span>'
    return f"{v:,.0f}".replace(",", " ") + " €"


def fr_date(d):
    return d.strftime("%d/%m/%Y") if d else "non publiée"


def fr_month(d):
    return f"{MOIS[d.month - 1]} {d.year}" if d else "non estimée"


def de(n):
    return ("d'" if n[0].lower() in "aeiouyéèêàh" else "de ") + n


def en_dep(nom):
    """Préposition devant un nom de département."""
    special = {"Paris": "à Paris", "Mayotte": "à Mayotte", "La Réunion": "à La Réunion"}
    if nom in special:
        return special[nom]
    pluriel = ("Hauts-", "Hautes-", "Alpes", "Ardennes", "Bouches", "Côtes", "Deux-", "Landes",
               "Pyrénées", "Vosges", "Yvelines")
    masculin = ("Territoire", "Val-", "Bas-", "Haut-", "Calvados", "Cantal", "Cher", "Doubs", "Finistère",
                "Gard", "Gers", "Jura", "Loir-", "Loiret", "Lot", "Morbihan", "Nord", "Pas-", "Puy-",
                "Rhône", "Tarn", "Var", "Vaucluse")
    if nom.startswith(pluriel):
        return "dans les " + nom
    if nom.startswith(masculin):
        return "dans le " + nom
    return "en " + nom


def slugify(s):
    s = s.lower()
    for a, b in (("éèêë", "e"), ("àâä", "a"), ("îï", "i"), ("ôö", "o"), ("ùûü", "u"), ("ç", "c"), ("'’ ", "-")):
        for ch in a:
            s = s.replace(ch, b)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "", s)).strip("-")


def dep_slug(code):
    return f"{code.lower()}-{slugify(DEPARTEMENTS[code])}"


def short(s, n=150):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def titulaires_txt(s, maxi=2):
    names = [t.strip() for t in (s or "").split(" ; ") if t.strip()]
    if not names:
        return "non publié"
    extra = len(names) - maxi
    return ", ".join(names[:maxi]) + (f" et {extra} autre{'s' if extra > 1 else ''}" if extra > 0 else "")


def median(vals):
    vals = sorted(vals)
    return vals[len(vals) // 2] if vals else None


def top(counter, n=5):
    return sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:n]


def pl(n, s="s"):
    return s if n > 1 else ""


def ech(n):
    return f"{nb(n)} échéance{pl(n)}"


def jsonld(obj):
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def breadcrumb_ld(items):
    return jsonld({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": BASE + u} for i, (n, u) in enumerate(items)]})


def crumbs(items):
    parts = [f'<a href="{u}">{esc(n)}</a>' for n, u in items[:-1]] + [f"<span>{esc(items[-1][0])}</span>"]
    return '<nav class="crumb" aria-label="Fil d\'Ariane">' + " <i>›</i> ".join(parts) + "</nav>"


def page(title, description, body, path, extra_head="", robots="index,follow"):
    url = BASE + path
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="website">
<meta property="og:locale" content="fr_FR">
<meta property="og:site_name" content="{esc(SITE['name'])}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(url)}">
<meta name="twitter:card" content="summary">
<meta name="theme-color" content="#10243e">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/style.css">
{extra_head}
</head>
<body>
<a class="skip" href="#contenu">Aller au contenu</a>
<header class="top"><div class="wrap">
<a class="brand" href="/"><svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7"/><path d="M8 21l5-6 4 4 7-9" fill="none" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg><span>Marchés <b>à renouveler</b></span></a>
<nav><a href="/#metiers">Métiers</a><a href="/departements/">Départements</a><a href="/methode/">Méthode</a>{NAV_PACK}</nav>
</div></header>
<main id="contenu">
{body}
</main>
<footer class="foot"><div class="wrap">
<div><strong>{esc(SITE['name'])}</strong><p>Les marchés publics attribués et ceux qui arrivent à échéance, par métier et par département. Données mises à jour chaque jour.</p></div>
<div><p><a href="/methode/">Méthode et limites</a><br><a href="/departements/">Tous les départements</a><br><a href="/mentions-legales/">Mentions légales</a></p></div>
<div><p>Source : données essentielles de la commande publique consolidées, data.gouv.fr, Licence Ouverte 2.0.<br>Dernière mise à jour : {fr_date(TODAY)}. Les échéances sont des estimations.</p></div>
</div></footer>
</body>
</html>
"""


def table(headers, rows, cls=""):
    th = "".join(f'<th scope="col">{esc(h)}</th>' for h in headers)
    trs = "".join("<tr>" + "".join(f'<td data-l="{esc(h)}">{c}</td>' for h, c in zip(headers, r)) + "</tr>" for r in rows)
    return f'<div class="tbl {cls}"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def write(out, path, content):
    rel = path.strip("/")
    if path.endswith((".xml", ".txt", ".html")):
        full = os.path.join(out, rel)
    else:
        full = os.path.join(out, rel, "index.html")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def filter_input():
    return ('<input class="filter" type="search" placeholder="Filtrer : nom ou numéro du département" '
            f'aria-label="Filtrer les départements" oninput="{FILTER_JS}">')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--out", default="public")
    ap.add_argument("--report", default="data/schema_report.json")
    args = ap.parse_args()
    out = args.out

    con = duckdb.connect()
    src = f"read_parquet('{args.parquet}')"
    desc = con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()
    columns = [r[0] for r in desc]
    m = resolve_columns(columns)
    total = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    missing = [k for k in REQUIRED if k not in m]
    report = {
        "genere_le": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lignes": total,
        "colonnes": [{"nom": r[0], "type": r[1]} for r in desc],
        "correspondance": m,
        "colonnes_requises_manquantes": missing,
    }
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    if missing:
        print("Colonnes requises introuvables :", missing, file=sys.stderr)
        sys.exit(2)

    def col(key, cast=None, default="NULL"):
        if key not in m:
            return default
        return f"TRY_CAST({q(m[key])} AS {cast})" if cast else q(m[key])

    montant = col("montant", "DOUBLE")
    if "montant_anomalie" in m:
        montant = f"CASE WHEN {col('montant_anomalie', 'BOOLEAN')} IS TRUE THEN NULL ELSE {montant} END"
    where_actuel = f"WHERE {col('actuel', 'BOOLEAN')} IS NOT FALSE" if "actuel" in m else ""
    group_key = col("uid", "VARCHAR") if "uid" in m else (
        f"concat_ws('|', {col('acheteur_nom', 'VARCHAR')}, {col('objet', 'VARCHAR')}, {col('date_notif', 'VARCHAR')})")
    con.execute(f"""
        CREATE TABLE marches AS
        SELECT {group_key} AS cle,
               any_value({col('objet', 'VARCHAR')}) AS objet,
               any_value(replace({col('cpv', 'VARCHAR')}, '-', '')) AS cpv,
               max({montant}) AS montant,
               max({col('date_notif', 'DATE')}) AS date_notif,
               max({col('duree', 'INTEGER')}) AS duree,
               any_value({col('acheteur_nom', 'VARCHAR')}) AS acheteur,
               any_value(upper(trim({col('acheteur_dep', 'VARCHAR')}))) AS dep,
               string_agg(DISTINCT {col('tit_nom', 'VARCHAR')}, ' ; ') AS titulaires,
               any_value({col('tit_cat', 'VARCHAR')}) AS categorie,
               any_value({col('region', 'VARCHAR')}) AS region
        FROM {src} {where_actuel}
        GROUP BY 1
    """)
    con.execute("""
        ALTER TABLE marches ADD COLUMN fin DATE;
        UPDATE marches SET fin = date_notif + to_months(duree)
        WHERE date_notif IS NOT NULL AND duree BETWEEN 1 AND 120;
    """)
    horizon = TODAY + dt.timedelta(days=548)
    token = re.sub(r"[^A-Za-z0-9]", "", os.environ.get("LIST_TOKEN", ""))
    vente_active = len(token) >= 24 and bool(SITE.get("lien_paiement"))
    if vente_active:
        global NAV_PACK
        NAV_PACK = '<a class="navpack" href="/pack/">Le pack</a>'

    # ---------- 1re passe : calcul ----------
    data = {}
    for met in METIERS:
        cond = " OR ".join(f"cpv LIKE '{p}%'" for p in met["cpv"])
        rows = con.execute(f"""
            SELECT dep, objet, acheteur, titulaires, montant, date_notif, duree, fin, categorie, region
            FROM marches WHERE ({cond}) AND dep IS NOT NULL AND objet IS NOT NULL
              AND date_notif >= DATE '2019-01-01' AND date_notif <= current_date
            ORDER BY date_notif DESC
        """).fetchall()
        by_dep = {}
        for r in rows:
            if r[0] in DEPARTEMENTS:
                by_dep.setdefault(r[0], []).append(r)
        for dep, lst in by_dep.items():
            if len(lst) < MIN_MARCHES:
                continue
            a_venir = sorted([r for r in lst if r[7] and TODAY <= r[7] <= horizon], key=lambda r: r[7])
            tit, ach = {}, {}
            for r in lst:
                for t in (r[3] or "").split(" ; "):
                    if t.strip():
                        tit[t.strip()] = tit.get(t.strip(), 0) + 1
                if r[2]:
                    ach[r[2]] = ach.get(r[2], 0) + 1
            cats = [r[8] for r in lst if r[8]]
            regions = [r[9] for r in lst if r[9]]
            data[(met["slug"], dep)] = {
                "lst": lst, "a_venir": a_venir, "tit": tit, "ach": ach,
                "mediane": median([r[4] for r in lst if r[4] and r[4] > 0]),
                "duree_med": median([r[6] for r in lst if r[6] and 1 <= r[6] <= 120]),
                "pme": (round(100 * sum(1 for c in cats if c.upper() == "PME") / len(cats)) if len(cats) >= 5 else None),
                "region": max(set(regions), key=regions.count) if regions else None,
            }
    met_by_slug = {x["slug"]: x for x in METIERS}
    deps_of, mets_of = {}, {}
    for (ms, dep) in data:
        deps_of.setdefault(ms, []).append(dep)
        mets_of.setdefault(dep, []).append(ms)
    urls = []

    # ---------- pages métier × département ----------
    for (ms, dep), d in data.items():
        met = met_by_slug[ms]
        nom = DEPARTEMENTS[dep]
        lieu = en_dep(nom)
        path = f"/{ms}/{dep_slug(dep)}/"
        lst, a_venir = d["lst"], d["a_venir"]
        free = a_venir[:SITE["lignes_gratuites"]]
        reste = len(a_venir) - len(free)
        top_tit, top_ach = top(d["tit"]), top(d["ach"])
        nmet = met["nom_court"]
        na = '<span class="na">non publié</span>'

        kpis = f"""<div class="kpis">
<div><b>{nb(len(lst))}</b><span>marchés publiés depuis 2019</span></div>
<div class="hot"><b>{nb(len(a_venir))}</b><span>échéances dans les 18 mois</span></div>
<div><b>{euro(d['mediane'])}</b><span>montant médian</span></div>
<div><b>{(str(d['duree_med']) + ' mois') if d['duree_med'] else na}</b><span>durée médiane</span></div>
</div>"""
        n_ach = len(d["ach"])
        phrases = [f"{nb(len(lst))} marchés publics {de(nmet)} ont été publiés {lieu} depuis 2019 "
                   f"par {nb(n_ach)} acheteur{pl(n_ach)} public{pl(n_ach)}."]
        if top_ach:
            phrases.append(f"Le plus actif est {top_ach[0][0]}, avec {top_ach[0][1]} marché{pl(top_ach[0][1])}.")
        if d["pme"] is not None:
            phrases.append(f"{d['pme']} % des attributions dont la taille du titulaire est connue sont allées à des PME.")
        if a_venir:
            phrases.append(f"{nb(len(a_venir))} de ces marchés arrivent à échéance d'ici {fr_month(horizon)}, "
                           f"la prochaine en {fr_month(a_venir[0][7])}.")
        intro = " ".join(esc(p) for p in phrases)

        if free:
            t1 = table(["Objet du marché", "Acheteur", "Titulaire actuel", "Montant", "Durée", "Échéance estimée"],
                       [[esc(short(r[1])), esc(r[2]), esc(titulaires_txt(r[3])), euro(r[4]), f"{r[6]} mois",
                         f'<span class="pill">{fr_month(r[7])}</span>'] for r in free], "ech")
        else:
            t1 = '<p class="empty">Aucune échéance estimée dans les 18 mois d\'après les données publiées.</p>'
        pay = SITE.get("lien_paiement") if vente_active else None
        more = ""
        if reste > 0:
            if pay:
                cta = f'<a class="btn" href="{esc(pay)}" rel="nofollow">Obtenir toutes les listes · {esc(SITE["prix"])}</a>'
            else:
                cta = '<span class="soon">Listes complètes bientôt disponibles</span>'
            verbe = "arrivent" if reste > 1 else "arrive"
            more = (f'<aside class="lock"><div><strong>{nb(reste)} autre{pl(reste)} marché{pl(reste)} '
                    f'{esc(de(nmet))} {verbe} à échéance {esc(lieu)}.</strong>'
                    "<p>Le pack donne toutes les échéances de tous les métiers et de tous les départements, "
                    "en fichiers à ouvrir dans Excel : acheteur, titulaire en place, montant, échéance estimée. "
                    "Paiement unique, fichiers mis à jour chaque jour."
                    + (' <a href="/pack/">Voir ce que contient le pack</a>' if pay else "") + "</p></div>"
                    f"{cta}</aside>")
        t2 = table(["Objet du marché", "Acheteur", "Titulaire", "Montant", "Notifié le"],
                   [[esc(short(r[1])), esc(r[2]), esc(titulaires_txt(r[3])), euro(r[4]), fr_date(r[5])] for r in lst[:20]])
        li_tit = "".join(f"<li><span>{esc(n)}</span><b>{c}</b></li>" for n, c in top_tit)
        li_ach = "".join(f"<li><span>{esc(n)}</span><b>{c}</b></li>" for n, c in top_ach)
        bloc_tit = f'<ol class="rank">{li_tit}</ol>' if li_tit else '<p class="empty">Titulaires non publiés.</p>'
        bloc_ach = f'<ol class="rank">{li_ach}</ol>' if li_ach else '<p class="empty">Acheteurs non publiés.</p>'

        faq = []
        if a_venir:
            ex = " ; ".join(f"« {short(r[1], 70)} » ({r[2]}, {fr_month(r[7])})" for r in a_venir[:3])
            faq.append((f"Quels marchés publics {de(nmet)} arrivent à échéance {lieu} ?",
                        f"{nb(len(a_venir))} marchés ont une échéance estimée dans les 18 mois. Les plus proches : {ex}."))
        if top_tit:
            faq.append((f"Quelles entreprises remportent les marchés {de(nmet)} {lieu} ?",
                        "Depuis 2019, les titulaires les plus fréquents sont "
                        + ", ".join(f"{n} ({c})" for n, c in top_tit[:4]) + "."))
        if top_ach:
            faq.append((f"Quels acheteurs publics passent le plus de marchés {de(nmet)} {lieu} ?",
                        "Les acheteurs les plus actifs sont " + ", ".join(f"{n} ({c})" for n, c in top_ach[:4]) + "."))
        faq.append(("Comment l'échéance est-elle estimée ?",
                    "Elle correspond à la date de notification du marché augmentée de la durée publiée par l'acheteur. "
                    "Les reconductions et les avenants ne sont pas toujours publiés : la date réelle peut différer."))
        faq_html = "".join(f"<details><summary>{esc(qq)}</summary><p>{esc(aa)}</p></details>" for qq, aa in faq)

        autres = sorted(x for x in mets_of[dep] if x != ms)
        chips_m = "".join(f'<a href="/{x}/{dep_slug(dep)}/">{esc(met_by_slug[x]["nom"])}</a>' for x in autres)
        voisins = sorted(x for x in deps_of[ms]
                         if x != dep and d["region"] and data[(ms, x)]["region"] == d["region"])
        chips_d = "".join(f'<a href="/{ms}/{dep_slug(x)}/">{esc(DEPARTEMENTS[x])}</a>' for x in voisins)
        sec_m = f'<section><h2>Autres métiers {esc(lieu)}</h2><div class="chips">{chips_m}</div></section>' if chips_m else ""
        sec_d = (f'<section><h2>{esc(met["nom"])} dans les départements de la région</h2>'
                 f'<div class="chips">{chips_d}</div></section>') if chips_d else ""

        bc = [("Accueil", "/"), (met["nom"], f"/{ms}/"), (nom, path)]
        title = f"Marchés publics {nmet} {lieu} ({dep}) : attributions et échéances"
        descr = (f"{nb(len(lst))} marchés publics {de(nmet)} {lieu} depuis 2019, dont {nb(len(a_venir))} arrivent "
                 "à échéance dans les 18 mois : acheteurs, titulaires, montants.")
        head = breadcrumb_ld(bc) + jsonld({
            "@context": "https://schema.org", "@type": "Dataset",
            "name": f"Marchés publics {de(nmet)} {lieu} : attributions et échéances estimées",
            "description": descr, "url": BASE + path,
            "license": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            "isAccessibleForFree": True, "dateModified": TODAY.isoformat(),
            "temporalCoverage": f"2019-01-01/{TODAY.isoformat()}",
            "spatialCoverage": {"@type": "Place", "name": f"{nom}, France"},
            "creator": {"@type": "Organization", "name": SITE["name"], "url": BASE + "/"},
            "isBasedOn": "https://www.data.gouv.fr/datasets/donnees-essentielles-de-la-commande-publique-consolidees-format-tabulaire/"})
        head += jsonld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": qq, "acceptedAnswer": {"@type": "Answer", "text": aa}} for qq, aa in faq]})
        body = f"""<section class="hero small"><div class="wrap">
{crumbs(bc)}
<h1>Marchés publics {esc(de(nmet))} {esc(lieu)} <small>({esc(dep)})</small></h1>
<p class="lead">{intro}</p>
{kpis}
</div></section>
<div class="wrap">
<section><h2>Marchés {esc(de(nmet))} qui arrivent à échéance {esc(lieu)}</h2>
<p class="hint">Un marché qui se termine est en général remis en concurrence quelques mois avant son échéance.</p>
{t1}
{more}</section>
<section class="two">
<div><h2>Entreprises le plus souvent titulaires</h2>{bloc_tit}</div>
<div><h2>Acheteurs publics les plus actifs</h2>{bloc_ach}</div>
</section>
<section><h2>Derniers marchés {esc(de(nmet))} attribués {esc(lieu)}</h2>
{t2}</section>
<section><h2>Questions fréquentes</h2><div class="faq">{faq_html}</div></section>
{sec_m}
{sec_d}
</div>"""
        write(out, path, page(title, descr, body, path, head))
        urls.append(path)

    # ---------- pages métier ----------
    for met in METIERS:
        ms = met["slug"]
        deps = sorted(deps_of.get(ms, []))
        if not deps:
            continue
        path = f"/{ms}/"
        tot = sum(len(data[(ms, x)]["lst"]) for x in deps)
        n_ech = sum(len(data[(ms, x)]["a_venir"]) for x in deps)
        cards = "".join(
            f'<a class="dep" href="/{ms}/{dep_slug(x)}/" data-n="{esc((DEPARTEMENTS[x] + " " + x).lower())}">'
            f'<i>{esc(x)}</i><span>{esc(DEPARTEMENTS[x])}</span>'
            f'<em>{ech(len(data[(ms, x)]["a_venir"]))}</em></a>' for x in deps)
        bc = [("Accueil", "/"), (met["nom"], path)]
        title = f"Marchés publics {met['nom_court']} : échéances et attributions par département"
        descr = (f"{nb(tot)} marchés publics {de(met['nom_court'])} depuis 2019 dans {len(deps)} départements, "
                 f"dont {nb(n_ech)} arrivent à échéance dans les 18 mois.")
        body = f"""<section class="hero small"><div class="wrap">
{crumbs(bc)}
<h1>Marchés publics {esc(de(met['nom_court']))} par département</h1>
<p class="lead">{esc(met['intro'])}</p>
<div class="kpis"><div><b>{nb(tot)}</b><span>marchés publiés depuis 2019</span></div>
<div class="hot"><b>{nb(n_ech)}</b><span>échéances dans les 18 mois</span></div>
<div><b>{len(deps)}</b><span>départements couverts</span></div></div>
</div></section>
<div class="wrap"><section>
<h2>Choisissez un département</h2>
{filter_input()}
<div class="deps">{cards}</div>
</section></div>"""
        write(out, path, page(title, descr, body, path, breadcrumb_ld(bc)))
        urls.append(path)

    # ---------- pages département ----------
    for dep in sorted(mets_of):
        nom = DEPARTEMENTS[dep]
        lieu = en_dep(nom)
        path = f"/departements/{dep_slug(dep)}/"
        mets = sorted(mets_of[dep], key=lambda x: -len(data[(x, dep)]["a_venir"]))
        n_ech = sum(len(data[(x, dep)]["a_venir"]) for x in mets)
        cards = "".join(
            f'<a class="card" href="/{x}/{dep_slug(dep)}/"><strong>{esc(met_by_slug[x]["nom"])}</strong>'
            f'<span><b>{nb(len(data[(x, dep)]["a_venir"]))}</b> échéance{pl(len(data[(x, dep)]["a_venir"]))} · {nb(len(data[(x, dep)]["lst"]))} marchés</span></a>'
            for x in mets)
        bc = [("Accueil", "/"), ("Départements", "/departements/"), (nom, path)]
        title = f"Marchés publics {lieu} ({dep}) : échéances à venir par métier"
        descr = (f"{nb(n_ech)} marchés publics arrivent à échéance {lieu} dans les 18 mois, "
                 f"répartis sur {len(mets)} métiers suivis.")
        body = f"""<section class="hero small"><div class="wrap">
{crumbs(bc)}
<h1>Marchés publics {esc(lieu)} <small>({esc(dep)})</small></h1>
<p class="lead">{esc(descr)}</p></div></section>
<div class="wrap"><section><h2>Par métier</h2><div class="cards">{cards}</div></section></div>"""
        write(out, path, page(title, descr, body, path, breadcrumb_ld(bc)))
        urls.append(path)

    path = "/departements/"
    cards = "".join(
        f'<a class="dep" href="/departements/{dep_slug(x)}/" data-n="{esc((DEPARTEMENTS[x] + " " + x).lower())}">'
        f'<i>{esc(x)}</i><span>{esc(DEPARTEMENTS[x])}</span>'
        f'<em>{ech(sum(len(data[(y, x)]["a_venir"]) for y in mets_of[x]))}</em></a>' for x in sorted(mets_of))
    bc = [("Accueil", "/"), ("Départements", path)]
    body = f"""<section class="hero small"><div class="wrap">{crumbs(bc)}
<h1>Marchés publics par département</h1>
<p class="lead">Les échéances à venir dans chaque département, tous métiers suivis confondus.</p></div></section>
<div class="wrap"><section>
{filter_input()}
<div class="deps">{cards}</div></section></div>"""
    write(out, path, page("Marchés publics par département : échéances à venir",
                          "Les marchés publics qui arrivent à échéance dans chaque département français, par métier.",
                          body, path, breadcrumb_ld(bc)))
    urls.append(path)

    # ---------- accueil ----------
    tot_ech = sum(len(d["a_venir"]) for d in data.values())
    tot_marches = sum(len(d["lst"]) for d in data.values())
    cards = ""
    for met in METIERS:
        ms = met["slug"]
        if ms not in deps_of:
            continue
        e = sum(len(data[(ms, x)]["a_venir"]) for x in deps_of[ms])
        cards += (f'<a class="card" href="/{ms}/"><strong>{esc(met["nom"])}</strong>'
                  f'<span><b>{nb(e)}</b> échéance{pl(e)} · {len(deps_of[ms])} départements</span></a>')
    offre = btn_pack = ""
    if vente_active:
        btn_pack = f' <a class="btn ghost" href="/pack/">Le pack complet · {esc(SITE["prix"])}</a>'
        offre = ('<section id="pack"><h2>Toutes les échéances dans des fichiers Excel</h2>'
                 f'<aside class="lock"><div><strong>Les pages du site montrent les {SITE["lignes_gratuites"]} prochaines '
                 f'échéances de chaque métier et département. Le pack contient les {nb(tot_ech)}.</strong>'
                 "<p>Un fichier par métier, tous départements, à ouvrir dans Excel : acheteur, titulaire en place, "
                 "montant, échéance estimée. Paiement unique, sans abonnement, fichiers mis à jour chaque jour. "
                 '<a href="/pack/">Voir ce que contient le pack</a></p></div>'
                 f'<a class="btn" href="{esc(SITE["lien_paiement"])}" rel="nofollow">Obtenir toutes les listes · '
                 f'{esc(SITE["prix"])}</a></aside></section>')
    head = jsonld({"@context": "https://schema.org", "@type": "WebSite", "name": SITE["name"], "url": BASE + "/",
                   "description": SITE["baseline"], "inLanguage": "fr-FR"})
    body = f"""<section class="hero"><div class="wrap">
<p class="eyebrow">Données publiques · mises à jour chaque jour</p>
<h1>Repérez les marchés publics qui vont être remis en concurrence</h1>
<p class="lead">Chaque marché public a une date de début et une durée. Nous calculons quand il se termine, pour que vous sachiez à l'avance quels contrats se libèrent près de chez vous, chez quel acheteur et qui les détient aujourd'hui.</p>
<div class="kpis"><div class="hot"><b>{nb(tot_ech)}</b><span>échéances dans les 18 mois</span></div>
<div><b>{nb(tot_marches)}</b><span>marchés analysés depuis 2019</span></div>
<div><b>{len(deps_of)}</b><span>métiers suivis</span></div>
<div><b>{len(mets_of)}</b><span>départements</span></div></div>
<p class="actions"><a class="btn" href="#metiers">Choisir mon métier</a> <a class="btn ghost" href="/departements/">Voir par département</a>{btn_pack}</p>
</div></section>
<div class="wrap">
<section id="metiers"><h2>Choisissez votre métier</h2><div class="cards">{cards}</div></section>
<section><h2>Comment ça marche</h2><div class="steps">
<div><i>1</i><strong>Les acheteurs publient</strong><p>Mairies, départements, hôpitaux : chaque marché attribué est publié avec son titulaire, son montant et sa durée.</p></div>
<div><i>2</i><strong>Nous calculons l'échéance</strong><p>Date de notification plus durée : vous voyez quels marchés se terminent dans les 18 mois.</p></div>
<div><i>3</i><strong>Vous prenez de l'avance</strong><p>Vous connaissez l'acheteur, le titulaire en place et le montant avant la publication de la consultation.</p></div>
</div></section>
{offre}
<section class="note"><h2>Ce que ces données ne disent pas</h2><p>Les échéances sont des estimations, et tous les acheteurs ne publient pas leurs données. Rien ne garantit qu'un marché sera relancé à la date indiquée. Le détail est dans <a href="/methode/">la méthode</a>.</p></section>
</div>"""
    write(out, "/", page(f"{SITE['name']} : les marchés publics qui arrivent à échéance, par métier et département",
                         "Repérez à l'avance les marchés publics qui vont être remis en concurrence : acheteur, "
                         "titulaire en place, montant et échéance estimée, par métier et par département.",
                         body, "/", head))
    urls.insert(0, "/")

    # ---------- page du pack ----------
    if vente_active:
        path = "/pack/"
        prix, pay, gratuit = SITE["prix"], SITE["lien_paiement"], SITE["lignes_gratuites"]
        n_met, n_dep = len(deps_of), len(mets_of)
        cta = f'<a class="btn" href="{esc(pay)}" rel="nofollow">Obtenir toutes les listes · {esc(prix)}</a>'
        atouts = [
            ("Toutes les échéances", f"{nb(tot_ech)} marchés qui se terminent dans les 18 mois, au lieu des "
                                     f"{gratuit} premiers affichés sur chaque page du site."),
            ("Tous les métiers", f"Un fichier pour chacun des {n_met} métiers suivis, sans supplément : "
                                 "vous n'achetez pas métier par métier."),
            ("Tous les départements", f"Chaque fichier couvre les {n_dep} départements : filtrez le vôtre "
                                      "ou regardez les départements voisins."),
            ("Un seul paiement", f"{prix}, une fois. Pas d'abonnement, pas de renouvellement automatique."),
            ("Mis à jour chaque jour", "Les fichiers sont régénérés chaque jour à partir des données publiques. "
                                       "Vous retéléchargez la dernière version quand vous voulez, depuis la même page."),
            ("Prêt pour Excel", "Des fichiers à ouvrir directement dans Excel, à trier et filtrer par échéance, "
                                "acheteur, montant ou titulaire. Aucun logiciel, aucun compte à créer."),
        ]
        atouts_html = "".join(f"<div><i>✓</i><strong>{esc(t)}</strong><p>{esc(x)}</p></div>" for t, x in atouts)
        compare = table(["", "Sur le site, gratuitement", "Avec le pack"], [
            ["Échéances visibles", f"Les {gratuit} prochaines par métier et département", f"Les {nb(tot_ech)}, sans limite"],
            ["Métiers", f"{n_met}, page par page", f"{n_met}, un fichier complet par métier"],
            ["Départements", f"{n_dep}, page par page", f"{n_dep} réunis dans chaque fichier"],
            ["Tri et filtres", "Non", "Oui, dans Excel"],
            ["Mise à jour", "Chaque jour", "Chaque jour, fichiers à retélécharger librement"],
            ["Prix", "Gratuit", f"{esc(prix)}, paiement unique"]])
        colonnes = [("Département", "Numéro et nom du département de l'acheteur."),
                    ("Objet du marché", "L'intitulé publié par l'acheteur."),
                    ("Acheteur", "La collectivité ou l'établissement public qui a passé le marché."),
                    ("Titulaire(s)", "L'entreprise ou les entreprises en place aujourd'hui."),
                    ("Montant", "Le montant publié, en euros, quand il est connu et cohérent."),
                    ("Date de notification et durée", "Le point de départ du marché et sa durée en mois."),
                    ("Échéance estimée", "La date à laquelle le marché devrait se terminer.")]
        col_html = "".join(f"<li><span><b>{esc(c)}</b> : {esc(x)}</span></li>" for c, x in colonnes)
        faq = [
            ("Est-ce un abonnement ?", f"Non. Vous payez {prix} une seule fois, sans renouvellement automatique."),
            ("Que contient le pack exactement ?",
             f"Un fichier par métier ({n_met} métiers), couvrant {n_dep} départements, avec toutes les échéances "
             f"estimées dans les 18 mois : {nb(tot_ech)} marchés à ce jour. Pour chaque marché : objet, acheteur, "
             "titulaire en place, montant, date de notification, durée et échéance estimée."),
            ("Les fichiers sont-ils mis à jour ?",
             "Oui, chaque jour. Votre page de téléchargement reste la même : vous y retrouvez la dernière version."),
            ("Comment ouvrir les fichiers ?",
             "Ce sont des fichiers CSV prévus pour Excel : un double-clic suffit. Ils s'ouvrent aussi dans "
             "LibreOffice et Google Sheets."),
            ("À quoi servent ces listes ?",
             "À savoir à l'avance quels marchés publics de votre métier se terminent, chez quel acheteur et avec quel "
             "titulaire, pour vous préparer avant la publication de la nouvelle consultation."),
            ("Les échéances sont-elles certaines ?",
             "Non, ce sont des estimations : date de notification plus durée publiée. Les reconductions et avenants "
             "ne sont pas toujours publiés, et tous les acheteurs ne publient pas leurs données."),
        ]
        faq_html = "".join(f"<details><summary>{esc(qq)}</summary><p>{esc(aa)}</p></details>" for qq, aa in faq)
        bc = [("Accueil", "/"), ("Le pack complet", path)]
        descr = (f"Toutes les échéances de marchés publics en fichiers Excel : {nb(tot_ech)} marchés, {n_met} métiers, "
                 f"{n_dep} départements. Paiement unique de {prix}, sans abonnement, mis à jour chaque jour.")
        head = breadcrumb_ld(bc) + jsonld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": qq, "acceptedAnswer": {"@type": "Answer", "text": aa}} for qq, aa in faq]})
        body = f"""<section class="hero small"><div class="wrap">
{crumbs(bc)}
<p class="eyebrow">Paiement unique · sans abonnement</p>
<h1>Toutes les échéances de marchés publics, dans des fichiers Excel</h1>
<p class="lead">Le site montre gratuitement les {gratuit} prochaines échéances de chaque page. Le pack vous donne la liste complète, pour tous les métiers et tous les départements, en un seul paiement de {esc(prix)}.</p>
<div class="kpis"><div class="hot"><b>{nb(tot_ech)}</b><span>échéances dans les 18 mois</span></div>
<div><b>{n_met}</b><span>métiers, un fichier chacun</span></div>
<div><b>{n_dep}</b><span>départements</span></div>
<div><b>{esc(prix)}</b><span>une seule fois</span></div></div>
<p class="actions">{cta}</p>
</div></section>
<div class="wrap">
<section><h2>Ce que vous obtenez</h2><div class="steps">{atouts_html}</div></section>
<section><h2>Le site gratuit et le pack, côte à côte</h2>{compare}</section>
<section><h2>Ce qu'il y a dans chaque fichier</h2>
<p class="hint">Une ligne par marché qui arrive à échéance.</p><ol class="rank">{col_html}</ol></section>
<section><aside class="lock"><div><strong>{nb(tot_ech)} échéances, {n_met} métiers, {n_dep} départements.</strong>
<p>Un seul paiement, sans abonnement. Fichiers mis à jour chaque jour.</p></div>{cta}</aside></section>
<section><h2>Questions fréquentes</h2><div class="faq">{faq_html}</div></section>
<section class="note"><h2>À savoir avant d'acheter</h2><p>Les échéances sont des estimations et rien ne garantit qu'un marché sera relancé à la date indiquée. Le détail est dans <a href="/methode/">la méthode</a>. Vous pouvez juger de la qualité des données sur les pages gratuites du site avant d'acheter.</p></section>
</div>"""
        write(out, path, page("Pack complet : toutes les échéances de marchés publics en fichiers Excel | " + SITE["name"],
                              descr, body, path, head))
        urls.append(path)

    # ---------- pages fixes ----------
    bc = [("Accueil", "/"), ("Méthode et limites", "/methode/")]
    methode = f"""<section class="hero small"><div class="wrap">{crumbs(bc)}<h1>Méthode et limites</h1>
<p class="lead">D'où viennent les données, comment l'échéance est calculée et ce que le site ne garantit pas.</p></div></section>
<div class="wrap prose">
<h2>Source</h2><p>Les « données essentielles de la commande publique » sont publiées par les acheteurs publics et consolidées chaque jour sur <a href="https://www.data.gouv.fr/datasets/donnees-essentielles-de-la-commande-publique-consolidees-format-tabulaire/" rel="noopener">data.gouv.fr</a> sous Licence Ouverte 2.0. Le site est régénéré chaque matin.</p>
<h2>Calcul de l'échéance</h2><p>Échéance estimée = date de notification + durée en mois publiée par l'acheteur. Seules les durées de 1 à 120 mois sont retenues.</p>
<h2>Limites</h2><ul>
<li>Tous les acheteurs ne publient pas leurs données : des marchés manquent.</li>
<li>Reconductions, avenants et résiliations ne sont pas toujours publiés : l'échéance réelle peut différer.</li>
<li>Les montants signalés comme anormaux dans la source ne sont pas affichés.</li>
<li>Rien n'indique qu'un marché sera effectivement relancé, ni à quelle date.</li></ul>
<h2>Classement</h2><p>Les marchés sont classés par métier d'après leur code CPV principal, et par département d'après celui de l'acheteur. Les pages comptant moins de {MIN_MARCHES} marchés ne sont pas publiées.</p>
<h2>Rectification</h2><p>Une information vous concernant est inexacte ? Écrivez à l'adresse indiquée dans les <a href="/mentions-legales/">mentions légales</a> : elle sera corrigée ou retirée.</p></div>"""
    write(out, "/methode/", page("Méthode et limites des données | " + SITE["name"],
                                 "D'où viennent les données sur les marchés publics, comment l'échéance est estimée "
                                 "et ce que le site ne garantit pas.", methode, "/methode/", breadcrumb_ld(bc)))
    urls.append("/methode/")

    ed = SITE["editeur"]
    mentions = f"""<section class="hero small"><div class="wrap"><h1>Mentions légales</h1></div></section><div class="wrap prose">
<p>Éditeur : {esc(ed['nom'])}, {esc(ed['statut'])}, SIRET {esc(ed['siret'])}, {esc(ed['adresse'])}. Contact : {esc(ed['contact'])}.</p>
<p>Hébergeur : GitHub Inc., 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis.</p>
<p>Ce site ne dépose aucun cookie et ne collecte aucune donnée sur ses visiteurs.</p>
<p>Données : DECP consolidées, data.gouv.fr, Licence Ouverte 2.0. Les noms d'entreprises titulaires sont des données publiques publiées par les acheteurs.</p></div>"""
    write(out, "/mentions-legales/", page("Mentions légales | " + SITE["name"], "Mentions légales du site.",
                                          mentions, "/mentions-legales/", robots="noindex,follow"))
    nf = ('<section class="hero small"><div class="wrap"><h1>Page introuvable</h1>'
          '<p class="lead">Cette page n\'existe pas ou plus.</p>'
          '<p class="actions"><a class="btn" href="/">Retour à l\'accueil</a></p></div></section>')
    write(out, "/404.html", page("Page introuvable | " + SITE["name"], "Page introuvable.", nf, "/404.html", robots="noindex"))

    # ---------- fichiers payants (chemin secret, hors plan du site) ----------
    if len(token) >= 24:
        base_acces = f"/acces/{token}/"
        liens = ""
        for met in METIERS:
            ms = met["slug"]
            if ms not in deps_of:
                continue
            full = os.path.join(out, base_acces.strip("/"), f"{ms}.csv")
            os.makedirs(os.path.dirname(full), exist_ok=True)
            n = 0
            with open(full, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Département", "Nom du département", "Objet du marché", "Acheteur", "Titulaire(s)",
                            "Montant (€)", "Date de notification", "Durée (mois)", "Échéance estimée"])
                for dep in sorted(deps_of[ms]):
                    for r in data[(ms, dep)]["a_venir"]:
                        w.writerow([dep, DEPARTEMENTS[dep], short(r[1], 400), r[2] or "", r[3] or "",
                                    "" if not r[4] or r[4] <= 0 else round(r[4]), fr_date(r[5]), r[6],
                                    r[7].strftime("%d/%m/%Y")])
                        n += 1
            liens += (f'<a class="card" href="{ms}.csv" download><strong>{esc(met["nom"])}</strong>'
                      f"<span><b>{nb(n)}</b> échéances · fichier CSV pour Excel</span></a>")
        acces = f"""<section class="hero small"><div class="wrap"><h1>Vos listes complètes</h1>
<p class="lead">Merci pour votre achat. Gardez l'adresse de cette page : les fichiers y sont mis à jour chaque jour. Dernière mise à jour : {fr_date(TODAY)}.</p></div></section>
<div class="wrap"><section><h2>Téléchargez un fichier par métier</h2><div class="cards">{liens}</div></section>
<section class="note"><h2>À savoir</h2><p>Chaque fichier contient tous les départements : filtrez la première colonne dans Excel. Les échéances sont des estimations, voir <a href="/methode/">la méthode</a>. Un problème ? Écrivez à {esc(SITE['editeur']['contact'])}.</p></section></div>"""
        write(out, base_acces, page("Vos listes complètes | " + SITE["name"], "Accès aux listes complètes.",
                                    acces, base_acces, robots="noindex,nofollow"))

    sm = "".join(f"<url><loc>{esc(BASE + u)}</loc><lastmod>{TODAY.isoformat()}</lastmod></url>" for u in urls)
    write(out, "/sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>'
          f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
    write(out, "/robots.txt", f"User-agent: *\nAllow: /\nDisallow: /acces/\nSitemap: {BASE}/sitemap.xml\n")
    here = os.path.dirname(os.path.abspath(__file__))
    shutil.copy(os.path.join(here, "style.css"), os.path.join(out, "style.css"))
    shutil.copy(os.path.join(here, "favicon.svg"), os.path.join(out, "favicon.svg"))

    report["site"] = {"pages_metier_departement": len(data), "urls": len(urls), "echeances": tot_ech,
                      "metiers": len(deps_of), "departements": len(mets_of), "vente_active": vente_active}
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(report["site"])


if __name__ == "__main__":
    main()
