"""Step 2 - assemblies: plan what to measure BEFORE measuring it.

Reads the drawing register (from qa.py), the luminaire/fixture schedule and the
assembly library, then writes a checklist of exactly what the takeoff will count:

  - every fixture type in the schedule, with its description, wattage, mounting
  - how it is measured: EACH (count tags) or LENGTH (linear runs, priced per ft)
  - which assembly from the library it maps to (drives the derived quantities)
  - a preview count per sheet, so you can sanity-check before the real run
  - tag-like labels on the plans that are NOT in the schedule (not counted)

Nothing is measured until a person approves the checklist (status: proposed ->
approved). Counting only scheduled types is what stops the takeoff counting
circuit numbers, sensor labels or room tags as lights.

Usage: py scripts/assemblies.py <drawings.pdf> --out outputs/<name> [--library assemblies/electrical-library.csv]
"""
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

TAG_RE = re.compile(r"^[A-Z]{1,4}\d{1,3}[A-Z]{0,3}\d{0,2}$")  # L06, AL17A, SL1BE, CS07AE1
ALPHA_MARK_RE = re.compile(r"^[A-Z]{2,3}$")  # EX, EXD: accepted only if the schedule lists them
LINEAR_RE = re.compile(r"\bLINEAR\b|/\s*FT\b|PER (?:LINEAR )?FOOT|LENGTHS? AS INDICATED|CONTINUOUS ROW", re.I)
# priced by length only when the schedule says so; "2 FOOT LONG" is a fixed unit you count
BY_LENGTH_RE = re.compile(r"/\s*FT\b|PER (?:LINEAR )?FOOT|LENGTHS? AS (?:INDICATED|SHOWN)|CONTINUOUS (?:ROW|RUN)S?|"
                          r"RUN LENGTHS?|"
                          r"FIELD[- ]CUT|\[LENGTH\]", re.I)
FIXED_LEN_RE = re.compile(r"\b\d+(?:-\d+/\d+)?[- ](?:FOOT|FT\.?)(?:\s+LONG)?\b", re.I)


def measured_by_length(desc):
    if BY_LENGTH_RE.search(desc):
        return True
    return bool(LINEAR_RE.search(desc)) and not FIXED_LEN_RE.search(desc)
SCHEDULE_RE = re.compile(r"(?:LIGHTING )?(?:FIXTURE|LUMINAIRE) SCHEDULE", re.I)
# header word -> column role. "Label"/"Mark" are what many offices call the type column.
HEADER_ROLES = {"TYPE": "TYPE", "LABEL": "TYPE", "MARK": "TYPE", "TAG": "TYPE",
                "DESCRIPTION": "DESCRIPTION", "MANUFACTURER": "MANUFACTURER", "CATALOG": "CATALOG",
                "LAMPS": "LAMPS", "WATTAGE": "WATTAGE", "WATTS": "WATTAGE", "VOLTAGE": "VOLTAGE",
                "MOUNTING": "MOUNTING", "MOUNT": "MOUNTING",
                # not read, but they bound their neighbours
                "REMARKS": "REMARKS", "NOTES": "REMARKS", "LUMENS": "LUMENS", "COLOR": "COLOR"}
import os
import shutil
# Tesseract is only needed when the fixture schedule is drawn as outlines (no text layer)
TESSERACT = os.environ.get("TESSERACT") or shutil.which("tesseract") or "C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSDATA = os.environ.get("TESSDATA_PREFIX") or str(Path(TESSERACT).parent / "tessdata")


def reading_frames(page):
    """Words grouped by text direction, with coordinates turned so each group reads
    left-to-right, top-to-bottom. Schedules are often printed sideways on a sheet."""
    tp = page.get_textpage()
    dirs = {}
    for bi, b in enumerate(page.get_text("dict", textpage=tp)["blocks"]):
        for li, line in enumerate(b.get("lines", [])):
            dirs[(bi, li)] = (round(line["dir"][0]), round(line["dir"][1]))
    frames = defaultdict(list)
    for w in page.get_text("words", textpage=tp):
        d = dirs.get((w[5], w[6]), (1, 0))
        x0, y0, x1, y1 = w[:4]
        if d == (0, 1):      # text runs down the page: next line is to its left
            box = (y0, -x1, y1, -x0)
        elif d == (0, -1):   # text runs up the page: next line is to its right
            box = (-y1, x0, -y0, x1)
        elif d == (-1, 0):   # upside down
            box = (-x1, -y1, -x0, -y0)
        else:
            box = (x0, y0, x1, y1)
        frames[d].append((*box, w[4]))
    return frames


def header_rows(words):
    """Every schedule table header on the page: a list of {role: header word}.

    A header line holds a type column ("Type", "Label", "Mark") and a description-ish
    column. Several tables can sit side by side on one line: each starts at its own
    type column. When there is no DESCRIPTION column, the catalog / manufacturer
    column carries the description."""
    tables, seen_lines = [], []
    for anchor in [w for w in words if HEADER_ROLES.get(w[4].upper().strip(":/")) == "TYPE"]:
        if any(abs(anchor[1] - y) < 26 for y in seen_lines):
            continue
        line = sorted([w for w in words if abs(w[1] - anchor[1]) < 26 and HEADER_ROLES.get(w[4].upper().strip(":/"))],
                      key=lambda w: w[0])
        starts = [w for w in line if HEADER_ROLES[w[4].upper().strip(":/")] == "TYPE"]
        # tables side by side repeat their headers; if OCR dropped a "TYPE", infer it from the
        # first table's spacing between TYPE and its catalog/description header
        cats = [w for w in line if HEADER_ROLES[w[4].upper().strip(":/")] in ("DESCRIPTION", "CATALOG")]
        if starts and len(cats) > len(starts):
            off = cats[0][0] - starts[0][0]
            for c in cats[1:]:
                if not any(c[0] - off - 40 <= t[0] < c[0] for t in starts):
                    starts.append((c[0] - off, c[1], c[0] - off + 25, c[3], "TYPE"))
            starts.sort(key=lambda w: w[0])
            line = sorted(line + [t for t in starts if t not in line], key=lambda w: w[0])
        found = []
        for i, t in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else 1e9
            cols = {}
            for w in line:
                role = HEADER_ROLES[w[4].upper().strip(":/")]
                if t[0] <= w[0] < end and role not in cols:
                    cols[role] = w
            if "DESCRIPTION" not in cols:
                for alt in ("CATALOG", "MANUFACTURER"):
                    if alt in cols:
                        cols["DESCRIPTION"] = cols.pop(alt)  # one column, one role
                        break
            if "DESCRIPTION" in cols and cols["TYPE"][0] < cols["DESCRIPTION"][0]:
                found.append((cols, end))
        if found:
            seen_lines.append(anchor[1])
            tables += found
    return tables


def header_row(words):
    """First table header on the page (kept for callers that only need to know one exists)."""
    rows = header_rows(words)
    return rows[0][0] if rows else None


OCR_LOOKALIKE = {"O": "0", "I": "1", "S": "53", "Z": "2", "B": "8", "Q": "0", "D": "0"}


def label_candidates(token, known):
    """Labels in `known` this OCR token could be: lookalike letters after the first
    character may really be digits (O->0, I->1, S->5/3 ...)."""
    if token in known:
        return [token]
    spots = [(i, OCR_LOOKALIKE[c]) for i, c in enumerate(token) if i > 0 and c in OCR_LOOKALIKE]
    if not spots or len(spots) > 5:
        return []
    out = set()

    def walk(k, chars):
        if k == len(spots):
            cand = "".join(chars)
            if cand in known:
                out.add(cand)
            return
        i, subs = spots[k]
        for c in (token[i],) + tuple(subs):
            chars[i] = c
            walk(k + 1, chars)
        chars[i] = token[i]

    walk(0, list(token))
    return sorted(out)


def _key(label):
    """Schedule sort key: L03G -> ('L', 3, 'G')."""
    m = re.match(r"^([A-Z]+)(\d+)(.*)$", label)
    return (m.group(1), int(m.group(2)), m.group(3)) if m else (label, 0, "")


def resolve_labels(tokens, known):
    """OCR tokens in table order -> labels. Ambiguous readings (LOS = L03 or L05) are
    settled by the schedule's own order: the reading that sits between its neighbours."""
    cands = [label_candidates(t, known) for t in tokens]
    out = [c[0] if len(c) == 1 else None for c in cands]
    for i, c in enumerate(cands):
        if len(c) > 1:
            prev = next((out[j] for j in range(i - 1, -1, -1) if out[j]), None)
            nxt = next((out[j] for j in range(i + 1, len(out)) if out[j]), None)
            fit = [x for x in c if (not prev or _key(prev)[0] != _key(x)[0] or _key(prev) <= _key(x))
                   and (not nxt or _key(nxt)[0] != _key(x)[0] or _key(x) <= _key(nxt))]
            out[i] = fit[0] if len(fit) == 1 else None  # still ambiguous: leave it out, don't guess
    # a single candidate can still be wrong (LOS -> L03 when the row is really L05, which
    # isn't on the plans): a label that breaks the schedule's order is rejected, not forced
    for i, l in enumerate(out):
        if l:
            prev = next((out[j] for j in range(i - 1, -1, -1) if out[j]), None)
            nxt = next((out[j] for j in range(i + 1, len(out)) if out[j]), None)
            same = lambda o: o and _key(o)[0] == _key(l)[0]  # order only holds within a family (L.., CS..)
            if (same(prev) and _key(l) < _key(prev)) or (same(nxt) and _key(nxt) < _key(l)):
                out[i] = None
    return out


def ocr_column(page, x0, x1, y0, y1, dpi=600, whitelist=True):
    """Re-OCR one narrow column (displayed coords) at high resolution, letters and digits
    only. Type labels are short and small; the full-sheet pass misreads them."""
    import pytesseract
    from PIL import Image
    pytesseract.pytesseract.tesseract_cmd = TESSERACT
    clip = pymupdf.Rect(x0, y0, x1, y1) & page.rect
    if clip.is_empty or clip.width < 2 or clip.height < 2:
        return []  # degenerate table bounds: nothing to read
    pix = page.get_pixmap(clip=clip, dpi=dpi)  # clip is in displayed coordinates
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    cfg = "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" if whitelist else "--psm 3"
    data = pytesseract.image_to_data(img, config=cfg, output_type=pytesseract.Output.DICT)
    kx, ky = pix.width / clip.width, pix.height / clip.height  # true scale; dpi/72 drifts with rounding
    words = []
    for i, t in enumerate(data["text"]):
        if t.strip():
            L, T, W, H = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            words.append((clip.x0 + L / kx, clip.y0 + T / ky, clip.x0 + (L + W) / kx, clip.y0 + (T + H) / ky, t.strip()))
    return sorted(words, key=lambda w: w[1])


def ocr_frames(page, cache):
    """OCR words for a sheet whose text is drawn as outlines (no text layer), turned into
    the displayed (upright) frame. Cached: OCR of one sheet takes about a minute."""
    cache = Path(cache)
    if cache.exists():
        words = [tuple(w) for w in json.loads(cache.read_text(encoding="utf-8"))]
    else:
        # own render + Tesseract: PyMuPDF's OCR text page drifted ~9pt down a rotated sheet
        words = ocr_column(page, page.rect.x0, page.rect.x1, page.rect.y0, page.rect.y1, dpi=300, whitelist=False)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(words), encoding="utf-8")
    return {"display": words}


def column_spans(words, cols, hi_limit=1e9):
    """Column extents from the whitespace gutters in the table body, so it works
    whether headers are centred over their column or left-aligned."""
    hy = min(w[1] for w in cols.values())
    lo = min(w[0] for w in cols.values()) - 150
    hi = min(max(w[2] for w in cols.values()) + 600, hi_limit - 2)
    body = [w for w in words if hy + 5 < w[1] < hy + 1800 and lo <= w[0] and w[2] <= hi]
    covered = [0] * (int(hi - lo) + 2)
    for w in body:
        for x in range(int(w[0] - lo), int(w[2] - lo) + 1):
            covered[x] += 1
    runs, start, gap = [], None, 0
    for x, c in enumerate(covered):
        if c:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= 6:  # 6pt of clear paper = a gutter between columns
                runs.append((start + lo, x - gap + lo))
                start, gap = None, 0
    if start is not None:
        runs.append((start + lo, len(covered) + lo))
    spans = {}
    for role, h in cols.items():
        cx = (h[0] + h[2]) / 2
        best = min(runs, key=lambda r: 0 if r[0] <= cx <= r[1] else min(abs(cx - r[0]), abs(cx - r[1])), default=None)
        if best:
            spans[role] = list(best)
    # two headers over one block of text = columns that touch: split at the
    # least-covered vertical line between the two headers
    order = sorted(spans, key=lambda r: cols[r][0])
    for a, b in zip(order, order[1:]):
        if spans[a] == spans[b] or spans[a][1] > cols[b][0]:
            ca, cb = (cols[a][0] + cols[a][2]) / 2, cols[b][0]
            xs = range(int(ca - lo) + 1, int(cb - lo))
            if xs:
                cut = min(xs, key=lambda x: (covered[x], -x)) + lo
                spans[b] = [cut + 1, spans[b][1]]
                spans[a] = [spans[a][0], cut - 1]
    return {k: tuple(v) for k, v in spans.items()}


def _turn(d, x, y):
    """A point into the reading frame of text direction d (same turn as reading_frames)."""
    return {(0, 1): (y, -x), (0, -1): (-y, x), (-1, 0): (-x, -y)}.get(d, (x, y))


def row_rules(page, d, type_span):
    """y of the table's horizontal ruled lines that cross the type column, in the reading frame.
    These are the true row boundaries: a label is often centred in a tall cell."""
    cx = (type_span[0] + type_span[1]) / 2
    ys = set()
    for dr in page.get_drawings():
        for it in dr["items"]:
            if it[0] == "l":
                segs = [(it[1], it[2])]
            elif it[0] == "re":
                r = it[1]
                segs = [(r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)]
            else:
                continue
            for a, b in segs:
                if d == "display":  # OCR words live in the displayed frame
                    pa, pb = a * page.rotation_matrix, b * page.rotation_matrix
                    (ax, ay), (bx, by) = (pa.x, pa.y), (pb.x, pb.y)
                else:
                    (ax, ay), (bx, by) = _turn(d, a.x, a.y), _turn(d, b.x, b.y)
                # a row rule spans the table; short strokes are letters drawn as outlines
                if abs(ay - by) < 0.5 and min(ax, bx) <= cx <= max(ax, bx) and abs(ax - bx) >= 60:
                    ys.add(round(ay, 1))
    return sorted(ys)


def cell_blocks(words, span, y_lo, y_hi):
    """Text of one column split into cells by vertical whitespace: lines that sit close
    together are one cell; a gap clearly bigger than the line spacing starts the next."""
    ws = sorted([w for w in words if span[0] - 1 <= (w[0] + w[2]) / 2 <= span[1] + 1 and y_lo <= w[1] < y_hi],
                key=lambda w: (w[1], w[0]))
    lines = []
    for w in ws:
        if lines and abs(w[1] - lines[-1][0][1]) < 2:
            lines[-1].append(w)
        else:
            lines.append([w])
    if not lines:
        return []
    gaps = sorted(b[0][1] - a[0][1] for a, b in zip(lines, lines[1:]))
    step = gaps[len(gaps) // 4] if gaps else 0  # a low quartile = normal line spacing inside a cell
    blocks = [[lines[0]]]
    for a, b in zip(lines, lines[1:]):
        if step and b[0][1] - a[0][1] > step * 1.45:
            blocks.append([])
        blocks[-1].append(b)
    out = []
    for blk in blocks:
        top, bot = blk[0][0][1], blk[-1][0][3]
        out.append((top, bot, [((ln[0][1] + ln[0][3]) / 2, " ".join(w[4] for w in ln)) for ln in blk]))
    return out


def read_table(page, d, words, cols, end, header_y, known=None):
    """One schedule table -> {mark: fields}. known = labels printed on the plans (OCR only)."""
    spans = column_spans(words, cols, end)
    if "TYPE" not in spans or "DESCRIPTION" not in spans:
        return {}, None
    inside = lambda w, role: role in spans and spans[role][0] - 1 <= (w[0] + w[2]) / 2 <= spans[role][1] + 1
    if known is None:
        marks = [w for w in words if inside(w, "TYPE") and w[1] > header_y and TAG_RE.match(w[4])]
    else:
        # OCR sheet: re-read the type column sharp, then accept only labels printed on the plans
        body = [w for w in words if w[1] > header_y and spans["TYPE"][0] - 2 <= w[0] < end]
        bottom = max((w[3] for w in body), default=header_y + 400)
        # type column = from its header to where the description text actually starts
        # (the gutter between them is unreliable in OCR words)
        tx1 = max(spans["TYPE"][1], cols["TYPE"][2])
        desc_x0 = min([w[0] for w in body if w[0] > tx1 and w[0] < cols["DESCRIPTION"][0]], default=tx1 + 40)
        col = ocr_column(page, spans["TYPE"][0] - 6, desc_x0 - 2, header_y + 2, bottom + 4)
        spans["TYPE"] = (spans["TYPE"][0], desc_x0 - 2)
        labels = resolve_labels([w[4] for w in col], known)
        marks = [(*w[:4], l) for w, l in zip(col, labels) if l]
    marks.sort(key=lambda w: w[1])
    if not marks:
        return {}, spans
    rules = row_rules(page, d, spans["TYPE"])
    centre = lambda w: (w[1] + w[3]) / 2

    def ruled_cell(m):
        above = [y for y in rules if y < centre(m)]
        below = [y for y in rules if y > centre(m)]
        return (above[-1], below[0]) if above and below and below[0] - above[-1] < 400 else None

    cells = {id(m): ruled_cell(m) for m in marks}
    roles = ("DESCRIPTION", "WATTAGE", "MOUNTING", "CATALOG")
    in_table = [w for w in words if spans["TYPE"][0] - 2 <= w[0] < end]
    if sum(1 for c in cells.values() if c) >= 0.8 * len(marks):
        # ruled table: each label owns the cell between the rules around it
        text = {}
        for i, m in enumerate(marks):
            y0, y1 = cells[id(m)] or (m[1] - 3, marks[i + 1][1] - 3 if i + 1 < len(marks) else m[1] + 400)
            band = sorted([w for w in in_table if y0 <= w[1] < y1], key=lambda w: (round(w[1] / 6), w[0]))
            text[id(m)] = {r: " ".join(w[4] for w in band if inside(w, r) and w[:4] != m[:4]) for r in roles}
    else:
        # unruled (often a pasted spreadsheet): split each column into cells by whitespace,
        # give each cell to the label nearest its centre (labels sit centred in tall cells)
        text = {id(m): {r: [] for r in roles} for m in marks}
        y_lo, y_hi = max(header_y + 4, marks[0][1] - 60), marks[-1][3] + 120
        centres = [centre(m) for m in marks]
        # top-aligned rows (label on the row's first line) vs centred labels in tall cells
        # a description line starting level with (almost) every label = labels sit on the row's first line
        desc_lines = [w[1] for w in in_table if spans["DESCRIPTION"][0] <= (w[0] + w[2]) / 2 <= spans["DESCRIPTION"][1]]
        top_aligned = sum(1 for m in marks if any(abs(t - m[1]) < 4 for t in desc_lines)) >= 0.8 * len(marks)
        if top_aligned:
            # label on the row's first line: the row runs down to the next label. Columns are
            # bounded by the header words themselves (OCR gutters are unreliable)
            heads = sorted(cols.values(), key=lambda w: w[0])
            right_of = lambda w: min([h[0] for h in heads if h[0] > w[0] + 5] or [end])
            # description: from the type column to the next header; short value columns
            # (watts, mount): tight around their own header word
            bounds = {r: ((spans["TYPE"][1] + 2, right_of(cols[r]) - 3) if r == "DESCRIPTION"
                          else (cols[r][0] - 15, cols[r][2] + 15)) for r in roles if r in cols}
            text = {}
            for i, m in enumerate(marks):
                y0, y1 = m[1] - 3, (marks[i + 1][1] - 3 if i + 1 < len(marks) else m[1] + 60)
                band = sorted([w for w in in_table if y0 <= w[1] < y1], key=lambda w: (round(w[1] / 6), w[0]))
                text[id(m)] = {r: " ".join(w[4] for w in band if r in bounds
                                           and bounds[r][0] <= (w[0] + w[2]) / 2 < bounds[r][1]) for r in roles}
            marks_done = True
        else:
            nearest = lambda y: min(marks, key=lambda m: abs(centre(m) - y))
            marks_done = False
        for r in roles if not marks_done else ():
            if r not in spans or (r == "CATALOG" and spans[r] == spans["DESCRIPTION"]):
                continue
            for top, bot, lines in cell_blocks(in_table, spans[r], y_lo, y_hi):
                if sum(1 for c in centres if top - 2 <= c <= bot + 2) > 1:
                    for ly, t in lines:  # one block covering several labels = one value per line
                        text[id(nearest(ly))][r].append(t)
                else:
                    text[id(nearest((top + bot) / 2))][r].append(" ".join(t for _, t in lines))
        if not marks_done:
            text = {k: {r: " ".join(v) for r, v in t.items()} for k, t in text.items()}
    out = {}
    for m in marks:
        t = text[id(m)]
        out.setdefault(m[4], {"description": t["DESCRIPTION"][:400], "watts": t["WATTAGE"][:40],
                              "mounting": t["MOUNTING"][:60], "catalog": t["CATALOG"][:80]})
    return out, spans


def read_schedule(doc, pages, frames_for=None, known=None, source="text"):
    """Fixture types from the schedule pages: {mark: {description, watts, mounting, page}}.

    frames_for(pno) -> {direction: words}; text layer by default, OCR for outlined text."""
    frames_for = frames_for or (lambda pno: reading_frames(doc[pno - 1]))
    types, last = {}, None  # last = tables of the previous page, for continuation sheets
    for pno in pages:
        page = doc[pno - 1]
        frames = frames_for(pno)
        tables = []
        for d, words in frames.items():
            for cols, end in header_rows(words):
                tables.append((d, cols, end, max(w[3] for w in cols.values())))
        if not tables and last:  # a continuation sheet with no header row: reuse the last layout
            tables = [(d, cols, end, -1e9) for d, cols, end, _ in last]
        last = tables or last
        for d, cols, end, hy in tables:
            found, _ = read_table(page, d, frames.get(d, []), cols, end, hy, known)
            for mark, f in found.items():
                types.setdefault(mark, {**f, "schedule_page": pno, "source": source})
    return types


def load_library(path):
    lib = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lib[r["assembly"]].append(r)
    return lib


def pick_assembly(desc, lib):
    """Assembly whose keyword appears EARLIEST in the description (the noun the
    designer led with: 'LINEAR LED ... WITH INTEGRAL DOWNLIGHTS' is LINEAR).
    GENERAL is the fallback."""
    for name in ("EXIT_SIGN", "EMERGENCY"):  # life-safety identity beats any other word
        if name in lib and re.search(rf"(?<!\w)(?:{lib[name][0]['match_keywords']})(?!\w)", desc, re.I):
            return name
    best = ("GENERAL", 10 ** 9)
    for name, rows in lib.items():
        if name == "GENERAL":
            continue
        m = re.search(rf"(?<!\w)(?:{rows[0]['match_keywords']})(?!\w)", desc, re.I)
        if m and m.start() < best[1]:
            best = (name, m.start())
    return best[0]


def plan_pages(register, doc, types):
    """Lighting plan sheets = lighting-discipline sheets that are not schedules and carry scheduled tags."""
    pages = []
    for s in register["sheets"]:
        if s["discipline"] != "electrical-lighting":
            continue
        page = doc[s["page"] - 1]
        text = page.get_text()
        title = (s["title"] or "").upper()
        if any(header_row(w) for w in reading_frames(page).values()) or ("SCHEDULE" in title and "PLAN" not in title):
            continue  # a schedule sheet (header row, or a continuation sheet titled SCHEDULE)
        if any(re.search(rf"\b{re.escape(t)}\b", text) for t in types):
            pages.append(s)
    return pages


def tag_words(page):
    """Free-standing labels: words on lines of <= 3 words (skips notes paragraphs)."""
    words = page.get_text("words")
    per_line = Counter((w[5], w[6]) for w in words)  # notes paragraphs have long lines; tags stand alone
    return [w for w in words if per_line[(w[5], w[6])] <= 3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--library", default=str(Path(__file__).parent.parent / "assemblies" / "electrical-library.csv"))
    a = ap.parse_args()

    register = json.loads(Path(a.out + "-drawings.json").read_text(encoding="utf-8"))
    doc = pymupdf.open(a.pdf)
    flat = lambda pno: " ".join(doc[pno - 1].get_text().split())  # titles wrap across lines
    sched_pages = [s["page"] for s in register["sheets"]
                   if s["discipline"].startswith("electrical") and SCHEDULE_RE.search(flat(s["page"]))]
    types = read_schedule(doc, sched_pages)

    # labels actually printed on the lighting plans - the schedule has to explain these
    lighting = [s for s in register["sheets"] if s["discipline"] == "electrical-lighting"
                and "PLAN" in (s["title"] or "").upper() and "SCHEDULE" not in (s["title"] or "").upper()]
    plan_labels = Counter(w[4] for s in lighting for w in tag_words(doc[s["page"] - 1]) if TAG_RE.match(w[4]))
    explained = lambda: sum(n for l, n in plan_labels.items() if l in types) / max(sum(plan_labels.values()), 1)
    ocr_pages = []
    if plan_labels and explained() < 0.6:
        # the real schedule is often drawn as outlines (no text layer): OCR the schedule sheets
        ocr_pages = [s["page"] for s in register["sheets"] if s["discipline"].startswith("electrical")
                     and "SCHEDULE" in (s["title"] or "").upper() and s["page"] not in sched_pages]
        before = explained()
        # letter-only marks (FCPSF EX / EXD exit signs) are fixture types too, but only when the
        # schedule's type column says so - so they go in as candidates here, not in plan_labels
        alpha = {w[4] for s in lighting for w in tag_words(doc[s["page"] - 1]) if ALPHA_MARK_RE.match(w[4])}
        ocr_types = read_schedule(
            doc, ocr_pages, known=set(plan_labels) | alpha, source="ocr",
            frames_for=lambda pno: ocr_frames(doc[pno - 1], f"{a.out}-ocr/page-{pno}.json"))
        # a fixture family has several types (L01..L23, CS01..CS05). A lone OCR hit (S3,
        # V1372) is a calc-zone or panel label from another table on the sheet, not a fixture
        fam = lambda l: re.match(r"[A-Z]+", l).group(0)
        fam_n = Counter(fam(t) for t in ocr_types)
        families = {f for f, n in fam_n.items() if n >= 3}
        ocr_types = {t: v for t, v in ocr_types.items() if fam_n[fam(t)] >= 2 or ALPHA_MARK_RE.match(t)}
        if ocr_types:
            # the drawn schedule sheet is the real one. Text-layer "schedules" next to it were
            # cut sheets / photometrics (FCPSF W03 got a cut-sheet product): drop their types
            # that aren't on the plans, and flag the rest
            for t in [t for t in types if t not in ocr_types]:
                if not plan_labels.get(t):
                    del types[t]
                else:
                    types[t]["description"] = (f"[from p{types[t]['schedule_page']}, not the luminaire "
                                               f"schedule - verify] " + types[t]["description"])
        types.update(ocr_types)
        # OCR misses some rows. A label of a known fixture family (L.., CS.., W..) printed
        # on the plans is still a fixture: count it, and say its schedule row wasn't read
        for label, n in plan_labels.items():
            if label not in types and fam(label) in families and n >= 2:
                types[label] = {"description": "(schedule row not read - check the luminaire schedule sheet)",
                                "watts": "", "mounting": "", "catalog": "", "schedule_page": None,
                                "source": "plans only"}
        print(f"text-layer schedules explain {before:.0%} of plan labels; OCR of {len(ocr_pages)} schedule "
              f"sheets added {len(ocr_types)} types -> {explained():.0%}")
    # photometric calc summaries look like schedules (label + description) but their rows
    # are illuminance zones: "3.5 fc 5.9 fc 1.6 fc 3.7:1"
    types = {t: v for t, v in types.items() if not re.search(r"\d\s*fc\b", v["description"])}
    if not types:
        raise SystemExit("No fixture schedule found - point me at the schedule sheet.")
    lib = load_library(a.library)
    plans = plan_pages(register, doc, types)

    preview = defaultdict(Counter)
    unscheduled = Counter()
    for s in plans:
        for w in tag_words(doc[s["page"] - 1]):
            if w[4] in types:
                preview[w[4]][s["sheet"]] += 1
            elif TAG_RE.match(w[4]):
                unscheduled[w[4]] += 1

    items = []
    for mark, t in types.items():
        # "SAME AS TYPE AL17, EXCEPT ..." inherits the parent's description for classification
        # (OCR reads "SAME AS LO6" - the O after the letters is a zero)
        same = re.search(r"SAME AS (?:TYPE )?([A-Z]{1,3}?)([\dO]{1,3})([A-Z]{0,2})\b", t["description"])
        parent = same and same.group(1) + same.group(2).replace("O", "0") + same.group(3)
        basis = (types[parent]["description"] + " " + t["description"]
                 if parent in types else t["description"])
        linear = measured_by_length(basis)
        items.append({
            "mark": mark, "description": t["description"], "watts": t["watts"], "mounting": t["mounting"], "catalog": t.get("catalog", ""),
            "schedule_page": t["schedule_page"], "schedule_source": t.get("source", "text"),
            "measure": "LENGTH" if linear else "EACH",
            "assembly": pick_assembly(basis, lib), "same_as": parent or None,
            "preview_tags": sum(preview[mark].values()), "preview_by_sheet": dict(preview[mark]),
            # existing fixtures (Bliss EX1 "existing globe, new LED lamping") are not new fixtures:
            # left out by default, the estimator can include them as relamp scope at approval
            "include": sum(preview[mark].values()) > 0 and not re.match(r"\W*EXISTING\b", t["description"], re.I),
        })
    plan = {"status": "proposed", "pdf": a.pdf, "library": a.library,
            "plan_sheets": [{"page": s["page"], "sheet": s["sheet"], "title": s["title"]} for s in plans],
            "schedule_pages": sched_pages, "ocr_schedule_pages": ocr_pages, "items": items,
            "not_counted": [{"label": k, "tags": v, "why": "not in the fixture schedule"}
                            for k, v in unscheduled.most_common()]}
    Path(a.out + "-plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")

    md = [f"# Takeoff plan (status: {plan['status']})", "",
          f"Plan sheets: {', '.join(s['sheet'] for s in plans)}  |  schedule pages: {sched_pages}", "",
          "| mark | measure | assembly | tags on plans | description |", "|---|---|---|---|---|"]
    for it in sorted(items, key=lambda r: -r["preview_tags"]):
        md.append(f"| {it['mark']} | {it['measure']} | {it['assembly']} | {it['preview_tags']} | "
                  f"{it['description'][:70]} |")
    md += ["", "## Labels on the plans NOT counted (not in the schedule)", ""]
    md += [f"- {r['label']} x{r['tags']}" for r in plan["not_counted"][:30]] or ["- none"]
    md += ["", "Approve by setting `\"status\": \"approved\"` in the plan JSON "
           "(and `include: false` on any type you don't want)."]
    Path(a.out + "-plan.md").write_text("\n".join(md), encoding="utf-8")
    inc = [i for i in items if i["include"]]
    print(f"{len(types)} scheduled types | {len(inc)} found on {len(plans)} plan sheets | "
          f"{sum(i['measure'] == 'LENGTH' for i in inc)} linear (measured by length) | "
          f"{len(unscheduled)} unscheduled labels skipped -> {a.out}-plan.md")


if __name__ == "__main__":
    main()
