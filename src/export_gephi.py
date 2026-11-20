#!/usr/bin/env python3
"""
Exporte le réseau d'affinités entre députés pour Gephi (output/gephi/ par défaut).

Fichiers produits :
  reseau.gexf  graphe complet AVEC les couleurs officielles des groupes (à ouvrir directement dans Gephi)
  noeuds.csv   Id, Label ("Prénom Nom"), Groupe, Groupe_id, Couleur, Groupe_actuel, Departement, Nb_votes
  aretes.csv   Source, Target, Type, Weight, Taux_accord, Votes_identiques, Votes_communs

Règle : deux députés sont reliés si, sur les scrutins retenus, ils ont pris une position
(pour, contre OU abstention) tous les deux au moins --min-commun fois, et si leur taux d'accord
(positions identiques / scrutins communs) est >= --min-accord (0.5 par défaut).
Abstention = position à part entière (--sans-abstentions pour la désactiver).

Le "groupe" d'un nœud est le groupe auquel le député appartenait le plus souvent pendant la période étudiée.

Exemples :
  python src/export_gephi.py
  python src/export_gephi.py --date-min 2024-07-18 --min-accord 0.7
  python src/export_gephi.py --types amendement ensemble --min-commun 50
  python src/export_gephi.py --periode mois --min-commun 10 --out output/gephi_mois
"""
import argparse
import csv
import os
import sqlite3
import sys
from array import array
from collections import Counter, defaultdict
from xml.sax.saxutils import quoteattr

import numpy as np

import config

CODE = {"pour": 1, "contre": 2, "abstention": 3}
GRIS = (150, 150, 150)


def periode_de(date, genre):
    a, m = date[:4], int(date[5:7])
    if genre == "annee":
        return a
    if genre == "trimestre":
        return f"{a}-T{(m - 1) // 3 + 1}"
    return date[:7]  # mois


def hex_vers_rgb(h):
    try:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (AttributeError, ValueError, IndexError):
        return GRIS


# ----------------------------------------------------------------------------- chargement
def charger(args):
    con = sqlite3.connect(args.db)
    positions = ["pour", "contre"] + ([] if args.sans_abstentions else ["abstention"])
    sql = [
        "SELECT v.scrutin_uid, s.date, v.depute_id, v.position, v.groupe_id",
        "FROM votes v JOIN scrutins s ON s.uid = v.scrutin_uid",
        "WHERE v.position IN (%s)" % ",".join("?" * len(positions)),
    ]
    params = list(positions)
    if args.date_min:
        sql.append("AND s.date >= ?")
        params.append(args.date_min)
    if args.date_max:
        sql.append("AND s.date <= ?")
        params.append(args.date_max)
    if args.legislature:
        sql.append("AND s.legislature IN (%s)" % ",".join("?" * len(args.legislature)))
        params += args.legislature
    if args.types:
        sql.append("AND s.type_objet IN (%s)" % ",".join("?" * len(args.types)))
        params += args.types
    if args.sans_delegation:
        sql.append("AND v.par_delegation = 0")
    if args.exclure_inconnus:
        sql.append("AND v.depute_id IN (SELECT depute_id FROM deputes WHERE nom IS NOT NULL)")

    scr_idx, dep_idx, dates = {}, {}, []
    rs, rd, rc, rg = array("i"), array("i"), array("b"), array("h")
    codes_groupes = {None: 0}            # groupe_id -> code (0 = groupe inconnu)
    groupes_dep = defaultdict(Counter)
    for uid, date, dep, pos, gid in con.execute(" ".join(sql), params):
        si = scr_idx.get(uid)
        if si is None:
            si = scr_idx[uid] = len(scr_idx)
            dates.append(date)
        di = dep_idx.get(dep)
        if di is None:
            di = dep_idx[dep] = len(dep_idx)
        rs.append(si)
        rd.append(di)
        rc.append(CODE[pos])
        rg.append(codes_groupes.setdefault(gid, len(codes_groupes)))
        if gid:
            groupes_dep[dep][gid] += 1

    if not scr_idx:
        sys.exit("Aucun vote ne correspond aux filtres.")

    M = np.zeros((len(dep_idx), len(scr_idx)), dtype=np.int8)
    ii, jj = np.frombuffer(rd, dtype=np.int32), np.frombuffer(rs, dtype=np.int32)
    M[ii, jj] = np.frombuffer(rc, dtype=np.int8)
    G = np.zeros(M.shape, dtype=np.int16)   # groupe du député à chaque scrutin (code, cf. codes_groupes)
    G[ii, jj] = np.frombuffer(rg, dtype=np.int16)

    # "sigle" contient ici l'étiquette d'agrégation : la famille (correspondance_groupes.csv), sinon le sigle
    groupes = {
        gid: {"sigle": etiquette or gid, "libelle": lib or gid, "couleur": coul}
        for gid, etiquette, lib, coul in con.execute(
            "SELECT groupe_id, COALESCE(famille, sigle), libelle, couleur FROM groupes")
    }
    deputes = {
        dep: {"label": label or dep, "departement": dpt or "", "actuel": actuel, "en_fonction": fonction}
        for dep, label, dpt, actuel, fonction in con.execute(
            "SELECT depute_id, nom_complet, departement, groupe_actuel_id, en_fonction FROM deputes")
    }
    con.close()
    deps = sorted(dep_idx, key=dep_idx.get)
    return M, deps, dates, groupes_dep, groupes, deputes, G, codes_groupes


def filtrer_clivants(M, dates, min_minorite):
    """Garde les scrutins où la part des votants hors position majoritaire est >= min_minorite."""
    comptes = np.stack([(M == k).sum(axis=0) for k in (1, 2, 3)])
    total = comptes.sum(axis=0)
    minorite = np.divide(total - comptes.max(axis=0), total, out=np.zeros(M.shape[1]), where=total > 0)
    garde = (total > 0) & (minorite >= min_minorite)
    return M[:, garde], [d for d, g in zip(dates, garde) if g], garde


# ----------------------------------------------------------------------------- calcul
def calculer_aretes(M, min_commun, min_accord):
    P = (M == 1).astype(np.float32)
    C = (M == 2).astype(np.float32)
    A = (M == 3).astype(np.float32)
    V = P + C + A
    identiques = P @ P.T + C @ C.T + A @ A.T
    communs = V @ V.T
    iu, ju = np.triu_indices(M.shape[0], 1)
    com = communs[iu, ju]
    ident = identiques[iu, ju]
    taux = np.where(com > 0, ident / np.maximum(com, 1), 0.0)
    ok = (com >= min_commun) & (taux >= min_accord)
    return iu[ok], ju[ok], ident[ok].astype(int), com[ok].astype(int), taux[ok]


# ----------------------------------------------------------------------------- écriture
def infos_noeud(dep, M_total, pos, groupes_dep, groupes, deputes):
    gid = groupes_dep[dep].most_common(1)[0][0] if groupes_dep[dep] else ""
    g = groupes.get(gid, {})
    d = deputes.get(dep, {})
    actuel = groupes.get(d.get("actuel"), {}).get("sigle", "")
    return {
        "id": dep,
        "label": d.get("label", dep),
        "groupe": g.get("sigle", gid),
        "groupe_id": gid,
        "couleur": g.get("couleur") or "",
        "actuel": actuel,
        "departement": d.get("departement", ""),
        "nb_votes": int((M_total[pos[dep]] != 0).sum()),
    }


def ecrire_csv_aretes(chemin, deps, aretes, poids, puissance):
    iu, ju, ident, com, taux = aretes
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Target", "Type", "Weight", "Taux_accord", "Votes_identiques", "Votes_communs"])
        for a, b, i, c, t in zip(iu, ju, ident, com, taux):
            poids_lien = int(i) if poids == "identiques" else round(float(t) ** puissance, 4)
            w.writerow([deps[a], deps[b], "Undirected", poids_lien, round(float(t), 4), int(i), int(c)])


def ecrire_csv_noeuds(chemin, noeuds):
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Id", "Label", "Groupe", "Groupe_id", "Couleur", "Groupe_actuel", "Departement", "Nb_votes"])
        for n in noeuds:
            w.writerow([n["id"], n["label"], n["groupe"], n["groupe_id"], n["couleur"],
                        n["actuel"], n["departement"], n["nb_votes"]])


def ecrire_gexf(chemin, deps, noeuds, aretes, poids, puissance):
    iu, ju, ident, com, taux = aretes
    with open(chemin, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<gexf xmlns="http://www.gexf.net/1.2draft" xmlns:viz="http://www.gexf.net/1.2draft/viz" version="1.2">\n')
        f.write('  <meta><creator>export_gephi.py</creator><description>Affinités de vote entre députés</description></meta>\n')
        f.write('  <graph defaultedgetype="undirected" mode="static">\n')
        f.write('    <attributes class="node">\n')
        for i, (titre, typ) in enumerate([("groupe", "string"), ("groupe_id", "string"), ("groupe_actuel", "string"),
                                          ("departement", "string"), ("nb_votes", "integer")]):
            f.write(f'      <attribute id="{i}" title="{titre}" type="{typ}"/>\n')
        f.write('    </attributes>\n    <attributes class="edge">\n')
        for i, (titre, typ) in enumerate([("taux_accord", "double"), ("votes_identiques", "integer"),
                                          ("votes_communs", "integer")]):
            f.write(f'      <attribute id="{i}" title="{titre}" type="{typ}"/>\n')
        f.write('    </attributes>\n    <nodes>\n')
        for n in noeuds:
            r, g, b = hex_vers_rgb(n["couleur"])
            f.write(f'      <node id={quoteattr(n["id"])} label={quoteattr(n["label"])}>\n')
            f.write('        <attvalues>')
            for i, k in enumerate(["groupe", "groupe_id", "actuel", "departement", "nb_votes"]):
                f.write(f'<attvalue for="{i}" value={quoteattr(str(n[k]))}/>')
            f.write('</attvalues>\n')
            f.write(f'        <viz:color r="{r}" g="{g}" b="{b}"/>\n      </node>\n')
        f.write('    </nodes>\n    <edges>\n')
        for k, (a, b, i, c, t) in enumerate(zip(iu, ju, ident, com, taux)):
            poids_lien = int(i) if poids == "identiques" else round(float(t) ** puissance, 4)
            f.write(f'      <edge id="{k}" source={quoteattr(deps[a])} target={quoteattr(deps[b])} weight="{poids_lien}">'
                    f'<attvalues><attvalue for="0" value="{round(float(t), 4)}"/>'
                    f'<attvalue for="1" value="{int(i)}"/><attvalue for="2" value="{int(c)}"/></attvalues></edge>\n')
        f.write('    </edges>\n  </graph>\n</gexf>\n')


def construire_parser():
    """Options communes (réutilisées par analyse_reseau.py pour garantir le même graphe)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--out", default=str(config.DIR_GEPHI), help="dossier de sortie")
    ap.add_argument("--date-min", help="AAAA-MM-JJ")
    ap.add_argument("--date-max", help="AAAA-MM-JJ")
    ap.add_argument("--legislature", type=int, nargs="+",
                    help="ne garder que ces législatures (ex. 16, ou 16 17) ; par défaut toutes celles de la base")
    ap.add_argument("--types", nargs="+", help="types d'objet : amendement ensemble article motion_censure ...")
    ap.add_argument("--sans-abstentions", action="store_true",
                    help="ignorer les abstentions (par défaut, l'abstention est une position comme pour/contre)")
    ap.add_argument("--sans-delegation", action="store_true", help="exclure les votes par délégation")
    ap.add_argument("--min-minorite", type=float, default=0,
                    help="ne garder que les scrutins où au moins cette part des votants n'est pas dans la position "
                         "majoritaire ; 0 = tout garder (défaut 0)")
    ap.add_argument("--min-commun", type=int, default=0.1, help="nb minimal de scrutins communs (défaut 0.1)")
    ap.add_argument("--min-accord", type=float, default=0, help="taux d'accord minimal pour un lien (défaut 0)")
    ap.add_argument("--poids", choices=["taux", "identiques"], default="taux",
                    help="poids des liens : taux d'accord ou nombre de positions identiques")
    ap.add_argument("--puissance", type=float, default=1.0,
                    help="si poids=taux : poids = taux ** puissance (ex. 4 accentue les écarts entre liens forts et faibles)")
    ap.add_argument("--periode", choices=["mois", "trimestre", "annee"],
                    help="un réseau par période au lieu d'un seul réseau")
    ap.add_argument("--formats", nargs="+", choices=["csv", "gexf"], default=["csv", "gexf"])
    ap.add_argument("--garder-isoles", action="store_true", help="garder les députés sans aucun lien")
    ap.add_argument("--exclure-inconnus", action="store_true",
                    help="écarter les députés sans nom dans les référentiels (test de robustesse)")
    return ap


def poids_aretes(ident, taux, poids, puissance):
    """Poids des liens tel qu'écrit dans Weight (même règle pour le CSV, le GEXF et l'analyse)."""
    return ident.astype(float) if poids == "identiques" else np.round(taux ** puissance, 4)


def main():
    args = construire_parser().parse_args()

    os.makedirs(args.out, exist_ok=True)
    M, deps, dates, groupes_dep, groupes, deputes, _G, _codes = charger(args)
    print(f"{len(deps)} députés, {M.shape[1]} scrutins chargés")
    M, dates, _garde = filtrer_clivants(M, dates, args.min_minorite)
    print(f"{M.shape[1]} scrutins après filtre 'clivants' (min-minorite = {args.min_minorite})")
    if M.shape[1] == 0:
        sys.exit("Plus aucun scrutin : baisse --min-minorite.")

    pos = {d: i for i, d in enumerate(deps)}
    tous_utilises = set(deps) if args.garder_isoles else set()

    def sortie(nom, sub, suffixe):
        aretes = calculer_aretes(sub, args.min_commun, args.min_accord)
        iu, ju = aretes[0], aretes[1]
        ids = set(deps) if args.garder_isoles else {deps[i] for i in iu} | {deps[j] for j in ju}
        noeuds = [infos_noeud(d, M, pos, groupes_dep, groupes, deputes) for d in sorted(ids)]
        if "csv" in args.formats:
            ecrire_csv_aretes(os.path.join(args.out, f"aretes{suffixe}.csv"), deps, aretes, args.poids, args.puissance)
        if "gexf" in args.formats:
            ecrire_gexf(os.path.join(args.out, f"reseau{suffixe}.gexf"), deps, noeuds, aretes, args.poids, args.puissance)
        tous_utilises.update(ids)
        return len(iu), len(noeuds)

    if args.periode:
        periodes = np.array([periode_de(d, args.periode) for d in dates])
        for p in sorted(set(periodes)):
            sub = M[:, periodes == p]
            n_liens, n_noeuds = sortie(p, sub, f"_{p}")
            print(f"  {p} : {sub.shape[1]} scrutins, {n_noeuds} nœuds, {n_liens} liens")
    else:
        n_liens, n_noeuds = sortie("reseau", M, "")
        print(f"{n_noeuds} nœuds, {n_liens} liens (min-commun={args.min_commun}, min-accord={args.min_accord})")

    if "csv" in args.formats:
        ecrire_csv_noeuds(os.path.join(args.out, "noeuds.csv"),
                          [infos_noeud(d, M, pos, groupes_dep, groupes, deputes) for d in sorted(tous_utilises)])
    print(f"Fichiers écrits dans {args.out}")


if __name__ == "__main__":
    main()