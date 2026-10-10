# Lighting takeoff cross-check - brief for Codex

Two rounds, **each started by a separate instruction from Hamza**. Round 1 is a blind count.
Round 2 is where you get our code and the comparison. Do not start round 2, run the
comparison, or open its output until Hamza says "start round 2" - if you see our results or
code first, you inherit our mistakes and the check is worthless.

## Stop rules (both rounds)

- **One pass per round.** Do the round once, write the files, stop. No second pass, no
  "let me double-check everything again", no re-running after you finish.
- **Can't decide something from one look at the drawing?** Mark it `unresolved`, write one line
  saying why, move on. Unresolved is an acceptable answer.
- **Only write your own files** (the ones named below, plus `codex/`). Never edit `scripts/`,
  `inputs/` or our files in `outputs/`.
- **When the round's files are written:** reply to Hamza with at most 5 lines (totals, how many
  unresolved, the single biggest issue) and stop. Do not start the next round, do not suggest
  another round, do not offer to keep going.

Tools you may use in both rounds: Python
(any Python 3 with PyMuPDF, pytesseract,
Pillow, openpyxl), Tesseract (`C:/Program Files/Tesseract-OCR/tesseract.exe`), rendering pages and
crops to PNG and looking at them. Put your own scripts and crops in `codex/`.

## Baseline (record these in your notes; if a hash differs, stop and tell Hamza)

| file | sha256 |
|---|---|
| inputs/fcpsf.pdf | 0c7bcd310dca5b57737e0b0a627ec986c613debfbca97131a2d7baf78a686d87 |
| inputs/bliss-hall.pdf | f705cbfbdea9fa66c36ceade2121787efeffa1fba6181cc288fbccc3335bd454 |

## Page numbering

All page numbers here are **1-based PDF page numbers** (PyMuPDF index + 1), not printed sheet numbers.

**fcpsf** (`inputs/fcpsf.pdf`)

| PDF page | sheet | role |
|---|---|---|
| 349 | E-201A | lighting plan |
| 350 | E-201B | lighting plan |
| 351 | E-201F | lighting plan |
| 352 | E-201H | lighting plan |
| 353 | E-201S | lighting plan |
| 354 | E-201V | lighting plan |
| 355 | E-202H | lighting plan |
| 356 | E-203 | lighting plan |
| 376-385 | E-6xx | luminaire schedule - **text drawn as outlines, no text layer: OCR or read the image** |

The tables on E-204/E-205 that look like schedules are photometric calculations, not the fixture schedule.

**bliss** (`inputs/bliss-hall.pdf`): lighting plans 62-66 = EL101-EL105; schedule 68-70 = EL601-EL603.

## Counting rules

1. **Scope**: only the plan pages listed above. Do not count enlarged plans, details, legends,
   key plans, title blocks or schedule pages. If an area is also drawn enlarged elsewhere, count
   it on the listed plan only.
2. **One mark = one type.** Variants are separate types: `L06`, `L06E` (emergency battery),
   `L06G` (generator transfer device) are counted separately, each under its own mark.
3. **Count fixtures, not tags.** If one tag labels several symbols (a row, "TYP", a bracket),
   count the symbols and note it. A multi-head assembly shown as one symbol with one tag = 1. If identical symbols always come in pairs joined by a line, measure the pair's overall length against the catalog length before calling it two fixtures - one fixture is often drawn as its two ends.
4. **Not counted** (list them in the notes file instead): existing-to-remain and existing
   fixtures (shown halftone/dashed, or described as existing in the schedule - a mark like `EX` can
   be a scheduled EXIT sign, so check the schedule before excluding anything), demolition, add/deduct alternates, and anything with
   a mark that is not in the schedule.
5. **Not fixtures** - watch for: dashed boundary / secure-perimeter lines (each dash looks like a
   small linear fixture), home-run arrows, circuit letters, occupancy sensors, switches, room tags.
6. **Linear types**: for types sold **each** (a 4 ft strip is one fixture), `count` = number of
   fixtures, `length_ft` empty. For types sold **by length** (schedule says per foot / "lengths as
   indicated"), `count` = number of **tagged fixtures** (several fixtures butted end to end, each with its own tag/circuit, are several - not one row) and `length_ft` = total drawn length on that sheet in feet,
   from the sheet's plan scale - check the scale against one dimension string; ignore details at
   other scales. Use drawn length, not the nominal schedule length, and say so if they disagree.

## Round 1 - independent count (blind)

Do **not** open `scripts/` or anything in `outputs/` except `outputs/fcpsf-drawings.md` (sheet register only).

Write three files. **All three are required** - without the points file the comparison cannot say which fixtures differ:

**`outputs/<set>-codex-points.csv`** - one row per fixture you counted (this is the evidence):
```
sheet,page,mark,x,y,x0,y0,x1,y1,length_ft,notes
E-201A,349,L06,1837.0,1513.0,1830.1,1505.2,1844.0,1521.0,,
```
- `x,y` = centre of the **fixture symbol** (not the tag); `x0..y1` = its bounding box; all in
  **PyMuPDF unrotated page coordinates** - the same space as `page.get_text("words")` and
  `page.get_drawings()`, i.e. NOT rotated for display. If you work from a rendered image, convert
  back (`pymupdf.Point(...) * page.derotation_matrix`, divided by the render scale).
- by-length types: one row per run, `x,y` = run midpoint, `length_ft` filled.

**`outputs/<set>-codex.csv`** - coverage: one row for **every scheduled type x every plan sheet**
(zeros included), so a missing row never has to be guessed:
```
sheet,mark,count,length_ft,status,method,notes
E-201A,L06,90,,counted,text tags + visual check,
E-201A,L20,0,,zero,,
E-203,CS01,,,unresolved,,"schedule row unreadable - cannot confirm type"
```
- `status`: `counted` (count > 0), `zero` (looked, none there), `unresolved` (couldn't decide - say why).
- `count` must equal the number of rows in the points file for that sheet and mark.
- marks exactly as printed in the schedule, upper-case.

**`outputs/<set>-codex-notes.md`** - the baseline hashes you checked; the fixture types you read
from the schedule with descriptions; everything you did not count and why (rule 4/5); every place
you were unsure.

Then stop and tell Hamza round 1 is done. **Do not run compare.py.**

## Round 2 - only when Hamza says "start round 2"

1. Run `<python> scripts/compare.py --out outputs/<set> --pdf inputs/<set>.pdf`. It compares per
   sheet/type and per fixture (same page and mark, centres within 30 pt = same fixture; lengths agree
   within max(2 ft, 5%); `unresolved` cells are left out of the score). Read `outputs/<set>-compare.md`.
2. Now open `outputs/<set>-takeoff.json`, `outputs/<set>-plan.json`/`.md` and `scripts/`
   (assemblies.py = schedule reading, takeoff.py + symbols.py = counting).
3. For every row in "Only ours", "Only theirs" and "Same spot, different type" (or, if there is no
   points file for that set, every row in "Count disagreements"): render ONE crop at that location
   and decide from the drawing who is right - us, you, or neither. When many rows share one cause
   (same symbol, same rule), settle 3 examples and write the rest as one grouped line - do not crop
   every one.
4. Where we are wrong, find the code that caused it and propose a fix as a diff in `codex/fixes.md`.
   Do not edit `scripts/`. Where you were wrong, say why in one line.
5. Look for errors the comparison cannot catch: both sides wrong the same way, types neither of us
   counted, and schedule descriptions misread by OCR - check every description in
   `outputs/<set>-plan.md` against the schedule image.

Output `codex/review-<set>.md`: a table
`page | sheet | x,y | ours | yours | right answer | crop file | cause`, then the proposed fixes, most important first.
Then stop (stop rules above). There is no round 3: fixes are applied on our side.
