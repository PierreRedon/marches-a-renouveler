#!/usr/bin/env python3
"""Envoie par e-mail l'adresse de la page de téléchargement aux nouveaux acheteurs du pack.

Lancé toutes les heures par .github/workflows/acces.yml. Le script lit dans Stripe les paiements
terminés des dernières 48 heures pour les liens de paiement du site, et écrit à chaque acheteur
qui n'a pas encore reçu son message. Il ne fait rien tant que les secrets ne sont pas en place.

Secrets attendus (variables d'environnement) :
  STRIPE_KEY          clé Stripe restreinte, lecture seule sur « Checkout Sessions » et « Payment Links »
  GMAIL_APP_PASSWORD  mot de passe d'application Gmail de l'adresse de contact
  LIST_TOKEN          le code de la page d'accès (déjà utilisé pour générer le site)
Aucune adresse d'acheteur n'est écrite dans les journaux ni dans le fichier d'état.
"""
import argparse
import hashlib
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from config import SITE

API = os.environ.get("STRIPE_API", "https://api.stripe.com")
RECUL = 48 * 3600        # fenêtre relue à chaque passage : rattrape un passage manqué
RECUL_SANS_ETAT = 3 * 3600  # sans fichier d'état, on ne remonte pas plus loin (pas de renvoi aux anciens acheteurs)
GARDE = 30 * 86400       # durée de conservation des empreintes dans le fichier d'état


def stripe_get(path, key, params=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.load(e).get("error", {}).get("message", "")
        except Exception:
            pass
        raise SystemExit(f"Stripe a refusé {path} (HTTP {e.code}) : {detail}")


def stripe_list(path, key, params):
    params = dict(params, limit=100)
    while True:
        page = stripe_get(path, key, params)
        yield from page.get("data", [])
        if not page.get("has_more") or not page.get("data"):
            return
        params["starting_after"] = page["data"][-1]["id"]


def message(dest, url, contact):
    m = EmailMessage()
    m["From"] = formataddr((SITE["name"], contact))
    m["To"] = dest
    m["Reply-To"] = contact
    m["Subject"] = f"Vos listes {SITE['name']} : votre page de téléchargement"
    m.set_content(f"""Bonjour,

Merci pour votre achat du pack {SITE['name']}.

Voici votre page de téléchargement, avec un fichier par métier à ouvrir dans Excel :
{url}

Les fichiers y sont mis à jour chaque jour, toujours à cette adresse : vous pouvez y revenir quand vous voulez, sans rien repayer. Conservez ce message pour la retrouver.

Cette adresse vous est réservée, merci de ne pas la diffuser.

Une question ? Répondez simplement à ce message.

{SITE['editeur']['nom']}
{SITE['url']}
""")
    return m


def connexion(host, contact, mdp):
    """Ouvre la session Gmail : port 465 (SSL), puis 587 (STARTTLS) si le serveur coupe la connexion."""
    derniere = None
    for port in (465, 587):
        try:
            if port == 465:
                smtp = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                smtp = smtplib.SMTP(host, port, timeout=30)
                smtp.starttls()
            smtp.login(contact, mdp)
            return smtp
        except smtplib.SMTPAuthenticationError as e:
            raise SystemExit(f"Gmail a refusé la connexion (code {e.smtp_code}) : le mot de passe d'application est "
                             "faux, révoqué, ou n'appartient pas au compte de l'adresse de contact.")
        except (smtplib.SMTPServerDisconnected, OSError) as e:
            derniere = e
            print(f"Port {port} : connexion coupée ({type(e).__name__})")
    # dernier recours et diagnostic : identification « LOGIN » pas à pas (aucun secret n'est affiché)
    try:
        smtp = smtplib.SMTP_SSL(host, 465, timeout=30)
        smtp.ehlo()
        print("Méthodes proposées par le serveur :", smtp.esmtp_features.get("auth", "aucune"),
              "| nom annoncé :", "." in smtp.local_hostname and "nom complet" or smtp.local_hostname)
        smtp.user, smtp.password = contact, mdp
        code, rep = smtp.auth("LOGIN", smtp.auth_login, initial_response_ok=False)
        print("Identification LOGIN acceptée :", code)
        return smtp
    except smtplib.SMTPAuthenticationError as e:
        raise SystemExit(f"Gmail a refusé la connexion (code {e.smtp_code}) : le mot de passe d'application est "
                         "faux, révoqué, ou n'appartient pas au compte de l'adresse de contact.")
    except (smtplib.SMTPException, OSError) as e:
        raise SystemExit(f"Gmail a coupé la connexion à chaque essai ({type(e).__name__} : {e}).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--etat", default="etat_acces.json", help="fichier d'état (empreintes des paiements déjà traités)")
    ap.add_argument("--essai", action="store_true",
                    help="n'écrit à aucun acheteur : envoie un message d'essai à l'adresse de contact")
    args = ap.parse_args()

    key = os.environ.get("STRIPE_KEY", "").strip()
    mdp_brut = os.environ.get("GMAIL_APP_PASSWORD", "")
    mdp = re.sub(r"[^A-Za-z0-9]", "", mdp_brut)  # un mot de passe d'application : 16 lettres, espaces sans importance
    token = re.sub(r"[^A-Za-z0-9]", "", os.environ.get("LIST_TOKEN", ""))
    manquants = [n for n, v in (("STRIPE_KEY", key), ("GMAIL_APP_PASSWORD", mdp)) if not v]
    if manquants:
        print("Envoi inactif : secret(s) absent(s) :", ", ".join(manquants))
        return
    if len(mdp) != 16:
        raise SystemExit(f"GMAIL_APP_PASSWORD ne ressemble pas à un mot de passe d'application Gmail : "
                         f"{len(mdp)} lettres ou chiffres au lieu de 16 ({len(mdp_brut.strip())} caractères saisis). "
                         "Ce n'est pas le mot de passe habituel du compte : il se crée sur myaccount.google.com/apppasswords.")
    if len(token) < 24:
        raise SystemExit("LIST_TOKEN absent ou trop court : impossible de construire l'adresse de téléchargement.")
    contact = SITE["editeur"]["contact"]
    url = f"{SITE['url'].rstrip('/')}/acces/{token}/"

    liens_site = {u for u in (SITE.get("lien_paiement"), SITE.get("lien_paiement_apres")) if u}
    plinks = {p["id"] for p in stripe_list("/v1/payment_links", key, {}) if p.get("url") in liens_site}
    if not plinks:
        raise SystemExit("Aucun lien de paiement Stripe ne correspond à ceux de scripts/config.py.")

    etat_connu = os.path.exists(args.etat)
    faits = {}
    if etat_connu:
        with open(args.etat, encoding="utf-8") as f:
            faits = json.load(f).get("faits", {})
    now = int(time.time())
    depuis = now - (RECUL if etat_connu else RECUL_SANS_ETAT)

    a_faire = []
    for s in stripe_list("/v1/checkout/sessions", key, {"status": "complete", "created[gte]": depuis}):
        if s.get("payment_link") not in plinks or s.get("payment_status") != "paid":
            continue
        h = hashlib.sha256(s["id"].encode()).hexdigest()
        dest = ((s.get("customer_details") or {}).get("email") or s.get("customer_email") or "").strip()
        if h not in faits and dest:
            a_faire.append((h, dest))
    print(f"Paiements à traiter : {len(a_faire)} (liens suivis : {len(plinks)})")

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    echecs = 0
    if args.essai or a_faire:
        with connexion(smtp_host, contact, mdp) as smtp:
            if args.essai:
                m = message(contact, url, contact)
                m.replace_header("Subject", "[Essai] " + m["Subject"])
                smtp.send_message(m)
                print("Message d'essai envoyé à l'adresse de contact. Aucun acheteur n'a été contacté.")
            else:
                for h, dest in a_faire:
                    try:
                        m = message(dest, url, contact)
                        smtp.send_message(m, to_addrs=[dest, contact])  # copie cachée à l'éditeur
                        faits[h] = now
                    except smtplib.SMTPException as e:
                        echecs += 1
                        print("Échec d'un envoi :", type(e).__name__)
                print(f"Messages envoyés : {len(a_faire) - echecs}, échecs : {echecs}")

    faits = {h: t for h, t in faits.items() if now - t < GARDE}
    with open(args.etat, "w", encoding="utf-8") as f:
        json.dump({"faits": faits}, f)
    if echecs:
        sys.exit(1)


if __name__ == "__main__":
    main()
