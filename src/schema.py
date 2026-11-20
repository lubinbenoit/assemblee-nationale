"""Schéma de la base SQLite."""

SCHEMA = """
-- ------------------------------------------------------------------ référentiels
CREATE TABLE IF NOT EXISTS groupes (
    groupe_id TEXT PRIMARY KEY,        -- organe "PO…" de type GP (même clé que dans les scrutins)
    legislature INTEGER,
    libelle TEXT,                      -- nom complet
    sigle TEXT,                        -- libelleAbrev (ex. UDR)
    sigle_court TEXT,                  -- libelleAbrege
    couleur TEXT,                      -- ex. #3367A7
    date_debut TEXT,
    date_fin TEXT,                     -- NULL = groupe encore actif
    position_politique TEXT,
    famille TEXT,                      -- étiquette d'agrégation des analyses (data/correspondance_groupes.csv), sinon le sigle
    effectif INTEGER,                  -- champs Datan (groupes-active.csv), facultatifs
    score_rose REAL,
    score_cohesion REAL,
    score_participation REAL,
    score_majorite REAL,
    source TEXT                        -- amo30 / csv_groupes / supplementaire / inconnu
);
CREATE TABLE IF NOT EXISTS deputes (
    depute_id TEXT PRIMARY KEY,        -- acteur "PA…" (même clé que dans les scrutins)
    civilite TEXT,
    prenom TEXT,
    nom TEXT,
    nom_complet TEXT,                  -- "Prénom Nom" (libellé des nœuds Gephi)
    date_naissance TEXT,
    date_deces TEXT,
    profession TEXT,
    categorie_socio_pro TEXT,
    region TEXT,                       -- circonscription du mandat de député le plus récent
    departement TEXT,
    circonscription TEXT,
    en_fonction INTEGER,               -- 1 si le mandat de député est toujours ouvert
    groupe_actuel_id TEXT REFERENCES groupes(groupe_id),   -- NULL si plus en fonction
    groupe_actuel_csv TEXT,            -- sigle du groupe actuel dans le CSV Datan (pour contrôle)
    source TEXT                        -- amo30 / csv_deputes / inconnu
);
CREATE TABLE IF NOT EXISTS mandats (   -- uniquement les mandats de type GP (groupe) et ASSEMBLEE (député)
    mandat_uid TEXT PRIMARY KEY,       -- "PM…"
    acteur_id TEXT NOT NULL REFERENCES deputes(depute_id),
    organe_id TEXT,                    -- groupe (GP) ou organe Assemblée (ASSEMBLEE)
    type_organe TEXT,
    legislature INTEGER,
    date_debut TEXT,
    date_fin TEXT,                     -- NULL = mandat en cours
    qualite TEXT,
    region TEXT,
    departement TEXT,
    num_departement TEXT,
    num_circo TEXT,
    cause_mandat TEXT,
    cause_fin TEXT
);

-- ------------------------------------------------------------------ scrutins
CREATE TABLE IF NOT EXISTS scrutins (
    uid TEXT PRIMARY KEY,              -- ex. VTANR5L17V154
    numero INTEGER,
    legislature INTEGER,
    date TEXT,
    titre TEXT,
    type_objet TEXT,                   -- amendement, ensemble, article, motion_censure...
    type_vote TEXT,
    sort TEXT,                         -- adopté / rejeté
    nb_votants INTEGER,
    suffrages_exprimes INTEGER,
    pour INTEGER,                      -- décomptes officiels (sans les mises au point)
    contre INTEGER,
    abstentions INTEGER,
    non_votants INTEGER,
    n_corrections INTEGER,
    ecart_decompte INTEGER,
    fichier TEXT
);
CREATE TABLE IF NOT EXISTS votes (
    scrutin_uid TEXT NOT NULL REFERENCES scrutins(uid),
    depute_id TEXT NOT NULL REFERENCES deputes(depute_id),
    position TEXT NOT NULL,            -- pour / contre / abstention / non_votant (après mise au point)
    position_initiale TEXT NOT NULL,
    groupe_id TEXT REFERENCES groupes(groupe_id),   -- groupe AU MOMENT du vote
    par_delegation INTEGER,
    corrige INTEGER,                   -- 0 = aucun, 1 = mise au point, 2 = dysfonctionnement
    PRIMARY KEY (scrutin_uid, depute_id)
);
CREATE TABLE IF NOT EXISTS groupe_scrutin (
    scrutin_uid TEXT NOT NULL REFERENCES scrutins(uid),
    groupe_id TEXT NOT NULL REFERENCES groupes(groupe_id),
    nombre_membres INTEGER,
    position_majoritaire TEXT,
    PRIMARY KEY (scrutin_uid, groupe_id)
);
CREATE INDEX IF NOT EXISTS idx_votes_depute ON votes(depute_id);
CREATE INDEX IF NOT EXISTS idx_votes_groupe ON votes(groupe_id);
CREATE INDEX IF NOT EXISTS idx_scrutins_date ON scrutins(date);
CREATE INDEX IF NOT EXISTS idx_mandats_acteur ON mandats(acteur_id, type_organe);
"""


def creer(con):
    con.executescript(SCHEMA)


def vider_referentiels(con):
    with con:
        for t in ("mandats", "deputes", "groupes"):
            con.execute(f"DELETE FROM {t}")


def vider_scrutins(con):
    with con:
        for t in ("votes", "groupe_scrutin", "scrutins"):
            con.execute(f"DELETE FROM {t}")
