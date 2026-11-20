"""Lecture des JSON de scrutins (un fichier par scrutin) et import dans la base."""
import glob
import json
import os
import re
import sqlite3
import sys

from outils import as_list, entier, get

# Clés du bloc "decompteNominatif" (et de "miseAuPoint") -> position normalisée
POSITIONS = {
    "pours": "pour",
    "contres": "contre",
    "abstentions": "abstention",
    "nonVotants": "non_votant",
    "nonVotantsVolontaires": "non_votant_volontaire",
}
# Clés du bloc "miseAuPoint.dysfonctionnement"
POSITIONS_DYSFONCTIONNEMENT = {
    "pour": "pour",
    "contre": "contre",
    "abstentions": "abstention",
    "nonVotants": "non_votant",
    "nonVotantsVolontaires": "non_votant_volontaire",
}


def voters(bloc):
    """Liste des 'votant' d'un bloc (pours, contres...), quelle que soit sa forme."""
    out = []
    for b in as_list(bloc):
        if isinstance(b, dict):
            out.extend(v for v in as_list(b.get("votant")) if isinstance(v, dict))
    return out


def classer(titre):
    """Classe grossièrement le type d'objet du scrutin à partir de son titre."""
    t = (titre or "").lower()
    if "motion de censure" in t:
        return "motion_censure"
    if "motion de rejet" in t or "motion de renvoi" in t:
        return "motion_procedure"
    if "sous-amendement" in t:
        return "sous_amendement"
    if "amendement" in t:
        return "amendement"
    if re.search(r"\bensemble\b", t):
        return "ensemble"
    if "article" in t:
        return "article"
    if "déclaration" in t:
        return "declaration"
    return "autre"


def lire_scrutin(chemin):
    with open(chemin, encoding="utf-8") as f:
        brut = json.load(f)
    s = brut.get("scrutin")
    if not isinstance(s, dict):
        return None

    uid = s.get("uid")
    titre = s.get("titre") or get(s, "objet", "libelle")
    dec = get(s, "syntheseVote", "decompte") or {}

    votes = {}      # depute_id -> dict
    groupes = {}    # groupe_id -> (scrutin_uid, groupe_id, nombre_membres, position_majoritaire)

    for g in as_list(get(s, "ventilationVotes", "organe", "groupes", "groupe")):
        gid = g.get("organeRef")
        vote = g.get("vote") or {}
        if gid in groupes:
            # Même groupe listé plusieurs fois dans un scrutin : on garde la première entrée pour
            # l'effectif et la position majoritaire (les votes des deux entrées sont bien tous lus).
            print(f"[AVERTISSEMENT] {os.path.basename(chemin)} : groupe {gid} présent plusieurs fois "
                  f"dans le scrutin {uid}", file=sys.stderr)
        else:
            groupes[gid] = (uid, gid, entier(g.get("nombreMembresGroupe")), vote.get("positionMajoritaire"))
        nominatif = vote.get("decompteNominatif") or {}
        for cle, pos in POSITIONS.items():
            for v in voters(nominatif.get(cle)):
                votes[v["acteurRef"]] = {
                    "pos": pos, "init": pos, "groupe": gid,
                    "deleg": 1 if str(v.get("parDelegation")).lower() == "true" else 0,
                    "corr": 0,
                }

    # Décompte initial recalculé, comparé au décompte officiel (contrôle de cohérence)
    recalcule = {"pour": 0, "contre": 0, "abstention": 0}
    for v in votes.values():
        if v["init"] in recalcule:
            recalcule[v["init"]] += 1
    ecart = int(
        recalcule["pour"] != entier(dec.get("pour"), -1)
        or recalcule["contre"] != entier(dec.get("contre"), -1)
        or recalcule["abstention"] != entier(dec.get("abstentions"), -1)
    )

    # Mises au point (corrections déclarées après le vote) et dysfonctionnements
    mp = s.get("miseAuPoint") or {}
    n_corr = 0

    def appliquer(correspondance, bloc_source, code):
        nonlocal n_corr
        for cle, pos in correspondance.items():
            for v in voters(bloc_source.get(cle)):
                dep = v["acteurRef"]
                if dep not in votes:
                    votes[dep] = {"pos": pos, "init": "non_votant", "groupe": None,
                                  "deleg": 1 if str(v.get("parDelegation")).lower() == "true" else 0,
                                  "corr": code}
                    n_corr += 1
                elif votes[dep]["pos"] != pos:
                    votes[dep]["pos"] = pos
                    votes[dep]["corr"] = code
                    n_corr += 1

    appliquer(POSITIONS, mp, 1)
    appliquer(POSITIONS_DYSFONCTIONNEMENT, mp.get("dysfonctionnement") or {}, 2)

    scrutin = (
        uid, entier(s.get("numero")), entier(s.get("legislature")), s.get("dateScrutin"), titre,
        classer(titre), get(s, "typeVote", "codeTypeVote"), get(s, "sort", "code"),
        entier(get(s, "syntheseVote", "nombreVotants")), entier(get(s, "syntheseVote", "suffragesExprimes")),
        entier(dec.get("pour")), entier(dec.get("contre")), entier(dec.get("abstentions")),
        entier(dec.get("nonVotants")), n_corr, ecart, os.path.basename(chemin),
    )
    lignes_votes = [
        (uid, dep, v["pos"], v["init"], v["groupe"], v["deleg"], v["corr"])
        for dep, v in votes.items()
    ]
    return scrutin, lignes_votes, list(groupes.values())


def importer_scrutins(con, dossier, legislatures):
    """Ajoute à la base les scrutins absents. legislatures : liste de numéros, ou [0] pour toutes.
    Retourne un dict de compteurs."""
    fichiers = sorted(glob.glob(os.path.join(dossier, "**", "*.json"), recursive=True))
    if not fichiers:
        sys.exit(f"Aucun fichier .json trouvé dans {dossier}")
    deja = {r[0] for r in con.execute("SELECT uid FROM scrutins")}
    c = dict(ajoutes=0, ignores=0, autre_legislature=0, erreurs=0, ecarts=0, corriges=0)
    for i, chemin in enumerate(fichiers, 1):
        try:
            res = lire_scrutin(chemin)
        except Exception as e:
            print(f"[ERREUR] {os.path.basename(chemin)} : {e!r}", file=sys.stderr)
            c["erreurs"] += 1
            continue
        if res is None:
            c["ignores"] += 1
            continue
        scrutin, lignes_votes, groupes = res
        if 0 not in legislatures and scrutin[2] not in legislatures:
            c["autre_legislature"] += 1
            continue
        uid = scrutin[0]
        if uid in deja:
            c["ignores"] += 1
            continue
        try:
            with con:  # transaction : en cas d'erreur, rien n'est écrit pour ce scrutin
                con.execute("INSERT INTO scrutins VALUES (" + ",".join("?" * 17) + ")", scrutin)
                con.executemany("INSERT INTO votes VALUES (?,?,?,?,?,?,?)", lignes_votes)
                con.executemany("INSERT INTO groupe_scrutin VALUES (?,?,?,?)", groupes)
        except sqlite3.IntegrityError as e:
            print(f"[ERREUR] {os.path.basename(chemin)} ({uid}) non inséré : {e}", file=sys.stderr)
            c["erreurs"] += 1
            continue
        deja.add(uid)
        c["ajoutes"] += 1
        c["ecarts"] += scrutin[15]
        c["corriges"] += 1 if scrutin[14] else 0
        if i % 500 == 0:
            print(f"  ... {i}/{len(fichiers)} fichiers de scrutins")
    return c
