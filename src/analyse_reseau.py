#!/usr/bin/env python3
"""
Analyse du réseau d'affinités entre députés, à partir de la base SQLite (output/analyse/ par défaut).

Le graphe est reconstruit EXACTEMENT comme dans export_gephi.py (mêmes options : dates, types,
abstentions, --min-commun, --min-accord, --min-minorite...). Les chiffres correspondent donc à ce que
tu vois dans Gephi. Pourquoi la base plutôt que aretes.csv : le CSV ne contient que les liens au-dessus du
seuil, donc impossible d'y calculer l'accord moyen "toutes paires" (sans seuil) ni de changer de période.

Fichiers produits :
  parametres.json                       période réellement couverte, filtres, nb de scrutins/députés
  cohesion_groupes.csv                  cohésion et dispersion par groupe
  matrices_groupes_long.csv             groupe x groupe : accord moyen, nb de liens, poids moyen des liens
  matrice_accord_moyen.csv, matrice_poids_moyen_liens.csv, matrice_nb_liens.csv, matrice_part_paires_liees.csv
  noeuds_metriques.csv                  par député : degrés, accord intra/hors groupe, z-score, PageRank...
  modularite.csv                        modularité de la partition par groupes (et Louvain, et Gephi si fourni)
  modularite_selon_seuil.csv            la même chose pour plusieurs seuils d'accord (--seuils)
  tableau_croise_groupes_louvain.csv    groupes x communautés
  deputes_inconnus.csv                  députés absents du référentiel (nommés par leur identifiant)
  deputes_changeant_groupe.csv          députés dont le groupe a changé pendant la période
  evolution_cohesion.csv, evolution_modularite.csv   mêmes mesures par période (--periode, défaut trimestre)

Précisions de méthode :
  * Groupe d'un député = groupe auquel il appartenait le plus souvent sur la période étudiée
    (pas le "groupe actuel" du CSV, qui ne sert qu'à repérer les écarts). Dans l'évolution, il est
    recalculé pour chaque période.
  * Un député dont le groupe principal couvre moins de --part-groupe-min (0.8) de ses votes est exclu des
    agrégats par groupe (mais reste dans le graphe, la modularité et noeuds_metriques.csv).
  * Les groupes sont agrégés par FAMILLE (data/correspondance_groupes.csv), à défaut par SIGLE : deux organes de
    même sigle ou de même famille fusionnent (ex. un groupe réenregistré sous un nouvel identifiant).
  * Accord moyen toutes paires = moyenne du taux d'accord sur toutes les paires ayant >= --min-commun
    scrutins communs, quel que soit le seuil --min-accord. Poids moyen des liens = moyenne sur les seuls
    liens retenus (>= --min-accord), c'est-à-dire "degré pondéré / degré" de Gephi.

Exemples :
  python src/analyse_reseau.py
  python src/analyse_reseau.py --date-min 2024-07-18 --date-max 2026-10-01 --min-accord 0.7
  python src/analyse_reseau.py --betweenness 100 --communautes-gephi gephi_noeuds_modularite.csv
"""
import copy
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import config
import export_gephi as eg

INCONNU = "(inconnu)"


# ----------------------------------------------------------------------------- matrices
def matrices(M):
    """Accord entre tous les députés : retourne (taux, communs), matrices n x n (taux NaN si 0 scrutin commun)."""
    P = (M == 1).astype(np.float32)
    C = (M == 2).astype(np.float32)
    A = (M == 3).astype(np.float32)
    V = P + C + A
    ident = (P @ P.T + C @ C.T + A @ A.T).astype(np.float64)
    com = (V @ V.T).astype(np.float64)
    taux = np.where(com > 0, ident / np.maximum(com, 1), np.nan)
    np.fill_diagonal(com, 0)
    return taux, com, ident


def groupe_principal(M, G, noms_codes):
    """Pour chaque député : (nom du groupe le plus fréquent, part de ses votes dans ce groupe)."""
    actif = M != 0
    ncodes = len(noms_codes)
    comptes = np.stack([((G == c) & actif).sum(axis=1) for c in range(ncodes)], axis=1)
    total = comptes.sum(axis=1)
    # fusion des codes qui ont le même nom (même sigle) avant de choisir le groupe principal
    noms_uniques = sorted(set(noms_codes))
    par_nom = np.stack([comptes[:, [i for i, n in enumerate(noms_codes) if n == u]].sum(axis=1)
                        for u in noms_uniques], axis=1)
    best = par_nom.argmax(axis=1)
    part = np.divide(par_nom.max(axis=1), total, out=np.zeros(len(total)), where=total > 0)
    return np.array([noms_uniques[i] for i in best]), part, total, noms_uniques, par_nom


def modularite(W, lab):
    """Modularité pondérée de Newman (résolution 1) d'une partition `lab` (tableau d'étiquettes)."""
    m2 = W.sum()
    if m2 == 0:
        return float("nan")
    k = W.sum(axis=1)
    same = lab[:, None] == lab[None, :]
    return float(((W - np.outer(k, k) / m2) * same).sum() / m2)


def louvain(W, seed):
    import networkx as nx
    n = W.shape[0]
    G = nx.Graph()
    G.add_nodes_from(range(n))
    iu, ju = np.nonzero(np.triu(W, 1))
    G.add_weighted_edges_from(zip(iu.tolist(), ju.tolist(), W[iu, ju].tolist()))
    comms = nx.community.louvain_communities(G, weight="weight", resolution=1.0, seed=seed)
    lab = np.zeros(n, dtype=int)
    for c, noeuds in enumerate(sorted(comms, key=len, reverse=True)):
        for i in noeuds:
            lab[i] = c
    return lab


def contingence(a, b):
    ua, ia = np.unique(a, return_inverse=True)
    ub, ib = np.unique(b, return_inverse=True)
    C = np.zeros((len(ua), len(ub)), dtype=np.int64)
    np.add.at(C, (ia, ib), 1)
    return C, ua, ub


def ari(C):
    comb = lambda x: x * (x - 1) / 2
    n = C.sum()
    s_ij, s_a, s_b = comb(C).sum(), comb(C.sum(axis=1)).sum(), comb(C.sum(axis=0)).sum()
    attendu = s_a * s_b / comb(n)
    maxi = (s_a + s_b) / 2
    return float((s_ij - attendu) / (maxi - attendu)) if maxi != attendu else 1.0


def nmi(C):
    n = C.sum()
    pij = C / n
    pi, pj = pij.sum(axis=1, keepdims=True), pij.sum(axis=0, keepdims=True)
    nz = pij > 0
    mi = (pij[nz] * np.log(pij[nz] / (pi @ pj)[nz])).sum()
    hi, hj = -(pi[pi > 0] * np.log(pi[pi > 0])).sum(), -(pj[pj > 0] * np.log(pj[pj > 0])).sum()
    return float(mi / ((hi + hj) / 2)) if hi + hj > 0 else 1.0


def pagerank(W, d=0.85, it=100):
    n = W.shape[0]
    s = W.sum(axis=1)
    P = np.where(s[:, None] > 0, W / np.maximum(s[:, None], 1e-12), 1.0 / n)
    r = np.full(n, 1.0 / n)
    for _ in range(it):
        r_new = (1 - d) / n + d * (P.T @ r)
        if np.abs(r_new - r).sum() < 1e-10:
            r = r_new
            break
        r = r_new
    return r


def vecteur_propre(W):
    if W.sum() == 0:
        return np.zeros(W.shape[0])
    vals, vecs = np.linalg.eigh(W)
    v = np.abs(vecs[:, -1])
    return v / v.max()


def betweenness(W, taux, k, seed):
    import networkx as nx
    G = nx.Graph()
    G.add_nodes_from(range(W.shape[0]))
    iu, ju = np.nonzero(np.triu(W, 1))
    G.add_weighted_edges_from((int(i), int(j), 1.0 / float(taux[i, j])) for i, j in zip(iu, ju))
    b = nx.betweenness_centrality(G, k=min(k, G.number_of_nodes()), weight="weight", seed=seed)
    return np.array([b[i] for i in range(W.shape[0])])


# ----------------------------------------------------------------------------- agrégats par groupe
def agregats_groupes(taux, com, lien, lab_ref, groupes_noms):
    """Matrices groupe x groupe. lab_ref : étiquette de groupe des députés inclus, None sinon."""
    idx = {g: i for i, g in enumerate(groupes_noms)}
    n, k = len(lab_ref), len(groupes_noms)
    Ig = np.zeros((n, k))
    for i, g in enumerate(lab_ref):
        if g is not None:
            Ig[i, idx[g]] = 1
    valide = (com > 0) & np.isfinite(taux)
    t0 = np.where(valide, taux, 0.0)
    v, l = valide.astype(float), lien.astype(float)
    nb_valides = Ig.T @ v @ Ig
    somme = Ig.T @ t0 @ Ig
    nb_liens = Ig.T @ l @ Ig
    somme_liens = Ig.T @ (t0 * l) @ Ig
    d = np.arange(k)
    for X in (nb_valides, somme, nb_liens, somme_liens):  # paires intra comptées deux fois
        X[d, d] /= 2
    with np.errstate(invalid="ignore", divide="ignore"):
        accord = np.where(nb_valides > 0, somme / nb_valides, np.nan)
        poids = np.where(nb_liens > 0, somme_liens / nb_liens, np.nan)
        part = np.where(nb_valides > 0, nb_liens / nb_valides, np.nan)
    return accord, poids, nb_liens, part, nb_valides


def stats(serie, prefixe):
    q = serie.quantile([0.25, 0.5, 0.75])
    return {f"{prefixe}_moyenne": serie.mean(), f"{prefixe}_ecart_type": serie.std(ddof=0),
            f"{prefixe}_min": serie.min(), f"{prefixe}_q1": q[0.25], f"{prefixe}_mediane": q[0.5],
            f"{prefixe}_q3": q[0.75], f"{prefixe}_max": serie.max()}


# ----------------------------------------------------------------------------- analyse d'un bloc de scrutins
def analyser(M, G, noms_codes, args, min_commun, deps, avec_noeuds=False, betw_k=0):
    """M, G : matrices députés x scrutins. Retourne un dict de résultats."""
    actifs = np.flatnonzero((M != 0).any(axis=1))
    M, G = M[actifs], G[actifs]
    deps_a = [deps[i] for i in actifs]
    n = len(deps_a)

    taux, com, ident = matrices(M)
    valide = (com >= min_commun) & np.isfinite(taux)
    np.fill_diagonal(valide, False)
    lien = valide & (taux >= args.min_accord)
    t0 = np.where(valide, taux, 0.0)
    if args.poids == "identiques":
        W = np.where(lien, ident, 0.0)
    else:
        W = np.where(lien, np.round(np.where(valide, taux, 0.0) ** args.puissance, 4), 0.0)

    grp, part_grp, nb_votes, noms_uniques, _ = groupe_principal(M, G, noms_codes)
    inclus = part_grp >= args.part_groupe_min
    lab_all = grp
    lab_ref = np.array([g if ok else None for g, ok in zip(grp, inclus)], dtype=object)
    groupes_presents = sorted(set(g for g in lab_ref if g is not None))

    accord, poids, nb_liens, part_liees, nb_valides = agregats_groupes(taux, com, lien, lab_ref, groupes_presents)

    # --- mesures par député
    degre = lien.sum(axis=1)
    deg_pond = (t0 * lien).sum(axis=1)                      # somme des taux d'accord des liens
    with np.errstate(invalid="ignore", divide="ignore"):
        poids_moyen = np.where(degre > 0, deg_pond / degre, np.nan)
    ref_arr = np.array([g if g is not None else "\0" for g in lab_ref], dtype=object)
    meme = lab_all[:, None] == ref_arr[None, :]
    np.fill_diagonal(meme, False)
    v_in, v_out = valide & meme, valide & ~meme
    with np.errstate(invalid="ignore", divide="ignore"):
        acc_intra = np.where(v_in.sum(1) > 0, (t0 * v_in).sum(1) / np.maximum(v_in.sum(1), 1), np.nan)
        acc_hors = np.where(v_out.sum(1) > 0, (t0 * v_out).sum(1) / np.maximum(v_out.sum(1), 1), np.nan)
    df = pd.DataFrame({
        "Id": deps_a, "Groupe": lab_all, "Part_groupe_principal": part_grp.round(4), "Inclus_agregats": inclus,
        "Nb_votes": (M != 0).sum(axis=1),
        "Degre": degre, "Degre_pondere": deg_pond.round(4), "Poids_moyen_lien": np.round(poids_moyen, 4),
        "Accord_intra": np.round(acc_intra, 4), "Accord_hors_groupe": np.round(acc_hors, 4),
    })
    # z-score de l'accord intra au sein du groupe (députés inclus seulement pour la moyenne/écart-type)
    z = np.full(n, np.nan)
    for g in groupes_presents:
        m_in = (lab_all == g) & inclus
        mu, sd = np.nanmean(acc_intra[m_in]), np.nanstd(acc_intra[m_in])
        cible = lab_all == g
        z[cible] = (acc_intra[cible] - mu) / sd if sd > 0 else 0.0
    df["Z_intra"] = np.round(z, 3)
    if avec_noeuds:
        df["Vecteur_propre"] = np.round(vecteur_propre(W), 4)
        df["PageRank"] = np.round(pagerank(W), 6)
        if betw_k:
            df["Betweenness"] = np.round(betweenness(W, taux, betw_k, args.seed), 6)

    # --- agrégats par groupe
    lignes = []
    for gi, g in enumerate(groupes_presents):
        sel = (df["Groupe"] == g) & df["Inclus_agregats"]
        ligne = {
            "Groupe": g, "Nb_deputes": int(sel.sum()),
            "Nb_deputes_exclus_mobilite": int(((df["Groupe"] == g) & ~df["Inclus_agregats"]).sum()),
            "Accord_moyen_toutes_paires": accord[gi, gi], "Poids_moyen_liens_intra": poids[gi, gi],
            "Nb_liens_intra": int(nb_liens[gi, gi]), "Part_paires_liees_intra": part_liees[gi, gi],
        }
        if sel.sum() > 0:
            ligne.update(stats(df.loc[sel, "Accord_intra"].dropna(), "Accord_intra"))
            ligne.update(stats(df.loc[sel, "Degre_pondere"], "Degre_pondere"))
        lignes.append(ligne)
    cohesion = pd.DataFrame(lignes)

    # --- modularité
    q_groupes = modularite(W, lab_all)
    res = {"df": df, "cohesion": cohesion, "groupes": groupes_presents, "W": W, "lab_all": lab_all,
           "matrices": (accord, poids, nb_liens, part_liees, nb_valides), "n": n, "Q_groupes": q_groupes,
           "nb_liens": int(lien.sum() // 2)}
    return res


def pivot_matrice(X, groupes):
    return pd.DataFrame(X, index=groupes, columns=groupes)


def main():
    ap = eg.construire_parser()
    ap.description = __doc__
    ap.set_defaults(out=str(config.DIR_ANALYSE), periode="trimestre")
    ap.add_argument("--sans-evolution", action="store_true", help="ne pas calculer l'évolution par période")
    ap.add_argument("--min-commun-periode", type=int, default=10,
                    help="--min-commun appliqué à chaque période (défaut 10)")
    ap.add_argument("--part-groupe-min", type=float, default=0.8,
                    help="part minimale des votes dans le groupe principal pour entrer dans les agrégats (défaut 0.8)")
    ap.add_argument("--betweenness", type=int, default=0, metavar="K",
                    help="calculer la betweenness sur K nœuds échantillonnés (0 = non, ex. 100). Lent sur un graphe dense")
    ap.add_argument("--communautes-gephi", help="CSV exporté de Gephi (Id + colonne modularity_class) pour comparer")
    ap.add_argument("--seed", type=int, default=42, help="graine de Louvain / de l'échantillonnage")
    ap.add_argument("--seuils", type=float, nargs="+", default=[0.5, 0.6, 0.7, 0.8, 0.9],
                    help="seuils d'accord testés pour modularite_selon_seuil.csv (défaut 0.5 ... 0.9)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    # ---- chargement et filtres (identiques à export_gephi.py)
    M, deps, dates, groupes_dep, groupes, deputes, G, codes = eg.charger(args)
    n_scr_charges = M.shape[1]
    M, dates, garde = eg.filtrer_clivants(M, dates, args.min_minorite)
    G = G[:, garde]
    if M.shape[1] == 0:
        sys.exit("Plus aucun scrutin : baisse --min-minorite.")
    noms_codes = [INCONNU] * len(codes)
    couleurs = {}
    for gid, c in codes.items():
        if gid is not None:
            g = groupes.get(gid, {})
            noms_codes[c] = g.get("sigle", gid)
            if g.get("couleur"):
                couleurs[g.get("sigle", gid)] = g["couleur"]
    print(f"{len(deps)} députés, {n_scr_charges} scrutins chargés, {M.shape[1]} après filtre 'clivants'")

    # ---- analyse globale
    res = analyser(M, G, noms_codes, args, args.min_commun, deps, avec_noeuds=True, betw_k=args.betweenness)
    df, cohesion = res["df"], res["cohesion"]
    df.insert(1, "Label", [deputes.get(d, {}).get("label", d) for d in df["Id"]])
    df.insert(3, "Groupe_actuel", [groupes.get(deputes.get(d, {}).get("actuel"), {}).get("sigle", "")
                                   for d in df["Id"]])
    df["Dans_referentiel"] = [d in deputes and deputes[d].get("label", d) != d for d in df["Id"]]
    df["En_fonction"] = [{1: True, 0: False}.get(deputes.get(d, {}).get("en_fonction")) for d in df["Id"]]
    df["Groupe_actuel_differe"] = (df["Groupe_actuel"] != "") & (df["Groupe_actuel"] != df["Groupe"])
    df.to_csv(os.path.join(args.out, "noeuds_metriques.csv"), index=False, encoding="utf-8")
    cohesion.insert(1, "Couleur", [couleurs.get(g, "") for g in cohesion["Groupe"]])
    cohesion = cohesion.sort_values("Nb_deputes", ascending=False)
    cohesion.round(4).to_csv(os.path.join(args.out, "cohesion_groupes.csv"), index=False, encoding="utf-8")

    # matrices groupe x groupe (groupes triés par effectif)
    ordre = list(cohesion["Groupe"])
    gp = res["groupes"]
    accord, poids, nb_liens, part_liees, nb_valides = res["matrices"]
    perm = [gp.index(g) for g in ordre]
    sel = lambda X: X[np.ix_(perm, perm)]
    for nom, X in [("accord_moyen", accord), ("poids_moyen_liens", poids), ("nb_liens", nb_liens),
                   ("part_paires_liees", part_liees)]:
        pivot_matrice(sel(X), ordre).round(4).to_csv(os.path.join(args.out, f"matrice_{nom}.csv"), encoding="utf-8")
    longues = []
    for i, a in enumerate(ordre):
        for j, b in enumerate(ordre):
            if j >= i:
                longues.append({"Groupe_A": a, "Groupe_B": b,
                                "Nb_paires_valides": int(sel(nb_valides)[i, j]),
                                "Accord_moyen_toutes_paires": sel(accord)[i, j],
                                "Nb_liens": int(sel(nb_liens)[i, j]),
                                "Part_paires_liees": sel(part_liees)[i, j],
                                "Poids_moyen_liens": sel(poids)[i, j]})
    pd.DataFrame(longues).round(4).to_csv(os.path.join(args.out, "matrices_groupes_long.csv"),
                                          index=False, encoding="utf-8")

    # modularité : groupes, Louvain, Gephi
    W, lab_groupes = res["W"], res["lab_all"]
    lab_lv = louvain(W, args.seed)
    C_lv, ug, ul = contingence(lab_groupes, lab_lv)
    pd.DataFrame(C_lv, index=ug, columns=[f"C{c}" for c in ul]).to_csv(
        os.path.join(args.out, "tableau_croise_groupes_louvain.csv"), encoding="utf-8")
    lignes_mod = [
        {"Partition": "Groupes politiques", "Nb_communautes": len(set(lab_groupes)),
         "Modularite": modularite(W, lab_groupes), "ARI_vs_groupes": 1.0, "NMI_vs_groupes": 1.0},
        {"Partition": "Louvain (networkx)", "Nb_communautes": len(set(lab_lv)),
         "Modularite": modularite(W, lab_lv), "ARI_vs_groupes": ari(C_lv), "NMI_vs_groupes": nmi(C_lv)},
    ]
    if args.communautes_gephi:
        g = pd.read_csv(args.communautes_gephi)
        col_id = next(c for c in g.columns if c.strip().lower() == "id")
        col_cl = next(c for c in g.columns if "modularity" in c.lower())
        classe = dict(zip(g[col_id].astype(str), g[col_cl]))
        ids_a = list(df["Id"])
        lab_gp = np.array([classe.get(d, -1) for d in ids_a])
        presents = lab_gp != -1
        C_gp, ug2, ul2 = contingence(lab_groupes[presents], lab_gp[presents])
        pd.DataFrame(C_gp, index=ug2, columns=[f"C{c}" for c in ul2]).to_csv(
            os.path.join(args.out, "tableau_croise_groupes_gephi.csv"), encoding="utf-8")
        lignes_mod.append({"Partition": "Gephi (fichier fourni)", "Nb_communautes": len(set(lab_gp[presents])),
                           "Modularite": modularite(W, np.where(presents, lab_gp, -np.arange(1, len(lab_gp) + 1))),
                           "ARI_vs_groupes": ari(C_gp), "NMI_vs_groupes": nmi(C_gp)})
    pd.DataFrame(lignes_mod).round(4).to_csv(os.path.join(args.out, "modularite.csv"), index=False, encoding="utf-8")

    # sensibilité de la modularité au seuil d'accord (le choix du seuil est arbitraire : on le fait varier)
    lignes_seuil = []
    for sc in args.seuils:
        a2 = copy.copy(args)
        a2.min_accord = sc
        r2 = analyser(M, G, noms_codes, a2, args.min_commun, deps)
        lv2 = louvain(r2["W"], args.seed)
        C2, _, _ = contingence(r2["lab_all"], lv2)
        lignes_seuil.append({"Seuil_accord": sc, "Nb_liens": r2["nb_liens"],
                             "Densite": r2["nb_liens"] / (r2["n"] * (r2["n"] - 1) / 2),
                             "Modularite_groupes": r2["Q_groupes"], "Modularite_louvain": modularite(r2["W"], lv2),
                             "Nb_communautes_louvain": len(set(lv2)),
                             "ARI_groupes_louvain": ari(C2), "NMI_groupes_louvain": nmi(C2)})
    pd.DataFrame(lignes_seuil).round(4).to_csv(os.path.join(args.out, "modularite_selon_seuil.csv"),
                                               index=False, encoding="utf-8")

    # députés à surveiller
    inconnus = df[~df["Dans_referentiel"]][["Id", "Groupe", "Nb_votes"]].copy()
    prem = {}
    if len(inconnus):
        import sqlite3
        con = sqlite3.connect(args.db)
        for d in inconnus["Id"]:
            r = con.execute("SELECT MIN(s.date), MAX(s.date) FROM votes v JOIN scrutins s ON s.uid=v.scrutin_uid "
                            "WHERE v.depute_id=?", (d,)).fetchone()
            prem[d] = r
        con.close()
        inconnus["Premier_vote"] = [prem[d][0] for d in inconnus["Id"]]
        inconnus["Dernier_vote"] = [prem[d][1] for d in inconnus["Id"]]
    inconnus.to_csv(os.path.join(args.out, "deputes_inconnus.csv"), index=False, encoding="utf-8")

    pos = {d: i for i, d in enumerate(deps)}
    mobiles = []
    for _, r in df[df["Part_groupe_principal"] < 1.0].iterrows():
        i = pos[r["Id"]]
        cnt = {}
        for c in np.unique(G[i][M[i] != 0]):
            nbr = int(((G[i] == c) & (M[i] != 0)).sum())
            nom = noms_codes[c]
            cnt[nom] = cnt.get(nom, 0) + nbr
        if len(cnt) > 1:
            mobiles.append({"Id": r["Id"], "Label": r["Label"], "Groupe_principal": r["Groupe"],
                            "Part_groupe_principal": r["Part_groupe_principal"], "Groupe_actuel": r["Groupe_actuel"],
                            "Votes_par_groupe": "; ".join(f"{k}:{v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1])),
                            "Exclu_des_agregats": not r["Inclus_agregats"]})
    pd.DataFrame(mobiles).to_csv(os.path.join(args.out, "deputes_changeant_groupe.csv"), index=False, encoding="utf-8")

    # ---- évolution
    if not args.sans_evolution:
        periodes = np.array([eg.periode_de(d, args.periode) for d in dates])
        evo_coh, evo_mod = [], []
        for p in sorted(set(periodes)):
            cols = periodes == p
            r = analyser(M[:, cols], G[:, cols], noms_codes, args, args.min_commun_periode, deps)
            c = r["cohesion"].copy()
            c.insert(0, "Periode", p)
            c.insert(2, "Nb_scrutins", int(cols.sum()))
            voulues = ["Periode", "Groupe", "Nb_scrutins", "Nb_deputes", "Accord_moyen_toutes_paires",
                       "Poids_moyen_liens_intra", "Part_paires_liees_intra", "Accord_intra_ecart_type"]
            evo_coh.append(c[[x for x in voulues if x in c.columns]])
            lv = louvain(r["W"], args.seed)
            Cp, _, _ = contingence(r["lab_all"], lv)
            evo_mod.append({"Periode": p, "Nb_scrutins": int(cols.sum()), "Nb_deputes": r["n"], "Nb_liens": r["nb_liens"],
                            "Modularite_groupes": r["Q_groupes"], "Modularite_louvain": modularite(r["W"], lv),
                            "ARI_groupes_louvain": ari(Cp), "NMI_groupes_louvain": nmi(Cp)})
            print(f"  {p} : {cols.sum()} scrutins, {r['n']} députés, Q(groupes) = {r['Q_groupes']:.3f}")
        pd.concat(evo_coh).round(4).to_csv(os.path.join(args.out, "evolution_cohesion.csv"), index=False, encoding="utf-8")
        pd.DataFrame(evo_mod).round(4).to_csv(os.path.join(args.out, "evolution_modularite.csv"), index=False, encoding="utf-8")

    # ---- paramètres / période couverte
    meta = {
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "periode_couverte": {"premier_scrutin": min(dates), "dernier_scrutin": max(dates)},
        "nb_scrutins_charges": n_scr_charges, "nb_scrutins_retenus": int(M.shape[1]),
        "nb_deputes": len(deps), "nb_deputes_hors_referentiel": int(len(inconnus)),
        "nb_deputes_changeant_groupe": len(mobiles),
        "parametres": {k: v for k, v in vars(args).items() if k not in ("db", "out")},
        "modularite_groupes": res["Q_groupes"], "nb_liens": res["nb_liens"],
    }
    with open(os.path.join(args.out, "parametres.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

    print(f"\nPériode couverte : {min(dates)} -> {max(dates)} ({M.shape[1]} scrutins)")
    print(f"Modularité de la partition par groupes : {res['Q_groupes']:.3f}  ({res['nb_liens']} liens)")
    print(f"Députés hors référentiel : {len(inconnus)} ; ayant changé de groupe : {len(mobiles)}")
    print(f"Fichiers écrits dans {args.out}")


if __name__ == "__main__":
    main()
