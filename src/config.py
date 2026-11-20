"""Chemins et paramètres communs. Tout est relatif à la racine du projet (le dossier qui contient src/)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- Données d'entrée (à ne jamais modifier à la main)
DATA = ROOT / "data"
DIR_SCRUTINS = DATA / "Scrutins.json"                      # recherche récursive (sous-dossier json/ inclus)
# Référentiel officiel AMO30 : tous acteurs, tous mandats, tous organes depuis la XIe législature
DIR_AMO = DATA / "AMO30_tous_acteurs_tous_mandats_tous_organes_historique.json" / "json"
DIR_AMO_ACTEURS = DIR_AMO / "acteur"
DIR_AMO_ORGANES = DIR_AMO / "organe"
# Compléments Datan (facultatifs, ne remplissent que ce qu'AMO30 ne donne pas)
CSV_DEPUTES = DATA / "liste_deputes_libre_office.csv"
CSV_GROUPES = DATA / "groupes-active.csv"
CSV_GROUPES_SUPP = DATA / "groupes-supplementaires.csv"   # optionnel : corrections manuelles (ex. couleur manquante)
CSV_CORRESPONDANCE = DATA / "correspondance_groupes.csv"  # optionnel : organe_id,famille (fusion de groupes)

# --- Données générées (peuvent être supprimées et régénérées)
OUTPUT = ROOT / "output"
DB = OUTPUT / "assemblee.sqlite"
DIR_GEPHI = OUTPUT / "gephi"
DIR_SERIES = OUTPUT / "series"
DIR_ANALYSE = OUTPUT / "analyse"

LEGISLATURES = [17]                  # législatures de scrutins à importer (0 = toutes ; ex. [16, 17] pour le quinquennat)
LEGISLATURE_MIN_REFERENTIEL = 16     # mandats et groupes conservés à partir de cette législature
