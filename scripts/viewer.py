"""Step 5 - audit viewer: every counted fixture circled on the real drawing.

Writes <out>-viewer/index.html + one image per plan sheet. Open index.html in a
browser (no server needed):
  - left: the BOQ (type, qty, length, HIGH/MEDIUM/LOW, how it was counted)
  - right: the sheet with every mark of the selected type numbered on it
  - click a mark (or use < >) to zoom to it and see why it got its confidence
  - mark each one correct / wrong; the review is kept in the browser and can be
    exported as JSON for the next takeoff run

Usage: py scripts/viewer.py <drawings.pdf> --out outputs/<name> [--zoom 1.5]
"""
import argparse
import json
from pathlib import Path

import pymupdf

HTML = Path(__file__).with_name("viewer_template.html")


def displayed(p, m):
    """Point + boxes from unrotated PDF space into the displayed sheet's space."""
    if m == pymupdf.Identity:
        return p
    pt = pymupdf.Point(p["x"], p["y"]) * m
    p["x"], p["y"] = round(pt.x, 1), round(pt.y, 1)
    for k in ("tag_bbox", "body_bbox", "symbol_bbox"):
        if p.get(k):
            r = pymupdf.Rect(p[k]) * m
            r.normalize()
            p[k] = [round(v, 1) for v in r]
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--zoom", type=float, default=1.5)
    a = ap.parse_args()
    res = json.loads(Path(a.out + "-takeoff.json").read_text(encoding="utf-8"))
    vdir = Path(a.out + "-viewer")
    (vdir / "sheets").mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(a.pdf)
    sheets, turn = [], {}
    for s in res["sheets"]:
        page = doc[s["page"] - 1]
        # render as a PDF viewer shows it; takeoff coordinates are unrotated, so they are
        # turned with the same matrix below
        turn[s["page"]] = page.rotation_matrix
        pix = page.get_pixmap(matrix=pymupdf.Matrix(a.zoom, a.zoom), colorspace=pymupdf.csGRAY)
        name = f"sheets/{s['sheet']}.png"
        pix.save(str(vdir / name))
        sheets.append({**s, "img": name, "w": page.rect.width, "h": page.rect.height})
    data = {
        "title": Path(a.pdf).stem, "sheets": sheets, "boq": res["boq"], "derived": res["derived"],
        "not_counted": res["not_counted"],
        "points": [displayed({k: p.get(k) for k in ("id", "mark", "sheet", "page", "x", "y", "length_ft", "confidence",
                                                     "notes", "tag_bbox", "body_bbox", "symbol_bbox", "tagged")},
                             turn[p["page"]])
                   for p in res["points"]],
    }
    html = HTML.read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    # index.html opens straight from disk; page.html is the same body for publishing as an artifact
    head = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    (vdir / "index.html").write_text(head + html.replace("<style>", "</head><body><style>", 1) + "</body></html>", encoding="utf-8")
    (vdir / "page.html").write_text(html, encoding="utf-8")
    print(f"{len(sheets)} sheets, {len(data['points'])} marks -> {vdir / 'index.html'}")


if __name__ == "__main__":
    main()
