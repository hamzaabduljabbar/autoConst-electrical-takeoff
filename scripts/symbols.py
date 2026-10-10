"""Vector symbol fingerprints - count fixtures, not tags.

Designers tag ONE fixture per room ("typical") and leave the rest untagged, so
a tag count undercounts (Bliss Hall EL104: 71 AL1 tags, 83 AL1 troffers).

How it works:
  1. components(): group the dark fixture-pen paths on a sheet into connected
     shapes; each shape gets a signature (short side, long side, path items).
  2. learn(): for every scheduled tag, the shape right next to it votes for
     "this signature is type X". Signatures voted by several tags of one type
     and not claimed by another label become that type's fingerprint.
  3. find every shape with a fingerprint on every plan sheet. A shape whose
     fingerprint belongs to several types (AL17 vs AL17A look identical) is
     given to the nearest tag of those types.

Anything uncertain stays visible: untagged shapes are MEDIUM, tags with no
shape next to them are LOW, and a type whose shape is shared with an
unscheduled label (e.g. an occupancy sensor) falls back to counting tags.
"""
import math
from collections import Counter, defaultdict

import pymupdf

TAG_REACH = 25      # pt: a tag labels the shape within this distance
MAX_SYMBOL = 120    # pt: bigger shapes are runs/walls, not point fixtures
MIN_VOTES = 2       # a fingerprint needs at least this many agreeing tags
MIN_SHARE = 0.25    # ...and at least this share of the type's tags
JOIN = 3.0          # pt: pieces of one symbol (exit arrow, wallwash chevron) sit this close


def _dark(c):
    return c is not None and max(c) <= 0.25


def components(page):
    """Connected dark shapes on a page -> list of {sig, rect}."""
    D = [d for d in page.get_drawings()
         if (_dark(d.get("color")) and (d.get("width") or 0) >= 0.7) or (d["type"] == "f" and _dark(d.get("fill")))]
    rects = []
    for d in D:
        r = pymupdf.Rect(d["rect"])
        if r.width < 0.1:
            r.x0, r.x1 = r.x0 - 0.3, r.x1 + 0.3
        if r.height < 0.1:
            r.y0, r.y1 = r.y0 - 0.3, r.y1 + 0.3
        rects.append(r)
    par = list(range(len(rects)))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i

    grid = defaultdict(list)
    for i, r in enumerate(rects):
        for gx in range(int(r.x0 // 40), int(r.x1 // 40) + 1):
            for gy in range(int(r.y0 // 40), int(r.y1 // 40) + 1):
                grid[(gx, gy)].append(i)
    for cell in grid.values():  # touching, overlapping or within JOIN pt = one shape
        for a in range(len(cell)):
            ra = rects[cell[a]]
            for b in range(a + 1, len(cell)):
                rb = rects[cell[b]]
                if ra.x0 - JOIN <= rb.x1 and rb.x0 - JOIN <= ra.x1 and ra.y0 - JOIN <= rb.y1 and rb.y0 - JOIN <= ra.y1:
                    par[find(cell[a])] = find(cell[b])
    groups = defaultdict(list)
    for i in range(len(rects)):
        groups[find(i)].append(i)
    out = []
    for idx in groups.values():
        u = pymupdf.Rect(rects[idx[0]])
        for i in idx[1:]:
            u |= rects[i]
        if max(u.width, u.height) > MAX_SYMBOL or min(u.width, u.height) < 1.0:
            continue  # runs/walls, or a single stroke (a perimeter dash) - not a fixture symbol
        n = sum(len(D[i]["items"]) for i in idx)
        w, h = round(u.width), round(u.height)
        out.append({"sig": (min(w, h), max(w, h), n), "rect": u})
    return drop_dashes(out)


DASH_CHAIN = 4  # identical shapes in a straight line, closer than their own length


def drop_dashes(shapes):
    """A heavy dashed line (secure perimeter, ceiling boundary) is drawn as separate dash
    shapes, and one dash looks exactly like a small linear fixture. Dashes come in long
    straight chains with gaps shorter than a dash; fixtures don't."""
    lines = defaultdict(list)
    for s in shapes:
        r = s["rect"]
        horiz = r.width >= r.height
        lines[(s["sig"], horiz, round((r.y0 + r.y1 if horiz else r.x0 + r.x1) / 2))].append(s)
    dashes = set()
    for (sig, horiz, _), row in lines.items():
        if sig[1] < 3 * max(sig[0], 1):
            continue  # dashes are long and thin; a row of square troffers is not a dashed line
        row.sort(key=lambda s: s["rect"].x0 if horiz else s["rect"].y0)
        chain = [row[0]]
        for s in row[1:] + [None]:
            if s is not None:
                a, b = chain[-1]["rect"], s["rect"]
                g = (b.x0 - a.x1) if horiz else (b.y0 - a.y1)
                if 0 <= g < 0.75 * sig[1]:
                    chain.append(s)
                    continue
            if len(chain) >= DASH_CHAIN:
                dashes.update(id(c) for c in chain)
            chain = [s]
    return [s for s in shapes if id(s) not in dashes]


def gap(a, b):
    return math.hypot(max(b.x0 - a.x1, a.x0 - b.x1, 0), max(b.y0 - a.y1, a.y0 - b.y1, 0))


def nearest(shapes, tb):
    best = None
    for s in shapes:
        g = gap(s["rect"], tb)
        if g <= TAG_REACH and (best is None or g < best[0]):
            best = (g, s)
    return best[1] if best else None


def learn(sheet_shapes, sheet_tags, each_types):
    """Fingerprints per type from the shapes next to their tags.

    sheet_tags: {page: [(label, Rect)]} including UNSCHEDULED labels, so a shape
    that a sensor label also points at is not taken as a fixture fingerprint."""
    votes = defaultdict(Counter)
    for page, tags in sheet_tags.items():
        for label, tb in tags:
            s = nearest(sheet_shapes[page], tb)
            if s:
                votes[label][s["sig"]] += 1
    owners = defaultdict(set)
    for label, c in votes.items():
        for sig, n in c.items():
            total = sum(c.values())
            if (n >= MIN_VOTES and n / total >= MIN_SHARE) or (label in each_types and total <= 2):
                owners[sig].add(label)
    prints, blocked = defaultdict(set), {}
    for sig, labels in owners.items():
        fixtures = labels & each_types
        others = labels - each_types
        for t in fixtures:
            if others and votes[t][sig] <= max(votes[o][sig] for o in others):
                blocked[t] = f"shape shared with {', '.join(sorted(others))}"
                continue
            prints[t].add(sig)
    return prints, blocked, votes
