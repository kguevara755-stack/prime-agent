#!/usr/bin/env python3
"""
build.py - Price book normalizer + app builder.

Reads the three source files from ./data, normalizes them, and writes a single
self-contained trainer.html with the dataset inlined as a JS object.

Usage:
    python3 build.py

Re-running this script is the only step required to refresh the app after the
price book is re-extracted. Drop new CSVs into ./data and run it again.

Design rules enforced here (see README.md):
  * No price is ever generated, interpolated, rounded or inferred.
  * "N/A" (field does not apply) and "UNKNOWN" (source did not print it) are
    preserved as distinct values and never coerced to 0 / "" / None.
  * The only corrections applied to source data are the hand-written entries in
    OVERRIDES below. Nothing is auto-detected.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
TEMPLATE = os.path.join(HERE, "trainer.template.html")
OUTPUT = os.path.join(HERE, "trainer.html")

PRICING_CSV = os.path.join(DATA_DIR, "pricing_master.csv")
PROMOS_CSV = os.path.join(DATA_DIR, "promotions.csv")
GAPS_MD = os.path.join(DATA_DIR, "extraction_gaps.md")

# Sentinels from the extraction. These are meaningfully different from each
# other and from a real value; never collapse them.
NA = "N/A"
UNKNOWN = "UNKNOWN"
SENTINELS = (NA, UNKNOWN)

CATEGORY_PREFIX = {
    "RM": "RM",
    "Clocks": "CLK",
    "Required Products": "REQ",
}

# Counts the extraction's verification pass asserts. A mismatch means the source
# changed shape and the build report will say so loudly.
EXPECTED_ROWS = 506
EXPECTED_BY_CATEGORY = {"RM": 413, "Clocks": 63, "Required Products": 30}


# ---------------------------------------------------------------------------
# OVERRIDES
# ---------------------------------------------------------------------------
# Hand-maintained corrections to values that are provably mis-printed in the
# source. Keyed by the synthetic row id (see make_id). Each entry maps a column
# name to the corrected value plus a reason string that is surfaced in the UI.
#
# Adding an entry is a deliberate, human decision. Never write code that infers
# a correction and never extend this dict programmatically. See README.md.
#
# For every overridden column the original printed value is preserved on the row
# as "<column>_source", and the row renders with an "adjusted, unconfirmed"
# badge everywhere it appears.
OVERRIDES = {
    "RM-0018": {
        "tier_min_ees": {
            "value": "1000",
            "reason": (
                "Source prints this tier as \"05) 100-1499\", sitting between "
                "\"04) 500-999\" and \"06) 1500-2499\" in the ladder for isolved "
                "Benefits Adminstration. The lower bound is almost certainly meant "
                "to read 1000. Adjusted so the ladder resolves; unconfirmed against "
                "the source system."
            ),
        }
    },
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def is_sentinel(value: str) -> bool:
    return value in SENTINELS


def parse_currency(value: str):
    """'$1,234.56' -> 1234.56. Sentinels and unparseable text -> None.

    Returns None rather than 0 so that "no price" can never be rendered or
    compared as a real price of zero.
    """
    if value is None or is_sentinel(value):
        return None
    text = value.strip()
    if not text:
        return None
    match = re.search(r"-?\$?\s*([\d,]+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_tier_bound(value: str, *, upper: bool):
    """Tier bounds to numbers. 'no maximum' -> Infinity. Sentinels -> None."""
    if value is None or is_sentinel(value):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text in ("no maximum", "no max", "+", "no minimum"):
        return float("inf") if upper else float("-inf")
    match = re.search(r"(\d[\d,]*)", text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def split_dependencies(value: str):
    """'Payroll; ACA Compliance' -> ['Payroll', 'ACA Compliance']."""
    if value is None or is_sentinel(value):
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def make_id(category: str, index_within_category: int) -> str:
    """Stable synthetic id: category prefix + zero padded source row index.

    product_code is not unique (part 8145 is three products, 16 rows are "0",
    30 are "N/A"), so everything downstream keys off this instead.
    """
    prefix = CATEGORY_PREFIX.get(category, slugify(category).upper()[:3] or "GEN")
    return "%s-%04d" % (prefix, index_within_category)


def json_ready(value):
    """json.dumps cannot emit Infinity as valid JS-safe JSON for our purposes.

    Tier maxima of Infinity are emitted as the string "Infinity" and rehydrated
    in the browser. Everything else passes through.
    """
    if isinstance(value, float):
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
    return value


# ---------------------------------------------------------------------------
# Load + normalize pricing_master.csv
# ---------------------------------------------------------------------------

def load_pricing(report):
    if not os.path.exists(PRICING_CSV):
        report.fatal("Missing required file: %s" % PRICING_CSV)

    with io.open(PRICING_CSV, encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))

    if not raw_rows:
        report.fatal("pricing_master.csv contains no data rows.")

    per_category_index = Counter()
    rows = []
    overrides_applied = []
    unused_overrides = set(OVERRIDES)

    for raw in raw_rows:
        category = (raw.get("category") or "").strip()
        per_category_index[category] += 1
        row_id = make_id(category, per_category_index[category])

        # Copy source values verbatim first; sentinels survive untouched.
        row = OrderedDict()
        row["id"] = row_id
        for column in (
            "category", "product_name", "product_code", "description",
            "pricing_model", "unit_price", "unit_basis", "tier_min_ees",
            "tier_max_ees", "monthly_minimum", "one_time_fee", "setup_fee",
            "billing_frequency", "required_dependencies", "conditions",
            "effective_date", "source_file", "source_page",
        ):
            row[column] = (raw.get(column) or "").strip()

        # --- apply OVERRIDES -------------------------------------------------
        override_spec = OVERRIDES.get(row_id)
        if override_spec:
            unused_overrides.discard(row_id)
            row["overrides"] = []
            for column, spec in override_spec.items():
                original = row.get(column)
                row[column + "_source"] = original
                row[column] = spec["value"]
                entry = {
                    "id": row_id,
                    "field": column,
                    "from": original,
                    "to": spec["value"],
                    "reason": spec["reason"],
                    "product_name": row["product_name"],
                }
                row["overrides"].append(entry)
                overrides_applied.append(entry)
            row["adjusted"] = True
        else:
            row["adjusted"] = False

        # --- derived numerics ------------------------------------------------
        row["unit_price_num"] = parse_currency(row["unit_price"])
        row["one_time_fee_num"] = parse_currency(row["one_time_fee"])
        row["monthly_minimum_num"] = parse_currency(row["monthly_minimum"])
        row["setup_fee_num"] = parse_currency(row["setup_fee"])
        row["tier_min_num"] = parse_tier_bound(row["tier_min_ees"], upper=False)
        row["tier_max_num"] = parse_tier_bound(row["tier_max_ees"], upper=True)
        row["tiered"] = row["tier_min_num"] is not None or row["tier_max_num"] is not None
        row["dependencies"] = split_dependencies(row["required_dependencies"])

        # --- price_type -------------------------------------------------------
        has_recurring = row["unit_price_num"] is not None
        has_one_time = row["one_time_fee_num"] is not None
        if has_recurring and has_one_time:
            row["price_type"] = "both"
        elif has_recurring:
            row["price_type"] = "recurring"
        elif has_one_time:
            row["price_type"] = "one_time"
        else:
            row["price_type"] = "none"

        # --- quiz eligibility -------------------------------------------------
        reasons = []
        if row["price_type"] == "none":
            reasons.append("no price")
        if row["pricing_model"] == "Custom":
            reasons.append("pricing_model = Custom")
        if row["pricing_model"] == UNKNOWN:
            reasons.append("pricing_model = UNKNOWN")
        if row["category"] == "Required Products":
            reasons.append("category = Required Products")
        row["quiz_eligible"] = not reasons
        row["exclusion_reasons"] = reasons

        # Grouping key for the tier ladder card. product_code alone is unsafe,
        # so the group is name + code within a category.
        row["group_id"] = "%s::%s::%s" % (
            row["category"], row["product_name"], row["product_code"]
        )

        rows.append(row)

    for missing in sorted(unused_overrides):
        report.warn(
            "OVERRIDES entry '%s' matched no row. The source may have been "
            "re-ordered - verify the id before trusting the build." % missing
        )

    return rows, overrides_applied


def build_groups(rows):
    """One card per product; multi-tier products collapse into a single group."""
    groups = OrderedDict()
    for row in rows:
        group = groups.get(row["group_id"])
        if group is None:
            group = {
                "group_id": row["group_id"],
                "category": row["category"],
                "product_name": row["product_name"],
                "product_code": row["product_code"],
                "description": row["description"],
                "row_ids": [],
            }
            groups[row["group_id"]] = group
        group["row_ids"].append(row["id"])
        if is_sentinel(group["description"]) and not is_sentinel(row["description"]):
            group["description"] = row["description"]

    for group in groups.values():
        group["tier_count"] = len(group["row_ids"])
    return list(groups.values())


# ---------------------------------------------------------------------------
# Load promotions.csv (optional - the app degrades gracefully without it)
# ---------------------------------------------------------------------------

def load_promotions(report):
    if not os.path.exists(PROMOS_CSV):
        report.warn(
            "data/promotions.csv not found. Promo recall drill will be disabled "
            "and the promotions panel will render empty. Drop the file in ./data "
            "and re-run to enable it - no other change is needed."
        )
        return [], []

    with io.open(PROMOS_CSV, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        raw_rows = list(reader)

    # The promotions schema was not specified up front, so the status column is
    # located by name rather than assumed by position.
    status_col = next(
        (c for c in columns if c and c.strip().lower() in
         ("status", "is_active", "isactive", "active")),
        None,
    )
    name_col = next(
        (c for c in columns if c and "name" in c.strip().lower()),
        columns[0] if columns else None,
    )

    promos = []
    for index, raw in enumerate(raw_rows, start=1):
        record = OrderedDict()
        record["id"] = "PROMO-%03d" % index
        for column in columns:
            record[column] = (raw.get(column) or "").strip()
        status = (raw.get(status_col) or "").strip() if status_col else ""
        record["_status"] = status or UNKNOWN
        record["_active"] = status.strip().lower() in ("active promo", "active", "true", "yes")
        record["_name"] = (raw.get(name_col) or "").strip() if name_col else record["id"]
        promos.append(record)

    if status_col is None:
        report.warn(
            "promotions.csv has no recognizable status column (looked for "
            "status / Is_Active). All promotions will show status UNKNOWN."
        )

    return promos, columns


# ---------------------------------------------------------------------------
# Build report
# ---------------------------------------------------------------------------

class Report(object):
    def __init__(self):
        self.warnings = []
        self.lines = []

    def say(self, text=""):
        self.lines.append(text)
        print(text)

    def warn(self, text):
        self.warnings.append(text)

    def fatal(self, text):
        sys.stderr.write("FATAL: %s\n" % text)
        raise SystemExit(1)

    def flush_warnings(self):
        if not self.warnings:
            return
        self.say("")
        self.say("WARNINGS (%d)" % len(self.warnings))
        self.say("-" * 68)
        for warning in self.warnings:
            self.say("  ! " + warning)


DRILL_LABELS = [
    ("flashcard", "Flashcard", "eligible rows", False),
    ("tier", "Tier resolution", "drillable tiered rows", False),
    ("speed", "Speed round", "eligible rows", False),
    ("quote", "Build the quote", "Required Products rows", False),
    ("promo", "Promo recall", "promotions", True),
]


def emit_report(report, rows, groups, promos, overrides_applied):
    say = report.say
    say("=" * 68)
    say("PRICE BOOK BUILD REPORT")
    say("=" * 68)
    say("")

    say("Rows by category")
    say("-" * 68)
    by_category = Counter(row["category"] for row in rows)
    for category in sorted(by_category):
        count = by_category[category]
        expected = EXPECTED_BY_CATEGORY.get(category)
        products = len({r["group_id"] for r in rows if r["category"] == category})
        flag = ""
        if expected is not None and expected != count:
            flag = "  <-- MISMATCH, expected %d" % expected
            report.warn("Category %s has %d rows, expected %d." % (category, count, expected))
        say("  %-20s %4d rows   %4d products%s" % (category, count, products, flag))
    total_flag = ""
    if len(rows) != EXPECTED_ROWS:
        total_flag = "  <-- MISMATCH, expected %d" % EXPECTED_ROWS
        report.warn("Total row count is %d, expected %d." % (len(rows), EXPECTED_ROWS))
    say("  %-20s %4d rows   %4d products%s" % ("TOTAL", len(rows), len(groups), total_flag))
    say("")

    say("Price type")
    say("-" * 68)
    by_type = Counter(row["price_type"] for row in rows)
    for price_type in ("recurring", "one_time", "both", "none"):
        say("  %-20s %4d" % (price_type, by_type.get(price_type, 0)))
    say("  %-20s %4d" % ("unit_price populated", sum(
        1 for r in rows if r["unit_price_num"] is not None)))
    say("  %-20s %4d" % ("one_time populated", sum(
        1 for r in rows if r["one_time_fee_num"] is not None)))
    say("  %-20s %4d" % ("monthly_minimum", sum(
        1 for r in rows if r["monthly_minimum_num"] is not None)))
    say("")

    say("Tiering")
    say("-" * 68)
    tiered = sum(1 for r in rows if r["tiered"])
    say("  %-20s %4d" % ("tiered rows", tiered))
    say("  %-20s %4d" % ("untiered rows", len(rows) - tiered))
    say("  %-20s %4d" % ("open-ended tops", sum(
        1 for r in rows if r["tier_max_num"] == float("inf"))))
    say("")

    say("Quiz eligibility")
    say("-" * 68)
    eligible = [r for r in rows if r["quiz_eligible"]]
    excluded = [r for r in rows if not r["quiz_eligible"]]
    say("  %-20s %4d" % ("eligible", len(eligible)))
    say("  %-20s %4d" % ("excluded", len(excluded)))
    reason_counts = Counter()
    for row in excluded:
        for reason in row["exclusion_reasons"]:
            reason_counts[reason] += 1
    for reason, count in reason_counts.most_common():
        say("      %-30s %4d rows" % (reason, count))
    say("")
    say("  Eligible by category")
    for category in sorted(by_category):
        say("      %-30s %4d" % (
            category, sum(1 for r in eligible if r["category"] == category)))
    say("")

    say("Overrides applied (%d)" % len(overrides_applied))
    say("-" * 68)
    if not overrides_applied:
        say("  none")
    for entry in overrides_applied:
        say("  %s  %s" % (entry["id"], entry["product_name"]))
        say("      %s: %r -> %r" % (entry["field"], entry["from"], entry["to"]))
        say("      reason: %s" % entry["reason"])
    say("")

    say("Promotions")
    say("-" * 68)
    if not promos:
        say("  0 loaded (data/promotions.csv absent)")
    else:
        active = sum(1 for p in promos if p["_active"])
        say("  %-20s %4d" % ("total", len(promos)))
        say("  %-20s %4d" % ("active", active))
        say("  %-20s %4d" % ("inactive/expired", len(promos) - active))
    say("")

    say("Drill modes (floor: %d items)" % MIN_POOL)
    say("-" * 68)
    availability = drill_availability(rows, promos)
    for key, label, unit, exempt in DRILL_LABELS:
        pool = availability[key]
        live = pool >= 1 if exempt else pool >= MIN_POOL
        say("  %-20s %-9s %4d %s%s" % (
            label, "ENABLED" if live else "disabled", pool, unit,
            "  (full-deck review, floor N/A)" if exempt and live else ""))
    say("")


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

MIN_POOL = 10

def drill_availability(rows, promos):
    """Precompute the eligible pool size behind each drill mode.

    A mode with fewer than MIN_POOL items is disabled in the UI with an
    explanation rather than being padded out with degraded questions. Mirrors
    the pool logic in the app so the build report and the UI agree.
    """
    eligible = [r for r in rows if r["quiz_eligible"]]

    # Tier resolution needs a ladder whose price column actually varies across
    # tiers, otherwise every multiple-choice option would be the same number.
    ladders = defaultdict(list)
    for row in eligible:
        if row["tiered"] and row["tier_min_num"] is not None:
            ladders[row["group_id"]].append(row)

    tier_ids = set()
    for bands in ladders.values():
        for column in ("unit_price", "one_time_fee"):
            priced = [r for r in bands if r[column + "_num"] is not None]
            if len(priced) >= 2 and len({r[column] for r in priced}) >= 2:
                tier_ids.update(r["id"] for r in priced)

    required = [r for r in rows if r["category"] == "Required Products" and r["dependencies"]]

    return {
        "flashcard": len(eligible),
        "tier": len(tier_ids),
        "speed": len(eligible),
        "quote": len(required),
        "promo": len(promos),
        "minimum": MIN_POOL,
    }


def main():
    report = Report()
    rows, overrides_applied = load_pricing(report)
    groups = build_groups(rows)
    promos, promo_columns = load_promotions(report)

    emit_report(report, rows, groups, promos, overrides_applied)

    payload_rows = [
        OrderedDict((key, json_ready(value)) for key, value in row.items())
        for row in rows
    ]

    fingerprint = hashlib.sha256(
        json.dumps(payload_rows, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]

    dataset = OrderedDict()
    dataset["schema_version"] = 1
    dataset["data_version"] = fingerprint
    dataset["counts"] = {
        "total": len(rows),
        "by_category": dict(Counter(r["category"] for r in rows)),
        "quiz_eligible": sum(1 for r in rows if r["quiz_eligible"]),
        "excluded": sum(1 for r in rows if not r["quiz_eligible"]),
    }
    dataset["rows"] = payload_rows
    dataset["groups"] = groups
    dataset["promotions"] = promos
    dataset["promotion_columns"] = promo_columns
    dataset["overrides"] = overrides_applied
    dataset["availability"] = drill_availability(rows, promos)
    dataset["gaps_present"] = os.path.exists(GAPS_MD)

    if not os.path.exists(TEMPLATE):
        report.fatal("Missing template: %s" % TEMPLATE)

    with io.open(TEMPLATE, encoding="utf-8") as handle:
        template = handle.read()

    if "/*__PRICEBOOK_DATA__*/" not in template:
        report.fatal("Template has no /*__PRICEBOOK_DATA__*/ placeholder.")

    serialized = json.dumps(dataset, ensure_ascii=False, default=str)
    # Guard against a stray "</script>" inside description text closing the tag.
    serialized = serialized.replace("</", "<\\/")

    html = template.replace("/*__PRICEBOOK_DATA__*/", serialized)
    html = html.replace("__DATA_VERSION__", fingerprint)

    with io.open(OUTPUT, "w", encoding="utf-8") as handle:
        handle.write(html)

    report.flush_warnings()
    report.say("")
    report.say("=" * 68)
    report.say("Wrote %s  (%.0f KB, data_version %s)" % (
        os.path.relpath(OUTPUT, HERE), os.path.getsize(OUTPUT) / 1024.0, fingerprint))
    report.say("Open it by double clicking. No server required.")
    report.say("=" * 68)


if __name__ == "__main__":
    main()
