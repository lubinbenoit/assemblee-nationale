#!/usr/bin/env python3
"""
Construit la base SQLite (output/assemblee.sqlite) à partir de :
  - data/Scrutins.json/**/*.json                        un JSON par scrutin
  - data/AMO30_.../json/acteur et /organe               référentiel officiel (députés, mandats, groupes, depuis la XIe législature)
  - data/groupes-active.csv, liste_deputes_libre_office.csv   compléments Datan (facultatifs)
  - data/groupes-supplementaires.csv                    corrections manuelles (facultatif)
  - data/correspondance_groupes.csv                     organe_id -> famille, pour fusionner des groupes (facultatif)

Étapes (chacune peut être relancée seule) :
  python src/build_db.py                  # "tout" : repart de zéro (référentiels + scrutins)
  python src/build_db.py referentiels     # réimporte seulement les référentiels (rapide à relancer après un changement de CSV)
  python src/build_db.py scrutins         # ajoute seulement les nouveaux scrutins (--forcer-scrutins : tout réimporter)

Modules : schema.py (tables), referentiels.py (députés/groupes/mandats), scrutins.py (votes), outils.py (utilitaires).
"""
import argparse
import os
import sqlite3
import sys

import config
import referentiels
import schema
import scrutins


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etape", nargs="?", choices=["tout", "referentiels", "scrutins"], default="tout")
    ap.add_argument("--db", default=str(config.DB), help="fichier SQLite")
    ap.add_argument("--input", default=str(config.DIR_SCRUTINS), help="dossier des JSON de scrutins (récursif)")
    ap.add_argument("--legislatures", type=int, nargs="+", default=config.LEGISLATURES,
                    help="législatures de scrutins à importer, ex. 16 17 ; 0 = toutes (défaut : 17)")
    ap.add_argument("--legislature-min-ref", type=int, default=config.LEGISLATURE_MIN_REFERENTIEL,
                    help="législature minimale gardée dans les mandats et groupes (défaut 16 = quinquennat actuel)")
    ap.add_argument("--forcer-scrutins", action="store_true", help="vider puis réimporter tous les scrutins")
    ap.add_argument("--sans-controles", action="store_true", help="ne pas exécuter les contrôles finaux")
    ap.add_argument("--amo-acteurs", default=str(config.DIR_AMO_ACTEURS))
    ap.add_argument("--amo-organes", default=str(config.DIR_AMO_ORGANES))
    ap.add_argument("--groupes-csv", default=str(config.CSV_GROUPES))
    ap.add_argument("--deputes-csv", default=str(config.CSV_DEPUTES))
    ap.add_argument("--groupes-supp", default=str(config.CSV_GROUPES_SUPP))
    ap.add_argument("--correspondance", default=str(config.CSV_CORRESPONDANCE))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    if args.etape == "tout" and os.path.exists(args.db):
        os.remove(args.db)
    con = sqlite3.connect(args.db)
    schema.creer(con)

    if args.etape in ("tout", "referentiels"):
        referentiels.importer_tout(con, args)

    if args.etape in ("tout", "scrutins"):
        if args.forcer_scrutins:
            schema.vider_scrutins(con)
        c = scrutins.importer_scrutins(con, args.input, args.legislatures)
        print(f"Scrutins ajoutés            : {c['ajoutes']}")
        print(f"Ignorés (déjà là / vides)   : {c['ignores']}")
        print(f"Autre législature           : {c['autre_legislature']}")
        print(f"Erreurs                     : {c['erreurs']}")
        print(f"Scrutins avec mise au point : {c['corriges']}")
        print(f"Écarts avec décompte officiel : {c['ecarts']}  (à examiner si > 0)")

    referentiels.finaliser(con, controles=not args.sans_controles)
    r = con.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM scrutins").fetchone()
    print(f"\nBase : {r[0]} scrutins, du {r[1]} au {r[2]}  ->  {args.db}")
    con.close()


if __name__ == "__main__":
    main()
