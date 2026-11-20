"""Fonctions utilitaires partagées par les modules de construction de la base."""
import csv
import io
import re
import unicodedata


def get(d, *cles):
    """Accès sécurisé à des dictionnaires imbriqués."""
    for k in cles:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def as_list(x):
    """None -> [], dict/valeur -> [x], liste -> liste sans les None (la conversion XML->JSON
    produit parfois [None, None] pour des balises vides, ou un dict au lieu d'une liste à 1 élément)."""
    if x is None:
        return []
    if isinstance(x, list):
        return [i for i in x if i is not None]
    return [x]


def val(x):
    """Valeur texte d'un champ issu de la conversion XML->JSON :
    'abc' -> 'abc' ; {'#text': 'abc', '@xsi:type': ...} -> 'abc' ; {'@xsi:nil': 'true'} -> None."""
    if x is None:
        return None
    if isinstance(x, dict):
        if str(x.get("@xsi:nil")).lower() == "true":
            return None
        x = x.get("#text")
        if x is None:
            return None
    s = str(x).strip()
    return s or None


def entier(x, defaut=None):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return defaut


def reel(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def norm(s):
    """Normalise un nom de colonne : minuscules, sans accents ni espaces superflus."""
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def lire_csv(chemin):
    """Lit un CSV (UTF-8 avec/sans BOM, sinon cp1252 ; séparateur , ; ou tabulation détecté).
    Retourne (liste de dicts, dict nom_normalisé -> nom_réel)."""
    with open(chemin, "rb") as f:
        brut = f.read()
    texte = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            texte = brut.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    entete = texte.splitlines()[0]
    delim = max([",", ";", "\t"], key=entete.count)
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=delim)
    lignes = list(lecteur)
    cols = {norm(c): c for c in (lecteur.fieldnames or [])}
    return lignes, cols


def champ(ligne, cols, *noms):
    """Valeur de la première colonne trouvée parmi `noms` (comparaison normalisée), None si vide."""
    for n in noms:
        c = cols.get(norm(n))
        if c is not None:
            v = ligne.get(c)
            return v.strip() if isinstance(v, str) and v.strip() != "" else None
    return None


def upsert(con, table, cle, ligne, ecraser):
    """Insère `ligne` (dict colonne -> valeur) ou met à jour la ligne existante de même clé.
    ecraser=True  : les valeurs non nulles de `ligne` remplacent l'existant (source prioritaire).
    ecraser=False : on ne remplit que les colonnes encore vides (source de complément)."""
    colonnes = list(ligne)
    maj = [c for c in colonnes if c != cle]
    if maj:
        modele = "{c} = COALESCE(excluded.{c}, {t}.{c})" if ecraser else "{c} = COALESCE({t}.{c}, excluded.{c})"
        action = "DO UPDATE SET " + ", ".join(modele.format(c=c, t=table) for c in maj)
    else:
        action = "DO NOTHING"
    con.execute(
        f"INSERT INTO {table} ({', '.join(colonnes)}) VALUES ({', '.join('?' * len(colonnes))}) "
        f"ON CONFLICT({cle}) {action}",
        [ligne[c] for c in colonnes],
    )
