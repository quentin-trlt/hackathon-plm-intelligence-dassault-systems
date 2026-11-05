"""Build the 3D shape profiles of the viewer (data/raw/geometry_reference.json) from the unchanged OEM XML.

Positions and shaft axes come from the XML. Shapes are parametric: sized from part names, XML masses and handbook
densities, or drawn by hand. They are not the OEM CAD. Requires numpy.

    python tools/viewer3d/build_data.py [xml_path] [output_path]
"""

import hashlib
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
XML = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data" / "raw" / "XPDMXML_ECAR_PLM.xml"
OUTPUT = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "data" / "raw" / "geometry_reference.json"
raw = XML.read_text()
root = ET.fromstring(raw)
for el in root.iter():
    el.tag = el.tag.split("}")[-1]
lines = {
    m.group(2): raw[: m.start()].count("\n") + 1
    for m in re.finditer(r'<(Product|ProductInst|Rep3DAggr) id="([^" ]+)"', raw)
}
prods = {}
for e in root.findall(".//Product"):
    key = int(e.get("id")[3:])
    prods[key] = dict(
        name=(e.findtext("Name") or "").removeprefix("ECAR-"),
        rev=e.findtext("RevisionName"),
        mass=float(e.findtext("WCGEquivalentComputedExt.V_WCG_Mass")),
        line=lines[e.get("id")],
        plm=e.findtext("ID"),
        xid=e.find("UniqueID").get("XID"),
        material=e.findtext("Material") or "",
        desc=e.findtext("Description") or "",
        maturity=e.findtext("Maturity"),
    )
inst = []
kids = defaultdict(list)
for e in root.findall(".//ProductInst"):
    i = dict(
        iid=int(e.get("id")[3:]),
        owned=int(e.findtext("Owned")[3:]),
        inst=int(e.findtext("Instancing")[3:]),
        R=np.array(list(map(float, e.findtext("RelativeTransformation3D/Rotation3D").split()))).reshape(3, 3).T,
        T=np.array(list(map(float, e.findtext("RelativeTransformation3D/Translation3D").split()))),
        line=lines[e.get("id")],
    )
    inst.append(i)
    kids[i["owned"]].append(i)
BLOCK = {1: "E-motor", 17: "Reducer", 51: "Housing"}
occ = []


def walk(node, R, T, chain, block):
    for i in kids[node]:
        R2, T2 = R @ i["R"], R @ i["T"] + T
        ch = chain + [(i["iid"], i["line"])]
        b = BLOCK.get(i["inst"], block)
        if i["inst"] in kids:
            walk(i["inst"], R2, T2, ch, b)
        else:
            occ.append(dict(pid=i["inst"], R=R2, T=T2, chain=ch, block=b))


walk(0, np.eye(3), np.zeros(3), [], None)
total = sum(prods[o["pid"]]["mass"] for o in occ)
assert len(occ) == 70 and abs(total - 100.1) < 1e-9

# Typical handbook densities (kg/m3), non-PLM, used only to size schematic shapes.
DENSITY = {
    "Bearing steel": 7810,
    "Alloy steel 42CrMo4": 7850,
    "EN AW-6082": 2700,
    "Electrical steel": 7650,
    "Copper": 8940,
    "Ductile cast iron": 7100,
    "16MnCr5": 7850,
    "20MnCr5": 7850,
    "AlSi9Cu3": 2760,
    "AlSi10Mg": 2680,
    "S355J2": 7850,
    "PA66-GF30": 1350,
    "FKM": 1850,
    "Steel class 10.9": 7850,
}


def density(material):
    for k, v in DENSITY.items():
        if k in material:
            return v
    raise KeyError(material)


# Shaft axes, all parallel to Y, read from the bearing positions in the XML (x, z in metres)
A1 = tuple(next(o["T"][[0, 2]] for o in occ if o["pid"] == 30))  # high-speed shaft / motor axis (HSH_BEA1/2/3)
A2 = tuple(next(o["T"][[0, 2]] for o in occ if o["pid"] == 38))  # intermediate shaft (ISH_BEA1/2)
A3 = tuple(next(o["T"][[0, 2]] for o in occ if o["pid"] == 34))  # low-speed shaft / differential (LSH_BEA1/2)
d12 = math.dist(A1, A2)
d23 = math.dist(A2, A3)

# Gear radii: equal thickness, radius ~ sqrt(mass), meshing pairs touch (centre distances from XML)
mA, mC, mB, mD = [prods[n]["mass"] for n in (42, 44, 46, 48)]
rA = d12 / (1 + math.sqrt(mC / mA))
rC = d12 - rA
rB = d23 / (1 + math.sqrt(mD / mB))
rD = d23 - rB


def thick(m, r, rho=7850):
    return m / (rho * math.pi * r * r)


# Housing outline: convex hull of the D10 L155 flange screws (world x, z), pushed outwards
flange = [o["T"] for o in occ if o["pid"] == 78]


def hull(pts):
    pts = sorted(set(pts))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


fl_pts = [(round(p[0], 4), round(p[2], 4)) for p in flange]
# lower mounting screws D10 L115 also bound the gearbox
fl_pts += [(round(o["T"][0], 4), round(o["T"][2], 4)) for o in occ if o["pid"] == 69]
h = hull(fl_pts)
cx = sum(p[0] for p in h) / len(h)
cz = sum(p[1] for p in h) / len(h)
outline = []
for x, z in h:
    dx, dz = x - cx, z - cz
    n = math.hypot(dx, dz)
    outline.append([round(x + dx / n * 0.018, 4), round(z + dz / n * 0.018, 4)])

# duplicate positions (same product, same world position)
seen = defaultdict(list)
for o in occ:
    seen[(o["pid"], tuple(np.round(o["T"], 9)))].append(o["chain"][-2][0] if len(o["chain"]) > 1 else o["chain"][-1][0])
dups = {k: v for k, v in seen.items() if len(v) > 1}

Y = [0, 1, 0]


def axis(R, local):
    v = R @ np.array(local)
    return [float(c) for c in v]


def P(x, y, z):
    return [float(x), float(y), float(z)]


# bearing positions along each axis (from the XML)
by = {pid: occ_[0]["T"][1] for pid in (3, 30, 32, 34, 36, 38, 40) for occ_ in [[o for o in occ if o["pid"] == pid]]}

EXACT, EST = "xml", "estimated"
BEARING = {  # bore radius, outer radius, width (m) -- sized so ring volume ~ mass / density
    3: (0.020, None, 0.020),
    30: (0.020, None, 0.024),
    32: (0.020, None, 0.022),
    34: (0.030, None, 0.028),
    36: (0.030, None, 0.026),
    38: (0.022, None, 0.028),
    40: (0.022, None, 0.026),
}
items = []
for k, o in enumerate(occ):
    pid, T, R = o["pid"], o["T"], o["R"]
    p = prods[pid]
    name = p["name"].replace("3D_", "").replace("3D ", "").strip()
    rho = density(p["material"])
    vol = p["mass"] / rho
    place, note, sh = EXACT, "", None
    x, y, z = T
    if pid in BEARING:
        ri, _, w = BEARING[pid]
        # rings + balls hold ~60% of the envelope volume
        ro = math.sqrt(ri * ri + vol / (0.6 * math.pi * w))
        sh = dict(t="bearing", ri=ri, ro=ro, w=w, pos=P(x, y, z), ax=Y)
        note = f"Deep-groove ball bearing {ri * 2000:.0f} x {ro * 2000:.0f} x {w * 1000:.0f} mm, sized from mass"
    elif pid == 5:
        yb1, yb2, yb3 = by[30], by[32], by[3]
        segs = [
            (-0.205, yb1 - 0.014, 0.016),
            (yb1 - 0.014, yb1 + 0.014, 0.020),
            (yb1 + 0.014, -0.072, 0.024),
            (-0.072, -0.028, 0.024),
            (-0.028, yb2 - 0.012, 0.026),
            (yb2 - 0.012, yb2 + 0.012, 0.020),
            (yb2 + 0.012, 0.10, 0.026),
            (0.10, 0.25, 0.034),
            (0.25, yb3 - 0.012, 0.026),
            (yb3 - 0.012, yb3 + 0.012, 0.020),
            (yb3 + 0.012, 0.345, 0.016),
        ]
        sh = dict(t="shaft", segs=segs, splines=[[-0.072, -0.028, 0.024, 0.027]], pos=P(A1[0], 0, A1[1]), ax=Y)
        place, note = EST, "On the motor axis, journals at the 3 bearing positions from the XML. Profile stylised"
    elif pid == 50:
        yb1, yb2 = by[38], by[40]
        segs = [
            (-0.2, yb1 - 0.015, 0.017),
            (yb1 - 0.015, yb1 + 0.015, 0.022),
            (yb1 + 0.015, -0.138, 0.027),
            (-0.138, -0.092, 0.030),
            (-0.092, -0.072, 0.034),
            (-0.072, -0.028, 0.030),
            (-0.028, yb2 - 0.014, 0.027),
            (yb2 - 0.014, yb2 + 0.014, 0.022),
            (yb2 + 0.014, 0.024, 0.017),
        ]
        sh = dict(
            t="shaft",
            segs=segs,
            splines=[[-0.138, -0.092, 0.030, 0.032], [-0.072, -0.028, 0.030, 0.032]],
            pos=P(A2[0], 0, A2[1]),
            ax=Y,
        )
        place, note = (
            EST,
            "On the intermediate axis, journals at the 2 bearing positions from the XML. Profile stylised",
        )
    elif pid == 7:
        sh = dict(t="jacket", ri=0.113, ro=0.124, h=0.17, pos=P(x, y, z), ax=Y)
        note = "Cooling jacket around the stator, at its XML position"
    elif pid == 14:
        L = 0.13
        ri = 0.070
        ro = math.sqrt(ri * ri + vol / (0.82 * math.pi * L))
        sh = dict(t="stator", ri=ri, ro=ro, h=L, slots=48, pos=P(x, y, z), ax=Y)
        note = f"Laminated core {ro * 2000:.0f}/{ri * 2000:.0f} mm x {L * 1000:.0f} mm, 48 slots (stylised)"
    elif pid == 16:
        sh = dict(t="hairpins", r=0.088, n=48, gap=0.065, pos=P(x, y, z), ax=Y)
        note = "End windings drawn as copper hairpins on both sides of the core (stylised)"
    elif pid == 10:
        sh = dict(t="tooth", pos=P(x, y, z), ax=Y, r=0.085)
        place, note = (
            EST,
            "The XML identifies this single tooth as required for meshing; its mass remains in the source total",
        )
    elif pid == 12:
        sh = dict(t="onecoil", pos=P(x, y, z), ax=Y, r=0.088, gap=0.065)
        place, note = (
            EST,
            "Single coil alongside the full coils; possible modelling reference, pending OEM confirmation",
        )
    elif pid in (42, 44, 46, 48):
        r = {42: rA, 44: rC, 46: rB, 48: rD}[pid]
        th = thick(p["mass"], r)
        mod = 0.0025 if pid in (42, 44) else 0.003
        hand = {42: 1, 44: -1, 46: 1, 48: -1}[pid]
        web = r > 0.06
        bore = {42: 0.027, 44: 0.032, 46: 0.032, 48: 0.064}[pid]
        sh = dict(
            t="gear",
            r=r,
            h=th,
            z=int(round(2 * r / mod)),
            m=mod,
            helix=hand * 20,
            web=web,
            bore=bore,
            pos=P(x, y, z),
            ax=Y,
        )
        note = (
            f"Helical gear, {int(round(2 * r / mod))} teeth. Radius set so meshing gears touch at the shaft distances "
            f"from the XML ({d12 * 1000:.0f} / {d23 * 1000:.0f} mm); width {th * 1000:.0f} mm from mass"
        )
    elif pid == 20:
        sh = dict(t="diffcase", pos=P(A3[0], -0.09, A3[1]), ax=Y)
        place, note = EST, "Centred on the differential axis between the side gears (XML); casing origin is offset"
    elif pid == 22:
        r = 0.011
        L = vol / (math.pi * r * r)
        sh = dict(t="pin", r=r, h=L, pos=P(x, y, z), ax=axis(R, [0, 1, 0]))
        note = f"Cross pin {L * 1000:.0f} mm, position and orientation from the XML"
    elif pid == 24:
        sh = dict(t="bevel", r=0.031, h=0.024, z=11, pos=P(x, y, z), ax=axis(R, [0, 0, 1]))
        note = "Bevel planet gear, position and orientation from the XML"
    elif pid == 26:
        toward = 1 if y < -0.09 else -1
        sh = dict(t="sidegear", r=0.036, h=0.024, z=16, pos=P(x, y, z), ax=[0, toward, 0])
        note = "Side gear with output spline, position from the XML"
    elif pid in (69, 71, 73, 74, 76, 78):
        m = re.search(r"D(\d+) L(\d+)", p["name"])
        if m:
            d, L = int(m.group(1)) / 1000, int(m.group(2)) / 1000
            note = f"Hex flange bolt M{int(d * 1000)} x {int(L * 1000)} mm, from the part name"
        else:
            d = 0.008
            L = vol / (math.pi * (d / 2) ** 2) * 0.85
            note = f"Hex flange bolt, M8 assumed, length {L * 1000:.0f} mm from mass"
        sh = dict(t="bolt", d=d, L=L, pos=P(x, y, z), ax=[0, -1, 0] if y > 0.2 else Y)
    elif pid == 67:
        sh = dict(t="elbow", pos=P(x, y, z), ax=Y)
        note = "90 degree coolant elbow at its XML position"
    elif pid == 59:
        sh = dict(t="bearingseat", ri=0.045, ro=0.072, h=0.03, pos=P(x, y, z), ax=Y)
        note = "Bearing seat on the differential axis, position from the XML"
    elif pid == 53:
        sh = dict(t="seal", ri=0.022, ro=0.034, h=0.009, pos=P(x, y, z), ax=Y)
        note = "Lip seal at its XML origin (housing origin, on the differential axis)"
    elif pid in (55, 57):
        y0, y1 = (-0.236, -0.171) if pid == 55 else (-0.169, 0.03)
        bosses = [[round(float(q[0]), 4), round(float(q[2]), 4)] for q in flange]
        bores = [[A1[0], A1[1], 0.036], [A2[0], A2[1], 0.038], [A3[0], A3[1], 0.05]]
        sh = dict(
            t="gearcase",
            outline=outline,
            y0=y0,
            y1=y1,
            bosses=bosses,
            bores=bores,
            side=-1 if pid == 55 else 1,
            op=0.18,
        )
        place, note = EST, "Modelled in place: outline from the flange bolt pattern (XML), split at the flange plane"
    elif pid == 61:
        sh = dict(t="motorcase", ri=0.126, ro=0.136, y0=0.02, y1=0.336, pos=P(A1[0], 0, A1[1]), ax=Y, op=0.2)
        place, note = EST, "Modelled in place: motor housing drawn around the motor axis"
    elif pid == 63:
        sh = dict(t="plate", ri=0.03, ro=0.13, h=0.012, pos=P(A1[0], y + 0.008, A1[1]), ax=Y)
        place, note = EST, "Axial position from the XML (y); drawn on the motor axis"
    elif pid == 65:
        sh = dict(t="bracket", pos=P(A3[0] + 0.02, -0.08, A3[1] - 0.17), ax=Y)
        place, note = EST, "Reconstructed mounting bracket under the reducer; shape and placement are illustrative"
    else:
        raise ValueError(pid)
    parent_name = None
    for iid, _ in reversed(o["chain"][:-1]):
        tgt = next(i for i in inst if i["iid"] == iid)["inst"]
        if prods[tgt]["name"].startswith("EI_"):
            parent_name = prods[tgt]["name"].replace("EI_", "").strip()
            break
    items.append(
        dict(
            k=k,
            pid=f"ID_{pid}",
            name=name,
            ei=parent_name,
            block=o["block"],
            rev=p["rev"],
            mass=p["mass"],
            material=p["material"],
            plm=p["plm"],
            xid=p["xid"],
            desc=p["desc"],
            pline=p["line"],
            chain=" > ".join(f"ID_{a} (line {b})" for a, b in o["chain"]),
            own=f"ID_{o['chain'][-2][0]} (line {o['chain'][-2][1]})"
            if len(o["chain"]) > 1
            else f"ID_{o['chain'][0][0]}",
            maturity=p["maturity"],
            source_rotation=R.tolist(),
            occurrence_path=[f"ID_{a}" for a, _ in o["chain"]],
            pos=P(*T),
            place=place,
            note=note,
            shape=sh,
            artefact=pid in (10, 12),
            dup=len(o["chain"]) > 1 and any(o["chain"][-2][0] in v for kk, v in dups.items() if kk[0] == pid),
        )
    )

qty = defaultdict(int)
for it in items:
    qty[it["pid"]] += 1
for it in items:
    it["qty"] = qty[it["pid"]]

out = dict(
    block_product_ids={name: prods[n]["plm"] for n, name in BLOCK.items()},
    root=dict(
        name=prods[0]["name"],
        rev=prods[0]["rev"],
        maturity=prods[0]["maturity"],
        mass=prods[0]["mass"],
        pid="ID_0",
        pline=prods[0]["line"],
    ),
    source=dict(
        file=XML.name,
        sha256=hashlib.sha256(XML.read_bytes()).hexdigest(),
        products=len(prods),
        instances=len(inst),
        representation_count=len(root.findall(".//Rep3DAggr")),
    ),
    items=items,
    total_mass=round(total, 3),
    n_occ=len(occ),
    n_parts=len(qty),
    axes=dict(d12=float(d12), d23=float(d23)),
    dups=[
        dict(pid=f"ID_{k[0]}", name=prods[k[0]]["name"].replace("3D_", ""), instances=[f"ID_{x}" for x in v])
        for k, v in dups.items()
    ],
)
OUTPUT.write_text(json.dumps(out, separators=(",", ":")))
print("occ", len(occ), "parts", len(qty), "mass", round(total, 3), "est", sum(i["place"] == EST for i in items))
print(
    "bearing ro mm",
    {
        k: round(math.sqrt(v[0] ** 2 + (prods[k]["mass"] / 7810) / (0.6 * math.pi * v[2])) * 1000, 1)
        for k, v in BEARING.items()
    },
)
