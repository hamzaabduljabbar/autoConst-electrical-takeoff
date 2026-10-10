# Electrical Takeoff (Claude skill)

**Lighting takeoff from PDF drawings: every fixture counted by type, checked two ways, and marked on the drawing so you can click through and verify it. Outputs an Excel BOQ and an interactive drawing viewer.**

Counting light fixtures means hunting through every lighting plan, matching each tag to the schedule, and hoping you didn't miss a type. This skill does the hunting, and shows you every fixture it counted, so checking takes minutes instead of redoing the count.

Same honesty rule as the other AutoConst tools ([Drawing Takeoff](https://github.com/hamzaabduljabbar/autoConst-drawing-takeoff-claude), [Spec Bid-Review](https://github.com/hamzaabduljabbar/autoConst-spec-bid-review-claude), [P6 Cost Loader](https://github.com/hamzaabduljabbar/autoConst-p6-cost-loader)): every number is traceable, nothing is guessed silently.

---

## Real results

**1. University building (Bliss Hall, public bid set), 5 lighting plans, 27 fixture types**

```
883 fixtures + 2,090 ft of linear fixtures | 0 LOW
Tag count = symbol count on every symbol-counted type:
  AL1 troffers 89 = 89 | BL17 cylinders 136 = 136 | BL18 87 = 87 | XL1 exit signs 75 = 75
QA flagged a 50% CD progress sheet left in the bid set + 4 stale-revision sheets
```

**2. Correctional facility, 420-page set, 8 lighting plans, 62 fixture types**

```
1,948 fixtures
Real luminaire schedule (E-601) drawn as outlines - no text - read with OCR
Photometric calc tables that look like schedules - detected and ignored
106 exit signs (marks EX / EXD, no digits) - counted
Drawing discrepancy flagged: CS02E1 on the plans x10, schedule lists CS02E (RFI)
```

**Blind cross-check.** A second AI counted both sets independently with no access to this code. Every disagreement was settled on the drawing:
- it was right that an exit sign was double-counted (XL2 drawn with the XL1 symbol) - fixed;
- it was wrong on the pendants (188 vs 94): each 4 ft SBL4 is drawn as its two end symbols, exactly 4.0 ft apart on 91 of 98.

## What you get

| File | What it is |
|---|---|
| `<p>-drawings.md` | Sheet register + "fix before takeoff" list (VOID/progress stamps, stale revisions, scanned sheets) |
| `<p>-plan.md` | Every fixture type from the schedule, how it will be counted, what is NOT counted. You approve this before counting. |
| `<p>-takeoff.xlsx` | **BOQ** (mark, description, count/ft, confidence), **Derived** (boxes, whips, cable, labour per assembly), **Marks** (every fixture with sheet + location), **Not counted** |
| `<p>-viewer/index.html` | The drawing viewer (below) |
| `<p>-verify/*.png` | Crops of counted fixtures per type for spot checks |

### The drawing viewer
- click a fixture type and every one of them lights up on the sheet
- zoom straight to any fixture, step through them with the arrow keys
- mark each one Correct (C) or Wrong (W) as you check; filter to just the flagged ones
- copy the counts out as CSV into your estimate

Open `examples/bliss-hall/viewer/index.html` to try it.

## How it works

1. **QA the pack** - find the lighting plans and schedule from the title blocks, flag sheets that shouldn't be priced.
2. **Plan, then approve** - read the schedule (OCR if it's drawn as outlines), list every type and how it'll be counted. Nothing is counted until you approve.
3. **Count two ways** - by tag and by the drawn symbol. Agreement = HIGH. Disagreement is flagged with the location, never averaged. Look-alikes (dashed boundary lines, arrows, other types' symbols) are rejected.
4. **Measure linear runs** in feet from the vectors at the sheet scale.
5. **Verify + view** - crops per type, then the clickable viewer.

## Install

```
pip install -r requirements.txt
```
Tesseract OCR is only needed when the fixture schedule has no text layer: install it and make sure `tesseract` is on PATH (or set `TESSERACT=<path to tesseract.exe>`).

Use as a Claude skill (put the folder in your skills directory - Claude follows `SKILL.md`), or run the scripts directly:

```
python scripts/qa.py         set.pdf --out outputs/job
python scripts/assemblies.py set.pdf --out outputs/job
#  review outputs/job-plan.md, then set "status": "approved" in outputs/job-plan.json
python scripts/takeoff.py    set.pdf --out outputs/job
python scripts/verify.py     set.pdf --out outputs/job
python scripts/viewer.py     set.pdf --out outputs/job
```

## Honest limits

- **Vector PDFs only.** Scanned sheets are flagged, not counted.
- **Fixtures need a type mark.** Untagged fixtures are found from their symbol only where the symbol is vector; where fixture graphics are pasted in as images, it counts by tag (marked MEDIUM).
- **Derived quantities use placeholder ratios** (`assemblies/electrical-library.csv`: cable per fixture, labour hours). Put in your own before you price.
- **You still check the flagged ones.** That's what the viewer is for.

## License

AutoConst Source Available License - free to use and modify for your own work, not for resale. See `LICENSE`.

Built by Hamza Jabbar - [hamzajabbar.online](https://hamzajabbar.online)
