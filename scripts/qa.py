"""Step 1 - drawing pack QA.

Reads a PDF drawing set and writes a sheet register (drawings.json + drawings.md):
sheet number, title, discipline, scale, vector vs scanned, layers, rotation,
and a list of problems to fix before any takeoff runs.

Why: the takeoff picks sheets from this register, not from file names, and a
scanned or rotated sheet silently ruins counts.

Usage: py scripts/qa.py <drawings.pdf> --out outputs/<name>
"""
import argparse
import json
import re
from pathlib import Path

import pymupdf

SHEET_RE = re.compile(r"^(?:[A-Z]{1,3}-?\d{1,2}\.\d{2}|[A-Z]{1,4}-?\d{2,3}(?:\.\d{1,2})?[A-Z]?)$")
STAMP_RE = re.compile(r"\d{2,3}%\s+(?:CONSTRUCTION|DESIGN|CD|DD)\b[^\n]*|PROGRESS SET|PRELIMINARY|"
                      r"NOT FOR CONSTRUCTION|SUPERSEDED|\bVOID\b", re.I)
DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{2,4})\b")
SCALE_RE = re.compile(
    r"(\d+(?:\s\d+)?/\d+\"\s*=\s*1'-0\"|\d+\"\s*=\s*1'-0\"|1\s*:\s*\d{2,4}|NTS|AS INDICATED)", re.I)
DISCIPLINE = [  # (prefix regex, discipline)
    (r"^EL", "electrical-lighting"), (r"^EP", "electrical-power"), (r"^ER", "electrical-raceway"),
    (r"^ES", "electrical-site"), (r"^EG", "electrical-grounding"), (r"^E", "electrical"),
    (r"^TC|^T", "telecom"), (r"^FA|^F", "fire"), (r"^M", "mechanical"), (r"^P", "plumbing"),
    (r"^S", "structural"), (r"^A", "architectural"), (r"^C", "civil"), (r"^L", "landscape"),
    (r"^G", "general"),
]
TITLE_WORDS = re.compile(r"PLAN|SCHEDULE|DETAILS?|DIAGRAM|RISER|LEGEND|SECTION|ELEVATION|NOTES|SYMBOL", re.I)


def discipline_of(sheet, title=""):
    for rx, name in DISCIPLINE:
        if sheet and re.match(rx, sheet):
            if name == "electrical":  # offices number differently (E-201 vs EL101): the title decides
                t = (title or "").upper()
                if "LIGHTING" in t:
                    return "electrical-lighting"
                if "POWER" in t:
                    return "electrical-power"
            return name
    return "unknown"


def _display(page, w):
    """A word's box as the sheet is displayed. Text coordinates are unrotated, so on a
    page with a /Rotate flag they have to be turned before 'bottom right' means anything."""
    return pymupdf.Rect(w[:4]) * page.rotation_matrix


def _in_corner(page, w):
    r, b = page.rect, _display(page, w)
    cx, cy = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    return cx >= r.x0 + r.width * 0.80 and cy >= r.y0 + r.height * 0.75


def title_block_words(page, words=None):
    """Words in the bottom-right title-block zone (right 20%, bottom 25%) as displayed."""
    words = page.get_text("words") if words is None else words
    return [w for w in words if _in_corner(page, w)]


def sheet_number(page, words=None):
    """Largest word that looks like a sheet number, title-block corner first, then anywhere.

    Returns (number, sideways). Sideways = the number reads vertically on the displayed
    sheet, i.e. the sheet shows up turned 90 degrees in a PDF viewer."""
    words = page.get_text("words") if words is None else words
    cands = []
    for w in words:
        if SHEET_RE.match(w[4]):
            b = _display(page, w)
            cands.append((min(b.width, b.height), w[4], b.height > b.width * 1.2, _in_corner(page, w)))
    for in_corner in (True, False):
        pool = [c for c in cands if c[3] == in_corner or not in_corner]
        if pool:
            size, num, sideways, _ = max(pool)
            if size >= 12:  # sheet numbers are the biggest text in a title block
                return num, sideways
    return None, False


def sheet_title(words):
    """Join title-block lines that contain a drawing-title keyword."""
    lines = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w[4])
    titles = []
    for parts in lines.values():
        t = " ".join(parts).strip()
        if TITLE_WORDS.search(t) and len(t) < 60 and not re.search(r"E L E C|I S S U E", t):
            if t not in titles:
                titles.append(t)
    return " / ".join(titles[:2])


def image_coverage(page):
    area = page.rect.width * page.rect.height
    covered = 0.0
    # image placements from metadata only: decoding each image (get_image_rects) is slow
    # on big sets and fails on oversized scans
    for info in page.get_image_info():
        covered += (pymupdf.Rect(info["bbox"]) & page.rect).get_area()
    return min(covered / area, 1.0) if area else 0.0


def analyse(pdf_path):
    doc = pymupdf.open(pdf_path)
    ocgs = doc.get_ocgs() or {}
    sheets, seen = [], {}
    for i, page in enumerate(doc):
        words = page.get_text("words")
        text = " ".join(w[4] for w in words)  # one extraction per page; 420-page sets add up
        tb = title_block_words(page, words)
        num, sideways = sheet_number(page, words)
        scale = SCALE_RE.search(" ".join(w[4] for w in tb))
        if not scale or "NTS" in scale.group(1).upper():
            # no scale in the title block, or 'NTS' there while the views carry real scales:
            # take the most common explicit scale note on the sheet
            found = [m.group(1) for m in SCALE_RE.finditer(text) if not re.search("INDICATED|NTS", m.group(1).upper())]
            if found:
                scale = re.match(r"(.*)", max(set(found), key=found.count))
        content_len = len(page.read_contents() or b"")
        cov = image_coverage(page)
        chars = len(text.strip())
        if chars < 50 and cov > 0.5:
            kind = "scanned"
        elif cov > 0.5:
            kind = "vector+raster"  # text layer exists but a big image sits under it
        else:
            kind = "vector"
        issues = []
        if kind == "scanned":
            issues.append("scanned: no text layer, counts need OCR/vision and must be LOW confidence")
        if kind == "vector+raster":
            issues.append("large embedded image under the drawing (often a raster background): "
                          "anything drawn only in the picture can't be counted from vectors")
        if sideways:
            issues.append("sheet displays sideways (title block runs vertically): "
                          "rotate it in the PDF; the takeoff reads it, but people reviewing it will tilt their heads")
        if not num:
            issues.append("no sheet number found in the title block")
        elif num in seen:
            issues.append(f"duplicate sheet number (also page {seen[num]})")
        if num:
            seen.setdefault(num, i + 1)
        stamp = STAMP_RE.search(text)
        if stamp:
            issues.append(f"status stamp '{stamp.group(0).strip()[:60]}': check this is the current issue")
        dates = [(int(y) % 100, int(m), int(d)) for m, d, y in DATE_RE.findall(" ".join(w[4] for w in tb))
                 if 1 <= int(m) <= 12]
        sheets.append({
            "page": i + 1, "sheet": num, "title": sheet_title(tb),
            "discipline": discipline_of(num, sheet_title(tb)), "scale": scale.group(1) if scale else None,
            "kind": kind, "text_chars": chars, "image_cover": round(cov, 2),
            "content_kb": content_len // 1024, "rotation": page.rotation,
            "size_pt": [round(page.rect.width), round(page.rect.height)], "issues": issues,
            "_latest": max(dates) if dates else None,
        })
    # a sheet whose newest title-block date is older than most of the set = likely a stale revision
    latest = [r["_latest"] for r in sheets if r["_latest"]]
    if latest:
        common = max(set(latest), key=latest.count)
        for r in sheets:
            d = r.pop("_latest")
            if d and d < common:
                r["issues"].append("title-block date %02d/%02d/%02d older than most of the set (%02d/%02d/%02d): possible stale revision"
                                   % (d[1], d[2], d[0], common[1], common[2], common[0]))
            r["latest_date"] = "%02d/%02d/%02d" % (d[1], d[2], d[0]) if d else None
    return {"file": str(pdf_path), "pages": len(doc), "layers": len(ocgs),
            "layer_names": sorted({v["name"] for v in ocgs.values()}), "sheets": sheets}


def write_md(reg, path):
    s = reg["sheets"]
    by_disc = {}
    for r in s:
        by_disc.setdefault(r["discipline"], []).append(r)
    out = [f"# Drawing register: {Path(reg['file']).name}", "",
           f"{reg['pages']} pages, {reg['layers']} PDF layers. "
           f"{sum(r['kind'] == 'vector' for r in s)} vector, "
           f"{sum(r['kind'] == 'vector+raster' for r in s)} vector+raster, "
           f"{sum(r['kind'] == 'scanned' for r in s)} scanned.", ""]
    problems = [r for r in s if r["issues"]]
    out += ["## Fix before takeoff", ""]
    out += [f"- p{r['page']} {r['sheet'] or '?'}: " + "; ".join(r["issues"]) for r in problems] or ["- none"]
    out.append("")
    for disc in sorted(by_disc):
        out += [f"## {disc}", "", "| page | sheet | title | scale | kind |", "|---|---|---|---|---|"]
        out += [f"| {r['page']} | {r['sheet'] or '?'} | {r['title']} | {r['scale'] or ''} | {r['kind']} |"
                for r in by_disc[disc]]
        out.append("")
    Path(path).write_text("\n".join(out), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True, help="output prefix, e.g. outputs/bliss")
    a = ap.parse_args()
    reg = analyse(a.pdf)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out + "-drawings.json").write_text(json.dumps(reg, indent=1), encoding="utf-8")
    write_md(reg, a.out + "-drawings.md")
    s = reg["sheets"]
    elec = [r for r in s if r["discipline"].startswith("electrical")]
    print(f"{reg['pages']} pages | {len(elec)} electrical | "
          f"{sum(1 for r in s if r['issues'])} with issues -> {a.out}-drawings.md")


if __name__ == "__main__":
    main()
