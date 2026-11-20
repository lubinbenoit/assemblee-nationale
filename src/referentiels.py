"""
Import des référentiels (députés, groupes, mandats) dans la base, par ordre de priorité décroissante :

  1. AMO30 (data/AMO30_.../json/acteur et /organe) : source principale, couvre actifs ET anciens ;
  2. data/groupes-supplementaires.csv : corrections manuelles (ex. couleur d'un groupe dissous) ;
  3. data/groupes-active.csv et data/liste_deputes_libre_office.csv (Datan) : compléments, ils ne
     remplissent que les champs encore vides (ex. scores Datan, députés absents d'AMO30) ;
  4. data/correspondance_groupes.csv : regroupe plusieurs organes sous une même "famille" pour les analyses.

Les fichiers de data/ ne sont jamais modifiés. La colonne `source` indique d'où vient chaque ligne.
"""
import glob
import json
import os
import sys

from outils import as_list, champ, entier, get, lire_csv, reel, upsert, val
import schema


# ----------------------------------------------------------------------------- AMO30
def importer_organes(con, dossier, legislature_min):
    """Groupes politiques (codeType == GP) depuis AMO30/organe/*.json."""
    n = 0
    fichiers = glob.glob(os.path.join(dossier, "**", "*.json"), recursive=True)
    with con:
        for chemin in fichiers:
            try:
                with open(chemin, encoding="utf-8") as f:
                    o = json.load(f).get("organe")
            except Exception as e:
                print(f"[ERREUR] {os.path.basename(chemin)} : {e!r}", file=sys.stderr)
                continue
            if not isinstance(o, dict) or val(o.get("codeType")) != "GP":
                continue
            leg = entier(val(o.get("legislature")))
            if leg is not None and leg < legislature_min:
                continue
            vm = o.get("viMoDe") or {}
            upsert(con, "groupes", "groupe_id", {
                "groupe_id": val(o.get("uid")),
                "legislature": leg,
                "libelle": val(o.get("libelle")),
                "sigle": val(o.get("libelleAbrev")),
                "sigle_court": val(o.get("libelleAbrege")),
                "couleur": val(o.get("couleurAssociee")),
                "date_debut": val(vm.get("dateDebut")),
                "date_fin": val(vm.get("dateFin")),
                "position_politique": val(o.get("positionPolitique")),
                "source": "amo30",
            }, ecraser=True)
            n += 1
    return n


def importer_acteurs(con, dossier, legislature_min):
    """Députés et leurs mandats (groupe politique GP, mandat de député ASSEMBLEE) depuis AMO30/acteur/*.json.
    Un acteur sans aucun de ces mandats pour les législatures retenues est ignoré (ministre, sénateur...)."""
    fichiers = sorted(glob.glob(os.path.join(dossier, "**", "*.json"), recursive=True))
    n_dep = n_mandats = 0
    with con:
        for i, chemin in enumerate(fichiers, 1):
            try:
                with open(chemin, encoding="utf-8") as f:
                    a = json.load(f).get("acteur")
            except Exception as e:
                print(f"[ERREUR] {os.path.basename(chemin)} : {e!r}", file=sys.stderr)
                continue
            if not isinstance(a, dict):
                continue
            uid = val(a.get("uid"))
            mandats = []
            for m in as_list(get(a, "mandats", "mandat")):
                if not isinstance(m, dict):
                    continue
                type_organe = val(m.get("typeOrgane"))
                if type_organe not in ("GP", "ASSEMBLEE"):
                    continue
                leg = entier(val(m.get("legislature")))
                if leg is None or leg < legislature_min:
                    continue
                refs = as_list(get(m, "organes", "organeRef"))
                lieu = get(m, "election", "lieu") or {}
                mandats.append({
                    "mandat_uid": val(m.get("uid")),
                    "acteur_id": uid,
                    "organe_id": val(refs[0]) if refs else None,
                    "type_organe": type_organe,
                    "legislature": leg,
                    "date_debut": val(m.get("dateDebut")),
                    "date_fin": val(m.get("dateFin")),
                    "qualite": val(get(m, "infosQualite", "libQualite")),
                    "region": val(lieu.get("region")),
                    "departement": val(lieu.get("departement")),
                    "num_departement": val(lieu.get("numDepartement")),
                    "num_circo": val(lieu.get("numCirco")),
                    "cause_mandat": val(get(m, "election", "causeMandat")),
                    "cause_fin": val(get(m, "mandature", "causeFin")),
                })
            if not mandats:
                continue

            ident = get(a, "etatCivil", "ident") or {}
            prenom, nom = val(ident.get("prenom")), val(ident.get("nom"))
            assemblee = sorted((m for m in mandats if m["type_organe"] == "ASSEMBLEE"),
                               key=lambda m: m["date_debut"] or "")
            dernier = assemblee[-1] if assemblee else {}
            upsert(con, "deputes", "depute_id", {
                "depute_id": uid,
                "civilite": val(ident.get("civ")),
                "prenom": prenom,
                "nom": nom,
                "nom_complet": " ".join(x for x in (prenom, nom) if x) or None,
                "date_naissance": val(get(a, "etatCivil", "infoNaissance", "dateNais")),
                "date_deces": val(get(a, "etatCivil", "dateDeces")),
                "profession": val(get(a, "profession", "libelleCourant")),
                "categorie_socio_pro": val(get(a, "profession", "socProcINSEE", "catSocPro")),
                "region": dernier.get("region"),
                "departement": dernier.get("departement"),
                "circonscription": dernier.get("num_circo"),
                "source": "amo30",
            }, ecraser=True)
            n_dep += 1
            for m in mandats:
                cols = list(m)
                con.execute(f"INSERT OR REPLACE INTO mandats ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [m[c] for c in cols])
                n_mandats += 1
            if i % 2000 == 0:
                print(f"  ... {i}/{len(fichiers)} fichiers d'acteurs")
    return n_dep, n_mandats


# ----------------------------------------------------------------------------- CSV (compléments)
def _ligne_groupe(r, cols):
    return {
        "groupe_id": champ(r, cols, "id"),
        "legislature": entier(champ(r, cols, "legislature")),
        "libelle": champ(r, cols, "libelle"),
        "sigle": champ(r, cols, "libelleAbrev"),
        "sigle_court": champ(r, cols, "libelleAbrege"),
        "couleur": champ(r, cols, "couleurAssociee"),
        "date_debut": champ(r, cols, "dateDebut"),
        "date_fin": champ(r, cols, "dateFin"),
        "position_politique": champ(r, cols, "positionPolitique"),
    }


def completer_groupes_csv(con, chemin):
    """groupes-active.csv (Datan) : ne remplit que les champs vides ; apporte les scores Datan."""
    lignes, cols = lire_csv(chemin)
    n = 0
    with con:
        for r in lignes:
            ligne = _ligne_groupe(r, cols)
            if not ligne["groupe_id"]:
                continue
            ligne.update({
                "effectif": entier(champ(r, cols, "effectif")),
                "score_rose": reel(champ(r, cols, "scoreRose")),
                "score_cohesion": reel(champ(r, cols, "scoreCohesion", "socreCohesion")),  # faute possible dans l'en-tête
                "score_participation": reel(champ(r, cols, "scoreParticipation")),
                "score_majorite": reel(champ(r, cols, "scoreMajorite")),
                "source": "csv_groupes",
            })
            upsert(con, "groupes", "groupe_id", ligne, ecraser=False)
            n += 1
    return n


def appliquer_supplementaires(con, chemin):
    """groupes-supplementaires.csv : corrections manuelles, PRIORITAIRES sur tout le reste (champs non vides)."""
    lignes, cols = lire_csv(chemin)
    n = 0
    with con:
        for r in lignes:
            ligne = _ligne_groupe(r, cols)
            if not ligne["groupe_id"]:
                continue
            ligne["source"] = "supplementaire"
            upsert(con, "groupes", "groupe_id", ligne, ecraser=True)
            n += 1
    return n


def completer_deputes_csv(con, chemin):
    """liste_deputes_libre_office.csv (Datan) : ne remplit que les champs vides ;
    ajoute les députés absents d'AMO30 (ex. élus après l'export AMO30)."""
    lignes, cols = lire_csv(chemin)
    n = 0
    with con:
        for r in lignes:
            ident = champ(r, cols, "identifiant")
            if not ident:
                continue
            ident = ident if ident.upper().startswith("PA") else "PA" + ident
            prenom, nom = champ(r, cols, "Prénom"), champ(r, cols, "Nom")
            upsert(con, "deputes", "depute_id", {
                "depute_id": ident,
                "prenom": prenom,
                "nom": nom,
                "nom_complet": " ".join(x for x in (prenom, nom) if x) or None,
                "region": champ(r, cols, "Région"),
                "departement": champ(r, cols, "Département"),
                "circonscription": champ(r, cols, "Numéro de circonscription"),
                "profession": champ(r, cols, "Profession"),
                "groupe_actuel_csv": champ(r, cols, "Groupe politique (abrégé)"),
                "source": "csv_deputes",
            }, ecraser=False)
            n += 1
    return n


def appliquer_correspondance(con, chemin):
    """correspondance_groupes.csv (colonnes organe_id, famille) : étiquette d'agrégation des analyses."""
    lignes, cols = lire_csv(chemin)
    n, inconnus = 0, []
    with con:
        for r in lignes:
            gid, famille = champ(r, cols, "organe_id", "id"), champ(r, cols, "famille")
            if not gid or not famille:
                continue
            cur = con.execute("UPDATE groupes SET famille = ? WHERE groupe_id = ?", (famille, gid))
            if cur.rowcount:
                n += 1
            else:
                inconnus.append(gid)
    return n, inconnus


def importer_tout(con, args):
    """Réimporte tous les référentiels (repart de zéro pour deputes, groupes, mandats)."""
    schema.vider_referentiels(con)
    amo_ok = os.path.isdir(args.amo_acteurs) and os.path.isdir(args.amo_organes)
    if amo_ok:
        n_org = importer_organes(con, args.amo_organes, args.legislature_min_ref)
        n_dep, n_man = importer_acteurs(con, args.amo_acteurs, args.legislature_min_ref)
        print(f"AMO30 : {n_org} groupes, {n_dep} députés, {n_man} mandats (législature >= {args.legislature_min_ref})")
    else:
        print(f"[AVERTISSEMENT] dossiers AMO30 introuvables ({args.amo_acteurs}) : seuls les CSV seront utilisés",
              file=sys.stderr)
    for chemin, fonction, libelle in [
        (args.groupes_csv, completer_groupes_csv, "groupes Datan (complément)"),
        (args.deputes_csv, completer_deputes_csv, "députés Datan (complément)"),
        (args.groupes_supp, appliquer_supplementaires, "groupes supplémentaires (prioritaires)"),
    ]:
        if os.path.exists(chemin):
            print(f"CSV {libelle} : {fonction(con, chemin)} lignes")
    if os.path.exists(args.correspondance):
        n, inconnus = appliquer_correspondance(con, args.correspondance)
        print(f"Correspondance de groupes : {n} appliquées")
        if inconnus:
            print(f"[AVERTISSEMENT] identifiants inconnus dans la correspondance : {inconnus}", file=sys.stderr)


# ----------------------------------------------------------------------------- finalisation et contrôles
def finaliser(con, controles=True):
    """Étape idempotente, exécutée après chaque étape de construction."""
    with con:
        # identifiants présents dans les votes mais absents de tous les référentiels
        con.execute("INSERT OR IGNORE INTO deputes(depute_id, source) SELECT DISTINCT depute_id, 'inconnu' FROM votes")
        con.execute("INSERT OR IGNORE INTO groupes(groupe_id, source) "
                    "SELECT DISTINCT groupe_id, 'inconnu' FROM votes WHERE groupe_id IS NOT NULL")
        con.execute("UPDATE groupes SET famille = sigle WHERE famille IS NULL AND sigle IS NOT NULL")

        # groupe actuel et statut, déduits des mandats ouverts (dateFin vide) pour les députés d'AMO30
        con.execute("""UPDATE deputes SET groupe_actuel_id = (
                           SELECT m.organe_id FROM mandats m
                           WHERE m.acteur_id = deputes.depute_id AND m.type_organe = 'GP' AND m.date_fin IS NULL
                           ORDER BY m.date_debut DESC LIMIT 1)
                       WHERE source = 'amo30'""")
        con.execute("""UPDATE deputes SET en_fonction = EXISTS (
                           SELECT 1 FROM mandats m
                           WHERE m.acteur_id = deputes.depute_id AND m.type_organe = 'ASSEMBLEE' AND m.date_fin IS NULL)
                       WHERE source = 'amo30'""")
        # députés connus seulement par le CSV Datan (députés actuels) : groupe retrouvé par son sigle
        con.execute("""UPDATE deputes SET en_fonction = 1,
                           groupe_actuel_id = (
                               SELECT g.groupe_id FROM groupes g
                               WHERE lower(g.sigle) = lower(deputes.groupe_actuel_csv)
                                  OR lower(g.sigle_court) = lower(deputes.groupe_actuel_csv)
                               ORDER BY (g.date_fin IS NULL) DESC, g.date_debut DESC LIMIT 1)
                       WHERE source = 'csv_deputes'""")

        # votes enregistrés sans groupe (ajoutés par une mise au point) : groupe du mandat à la date du scrutin
        con.execute("""UPDATE votes SET groupe_id = (
                           SELECT m.organe_id FROM mandats m JOIN scrutins s ON s.uid = votes.scrutin_uid
                           WHERE m.acteur_id = votes.depute_id AND m.type_organe = 'GP'
                             AND m.date_debut <= s.date AND (m.date_fin IS NULL OR m.date_fin >= s.date)
                           ORDER BY m.date_debut DESC LIMIT 1)
                       WHERE groupe_id IS NULL""")
        con.execute("INSERT OR IGNORE INTO groupes(groupe_id, source) "
                    "SELECT DISTINCT groupe_id, 'inconnu' FROM votes WHERE groupe_id IS NOT NULL")

    if not controles:
        return
    q = lambda sql: con.execute(sql).fetchone()[0]
    print("\n--- Contrôles")
    for src, n in con.execute("SELECT source, COUNT(*) FROM deputes GROUP BY source ORDER BY 2 DESC"):
        print(f"  députés ({src}) : {n}")
    for src, n in con.execute("SELECT source, COUNT(*) FROM groupes GROUP BY source ORDER BY 2 DESC"):
        print(f"  groupes ({src}) : {n}")
    inconnus = q("SELECT COUNT(*) FROM deputes WHERE nom IS NULL AND depute_id IN (SELECT DISTINCT depute_id FROM votes)")
    if inconnus:
        print(f"  [INFO] {inconnus} députés ayant voté restent sans nom (voir deputes WHERE nom IS NULL)")
    sans_nom = [r[0] for r in con.execute(
        "SELECT groupe_id FROM groupes WHERE libelle IS NULL AND groupe_id IN (SELECT DISTINCT groupe_id FROM votes)")]
    if sans_nom:
        print(f"  [INFO] groupes ayant voté sans nom : {sans_nom}")
    sans_couleur = [r[0] for r in con.execute(
        "SELECT COALESCE(sigle, groupe_id) FROM groupes WHERE couleur IS NULL "
        "AND groupe_id IN (SELECT DISTINCT groupe_id FROM votes)")]
    if sans_couleur:
        print(f"  [INFO] groupes sans couleur (à renseigner dans data/groupes-supplementaires.csv) : {sans_couleur}")
    if q("SELECT COUNT(*) FROM mandats"):
        ecarts = q("""SELECT COUNT(*) FROM votes v
                      JOIN scrutins s ON s.uid = v.scrutin_uid
                      JOIN mandats m ON m.acteur_id = v.depute_id AND m.type_organe = 'GP'
                           AND m.date_debut <= s.date AND (m.date_fin IS NULL OR m.date_fin >= s.date)
                      WHERE v.groupe_id IS NOT NULL AND m.organe_id <> v.groupe_id""")
        total = q("SELECT COUNT(*) FROM votes")
        print(f"  votes dont le groupe diffère de celui du mandat à la date du scrutin : {ecarts} / {total}")
        diff = q("""SELECT COUNT(*) FROM deputes d JOIN groupes g ON g.groupe_id = d.groupe_actuel_id
                    WHERE d.groupe_actuel_csv IS NOT NULL
                      AND lower(d.groupe_actuel_csv) NOT IN (lower(COALESCE(g.sigle, '')), lower(COALESCE(g.sigle_court, '')))""")
        if diff:
            print(f"  députés dont le groupe actuel (CSV Datan) diffère de celui d'AMO30 : {diff}")
