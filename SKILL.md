---
name: electrical-takeoff
description: Lighting/electrical fixture takeoff from a vector PDF drawing set. Runs drawing-pack QA, reads the luminaire schedule (OCR when it is drawn as outlines) and asks for approval of the fixture list, then counts every fixture by tag AND by drawn symbol, measures linear runs in feet from the vectors, verifies with crops, and writes an Excel BOQ with derived quantities plus a clickable HTML drawing viewer with every fixture marked. Use when someone wants an electrical, lighting or fixture takeoff, a luminaire count, linear feet of light fixtures, or to audit a lighting count.
---

# Electrical takeoff

Five steps. Each writes files next to `--out` (e.g. `outputs/<project>`). Never skip step 2's approval.

```
python scripts/qa.py         <set.pdf> --out outputs/<p>    # 1 sheet register + problems
python scripts/assemblies.py <set.pdf> --out outputs/<p>    # 2 plan: what will be counted
   -> show <p>-plan.md, get approval, set "status": "approved" in <p>-plan.json
python scripts/takeoff.py    <set.pdf> --out outputs/<p>    # 3 count + measure -> xlsx + json
python scripts/verify.py     <set.pdf> --out outputs/<p>    # 4 crops per type -> look at them
python scripts/viewer.py     <set.pdf> --out outputs/<p>    # 5 HTML drawing viewer
```

## 1. Drawing pack QA (`qa.py`)
Writes `<p>-drawings.md`. Read the "Fix before takeoff" list to the user: scanned sheets, sheets
with no number, progress/VOID/PRELIMINARY stamps, stale revisions. Lighting plans are picked from
this register, never from file names.

## 2. Assemblies (`assemblies.py`) - the planning gate
Reads the fixture schedule and writes `<p>-plan.md`:
- every scheduled type, measured EACH or LENGTH (LENGTH only when the schedule prices per foot /
  "lengths as indicated"), and the assembly it maps to in `assemblies/electrical-library.csv`
- if text-layer schedules explain under 60% of the labels on the plans, the real schedule is
  probably drawn as outlines: it OCRs the schedule sheets (needs Tesseract). Photometric tables
  and cut sheets are not the schedule - their rows are dropped or marked "verify"
- letter-only marks (EX, EXD exit signs) are accepted only if the schedule's type column lists them
- types described as EXISTING are left out by default (relamp scope - the user can include them)
- labels on the plans that are NOT in the schedule are listed, not counted

Show the user the table and the not-counted list. Only then set `"status": "approved"`.
The library ratios marked PLACEHOLDER are examples: tell the user to replace them.

## 3. Takeoff (`takeoff.py`)
- EACH types: counted by **symbol** (vector fingerprint learned from tagged fixtures) and checked
  against the **tag** count. If the learned symbol doesn't match most of the type's tags (fixture
  graphics pasted as images, tags drawn inside boxes) it falls back to counting tags.
- Look-alikes rejected: dashed boundary lines (chains of identical dashes), single strokes, and
  symbols whose nearest tag belongs to another type.
- LENGTH types: each tag is paired with the fixture body beside it; length = body length / sheet
  scale. If every body is the same tiny length, it measured a symbol, not the fixture: counted
  EACH, one per tag.
- Confidence: HIGH = tag and symbol agree; MEDIUM = one source only; LOW = check it.
- Output: `<p>-takeoff.xlsx` (BOQ, Derived, Marks, Not counted) and `<p>-takeoff.json`.

## 4. Verify (`verify.py`) - required before reporting numbers
Look at `<p>-verify/<TYPE>.png` for the big types and anything MEDIUM/LOW. Blue = tag, red = what
was counted. If a box sits on the wrong thing, fix the cause in code and re-run. Never edit
numbers by hand.

## 5. Viewer (`viewer.py`)
`<p>-viewer/index.html`: fixture list on the left, the sheet on the right, every fixture marked and
coloured by confidence. Click a type to see all of them, step with arrow keys, C = correct,
W = wrong, F = whole sheet, "To check" filter, Copy CSV.

## Optional: independent cross-check (`compare.py`, `docs/crosscheck-brief.md`)
Give the brief to a second AI for a blind count, then `python scripts/compare.py --out outputs/<p>
--pdf <set.pdf>`. Settle every disagreement on the drawing - neither count wins by default.

## Report to the user
Totals per type, how each was counted, the LOW list, QA problems, what was not counted and why.
State the limits: placeholder assembly ratios, types counted by tags only, unresolved schedule rows.

## Limits
Vector PDFs only (scanned sheets are flagged, not counted). Fixtures must carry a schedule type
mark on the plans. Untagged fixtures are found only where the symbol is vector, not an image.
