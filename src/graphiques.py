#!/usr/bin/env python3
"""
Dessine des graphiques (PNG) à partir des CSV produits par analyse_reseau.py.
Lit output/analyse/, écrit dans output/analyse/graphiques/.

  1_heatmap_accord_groupes.png     accord moyen entre groupes (toutes paires, sans seuil)
  2_boxplot_accord_intra.png       dispersion de l'accord de chaque député avec son groupe (+ les plus isolés nommés)
  3_ponts_et_frondeurs.png         accord intra-groupe vs accord hors-groupe, par député
  4_evolution_cohesion.png         cohésion de chaque groupe par période
  5_evolution_modularite.png       modularité (groupes / Louvain) et ARI par période
  6_modularite_selon_seuil.png     sensibilité au seuil d'accord

Usage : python src/graphiques.py   (nécessite matplotlib)
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

import config

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("matplotlib est nécessaire : pip install matplotlib")

GRIS = "#999999"


def couleur(c):
    return c if isinstance(c, str) and c.startswith("#") and len(c) == 7 else GRIS


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossier", default=str(config.DIR_ANALYSE))
    ap.add_argument("--nommes", type=int, default=8, help="nb de députés les plus isolés à nommer (défaut 8)")
    args = ap.parse_args()
    d = args.dossier
    sortie = os.path.join(d, "graphiques")
    os.makedirs(sortie, exist_ok=True)

    coh = pd.read_csv(os.path.join(d, "cohesion_groupes.csv"))
    couleurs = {g: couleur(c) for g, c in zip(coh["Groupe"], coh["Couleur"])}
    noeuds = pd.read_csv(os.path.join(d, "noeuds_metriques.csv"))
    ordre = list(coh["Groupe"])  # par effectif décroissant

    # 1. heatmap
    mat = pd.read_csv(os.path.join(d, "matrice_accord_moyen.csv"), index_col=0)
    fig, ax = plt.subplots(figsize=(0.8 * len(mat) + 3, 0.8 * len(mat) + 2))
    im = ax.imshow(mat.values, cmap="viridis", vmin=np.nanmin(mat.values), vmax=np.nanmax(mat.values))
    ax.set_xticks(range(len(mat)), mat.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(mat)), mat.index)
    for i in range(len(mat)):
        for j in range(len(mat)):
            v = mat.values[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8,
                        color="white" if v < (np.nanmin(mat.values) + np.nanmax(mat.values)) / 2 else "black")
    fig.colorbar(im, ax=ax, label="taux d'accord moyen")
    ax.set_title("Accord moyen entre groupes (toutes paires de députés)")
    fig.tight_layout()
    fig.savefig(os.path.join(sortie, "1_heatmap_accord_groupes.png"), dpi=150)
    plt.close(fig)

    # 2. boxplot de l'accord intra
    inclus = noeuds[noeuds["Inclus_agregats"] & noeuds["Accord_intra"].notna()]
    donnees = [inclus.loc[inclus["Groupe"] == g, "Accord_intra"].values for g in ordre]
    fig, ax = plt.subplots(figsize=(max(7, 0.9 * len(ordre) + 2), 5.5))
    bp = ax.boxplot(donnees, patch_artist=True, showfliers=True, flierprops={"markersize": 3})
    for patch, g in zip(bp["boxes"], ordre):
        patch.set_facecolor(couleurs.get(g, GRIS))
        patch.set_alpha(0.75)
    ax.set_xticks(range(1, len(ordre) + 1), ordre, rotation=45, ha="right")
    ax.set_ylabel("accord moyen avec les autres membres du groupe")
    ax.set_title("Cohésion interne : dispersion par groupe")
    pires = inclus.nsmallest(args.nommes, "Z_intra")
    decalage = {}
    for _, r in pires.iterrows():
        x = ordre.index(r["Groupe"]) + 1 if r["Groupe"] in ordre else None
        if x:
            k = decalage.get(x, 0)   # décale les étiquettes d'un même groupe pour qu'elles ne se chevauchent pas
            decalage[x] = k + 1
            ax.annotate(r["Label"], (x, r["Accord_intra"]), xytext=(6, -3 - 9 * k), textcoords="offset points", fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(sortie, "2_boxplot_accord_intra.png"), dpi=150)
    plt.close(fig)

    # 3. ponts et frondeurs
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    for g in ordre:
        s = noeuds[(noeuds["Groupe"] == g)]
        ax.scatter(s["Accord_intra"], s["Accord_hors_groupe"], s=14, alpha=0.8, color=couleurs.get(g, GRIS), label=g)
    autres = noeuds[~noeuds["Groupe"].isin(ordre)]
    if len(autres):
        ax.scatter(autres["Accord_intra"], autres["Accord_hors_groupe"], s=14, color=GRIS, label="autres")
    ax.set_xlabel("accord moyen avec son groupe")
    ax.set_ylabel("accord moyen avec les autres groupes")
    ax.set_title("Chaque point est un député : en haut à gauche, les plus 'transversaux'")
    ax.legend(fontsize=7, ncol=2, markerscale=1.2)
    fig.tight_layout()
    fig.savefig(os.path.join(sortie, "3_ponts_et_frondeurs.png"), dpi=150)
    plt.close(fig)

    # 4. évolution de la cohésion
    chemin = os.path.join(d, "evolution_cohesion.csv")
    if os.path.exists(chemin):
        evo = pd.read_csv(chemin)
        # Axe des périodes explicite et chronologique : avec des étiquettes texte, matplotlib les range dans l'ordre
        # où elles apparaissent, ce qui désordonne l'axe quand plusieurs législatures sont dans le même graphique.
        periodes = sorted(evo["Periode"].unique())
        pos = {p: i for i, p in enumerate(periodes)}
        fig, ax = plt.subplots(figsize=(max(9, 0.55 * len(periodes) + 3), 5.5))
        for g in ordre:
            s = evo[evo["Groupe"] == g]
            if len(s) and s["Nb_deputes"].max() >= 5:   # on évite les micro-groupes, trop bruités
                # une valeur par période de l'axe ; les périodes sans donnée (NaN) interrompent la ligne
                y = s.set_index("Periode")["Accord_moyen_toutes_paires"].reindex(periodes)
                ax.plot(range(len(periodes)), y.values, marker="o", color=couleurs.get(g, GRIS), label=g)
        ax.set_xticks(range(len(periodes)), periodes)
        ax.set_ylabel("accord moyen intra-groupe")
        ax.set_title("Évolution de la cohésion des groupes (>= 5 députés)")
        ax.tick_params(axis="x", rotation=45)
        ax.legend(fontsize=7, ncol=2)
        fig.tight_layout()
        fig.savefig(os.path.join(sortie, "4_evolution_cohesion.png"), dpi=150)
        plt.close(fig)

    # 5. évolution de la modularité
    chemin = os.path.join(d, "evolution_modularite.csv")
    if os.path.exists(chemin):
        evm = pd.read_csv(chemin).sort_values("Periode")
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(evm["Periode"], evm["Modularite_groupes"], marker="o", label="partition = groupes politiques")
        ax.plot(evm["Periode"], evm["Modularite_louvain"], marker="s", label="meilleure partition (Louvain)")
        ax.set_ylabel("modularité")
        ax2 = ax.twinx()
        ax2.plot(evm["Periode"], evm["ARI_groupes_louvain"], color="gray", ls="--", marker="^", label="ARI groupes vs Louvain")
        ax2.set_ylabel("ARI (1 = communautés identiques aux groupes)")
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=8)
        ax.tick_params(axis="x", rotation=45)
        ax.set_title("Les groupes structurent-ils le réseau ? Évolution par période")
        fig.tight_layout()
        fig.savefig(os.path.join(sortie, "5_evolution_modularite.png"), dpi=150)
        plt.close(fig)

    # 6. sensibilité au seuil
    chemin = os.path.join(d, "modularite_selon_seuil.csv")
    if os.path.exists(chemin):
        sz = pd.read_csv(chemin)
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.plot(sz["Seuil_accord"], sz["Modularite_groupes"], marker="o", label="groupes politiques")
        ax.plot(sz["Seuil_accord"], sz["Modularite_louvain"], marker="s", label="Louvain")
        ax.set_xlabel("seuil d'accord minimal pour créer un lien")
        ax.set_ylabel("modularité")
        ax.set_title("Sensibilité de la modularité au seuil")
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(sortie, "6_modularite_selon_seuil.png"), dpi=150)
        plt.close(fig)

    print(f"Graphiques écrits dans {sortie}")


if __name__ == "__main__":
    main()