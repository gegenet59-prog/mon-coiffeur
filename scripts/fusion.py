"""Base des salons de Mon coiffeur : fusion SIRENE (INSEE) + OpenStreetMap, découpée en tuiles.

Deux modes :
  python3 scripts/fusion.py initial ETAB.parquet UL.parquet OSM.json
      Fusion de départ : filtre une seule fois le gros fichier SIRENE (salons 96.02A ouverts)
      et enregistre la base SIRENE dans data/sirene.json.
  python3 scripts/fusion.py hebdo OSM.json
      Mise à jour de la semaine : si la clé INSEE (variable SIRENE_CLE) est présente, on ne demande
      à l'API SIRENE que les salons créés / modifiés / fermés depuis la dernière fois.
Dans les deux cas, on refait ensuite les tuiles avec la dernière liste OpenStreetMap.
"""
import json, math, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import date, timedelta
from pathlib import Path
from pyproj import Transformer

RACINE = Path(__file__).resolve().parent.parent
DATA = RACINE / "data"
BASE_SIRENE = DATA / "sirene.json"
TUILES = DATA / "tuiles"
PAS = 0.25                 # taille d'une tuile en degrés (~28 km × 18 km)
CODE_COIFFURE = "96.02A"

# ---------- Coordonnées : Lambert (INSEE) → GPS ----------
PROJ = {"971": 5490, "972": 5490, "973": 2972, "974": 2975, "976": 4471}
_conv = {}
def vers_gps(x, y, commune):
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return None
    epsg = PROJ.get(str(commune or "")[:3], 2154)
    if epsg not in _conv:
        _conv[epsg] = Transformer.from_crs(epsg, 4326, always_xy=True)
    lo, la = _conv[epsg].transform(x, y)
    if not (-90 < la < 90 and -180 < lo < 180):
        return None
    return round(la, 6), round(lo, 6)

# ---------- Règles communes ----------
def propre(t):
    t = (t or "").strip()
    return "" if t in ("[ND]", "None", "nan") else t

def est_barbier(nom):
    return bool(re.search(r"barb", nom or "", re.I))

def garder(r):
    """Un salon SIRENE est gardé s'il est ouvert, diffusible, localisé, et pas un coiffeur à domicile.
    Coiffeur à domicile probable = entrepreneur individuel sans enseigne (déclaré à son adresse perso)."""
    if r.get("etat") != "A" or r.get("diffusion") == "P" or not r.get("pos") or not en_france(r["pos"]):
        return False
    return bool(r.get("enseigne")) or (r.get("cj") or "1000")[:1] != "1"

FORMES = r"(?i)^(sarl|sas|sasu|eurl|snc|sa|selarl|scp)\s+|\s+(sarl|sas|sasu|eurl|snc)$"
def nom_sirene(r):
    nom = r.get("enseigne") or r.get("denom_usuelle") or r.get("denom") or ""
    return re.sub(FORMES, "", nom).strip(" -") or nom

# Zones plausibles (métropole, Corse, outre-mer) : écarte les coordonnées aberrantes
ZONES = [(41.3, -5.3, 51.2, 9.7), (15.8, -61.9, 16.6, -60.9), (14.3, -61.3, 14.9, -60.7), (2.0, -54.7, 5.9, -51.5),
         (-21.5, 55.1, -20.8, 55.9), (-13.1, 44.9, -12.6, 45.4)]
def en_france(p):
    return any(a <= p[0] <= c and b <= p[1] <= d for a, b, c, d in ZONES)

PETITS = {"De", "Du", "Des", "La", "Le", "Les", "Et", "A", "Au", "Aux", "En", "Sur", "D", "L"}
def titre(t):
    """« RUE DE BETHUNE » → « Rue de Bethune » (SIRENE est souvent en majuscules)."""
    if not t.isupper():
        return t
    m = re.split(r"(\W+)", t.title())
    return "".join(x.lower() if i > 0 and x in PETITS else x for i, x in enumerate(m))

VOIES = {"AV": "Avenue", "BD": "Boulevard", "PL": "Place", "CHE": "Chemin", "RTE": "Route", "ALL": "Allée", "IMP": "Impasse",
         "CRS": "Cours", "SQ": "Square", "PAS": "Passage", "QUA": "Quartier", "FG": "Faubourg", "PRV": "Parvis", "PROM": "Promenade",
         "R": "Rue", "RUE": "Rue", "QUAI": "Quai", "CC": "Centre commercial", "RES": "Résidence", "LOT": "Lotissement", "ZA": "ZA", "ZI": "ZI"}
def adresse(num, typ, voie, cp, ville):
    typ = VOIES.get(propre(typ).upper(), propre(typ))
    rue = " ".join(x for x in (propre(num), typ.upper(), propre(voie)) if x)
    return ", ".join(x for x in (titre(rue), " ".join(x for x in (propre(cp), titre(propre(ville))) if x)) if x)

# ---------- 1. Fusion de départ depuis les fichiers stock ----------
def lire_stock(etab, ul):
    import duckdb
    req = f"""
      SELECT e.siret, e.etatAdministratifEtablissement AS etat, e.statutDiffusionEtablissement AS diffusion,
             e.enseigne1Etablissement AS enseigne, e.denominationUsuelleEtablissement AS denom_usuelle,
             u.denominationUniteLegale AS denom, u.categorieJuridiqueUniteLegale AS cj,
             e.numeroVoieEtablissement AS num, e.typeVoieEtablissement AS typ, e.libelleVoieEtablissement AS voie,
             e.codePostalEtablissement AS cp, e.libelleCommuneEtablissement AS ville, e.codeCommuneEtablissement AS commune,
             e.coordonneeLambertAbscisseEtablissement AS x, e.coordonneeLambertOrdonneeEtablissement AS y
      FROM read_parquet('{etab}') e LEFT JOIN read_parquet('{ul}') u USING (siren)
      WHERE e.activitePrincipaleEtablissement = '{CODE_COIFFURE}' AND e.etatAdministratifEtablissement = 'A'
    """
    con = duckdb.connect()
    cur = con.execute(req)
    cols = [c[0] for c in cur.description]
    for ligne in cur.fetchall():
        yield normaliser(dict(zip(cols, ligne)))

def normaliser(d):
    pos = vers_gps(d.get("x"), d.get("y"), d.get("commune"))
    return {
        "siret": str(d["siret"]), "etat": d.get("etat"), "diffusion": d.get("diffusion"),
        "enseigne": titre(propre(d.get("enseigne"))), "denom_usuelle": titre(propre(d.get("denom_usuelle"))),
        "denom": titre(propre(d.get("denom"))), "cj": propre(str(d.get("cj") or "")),
        "adresse": adresse(d.get("num"), d.get("typ"), d.get("voie"), d.get("cp"), d.get("ville")),
        "pos": list(pos) if pos else None,
    } | ({"activite": d["activite"]} if d.get("activite") else {})

# ---------- 2. Mise à jour de la semaine via l'API SIRENE (clé INSEE) ----------
API = "https://api.insee.fr/api-sirene/3.11/siret"
def lire_api(depuis, cle):
    q = f"periode(activitePrincipaleEtablissement:{CODE_COIFFURE}) AND dateDernierTraitementEtablissement:[{depuis}T00:00:00 TO *]"
    curseur = "*"
    while True:
        url = f"{API}?" + urllib.parse.urlencode({"q": q, "nombre": 1000, "curseur": curseur})
        req = urllib.request.Request(url, headers={"X-INSEE-Api-Key-Integration": cle, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:          # aucun changement
                return
            if e.code == 429:
                time.sleep(10); continue
            raise
        for e in j.get("etablissements", []):
            p = (e.get("periodesEtablissement") or [{}])[0]
            a = e.get("adresseEtablissement") or {}
            u = e.get("uniteLegale") or {}
            yield normaliser({
                "siret": e["siret"], "diffusion": e.get("statutDiffusionEtablissement"),
                "etat": p.get("etatAdministratifEtablissement"),
                "enseigne": p.get("enseigne1Etablissement"), "denom_usuelle": p.get("denominationUsuelleEtablissement"),
                "denom": u.get("denominationUniteLegale"), "cj": u.get("categorieJuridiqueUniteLegale"),
                "num": a.get("numeroVoieEtablissement"), "typ": a.get("typeVoieEtablissement"),
                "voie": a.get("libelleVoieEtablissement"), "cp": a.get("codePostalEtablissement"),
                "ville": a.get("libelleCommuneEtablissement"), "commune": a.get("codeCommuneEtablissement"),
                "x": a.get("coordonneeLambertAbscisseEtablissement"), "y": a.get("coordonneeLambertOrdonneeEtablissement"),
                "activite": p.get("activitePrincipaleEtablissement"),
            })
        suivant = (j.get("header") or {}).get("curseurSuivant")
        if not suivant or suivant == curseur:
            return
        curseur = suivant

# ---------- 3. OpenStreetMap ----------
def lien(u):
    u = u.split(";")[0].strip()
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u if re.match(r"https?://[^\s/]+\.[^\s]+", u) else ""

CACHE_OSM = DATA / "osm.json"
def lire_osm(chemin):
    """Liste OSM de la semaine ; si le téléchargement a raté, on garde celle de la semaine d'avant."""
    try:
        sortie = extraire_osm(chemin)
    except Exception as e:
        print(f"Liste OpenStreetMap illisible ({e})"); sortie = []
    if len(sortie) > 1000:
        CACHE_OSM.write_text(json.dumps(sortie, ensure_ascii=False, separators=(",", ":")))
        return sortie
    if CACHE_OSM.exists():
        print("Téléchargement OpenStreetMap raté : on reprend la liste de la semaine précédente")
        return [o | {"pos": tuple(o["pos"])} for o in json.load(open(CACHE_OSM))]
    return sortie

def extraire_osm(chemin):
    sortie = []
    for e in json.load(open(chemin)).get("elements", []):
        t = e.get("tags", {})
        la, lo = e.get("lat", e.get("center", {}).get("lat")), e.get("lon", e.get("center", {}).get("lon"))
        if la is None or not t.get("name"):
            continue
        barbier = t.get("shop") == "barber" or bool(re.search(r"barber|male|men", t.get("hairdresser", ""))) or est_barbier(t["name"])
        adr = ", ".join(x for x in (" ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x),
                                    " ".join(x for x in (t.get("addr:postcode"), t.get("addr:city")) if x)) if x)
        sortie.append({"nom": t["name"], "pos": (round(la, 6), round(lo, 6)), "barbier": barbier, "adresse": adr,
                       "horaires": t.get("opening_hours", ""), "tel": t.get("phone") or t.get("contact:phone", ""),
                       "site": lien(t.get("website") or t.get("contact:website") or t.get("url") or t.get("contact:facebook") or "")})
    return sortie

# ---------- 4. Fusion : un même salon présent dans les deux sources n'apparaît qu'une fois ----------
def mots(t):
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return {m for m in re.findall(r"[a-z0-9]{3,}", t) if m not in {"salon", "coiffure", "coiffeur", "sarl", "sas", "eurl", "the", "les"}}

def dist(a, b):
    k = math.cos(math.radians(a[0]))
    return math.hypot((a[0] - b[0]) * 111320, (a[1] - b[1]) * 111320 * k)

def fusionner(sirene, osm):
    grille = {}
    for i, o in enumerate(osm):
        grille.setdefault((round(o["pos"][0], 3), round(o["pos"][1], 3)), []).append(i)
    salons = list(osm)                     # OpenStreetMap d'abord : vrais noms, horaires, téléphone
    doublons = 0
    for r in sirene:
        if not garder(r):
            continue
        p, nom = tuple(r["pos"]), nom_sirene(r)
        proches = [osm[i] for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for i in grille.get((round(p[0] + dx / 1000, 3), round(p[1] + dy / 1000, 3)), [])]
        m = mots(nom)
        meme = next((o for o in proches if dist(p, o["pos"]) < 25 or (dist(p, o["pos"]) < 120 and m & mots(o["nom"]))), None)
        if meme:
            doublons += 1
            if not meme["adresse"]:
                meme["adresse"] = r["adresse"]       # OSM sans adresse : on prend celle de SIRENE
            continue
        salons.append({"nom": nom, "pos": p, "barbier": est_barbier(nom), "adresse": r["adresse"], "horaires": "", "tel": "", "site": ""})
    return salons, doublons

def ecrire_tuiles(salons):
    if TUILES.exists():
        for f in TUILES.glob("*.json"):
            f.unlink()
    TUILES.mkdir(parents=True, exist_ok=True)
    paquets = {}
    for s in salons:
        cle = f"{math.floor(s['pos'][0] / PAS)}_{math.floor(s['pos'][1] / PAS)}"
        paquets.setdefault(cle, []).append([s["nom"], s["pos"][0], s["pos"][1], int(s["barbier"]), s["adresse"], s["horaires"], s["tel"], s.get("site", "")])
    for cle, liste in paquets.items():
        (TUILES / f"{cle}.json").write_text(json.dumps(liste, ensure_ascii=False, separators=(",", ":")))
    (DATA / "index.json").write_text(json.dumps({"pas": PAS, "maj": date.today().isoformat(), "salons": len(salons),
                                                  "tuiles": sorted(paquets)}, separators=(",", ":")))
    return len(paquets)

# ---------- Programme ----------
def main():
    mode = sys.argv[1]
    DATA.mkdir(exist_ok=True)
    if mode == "initial":
        etab, ul, osm_chemin = sys.argv[2:5]
        base = {r["siret"]: r for r in lire_stock(etab, ul)}
        info = {"maj_sirene": date.today().isoformat()}
        print(flush=True); print(f"SIRENE : {len(base)} salons ouverts (code {CODE_COIFFURE})")
    else:
        osm_chemin = sys.argv[2]
        stock = json.load(open(BASE_SIRENE))
        base, info = stock["salons"], stock["info"]
        cle = os.environ.get("SIRENE_CLE", "").strip()
        if cle:
            depuis = (date.fromisoformat(info["maj_sirene"]) - timedelta(days=2)).isoformat()
            ajouts = fermetures = 0
            for r in lire_api(depuis, cle):
                if r.pop("activite", CODE_COIFFURE) != CODE_COIFFURE or r["etat"] != "A":
                    fermetures += base.pop(r["siret"], None) is not None
                else:
                    ajouts += r["siret"] not in base
                    base[r["siret"]] = r
            info["maj_sirene"] = date.today().isoformat()
            print(flush=True); print(f"SIRENE (API) depuis le {depuis} : {ajouts} nouveaux, {fermetures} fermés ou changés d'activité")
        else:
            print("Pas de clé SIRENE_CLE : base SIRENE inchangée, seul OpenStreetMap est mis à jour")
    BASE_SIRENE.write_text(json.dumps({"info": info, "salons": base}, ensure_ascii=False, separators=(",", ":")))
    gardes = sum(garder(r) for r in base.values())
    print(flush=True); print(f"SIRENE gardés (localisés, hors coiffeurs à domicile) : {gardes}")
    osm = lire_osm(osm_chemin)
    print(f"OpenStreetMap : {len(osm)} salons avec un nom")
    salons, doublons = fusionner(base.values(), osm)
    n = ecrire_tuiles(salons)
    print(f"Total : {len(salons)} salons ({doublons} doublons retirés), {n} tuiles")

if __name__ == "__main__":
    main()
