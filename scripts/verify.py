"""Step 4 - visual verification: contact sheets of real crops, one per fixture type.

Each crop is the drawing around one counted point, with
  blue box = the tag that was counted
  red box  = the fixture body that was measured (LENGTH types)
and a caption with the point id and the measured length.

Claude (or a person) looks at these sheets and answers one question per crop:
is the box on the right thing? Wrong crops are written to <out>-review.json and
the takeoff is re-run with them corrected. Samples are spread across sheets and
include every non-HIGH point first.

Usage: py scripts/verify.py <drawings.pdf> --out outputs/<name> [--per-type 12] [--types BL1,SL1B]
"""
import argparse
import json
import random
from pathlib import Path

import pymupdf

CROP_PT = 70     # half-size of the crop around a point, in PDF points
CELL_PX = 360    # each crop is rendered into a cell this size
COLS = 4


def sample(points, n, seed=7):
    flagged = [p for p in points if p["confidence"] != "HIGH"]
    rest = [p for p in points if p["confidence"] == "HIGH"]
    random.Random(seed).shuffle(rest)
    by_sheet = {}
    for p in rest:  # round-robin across sheets so one sheet can't hide another's mistakes
        by_sheet.setdefault(p["sheet"], []).append(p)
    spread = []
    while any(by_sheet.values()) and len(spread) < n:
        for s in list(by_sheet):
            if by_sheet[s]:
                spread.append(by_sheet[s].pop())
    random.Random(seed).shuffle(flagged)
    # flagged points first (up to 3/4 of the sheet), then a spread of HIGH ones for contrast
    k = min(len(flagged), max(n * 3 // 4, n - len(spread)))
    return flagged[:k] + spread[:n - k]


def contact_sheet(doc, mark, pts, path):
    rows = (len(pts) + COLS - 1) // COLS
    sheet = pymupdf.open()
    W, H = COLS * CELL_PX, rows * (CELL_PX + 28)
    out = sheet.new_page(width=W, height=H)
    for i, p in enumerate(pts):
        page = doc[p["page"] - 1]
        cx, cy = p["x"], p["y"]
        boxes = ([p["tag_bbox"]] if p.get("tag_bbox") else []) + ([p["body_bbox"]] if p.get("body_bbox") else []) + ([p["symbol_bbox"]] if p.get("symbol_bbox") else [])
        if p.get("body_bbox"):  # centre on tag+body so long bodies stay in frame
            u = pymupdf.Rect(p["tag_bbox"]) | pymupdf.Rect(p["body_bbox"])
            cx, cy = (u.x0 + u.x1) / 2, (u.y0 + u.y1) / 2
        half = CROP_PT
        if p.get("body_bbox"):
            b = pymupdf.Rect(p["body_bbox"])
            half = max(CROP_PT, max(b.width, b.height) / 2 + 20)
        clip = pymupdf.Rect(cx - half, cy - half, cx + half, cy + half) & page.rect
        cell = pymupdf.Rect((i % COLS) * CELL_PX, (i // COLS) * (CELL_PX + 28),
                            (i % COLS + 1) * CELL_PX, (i // COLS) * (CELL_PX + 28) + CELL_PX)
        # render the crop as an image: pixmap clips use the page's displayed (rotated) space,
        # the same space the takeoff's coordinates are in
        out.insert_image(cell, pixmap=page.get_pixmap(clip=clip, dpi=int(72 * CELL_PX / clip.width)))
        sx, sy = cell.width / clip.width, cell.height / clip.height
        for j, bb in enumerate(boxes):
            r = pymupdf.Rect(bb)
            r = pymupdf.Rect(cell.x0 + (r.x0 - clip.x0) * sx - 3, cell.y0 + (r.y0 - clip.y0) * sy - 3,
                             cell.x0 + (r.x1 - clip.x0) * sx + 3, cell.y0 + (r.y1 - clip.y0) * sy + 3)
            out.draw_rect(r, color=(0.1, 0.35, 0.9) if (j == 0 and p.get("tag_bbox")) else (0.9, 0.1, 0.1), width=2.5)
        cap = f"{p['id']}  {p['sheet']}  " + (f"{p['length_ft']} ft  " if p.get("length_ft") else "") + p["confidence"]
        out.insert_text((cell.x0 + 6, cell.y1 + 18), cap, fontsize=13)
        out.draw_rect(cell, color=(0.6, 0.6, 0.6), width=0.8)
    out.get_pixmap(dpi=72).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-type", type=int, default=12)
    ap.add_argument("--types", default="")
    a = ap.parse_args()
    res = json.loads(Path(a.out + "-takeoff.json").read_text(encoding="utf-8"))
    doc = pymupdf.open(a.pdf)
    for pg in doc:  # text/vector coordinates are unrotated; render in the same space (in memory only)
        if pg.rotation:
            pg.set_rotation(0)
    want = set(filter(None, a.types.split(",")))
    vdir = Path(a.out + "-verify")
    vdir.mkdir(parents=True, exist_ok=True)
    made = []
    for row in res["boq"]:
        if want and row["mark"] not in want:
            continue
        pts = sample([p for p in res["points"] if p["mark"] == row["mark"]], a.per_type)
        path = vdir / f"{row['mark']}.png"
        contact_sheet(doc, row["mark"], pts, str(path))
        made.append((row["mark"], len(pts), str(path)))
    for m, n, p in made:
        print(f"{m}: {n} crops -> {p}")


if __name__ == "__main__":
    main()
