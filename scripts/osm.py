"""Transforme l'export Geofabrik (osmium export, GeoJSON ligne par ligne) en liste de salons OpenStreetMap.
Usage : python3 scripts/osm.py salons.geojsonseq sortie.json"""
import json, sys

def centre(g):
    pts = []
    def parcourir(c):
        if isinstance(c[0], (int, float)): pts.append(c)
        else:
            for x in c: parcourir(x)
    parcourir(g["coordinates"])
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)

elements = {}
for ligne in open(sys.argv[1], encoding="utf-8"):
    ligne = ligne.strip("\x1e \n")
    if not ligne:
        continue
    f = json.loads(ligne)
    t = f.get("properties", {})
    if t.get("shop") not in ("hairdresser", "barber") or not f.get("geometry"):
        continue
    la, lo = centre(f["geometry"])
    typ, ident = t.pop("@type", "node"), t.pop("@id", len(elements))
    elements[(typ, ident)] = {"type": typ, "id": ident, "lat": la, "lon": lo, "tags": t}
print(f"OpenStreetMap (Geofabrik) : {len(elements)} salons", flush=True)
json.dump({"elements": list(elements.values())}, open(sys.argv[2], "w"))
