"""Télécharge les salons OpenStreetMap de France morceau par morceau (une seule grosse demande est refusée)."""
import json, sys, time, urllib.parse, urllib.request
SERVEURS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
# Métropole + Corse découpées en 4 × 4, puis les outre-mer
LA, LO = (41.3, 51.2), (-5.3, 9.7)
CASES = [(LA[0] + i * (LA[1] - LA[0]) / 4, LO[0] + j * (LO[1] - LO[0]) / 4,
          LA[0] + (i + 1) * (LA[1] - LA[0]) / 4, LO[0] + (j + 1) * (LO[1] - LO[0]) / 4) for i in range(4) for j in range(4)]
CASES += [(15.8, -61.9, 16.6, -60.9), (14.3, -61.3, 14.9, -60.7), (2.0, -54.7, 5.9, -51.5), (-21.5, 55.1, -20.8, 55.9), (-13.1, 44.9, -12.6, 45.4)]

def demande(bbox):
    b = ",".join(f"{x:.3f}" for x in bbox)
    q = f'[out:json][timeout:180];nwr["shop"~"^(hairdresser|barber)$"]({b});out center tags;'
    for essai in range(2):
        for s in SERVEURS:
            try:
                req = urllib.request.Request(s, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": "mon-coiffeur (GitHub Actions)"})
                with urllib.request.urlopen(req, timeout=240) as r:
                    return json.load(r)["elements"]
            except Exception as e:
                print(f"  {s.split('/')[2]} : {e}")
                time.sleep(5)
        time.sleep(30)
    return None

elements, ratees = {}, 0
for n, bbox in enumerate(CASES, 1):
    res = demande(bbox)
    if res is None:
        ratees += 1; print(f"Morceau {n}/{len(CASES)} : raté"); continue
    for e in res:
        elements[(e["type"], e["id"])] = e
    print(f"Morceau {n}/{len(CASES)} : {len(res)} salons")
    time.sleep(2)
print(f"OpenStreetMap : {len(elements)} salons, {ratees} morceau(x) raté(s)")
# Liste incomplète = on préfère garder celle de la semaine d'avant
json.dump({"elements": list(elements.values()) if ratees == 0 else []}, open(sys.argv[1], "w"))
