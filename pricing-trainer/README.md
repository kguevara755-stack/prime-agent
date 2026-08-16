# Price Book Trainer

Lookup and drill tool for the isolved RM / Clocks / Required Products price book.
Two jobs: find any price in under five seconds, and train recall of those prices
until you can quote live on a call.

```
pricing-trainer/
├── data/
│   ├── pricing_master.csv       # 506 rows, 18 columns (source of truth)
│   ├── promotions.csv           # optional - see "Promotions" below
│   └── extraction_gaps.md       # known defects in the source; read before changing anything
├── build.py                     # normalizer + app builder
├── trainer.template.html        # the app; data placeholder gets filled at build time
├── trainer.html                 # GENERATED - open this
└── README.md
```

## Refreshing the data

One step:

```bash
cd pricing-trainer
python3 build.py
```

That reads `data/`, re-normalizes, and rewrites `trainer.html` with the dataset
inlined. Then open `trainer.html` by double clicking it. No server, no build
step, no network — the CSVs are embedded in the file rather than fetched,
because `fetch()` from `file://` is blocked by CORS and fails silently.

When the price book is re-extracted, drop the new CSVs into `data/` (same
filenames, same 18 columns) and re-run. Nothing else needs to change. The clock
prices are already versioned in the source as *effective June 10 2026, Version
1.1*, so expect to do this.

**Check the build report against these numbers.** It prints them every run:

| Category          | Rows |
|-------------------|-----:|
| RM                |  413 |
| Clocks            |   63 |
| Required Products |   30 |
| **Total**         |  506 |

A mismatch prints `<-- MISMATCH` next to the offending line and repeats it under
WARNINGS. Do not ship a build that warns without understanding why.

## Reading the build report

```
Rows by category      row and product counts, with expected-count assertions
Price type            recurring / one_time / both / none, plus column populations
Tiering               tiered vs untiered, open-ended top tiers
Quiz eligibility      eligible and excluded counts, with a reason breakdown
Overrides applied     every OVERRIDES entry that fired, old value -> new value
Promotions            total / active / inactive
Drill modes           which modes will be live in the UI and why
```

## OVERRIDES

`OVERRIDES` sits at the top of `build.py`. It is the **only** mechanism that
changes a source value, and every entry is written by hand. Nothing in this
codebase detects or infers a correction.

Currently one entry:

| Row id  | Product                        | Field          | Printed | Shown |
|---------|--------------------------------|----------------|--------:|------:|
| RM-0018 | isolved Benefits Adminstration | `tier_min_ees` |     100 |  1000 |

The source prints that tier as `05) 100-1499`, sitting between `04) 500-999` and
`06) 1500-2499`. The lower bound is almost certainly meant to read 1000.

### Adding an entry

1. Find the row's synthetic `id`. Expand the row in Lookup — it is printed as
   **Row id**. Ids are `PREFIX-NNNN` where the prefix is `RM`, `CLK` or `REQ` and
   `NNNN` is the row's 1-based position within its category, zero padded.
   Part numbers are *not* usable as keys: part 8145 maps to three different
   products at two prices, 16 RM rows have part `0`, and 30 have `N/A`.
2. Add a block to `OVERRIDES` keyed by that id:

   ```python
   OVERRIDES = {
       "RM-0018": {
           "tier_min_ees": {
               "value": "1000",
               "reason": "Why this is provably a misprint, and what it should read.",
           }
       },
   }
   ```

   The key is the row id, the inner keys are column names, `value` is always a
   **string** (it goes through the same parsers as source data), and `reason` is
   surfaced to the user on hover — write it for the person who has to trust the
   number on a call.
3. Re-run `python3 build.py` and confirm the entry appears under
   *Overrides applied* in the report.

### Removing an entry

Delete the block and re-run. The row reverts to the printed value everywhere and
loses its badge.

### What an override does to a row

- The printed value is preserved as `<column>_source` (e.g. `tier_min_ees_source`)
  and shown in the expanded detail as *"1000 (source printed 100)"*.
- The row renders an amber **adjusted, unconfirmed** badge everywhere it appears
  — the tier ladder, search results, and headcount resolver results. Hovering it
  shows both values and the reason.
- The row stays fully quiz eligible.
- If an id in `OVERRIDES` matches no row after a rebuild, the build **warns**
  rather than failing silently — that usually means the source was reordered and
  the id now points somewhere else. Verify before trusting the build.

## Promotions

`data/promotions.csv` is optional. If it is absent the build prints a warning,
the Promos tab explains what is missing, and the Promo recall drill is disabled.
Drop the file in and re-run — nothing else needs configuring. The loader finds
the status column by name (`status` / `Is_Active` / `active`) and treats
`Active Promo` / `active` / `true` / `yes` as active; every other column is
displayed as-is. The UI defaults to active-only with a toggle for expired ones.

Promotions are never linked to a price row and never modify a list price.

## Data rules the code enforces

These come from `data/extraction_gaps.md`. Read it before changing normalization.

- **No number is ever generated, interpolated, rounded or inferred.** Currency
  renders as the exact string the source printed. Parsed numeric copies
  (`unit_price_num` etc.) exist only for sorting and comparison.
- **`N/A` ≠ `UNKNOWN` ≠ zero.** `N/A` means the field does not apply, `UNKNOWN`
  means the source did not print it. They render as visually distinct badges
  with different hover text and are never coerced to `0`, `""` or `None`.
- **Price lives in two columns.** 389 rows carry `unit_price`, 242 carry
  `one_time_fee`, 164 carry both. Each row gets a `price_type` of `recurring` /
  `one_time` / `both` / `none`.
- **39 rows have no price at all** — the 30 Required Products rows, 7 RM rows
  priced "Custom", and 2 rows (Health & Welfare, Property & Casualty) where the
  source printed nothing. They stay fully searchable and are permanently
  excluded from every quiz answer pool.
- **Open-ended top tiers** (`no maximum`) parse to `Infinity` and render as `N+ EE`.
- **Clock ESA rows** carry a recurring fee with no printed billing frequency.
  They display as UNKNOWN, never assumed monthly, and are quiz-excluded.
- **`monthly_minimum`** (45 rows) is a separate constraint from unit price and
  gets its own callout in lookup, resolver and flashcard reveals. Quoting the
  PEPM without the minimum is the mistake this tool exists to prevent.
- **Multiple choice distractors are always real prices** — drawn from the same
  product's other tiers first, then from other products in the same category on
  the same price column. Never generated.
- **A drill mode with fewer than 10 eligible items is disabled** with an
  explanation rather than degrading question quality. Promo recall is the one
  exception: it reviews the full promotion deck rather than sampling from a
  pool, so the floor does not apply and the UI says so.

## Quiz eligibility

A row is excluded from drills if it has no price, its `pricing_model` is
`Custom` or `UNKNOWN`, or its category is Required Products. Current split:
**451 eligible / 55 excluded**. Expanded rows show their exclusion reason.

## Progress and storage

Leitner spaced repetition, five boxes, intervals 0 / 1 / 3 / 7 / 21 days, keyed
to row `id`. A miss demotes straight to box 1. Progress lives in `localStorage`
under `pricebookTrainer.progress.v1` and is scoped to the browser you drill in.

The stored blob records the `data_version` it was written against. After a
rebuild the app keeps every item whose row id still exists, drops the ones that
do not, and tells you how many were dropped. A rebuild never wipes progress.

"Drill these 10" on the Stats tab queues your weakest items as the draw pool for
the next drill session.

## Testing a change to the app

`trainer.html` is generated — edit `trainer.template.html`, never the output.
A quick syntax check before opening it in a browser:

```bash
python3 build.py
python3 - <<'PY'
import io
h = io.open('trainer.html', encoding='utf-8').read()
i, j = h.index('<script>'), h.rindex('</script>')
io.open('/tmp/app.js', 'w', encoding='utf-8').write(h[i+8:j])
PY
node --check /tmp/app.js
```
