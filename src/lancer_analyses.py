#!/usr/bin/env python3
"""
Lance export Gephi + analyse + graphiques pour chaque périmètre : législature 16, législature 17, et les deux ensemble.

  output/gephi_L16/   output/analyse_L16/
  output/gephi_L17/   output/analyse_L17/
  output/gephi_L16-17/  output/analyse_L16-17/

Les options supplémentaires sont transmises à export_gephi.py et analyse_reseau.py (mêmes options pour les deux) :
  python src/lancer_analyses.py --date-max 2026-10-01 --min-accord 0.7 --min-commun 200
  python src/lancer_analyses.py --perimetres 17        # un seul périmètre
"""
import argparse
import subprocess
import sys
from pathlib import Path

import config

SRC = Path(__file__).resolve().parent


def lancer(script, *args):
    cmd = [sys.executable, str(SRC / script), *map(str, args)]
    print("\n>", " ".join(cmd[1:]))
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--perimetres", nargs="+", default=["16", "17", "16-17"],
                    help="périmètres à traiter : 16, 17, 16-17 (défaut : les trois)")
    ap.add_argument("--db", default=str(config.DB))
    args, extra = ap.parse_known_args()

    for p in args.perimetres:
        legs = p.split("-")
        suffixe = "L" + p
        gephi = config.OUTPUT / f"gephi_{suffixe}"
        analyse = config.OUTPUT / f"analyse_{suffixe}"
        commun = ["--db", args.db, "--legislature", *legs, *extra]
        print(f"\n===== Périmètre : législature(s) {' + '.join(legs)} =====")
        lancer("export_gephi.py", *commun, "--out", gephi)
        lancer("analyse_reseau.py", *commun, "--out", analyse)
        lancer("graphiques.py", "--dossier", analyse)


if __name__ == "__main__":
    main()
