"""Compare our takeoff with an independent count (e.g. Codex), per sheet/type AND per fixture.

Reads (contract in docs/codex-crosscheck.md):
  <out>-takeoff.json         ours
  <out>-codex.csv            theirs: one row per scheduled type x plan sheet, with status
  <out>-codex-points.csv     theirs: one row per fixture with its location (optional but
                             needed to say WHICH fixtures differ)
Writes <out>-compare.md: input hashes, agreement, unresolved cells, every count
disagreement, and the unmatched fixtures on each side with their coordinates.

Usage: python scripts/compare.py --out outputs/<name> --pdf inputs/<name>.pdf
"""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

MATCH_PT = 30        # a fixture on both sides = same page, same mark, centres within this (PDF points)
LEN_TOL = (2.0, 0.05)  # lengths agree within max(2 ft, 5%)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def norm(mark):
    return mark.strip().upper()


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pdf")
    ap.add_argument("--other")
    a = ap.parse_args()
    ours_path = Path(a.out + "-takeoff.json")
    ours = json.loads(ours_path.read_text(encoding="utf-8"))
    other_path = Path(a.other or a.out + "-codex.csv")
    pts_path = Path(str(other_path).replace(".csv", "-points.csv"))

    theirs, unresolved = {}, []
    for r in read_csv(other_path):
        k = (r["sheet"].strip(), norm(r["mark"]))
        status = (r.get("status") or "counted").strip().lower()
        if status == "unresolved":
            unresolved.append((k, r.get("notes", "")))
            continue
        theirs[k] = {"count": int(float(r["count"] or 0)),
                     "length_ft": float(r["length_ft"]) if (r.get("length_ft") or "").strip() else None,
                     "method": r.get("method", ""), "notes": r.get("notes", "")}

    mine_n, mine_len = Counter(), defaultdict(float)
    for p in ours["points"]:
        k = (p["sheet"], norm(p["mark"]))
        mine_n[k] += 1
        mine_len[k] += p.get("length_ft") or 0

    rows, agree, total = [], 0, 0
    for k in sorted(set(mine_n) | set(theirs)):
        if any(k == u for u, _ in unresolved):
            continue
        m, t = mine_n.get(k, 0), theirs.get(k, {}).get("count", 0)
        ml, tl = round(mine_len.get(k, 0), 1), theirs.get(k, {}).get("length_ft")
        total += max(m, t)
        agree += min(m, t)
        len_off = tl is not None and abs((ml or 0) - tl) > max(LEN_TOL[0], LEN_TOL[1] * tl)
        if m != t or len_off:
            rows.append((k, m, t, ml, tl, theirs.get(k, {})))

    by_type = defaultdict(lambda: [0, 0])
    for k in set(mine_n) | set(theirs):
        by_type[k[1]][0] += mine_n.get(k, 0)
        by_type[k[1]][1] += theirs.get(k, {}).get("count", 0)

    md = [f"# Takeoff cross-check: ours vs {other_path.name}", "",
          "| input | sha256 (16) |", "|---|---|",
          *([f"| {a.pdf} | {sha(a.pdf)} |"] if a.pdf else []),
          f"| {ours_path.name} (ours) | {sha(ours_path)} |",
          f"| {other_path.name} (theirs) | {sha(other_path)} |",
          *([f"| {pts_path.name} (theirs) | {sha(pts_path)} |"] if pts_path.exists() else []), "",
          f"Counts: lengths agree within max({LEN_TOL[0]} ft, {LEN_TOL[1]:.0%}); marks compared upper-case.", "",
          f"Fixture agreement: **{agree} of {total}** ({100 * agree / max(total, 1):.1f}%) "
          f"matched per sheet and type. {len(rows)} sheet/type cells disagree, "
          f"{len(unresolved)} cells they marked unresolved (left out of the score).", ""]
    if unresolved:
        md += ["## Unresolved on their side", "", "| sheet | type | ours | their note |", "|---|---|---|---|"]
        md += [f"| {s} | {m} | {mine_n.get((s, m), 0)} | {n} |" for (s, m), n in unresolved] + [""]
    md += ["## Totals by type", "", "| type | ours | theirs | diff |", "|---|---|---|---|"]
    for t, (m, o) in sorted(by_type.items(), key=lambda kv: -abs(kv[1][0] - kv[1][1])):
        md.append(f"| {t} | {m} | {o} | {m - o:+d} |")
    md += ["", "## Count disagreements", "",
           "| sheet | type | ours | theirs | our ft | their ft | their method / notes |", "|---|---|---|---|---|---|---|"]
    for (sheet, mark), m, t, ml, tl, th in sorted(rows, key=lambda r: -abs(r[1] - r[2])):
        md.append(f"| {sheet} | {mark} | {m} | {t} | {ml or ''} | {tl if tl is not None else ''} | "
                  f"{th.get('method', '')} {th.get('notes', '')} |")

    if pts_path.exists():
        # fixture-level: pair each of their points with our nearest same-mark point on the page
        # cells they marked unresolved are partial on their side: leave them out here too
        skip = {k for k, _ in unresolved}
        tp = defaultdict(list)
        for r in read_csv(pts_path):
            if (r["sheet"].strip(), norm(r["mark"])) not in skip:
                tp[(int(r["page"]), norm(r["mark"]))].append(r)
        op = defaultdict(list)
        for p in ours["points"]:
            if (p["sheet"], norm(p["mark"])) not in skip:
                op[(p["page"], norm(p["mark"]))].append(p)
        only_ours, only_theirs, wrong_type = [], [], []
        for k in sorted(set(tp) | set(op)):
            mine, their = list(op.get(k, [])), list(tp.get(k, []))
            pairs = sorted(((math.hypot(p["x"] - float(r["x"]), p["y"] - float(r["y"])), i, j)
                            for i, p in enumerate(mine) for j, r in enumerate(their)))
            used_i, used_j = set(), set()
            for d, i, j in pairs:
                if d <= MATCH_PT and i not in used_i and j not in used_j:
                    used_i.add(i); used_j.add(j)
            only_ours += [mine[i] for i in range(len(mine)) if i not in used_i]
            only_theirs += [their[j] for j in range(len(their)) if j not in used_j]
        # an unmatched pair at the same spot with different marks = a type disagreement, not a miss
        for p in list(only_ours):
            hit = next((r for r in only_theirs if int(r["page"]) == p["page"]
                        and math.hypot(p["x"] - float(r["x"]), p["y"] - float(r["y"])) <= MATCH_PT), None)
            if hit:
                wrong_type.append((p, hit)); only_ours.remove(p); only_theirs.remove(hit)
        md += ["", f"## Fixture-level ({MATCH_PT} pt match radius, PyMuPDF unrotated page coordinates)", "",
               f"{len(only_ours)} fixtures only we counted, {len(only_theirs)} only they counted, "
               f"{len(wrong_type)} same spot but different type. "
               f"({sum(1 for p in ours['points'] if (p['sheet'], norm(p['mark'])) in skip)} of our fixtures sit in cells they left unresolved - not compared.)", ""]
        if wrong_type:
            md += ["### Same spot, different type", "", "| page | sheet | x | y | ours | theirs | their note |", "|---|---|---|---|---|---|---|"]
            md += [f"| {p['page']} | {p['sheet']} | {p['x']:.0f} | {p['y']:.0f} | {p['mark']} ({p['id']}) | {r['mark']} | {r.get('notes', '')} |"
                   for p, r in wrong_type] + [""]
        if only_ours:
            md += ["### Only ours", "", "| page | sheet | id | x | y | our confidence |", "|---|---|---|---|---|---|"]
            md += [f"| {p['page']} | {p['sheet']} | {p['id']} | {p['x']:.0f} | {p['y']:.0f} | {p['confidence']} |"
                   for p in sorted(only_ours, key=lambda p: (p["page"], p["id"]))] + [""]
        if only_theirs:
            md += ["### Only theirs", "", "| page | sheet | mark | x | y | their note |", "|---|---|---|---|---|---|"]
            md += [f"| {r['page']} | {r['sheet']} | {r['mark']} | {float(r['x']):.0f} | {float(r['y']):.0f} | {r.get('notes', '')} |"
                   for r in sorted(only_theirs, key=lambda r: (int(r["page"]), r["mark"]))]
    else:
        md += ["", f"_No {pts_path.name}: fixture-level comparison skipped._"]

    Path(a.out + "-compare.md").write_text("\n".join(md), encoding="utf-8")
    print(f"agreement {agree}/{total} ({100 * agree / max(total, 1):.1f}%), {len(rows)} cells differ, "
          f"{len(unresolved)} unresolved -> {a.out}-compare.md")


if __name__ == "__main__":
    main()
