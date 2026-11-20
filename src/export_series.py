#!/usr/bin/env python3
"""
Exporte des séries temporelles (CSV) depuis la base SQLite (output/series/ par défaut).

  cohesion_groupes.csv : Periode, Groupe, Couleur, Nb_scrutins, Cohesion_Rice, Participation
  accord_groupes.csv   : Periode, Groupe_A, Groupe_B, Nb_scrutins_communs, Taux_accord
  scrutins_periode.csv : Periode, Nb_scrutins, Nb_adoptes, Nb_rejetes, Taux_adoption

Définitions :
  Cohésion (indice de Rice) d'un groupe sur un scrutin = |pour - contre| / (pour + contre)
      1 = groupe unanime, 0 = groupe coupé en deux. Moyenne par période (abstentions non comptées).
  Accord entre deux groupes = part des scrutins où leurs positions majoritaires (pour/contre) coïncident.
  Participation = (pour + contre + abstention) / effectif du groupe, cumulé sur la période.

Exemple :
  python src/export_series.py --periode mois
"""
import argparse
import csv
import os
import sqlite3
from collections import defaultdict

import config


def periode_de(date, genre):
    a, m = date[:4], int(date[5:7])
    if genre == "annee":
        return a
    if genre == "trimestre":
        return f"{a}-T{(m - 1) // 3 + 1}"
    return date[:7]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--out", default=str(config.DIR_SERIES))
    ap.add_argument("--periode", choices=["mois", "trimestre", "annee"], default="mois")
    ap.add_argument("--date-min")
    ap.add_argument("--date-max")
    ap.add_argument("--types", nargs="+", help="types d'objet à retenir (défaut : tous)")
    ap.add_argument("--min-exprimes", type=int, default=3,
                    help="nb minimal de votants (pour+contre) d'un groupe pour compter un scrutin (défaut 3)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    con = sqlite3.connect(args.db)
    sigles = dict(con.execute("SELECT groupe_id, COALESCE(famille, sigle, groupe_id) FROM groupes"))
    couleurs = dict(con.execute("SELECT groupe_id, COALESCE(couleur, '') FROM groupes"))

    filtres, params = [], []
    if args.date_min:
        filtres.append("s.date >= ?")
        params.append(args.date_min)
    if args.date_max:
        filtres.append("s.date <= ?")
        params.append(args.date_max)
    if args.types:
        filtres.append("s.type_objet IN (%s)" % ",".join("?" * len(args.types)))
        params += args.types
    where = ("AND " + " AND ".join(filtres)) if filtres else ""

    # --- 1. Cohésion, participation, positions majoritaires par (scrutin, groupe)
    sql = f"""
        SELECT s.uid, s.date, v.groupe_id, gs.nombre_membres,
               SUM(v.position = 'pour'), SUM(v.position = 'contre'), SUM(v.position = 'abstention')
        FROM votes v
        JOIN scrutins s ON s.uid = v.scrutin_uid
        LEFT JOIN groupe_scrutin gs ON gs.scrutin_uid = v.scrutin_uid AND gs.groupe_id = v.groupe_id
        WHERE v.groupe_id IS NOT NULL {where}
        GROUP BY s.uid, v.groupe_id
        ORDER BY s.date
    """
    rice = defaultdict(list)
    part = defaultdict(lambda: [0, 0])
    majo = defaultdict(dict)
    for uid, date, gid, membres, p, c, a in con.execute(sql, params):
        per = periode_de(date, args.periode)
        if membres:
            part[(per, gid)][0] += p + c + a
            part[(per, gid)][1] += membres
        if p + c >= args.min_exprimes:
            rice[(per, gid)].append(abs(p - c) / (p + c))
            if p != c:
                majo[(per, uid)][gid] = "pour" if p > c else "contre"

    with open(os.path.join(args.out, "cohesion_groupes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Periode", "Groupe", "Couleur", "Nb_scrutins", "Cohesion_Rice", "Participation"])
        for (per, gid) in sorted(set(rice) | set(part)):
            r = rice.get((per, gid), [])
            votes, eff = part.get((per, gid), [0, 0])
            w.writerow([per, sigles.get(gid, gid), couleurs.get(gid, ""), len(r),
                        round(sum(r) / len(r), 4) if r else "",
                        round(votes / eff, 4) if eff else ""])

    # --- 2. Accord entre groupes (positions majoritaires)
    accord = defaultdict(lambda: [0, 0])
    for (per, _uid), positions in majo.items():
        gids = sorted(positions)
        for i, a in enumerate(gids):
            for b in gids[i + 1:]:
                k = (per, a, b)
                accord[k][0] += 1
                accord[k][1] += positions[a] == positions[b]
    with open(os.path.join(args.out, "accord_groupes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Periode", "Groupe_A", "Groupe_B", "Nb_scrutins_communs", "Taux_accord"])
        for (per, a, b), (n, ident) in sorted(accord.items()):
            w.writerow([per, sigles.get(a, a), sigles.get(b, b), n, round(ident / n, 4)])

    # --- 3. Scrutins et adoption par période
    sql = f"SELECT s.date, s.sort FROM scrutins s WHERE 1=1 {where} ORDER BY s.date"
    comptes = defaultdict(lambda: [0, 0, 0])
    for date, sort in con.execute(sql, params):
        c = comptes[periode_de(date, args.periode)]
        c[0] += 1
        if sort == "adopté":
            c[1] += 1
        elif sort == "rejeté":
            c[2] += 1
    with open(os.path.join(args.out, "scrutins_periode.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Periode", "Nb_scrutins", "Nb_adoptes", "Nb_rejetes", "Taux_adoption"])
        for per, (n, ad, rej) in sorted(comptes.items()):
            w.writerow([per, n, ad, rej, round(ad / n, 4) if n else ""])

    print(f"Séries écrites dans {args.out} ({len(comptes)} périodes)")
    con.close()


if __name__ == "__main__":
    main()
