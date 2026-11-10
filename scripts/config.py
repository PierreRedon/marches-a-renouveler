"""Réglages du site. Tout ce qui change sans toucher au code est ici."""

SITE = {
    "name": "Marchés à renouveler",
    "baseline": "Les marchés publics attribués et ceux qui arrivent à échéance, par métier et par département.",
    "url": "https://www.marches-a-renouveler.fr",
    "lignes_gratuites": 5,
    "prix": "19 € HT",
    # slug du métier -> lien de paiement. Vide tant que le compte de paiement n'existe pas.
    "liens_paiement": {},
    "editeur": {
        "nom": "Pierre Redon",
        "statut": "entrepreneur individuel",
        "siret": "981 175 714 00030",
        "adresse": "46 route de Vouillé, 86170 Cissé",
        "contact": "pierreredondev@gmail.com",
    },
}

METIERS = [
    {"slug": "espaces-verts", "nom": "Espaces verts et élagage", "nom_court": "espaces verts", "cpv": ["7731", "7734"],
     "intro": "Entretien et création d'espaces verts, élagage : marchés attribués et échéances à venir, département par département."},
    {"slug": "nettoyage", "nom": "Nettoyage de locaux", "nom_court": "nettoyage de locaux", "cpv": ["9091"],
     "intro": "Nettoyage de bâtiments et de locaux : marchés attribués et échéances à venir, département par département."},
    {"slug": "voirie", "nom": "Voirie et réseaux", "nom_court": "voirie", "cpv": ["45233", "45232"],
     "intro": "Travaux de voirie et de réseaux : marchés attribués et échéances à venir, département par département."},
    {"slug": "peinture-revetements", "nom": "Peinture et revêtements", "nom_court": "peinture et revêtements", "cpv": ["4544", "4543"],
     "intro": "Peinture, sols et revêtements : marchés attribués et échéances à venir, département par département."},
    {"slug": "electricite", "nom": "Électricité", "nom_court": "électricité", "cpv": ["4531"],
     "intro": "Travaux d'installation électrique : marchés attribués et échéances à venir, département par département."},
    {"slug": "plomberie-chauffage", "nom": "Plomberie et chauffage", "nom_court": "plomberie et chauffage", "cpv": ["4533"],
     "intro": "Plomberie, chauffage et ventilation : marchés attribués et échéances à venir, département par département."},
    {"slug": "couverture-charpente", "nom": "Couverture et charpente", "nom_court": "couverture et charpente", "cpv": ["4526"],
     "intro": "Couverture, charpente et étanchéité : marchés attribués et échéances à venir, département par département."},
    {"slug": "menuiserie", "nom": "Menuiserie", "nom_court": "menuiserie", "cpv": ["4542"],
     "intro": "Menuiseries intérieures et extérieures : marchés attribués et échéances à venir, département par département."},
]

DEPARTEMENTS = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence", "05": "Hautes-Alpes",
    "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes", "09": "Ariège", "10": "Aube", "11": "Aude",
    "12": "Aveyron", "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs", "26": "Drôme",
    "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne", "32": "Gers",
    "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire",
    "38": "Isère", "39": "Jura", "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère",
    "49": "Maine-et-Loire", "50": "Manche", "51": "Marne", "52": "Haute-Marne", "53": "Mayenne",
    "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan", "57": "Moselle", "58": "Nièvre", "59": "Nord",
    "60": "Oise", "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales", "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône",
    "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie",
    "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne", "78": "Yvelines", "79": "Deux-Sèvres",
    "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne", "83": "Var", "84": "Vaucluse", "85": "Vendée",
    "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne", "90": "Territoire de Belfort",
    "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis", "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane", "974": "La Réunion", "976": "Mayotte",
}
