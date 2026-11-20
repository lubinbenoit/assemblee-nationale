#!/usr/bin/env python3
"""
Repère dans les JSON de scrutins :
  - les scrutins où un même groupe (organeRef) apparaît plusieurs fois ;
  - les identifiants de scrutin (uid) présents dans plusieurs fichiers.

Usage : python src/diagnostic_doublons.py
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict

import config


def main():
    dossier = sys.argv[1] if len(sys.argv) > 1 else str(config.DIR_SCRUTINS)
    fichiers = sorted(glob.glob(os.path.join(dossier, "**", "*.json"), recursive=True))
    uids = defaultdict(list)
    n_groupes_doubles = 0
    for chemin in fichiers:
        try:
            with open(chemin, encoding="utf-8") as f:
                s = json.load(f).get("scrutin")
        except Exception as e:
            print(f"[illisible] {os.path.basename(chemin)} : {e!r}")
            continue
        if not isinstance(s, dict):
            continue
        uids[s.get("uid")].append(os.path.basename(chemin))
        g = ((s.get("ventilationVotes") or {}).get("organe") or {}).get("groupes") or {}
        liste = g.get("groupe")
        liste = liste if isinstance(liste, list) else ([liste] if liste else [])
        compte = Counter(x.get("organeRef") for x in liste)
        for gid, n in compte.items():
            if n > 1:
                n_groupes_doubles += 1
                print(f"\n{os.path.basename(chemin)} (uid {s.get('uid')}, {s.get('dateScrutin')}) : "
                      f"groupe {gid} listé {n} fois")
                for x in liste:
                    if x.get("organeRef") == gid:
                        d = (x.get("vote") or {}).get("decompteVoix") or {}
                        print(f"   nombreMembresGroupe={x.get('nombreMembresGroupe')} "
                              f"position={((x.get('vote') or {}).get('positionMajoritaire'))} decompteVoix={d}")
    doubles = {u: f for u, f in uids.items() if len(f) > 1}
    print(f"\n{len(fichiers)} fichiers analysés ; {n_groupes_doubles} cas de groupe en double ; "
          f"{len(doubles)} uid présents dans plusieurs fichiers")
    for u, f in list(doubles.items())[:20]:
        print(f"  {u} : {f}")


if __name__ == "__main__":
    main()