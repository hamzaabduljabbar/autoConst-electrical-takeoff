"""Step 3 - takeoff: measure exactly what the approved plan says, nothing else.

For every fixture type in the APPROVED plan (from assemblies.py):
  EACH   -> every tag on the plan sheets becomes one numbered point (sheet, x, y)
  LENGTH -> every tag is also paired with the fixture body drawn next to it
            (two parallel fixture-pen lines), and the body's length is converted
            to feet with the sheet scale

Each point is recorded in <out>-takeoff.json, so quantities are arithmetic on
recorded points, duplicates can be checked in code, and every number can be
clicked back to its spot on the drawing (viewer.py).

Confidence per point:
  HIGH    tag found, (LENGTH) one fixture body right next to it, sheet has one scale
  MEDIUM  body found but further away / sheet has several scales
  LOW     LENGTH tag with no body found - length unknown, needs a look

Usage: py scripts/takeoff.py <drawings.pdf> --out outputs/<name>
"""
import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

import sys
sys.path.insert(0, str(Path(__file__).parent))

SCALE_RE = re.compile(r"(\d+)?\s*(\d+)/(\d+)\"\s*=\s*1'-0\"|(\d+)\"\s*=\s*1'-0\"|1\s*:\s*(\d{2,4})")


def pt_per_ft(scale_text):
    """PDF points per real foot, from an imperial or metric scale note."""
    m = SCALE_RE.search(scale_text or "")
    if not m:
        return None
    whole, num, den, inches, ratio = m.groups()
    if num:
        paper_in = (int(whole) if whole else 0) + int(num) / int(den)
    elif inches:
        paper_in = int(inches)
    else:
        return 72 * 12 / int(ratio)  # 1:100 -> 1 ft real = 12/100 in paper
    return paper_in * 72


def tag_words(page):
    """Free-standing labels: words on lines of <= 3 words (skips notes paragraphs)."""
    words = page.get_text("words")
    per_line = Counter((w[5], w[6]) for w in words)  # notes paragraphs have long lines; tags stand alone
    return [w for w in words if per_line[(w[5], w[6])] <= 3]


def fixture_bodies(page, min_len_pt):
    """Axis-aligned fixture bodies = pairs of parallel dark lines 2-14pt apart.

    Returns rects. Lines are taken from the dark heavy pen (the fixture layer in
    lighting plans is drawn black and heavier than the grey architecture)."""
    segs = {"v": [], "h": []}
    for d in page.get_drawings():
        col = d.get("color")
        if not col or max(col) > 0.25 or (d.get("width") or 0) < 0.7:
            continue
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
            elif it[0] == "re":
                r = it[1]
                for a, b in ((r.tl, r.bl), (r.tr, r.br), (r.tl, r.tr), (r.bl, r.br)):
                    _add(segs, a, b, min_len_pt)
                continue
            else:
                continue
            _add(segs, a, b, min_len_pt)
    bodies = []
    for axis in ("v", "h"):
        lines = sorted(set(segs[axis]))  # (pos, lo, hi) rounded -> duplicates collapse
        by_span = defaultdict(list)
        for pos, lo, hi in lines:
            by_span[(lo, hi)].append(pos)
        for (lo, hi), poss in by_span.items():
            poss.sort()
            for p1, p2 in zip(poss, poss[1:]):
                if 2 <= p2 - p1 <= 14:
                    bodies.append(pymupdf.Rect(p1, lo, p2, hi) if axis == "v" else pymupdf.Rect(lo, p1, hi, p2))
    return bodies


def _add(segs, a, b, min_len):
    if abs(a.x - b.x) < 0.5 and abs(a.y - b.y) >= min_len:
        segs["v"].append((round(a.x, 1), round(min(a.y, b.y), 1), round(max(a.y, b.y), 1)))
    elif abs(a.y - b.y) < 0.5 and abs(a.x - b.x) >= min_len:
        segs["h"].append((round(a.y, 1), round(min(a.x, b.x), 1), round(max(a.x, b.x), 1)))


def rect_gap(a, b):
    dx = max(b.x0 - a.x1, a.x0 - b.x1, 0)
    dy = max(b.y0 - a.y1, a.y0 - b.y1, 0)
    return math.hypot(dx, dy)


def measure_sheet(doc, sheet, items, scales_on_sheet):
    page = doc[sheet["page"] - 1]
    ppf = pt_per_ft(sheet.get("scale") or "")
    words = [w for w in tag_words(page) if w[4] in items]
    seen, points = set(), []
    linear_marks = {m for m, it in items.items() if it["measure"] == "LENGTH"}
    bodies = fixture_bodies(page, (ppf or 18) * 0.9) if linear_marks & {w[4] for w in words} else []
    claimed = Counter()
    for w in words:
        key = (w[4], round(w[0]), round(w[1]))
        if key in seen:  # the same label drawn twice on top of itself
            continue
        seen.add(key)
        tb = pymupdf.Rect(w[:4])
        pt = {"mark": w[4], "sheet": sheet["sheet"], "page": sheet["page"],
              "x": round((tb.x0 + tb.x1) / 2, 1), "y": round((tb.y0 + tb.y1) / 2, 1),
              "tag_bbox": [round(v, 1) for v in tb], "confidence": "HIGH", "notes": []}
        if scales_on_sheet > 1:
            pt["confidence"] = "MEDIUM"
            pt["notes"].append("sheet has more than one scale")
        if w[4] in linear_marks:
            near = sorted(((rect_gap(tb, b), i) for i, b in enumerate(bodies)), key=lambda t: t[0])
            near = [t for t in near if t[0] <= 30]
            if not near or not ppf:
                pt["confidence"] = "LOW"
                pt["length_ft"] = None
                pt["notes"].append("no fixture body found next to the tag" if ppf else "no sheet scale")
            else:
                gap, i = near[0]
                b = bodies[i]
                claimed[i] += 1
                pt["body_bbox"] = [round(v, 1) for v in b]
                pt["body_index"] = i
                pt["length_ft"] = round(max(b.width, b.height) / ppf, 1)  # 0.1 ft: the box includes pen thickness
                if gap > 12:
                    pt["confidence"] = "MEDIUM"
                    pt["notes"].append(f"body is {gap:.0f}pt from the tag")
                if pt["length_ft"] < 1.5:  # no real 'lengths as indicated' run is this short
                    pt["confidence"] = "LOW"
                    pt["notes"].append("under 1.5 ft: probably a fixed-size symbol, not a scaled run")
        points.append(pt)
    for pt in points:  # one body claimed by two tags = one of them is measuring the wrong thing
        if claimed.get(pt.get("body_index"), 0) > 1:
            pt["confidence"] = "LOW"
            pt["notes"].append("fixture body shared with another tag")
    return points, ppf


LABEL_RE = re.compile(r"^[A-Z]{1,4}\d{1,3}[A-Z]{0,3}\d{0,2}$")


def symbol_pass(doc, plan_sheets, items, tag_points):
    """Replace tag-based points of EACH types with one point per fixture SYMBOL.

    Returns (points, method_by_type). Types with no trustworthy fingerprint keep
    their tag points, marked as counted by tags."""
    from symbols import components, learn, nearest, gap
    each = {m for m, it in items.items() if it["measure"] == "EACH"}
    shapes, labels = {}, {}
    for s in plan_sheets:
        page = doc[s["page"] - 1]
        shapes[s["page"]] = components(page)
        labels[s["page"]] = [(w[4], pymupdf.Rect(w[:4])) for w in tag_words(page) if LABEL_RE.match(w[4])]
    prints, blocked, votes = learn(shapes, labels, each)
    sig_types = defaultdict(set)
    for t, sigs in prints.items():
        for sig in sigs:
            sig_types[sig].add(t)

    method, out = {}, [p for p in tag_points if p["mark"] not in prints]
    for t in each:
        if t not in prints:
            method[t] = ("tags", blocked.get(t, "no symbol shape found next to its tags"))
    for t in prints:
        method[t] = ("symbols", sorted(prints[t]))
    sheet_name = {s["page"]: s["sheet"] for s in plan_sheets}
    for page, shp in shapes.items():
        tags_here = [(l, r) for l, r in labels[page] if l in prints]
        tag_shape = {}  # id(shape) -> label of the tag that points at it
        for l, r in tags_here:
            s = nearest(shp, r)
            if s and (s["sig"] in prints[l] or s["sig"] in sig_types):  # the tag is the authority on type
                tag_shape.setdefault(id(s), l)
        for s in shp:
            cands = sig_types.get(s["sig"])
            if not cands:
                continue
            c = s["rect"]
            centre = pymupdf.Rect(c.x0 + c.width / 2, c.y0 + c.height / 2, c.x0 + c.width / 2, c.y0 + c.height / 2)
            notes, conf = [], "HIGH"
            if id(s) in tag_shape:
                t = tag_shape[id(s)]
            elif any(l not in cands and l in items and nearest(shp, r) is s for l, r in labels[page]):
                continue  # it carries another scheduled type's tag (Bliss: XL2 drawn like XL1) - counted there
            else:
                conf = "MEDIUM"
                near = [(gap(r, centre), l) for l, r in tags_here if l in cands]
                if near:
                    d, t = min(near)
                    notes.append(f"untagged symbol - same shape as tagged {t}" +
                                 (f" (nearest {t} tag {d:.0f}pt away)" if len(cands) > 1 else ""))
                else:
                    t = max(cands, key=lambda k: votes[k][s["sig"]])
                    notes.append("untagged symbol, no tag of this type on the sheet")
            out.append({"mark": t, "sheet": sheet_name[page], "page": page,
                        "x": round(c.x0 + c.width / 2, 1), "y": round(c.y0 + c.height / 2, 1),
                        "symbol_bbox": [round(v, 1) for v in c], "tagged": id(s) in tag_shape,
                        "confidence": conf, "notes": notes})
        for l, r in tags_here:  # a tag whose shape didn't match: keep it so nothing silently drops
            s = nearest(shp, r)
            if not s or not (s["sig"] in prints[l] or s["sig"] in sig_types):
                pt = {"mark": l, "sheet": sheet_name[page], "page": page,
                      "x": round((r.x0 + r.x1) / 2, 1), "y": round((r.y0 + r.y1) / 2, 1),
                      "tag_bbox": [round(v, 1) for v in r], "tagged": True}
                if s and not sig_types.get(s["sig"]):  # a shape is there, just drawn differently
                    pt.update(confidence="MEDIUM", symbol_bbox=[round(v, 1) for v in s["rect"]],
                              notes=["symbol variant (e.g. arrows, emergency fill) - counted by its tag"])
                else:
                    pt.update(confidence="LOW", notes=["tag with no fixture symbol next to it - check the drawing"])
                out.append(pt)
    # one fixture = one point: two hits of the same type closer than 10pt are pieces of one symbol
    out.sort(key=lambda p: (not p.get("tagged"), p["confidence"] != "HIGH"))  # keep the best-evidenced one
    kept = []
    for p in out:
        if p["mark"] in prints and any(q["mark"] == p["mark"] and q["page"] == p["page"]
                                       and math.hypot(q["x"] - p["x"], q["y"] - p["y"]) < 10 for q in kept):
            continue
        kept.append(p)
    # a fingerprint most of the type's own tags don't sit on is usually the wrong shape
    # (a home-run arrowhead, a stray dash) - e.g. tags drawn INSIDE the fixture box.
    # Then the tags are the better count. Exception: exit signs - tags sit far away, but
    # the shapes found match the tag count, so that fingerprint is fine.
    n_tags = Counter(l for lab in labels.values() for l, _ in lab if l in prints)
    for t in prints:
        found = sum(1 for p in kept if p["mark"] == t and "symbol_bbox" in p and p["confidence"] != "LOW")
        linked = sum(1 for p in kept if p["mark"] == t and p.get("tagged") and p["confidence"] == "HIGH")
        if linked < 0.5 * n_tags[t] and abs(found - n_tags[t]) > 0.1 * n_tags[t]:
            kept = [p for p in kept if p["mark"] != t] + [p for p in tag_points if p["mark"] == t]
            method[t] = ("tags", f"learned symbol found only {found} shapes for {n_tags[t]} tags - counted by tags")
    # a tag with no matched symbol is text evidence only - not HIGH (HIGH = tag AND symbol agree)
    for p in kept:
        if method.get(p["mark"], ("",))[0] == "tags" and p["confidence"] == "HIGH" and "symbol_bbox" not in p:
            p["confidence"] = "MEDIUM"
            p["notes"] = list(p.get("notes", [])) + ["counted by its tag; fixture symbol not matched"]
        if items.get(p["mark"], {}).get("schedule_source") == "plans only":
            p["confidence"] = "LOW" if p["confidence"] == "LOW" else "MEDIUM"
            p["notes"] = list(p.get("notes", [])) + ["type not read from the schedule - confirm it exists"]
    return kept, method


def load_library(path):
    lib = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lib[r["assembly"]].append(r)
    return lib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    plan = json.loads(Path(a.out + "-plan.json").read_text(encoding="utf-8"))
    if plan["status"] != "approved":
        raise SystemExit("Plan is not approved yet. Review <out>-plan.md, then set status to approved.")
    register = json.loads(Path(a.out + "-drawings.json").read_text(encoding="utf-8"))
    sheets = {s["page"]: s for s in register["sheets"]}
    items = {it["mark"]: it for it in plan["items"] if it["include"]}
    doc = pymupdf.open(a.pdf)

    all_points, sheet_info = [], []
    for ps in plan["plan_sheets"]:
        s = sheets[ps["page"]]
        page_text = doc[s["page"] - 1].get_text()
        n_scales = len(set(re.findall(r"\d+/\d+\"\s*=\s*1'-0\"", page_text))) or 1
        pts, ppf = measure_sheet(doc, s, items, n_scales)
        all_points += pts
        sheet_info.append({"page": s["page"], "sheet": s["sheet"], "scale": s.get("scale"),
                           "pt_per_ft": ppf, "points": len(pts)})
    # every run of a LENGTH type the same tiny length = we measured a fixed-size symbol, not
    # the fixture. Length would be fiction: count it EACH, one per tag. Keep these types OUT
    # of the symbol pass: Bliss SL1B (4 ft SBL4) is drawn as two 1 ft end symbols joined by
    # its body line - counting untagged symbols there counts every fixture twice
    fixed = {}
    for mark, it in list(items.items()):
        if it["measure"] != "LENGTH":
            continue
        lens = [round(p["length_ft"]) for p in all_points if p["mark"] == mark and p.get("length_ft")]
        common = Counter(lens).most_common(1)
        same = common and common[0][1] / len(lens)
        if common and common[0][0] <= 2 and (len(lens) >= 5 and same >= 0.9 or len(lens) >= 2 and same == 1):
            fixed[mark] = (common[0][0], common[0][1], len(lens))
            items[mark] = {**it, "measure": "EACH"}
            for p in all_points:
                if p["mark"] == mark:
                    p["length_ft"] = None
                    p["notes"] = [n for n in p["notes"] if not n.startswith("under 1.5 ft")]
                    if p["confidence"] == "LOW" and not p["notes"]:
                        p["confidence"] = "MEDIUM"
                    p["notes"].append("fixed-size symbol, length not measured")
    all_points, method = symbol_pass(doc, [sheets[ps["page"]] for ps in plan["plan_sheets"]],
                                     {m: it for m, it in items.items() if m not in fixed}, all_points)
    tags_seen = Counter(p["mark"] for p in all_points if p.get("tagged") or "tag_bbox" in p)
    counters = Counter()
    for pt in all_points:
        counters[pt["mark"]] += 1
        pt["id"] = f"{pt['mark']}-{counters[pt['mark']]:03d}"

    lib = load_library(plan["library"])
    boq, derived = [], defaultdict(float)
    for mark, it in items.items():
        pts = [p for p in all_points if p["mark"] == mark]
        if not pts:
            continue
        conf = Counter(p["confidence"] for p in pts)
        row = {"mark": mark, "description": it["description"], "assembly": it["assembly"],
               "measure": it["measure"], "count": len(pts), "tags": tags_seen[mark],
               "counted_by": method.get(mark, ("tags+bodies",))[0],
               "sheets": dict(Counter(p["sheet"] for p in pts)),
               "high": conf["HIGH"], "medium": conf["MEDIUM"], "low": conf["LOW"]}
        if mark in fixed:
            ft, n, of = fixed[mark]
            row["notes"] = (f"body next to the tag measures a fixed {ft} ft on {n}/{of} tags - a symbol, "
                            "not the fixture: counted EACH, one per tag - take the unit length from the catalog number")
        elif it["measure"] == "LENGTH":
            row["length_ft"] = round(sum(p["length_ft"] or 0 for p in pts), 1)
            row["unmeasured"] = sum(1 for p in pts if p.get("length_ft") is None)
        boq.append(row)
        for li in lib[it["assembly"]]:
            q = float(li["qty_per_primary"]) * len(pts)
            unit = li["unit"]
            name = li["item"].replace("(as scheduled)", f"type {mark}")
            if row["measure"] == "LENGTH" and "(as scheduled)" in li["item"]:
                q, unit = row["length_ft"], "ft"
            derived[(name, unit, li["kind"], li["note"])] += q

    result = {"pdf": a.pdf, "plan": a.out + "-plan.json", "sheets": sheet_info,
              "boq": sorted(boq, key=lambda r: r["mark"]), "points": all_points,
              "derived": [{"item": k[0], "unit": k[1], "kind": k[2], "qty": round(v, 2), "note": k[3]}
                          for k, v in derived.items()],
              "not_counted": plan.get("not_counted", [])}
    Path(a.out + "-takeoff.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    write_excel(result, a.out + "-takeoff.xlsx")
    lows = sum(1 for p in all_points if p["confidence"] == "LOW")
    print(f"{len(all_points)} points | {len(boq)} types | "
          f"{sum(r.get('length_ft', 0) for r in boq):.0f} ft of linear | {lows} LOW to review -> {a.out}-takeoff.xlsx")


def write_excel(res, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    bold, low_fill = Font(bold=True), PatternFill("solid", fgColor="FDE2E1")
    ws = wb.active
    ws.title = "BOQ"
    ws.append(["Type", "Description", "Assembly", "Measured as", "Qty (each)", "Length (ft)",
               "HIGH", "MEDIUM", "LOW", "Sheets", "Notes"])
    for r in res["boq"]:
        ws.append([r["mark"], r["description"][:120], r["assembly"], r["measure"], r["count"],
                   r.get("length_ft"), r["high"], r["medium"], r["low"],
                   ", ".join(f"{k} ({v})" for k, v in r["sheets"].items()), r.get("notes", "")])
        if r["low"]:
            for c in ws[ws.max_row]:
                c.fill = low_fill
    d = wb.create_sheet("Derived (assemblies)")
    d.append(["Item", "Unit", "Kind", "Qty", "Note"])
    for r in res["derived"]:
        d.append([r["item"], r["unit"], r["kind"], r["qty"], r["note"]])
    m = wb.create_sheet("Marks")
    m.append(["ID", "Type", "Sheet", "Page", "x", "y", "Length (ft)", "Confidence", "Notes"])
    for p in res["points"]:
        m.append([p["id"], p["mark"], p["sheet"], p["page"], p["x"], p["y"], p.get("length_ft"),
                  p["confidence"], "; ".join(p["notes"])])
        if p["confidence"] == "LOW":
            for c in m[m.max_row]:
                c.fill = low_fill
    n = wb.create_sheet("Not counted")
    n.append(["Label on plans", "Times seen", "Why not counted"])
    for r in res["not_counted"]:
        n.append([r["label"], r["tags"], r["why"]])
    for sheet in wb.worksheets:
        for c in sheet[1]:
            c.font = bold
        sheet.freeze_panes = "A2"
    wb.save(path)


if __name__ == "__main__":
    main()
