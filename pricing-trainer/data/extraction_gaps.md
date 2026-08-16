# extraction_gaps.md

Source system: **Price Book V2** (Power BI app report, "PriceBook V2" workspace), data updated 8/15/26.
Pages opened: Announcement, Menu, RM, Required Products, ActivePromotions, Clock Update.
Pages NOT opened (out of scope): FSR, Network, Tax Services, Benefit Services.

## 1. Source locations that were not provided / could not be opened

- The prompt's source placeholders were left unfilled: no SharePoint / Teams / Drive folder path, no deck name (e.g. "Win One 2025 Menu"), and no linked rate sheets / price books / PDFs were supplied. The only source available in the session was the already-open Power BI report **Price Book V2**. Everything in the two CSVs comes from that report. If a menu deck or external rate cards exist, they were not extracted.
- The Announcement page contains a hyperlink labelled "Price Book V2" with the text "Please use the following link. If it takes you to the same page, you are in the right spot" - it is a self-reference, so no external rate card was reachable from it.
- No other outbound links to sub-decks or rate cards were found on the in-scope pages.

## 2. Report-wide filter that limits what the source shows

- All pages are filtered "Effective End Date **is blank**" and "Effective Start **is (All)**". The source therefore only displays line items that have not been end-dated. Any superseded / expired price rows exist in the dataset but are hidden and were not extracted.
- The RM page's table visual is additionally filtered "Price Book **is CAM Price List**". The page is titled "isolved RM Price Book" but the underlying Price Book field value is "CAM Price List" - a naming mismatch between page title and data.
- The dataset's Price Book field contains these values (row counts as shown in the filter list, in that filter context): (Blank) 13, Benefit Services Price List 65, CAM Price List 386, Clock Price List 60, FSR Price List 540, Network Price List 322. There is no value literally named "RM Price List".
- **Unresolved count discrepancy:** the filter list shows "Clock Price List 60", while the Clock Price Book visual renders 47 product rows (63 priced line items once the 16 ESA SKUs are split out). The two counts were measured under different filter contexts and could not be reconciled from the report UI.

## 3. Missing / illegible / not-printed figures (UNKNOWN cells)

Total UNKNOWN cells in pricing_master.csv: **45**  (unit_price 9, pricing_model 18, unit_basis 2, billing_frequency 16)

### 3a. RM rows where no price is printed
Unit UOM reads "Custom" and every fee column is blank -> unit_price = UNKNOWN, pricing_model = "Custom":
- Time off Program Analysis (part 7945), no tier - source row 297
- Compliance Pro (8086), tier "04) 250-+EE" - source row 309
- Compliance Pro + (8087), tier "04) 250-+EE" - source row 313
- Compliance Pro Advantage (8088), tier "04) 250-+EE" - source row 317
- Compliance Pro with PAC (8086), tier "04) 250-+EE" - source row 372
- Compliance Pro + with PAC (8422), tier "04) 250-+EE" - source row 376
- Compliance Pro Advantage with PAC (8088), tier "04) 250-+EE" - source row 380

No fee **and** no UOM anywhere on the row -> unit_price, pricing_model and unit_basis all UNKNOWN:
- Health & Welfare (part 0) - source row 340
- Property & Casualty (part 0) - source row 341

### 3b. Clock ESA (extended service agreement) SKUs
The Clock Price Book prints an "ESA Part #" and "ESA Fee" on the same row as each clock kit, but prints **no billing frequency for the ESA fee**. All 16 ESA rows therefore carry pricing_model = UNKNOWN and billing_frequency = UNKNOWN (unit_price is populated with the printed ESA fee).

### 3c. Promotions
- **Acrisure Promotion**: the Language column is empty for this promotion and its description contains no stacking statement -> stacking_rules = UNKNOWN.

## 4. Ambiguous pricing models / schema mapping decisions

These are places where the source structure does not map 1:1 onto the requested column set. No figure was altered; the decisions below only affect which column a printed figure landed in.

1. **RM has three fee columns** - "One-Time Fee / One-Time UOM", "Base Fee / Base UOM", "Unit Fee / Unit UOM" - and the requested schema has no base_fee column. Rule applied: recurring per-unit amounts -> unit_price; amounts labelled one-time -> one_time_fee; Base Fee amounts whose Base UOM says "Monthly Minmum" [sic] / "Annual Minimum" -> monthly_minimum (with the source UOM label kept in the cell); all other Base Fee amounts (Base UOM = "Monthly", "Per User Per Month", "Per Year/FEIN", "Per Year", "PEPY", "Annual Renewal Fee") are recorded verbatim in the conditions field prefixed "Base Fee:" so nothing is lost. 164 RM rows carry both a one-time fee and a recurring fee; in those rows the one-time amount and its One-Time UOM are also noted in conditions.
2. **No setup_fee column exists in any source.** setup_fee = N/A on every row. One-time amounts are in one_time_fee even where the product name says "Setup fee" or "Implementation" - the source column heading is literally "One-Time Fee".
3. **No billing_frequency column exists on the RM price book.** Frequency is embedded in the source UOM vocabulary (PEPM, Monthly, PEPY, Annual, per participant/month, per hour, One-Time, ...). Those exact terms are recorded in pricing_model / unit_basis and billing_frequency = N/A for all 413 RM rows.
4. **Clock Price Book has a single "Billing" column** (values "One-Time", "Monthly"). It is recorded in both pricing_model and billing_frequency because it is the only term the source gives. Clock rows have no UOM column, so unit_basis = N/A.
5. **Minimum employee counts and minimum order quantities have no dedicated column in the requested schema.** Employee minimums stated in the RM "First Notes" column (e.g. "25 Employee Minimum", "1-150EE Min into $2,550 Monthly min") are kept verbatim in conditions; clock accessory minimum order quantities ("Min 5", data-min-qty=5) are kept verbatim in conditions. Where First Notes states a monthly minimum with a dollar figure (e.g. "Monthly Minimum fee of $179", "$100 Monthly Minimum Fee Applies", "$129 Monthly Minimum Fee Applies"), the figure is also placed in monthly_minimum, flagged "(per First Notes)".
6. **effective_date** - only the Clock Price Book prints one ("Prices effective: June 10, 2026 - Version 1.1"), applied to all 63 clock rows. The RM and Required Products pages print no per-row effective date, so effective_date = N/A there. The dataset does contain "Effective Start" / "Effective End Date" fields, but they are not exposed as columns in the visuals, so per-row effective dates could not be read.
7. **Required Products page contains no pricing at all** - it is a product x platform-dependency matrix. All 30 rows therefore have N/A in every price, tier, minimum, fee and billing field; the checked columns are recorded in required_dependencies and the "Standalone" column plus the "Notes" column in conditions.
8. **Tier open ends and non-employee tiers.** Bands were split into tier_min_ees / tier_max_ees exactly as printed. Open-ended top tiers are written as tier_max_ees = "no maximum". Rows with no tier get N/A. Where the printed tier label is not a plain employee band, the original label is preserved verbatim in conditions.

## 5. Malformed / suspect values in the source (copied as printed, not corrected)

- Tier "05) 100-1499" on **isolved Benefits Adminstration** ($5.40 PEPM) sits between "04) 500-999" and "06) 1500-2499", so it is almost certainly meant to read 1000-1499. Recorded as printed: tier_min_ees 100, tier_max_ees 1499. **This creates an overlapping / gapped tier ladder for that product - do not interpolate.**
- Malformed open-ended tier labels: "07) 2501-+" (Always On-HR), "04) 250-+EE" (all six Compliance Pro variants), "06) 1000-+EE" (FSA, HRA, Transit, Parking, Tuition, Lifestyle Enrollment), "07) 1000-+EE" (Employee Learning Booster). Read as "min - no maximum"; original label kept in conditions.
- Hour-based tiers on **Customized Training** ("01) 1Hr" = $1,725.00 one-time, "02) 2Hr" = $2,300.00 one-time) are not employee bands: tier_min_ees / tier_max_ees = N/A, label preserved in conditions.
- Source spelling errors copied verbatim: "Additonal Paygroups Setup fee", "Monthly Minmum" (Base UOM), "Ceritfied Payroll", "isolved Benefits Adminstration", "straight deduciton", "Employement Record", "dependecy", "Formally Applicant Tracking".
- **Truncated product name:** part 8281 appears as both "Optimization Services: Accrual Plan" and "Optimization Services:" with an identical price row - the second name looks truncated/incomplete in the source.

## 6. Conflicts / same identifier, different price

No two independent documents were available to cross-check, so all conflicts below are **internal to Price Book V2**. Both values and both locations are listed; neither was chosen over the other.

### 6a. Clock Price Book - same part number, two different products and prices
| Part # | Value A (source section) | Value B (source section) |
|---|---|---|
| 1342 | "Barcode Swiper Card Reader" $145.00 One-Time (NXG G2 Pro section) and "Barcode Swipe Reader" $145.00 One-Time (NXG LE Clock section) | "Barcode Swipe Cards" $4.25 One-Time, Min 5 (Time Clock Accessories section) |
| 1347 | "HID Proximity Card Reader" $145.00 One-Time (NXG G2 Pro section) and "Proximity Card Reader" $145.00 One-Time (NXG LE Clock section) | "HID Proximity Card" $9.00 One-Time, Min 5 (Time Clock Accessories section) |
| 1348 | "Magnetic Swipe Card Reader" $95.00 One-Time (NXG LE Clock section) | "Magnetic Swipe Cards" $4.25 One-Time, Min 5 (Time Clock Accessories section) |

### 6b. RM price book - one part number shared by differently named products
Part 8145 is used by three products with two different one-time fees: "Custom Employee Handbooks" $3,000.00; "Independent Contractor vs Employee Analysis" $575.00; "Exempt vs Non Exempt Analysis" $575.00.
Other part numbers shared across different product names (prices differ by product/tier): 1978 (COBRA / COBRA Direct Billing), 7767 (1094/1095-C production (25 Employee Minimum) / ACA Print and File (isolved payroll)), 8086 (Compliance Pro / Compliance Pro with PAC), 8088 (Compliance Pro Advantage / Compliance Pro Advantage with PAC), 8281, 8283 (Optimization Services: Pay / Optimization Services: Benefit), 8667 (401(k) data transmission 180 degree / 360 degree), 8800 (four isolved 401(k) powered by 401GO variants).
Note also that 16 RM rows carry part number "0", which is not a usable product code.

## 7. Promotions - scope and status notes

- The scope list asked for "Current Promotions" and "Active Promotions". The source has **one** promotions page ("Active Promotions", Last Updated Nov 11, 2025) and labels promotions "Active Promo" / "Inactive Promo" via an Is_Active field and an on-page toggle. There is no "Current" label anywhere, so no promotion is tagged "current".
- The Is_Active field contains exactly 2 "Active Promo" and 4 "Inactive Promo" values. The page as delivered (before any interaction) displayed exactly the two Active promotions: "FY 26- Win Back Offer- 2nd & 6 months free**" and "Benefit Services Win-Back". The remaining four are recorded as "Inactive Promo". Their end dates (March 31 2026, December 31 2025, October 31 2025, October 31 2025) are all in the past, consistent with that labelling.
- The on-page "Active Promo" / "Inactive Promo" buttons behaved inconsistently during navigation (one state returned all six rows, and one bookmark state also applied a "Price Book is FSR Price List" filter to a companion visual). Status was therefore taken from the Is_Active field values and the untouched default view, not from the buttons.
- All 6 promotions were captured so nothing is silently dropped. If only the 2 Active ones are wanted, filter promotions.csv on status = "Active Promo".
- **Out-of-scope overlap:** two of the promotions ("Benefit Services Win-Back", "Benefit Services Promotion") apply to Benefits Services, which is an out-of-scope category. They are recorded because they appear on the in-scope Active Promotions page, and only the promotion's own text is captured - the Benefit Services price list was never opened and no Benefits Services pricing was pulled.
- Promotion descriptions and legal Language columns contain further conditions (bookings/commission treatment, discount-percentage caps, contract-term requirements). The stacking sentence and the eligibility language were extracted; the full multi-paragraph legal Language column was not copied into the CSV.

## 8. Out-of-scope dependency references recorded by name only

- Required Products notes reference out-of-scope or adjacent items by name only, e.g. "HR would be required if Eligibility Rules are in that package. It would be a straight deduciton if we don't use Benefits" (401k), "Benefits recommended for full experience" (COBRA), "Benefits and payroll recommended for full experience" (FSA). No pricing was pulled for those.
- The RM (CAM Price List) table itself contains benefits-flavoured products (e.g. isolved Benefits Administration, COBRA, FSA/HRA enrollment, Compliance Pro). These are rows of the in-scope RM price book, not of the out-of-scope "Benefit Services Price List" price book, which was never opened.

## 9. Verification pass result

- RM: all 414 grid rows x 13 columns re-scraped from a freshly reloaded page and compared cell-by-cell against the first capture - **0 differences**.
- Required Products: all 31 grid rows x 12 columns re-scraped and compared - **0 differences**.
- Clock Price Book: source re-opened, underlying HTML re-parsed, 7 sections / 47 rows and every part number, fee, billing term, ESA part, ESA fee, min-qty, status and series tag compared - **0 differences**. Effective-date line re-confirmed as "Prices effective: June 10, 2026 - Version 1.1".
- Promotions: 6 promotion rows re-read; the page's own toggle state changed which subset the visual rendered (see section 7), so status was reconciled against the Is_Active value counts rather than the toggle.
- Structural checks on pricing_master.csv: 506 data rows, every row exactly 18 fields, **0 empty cells**, **0 rows missing source_file or source_page**.
- Category check: only RM (413), Clocks (63) and Required Products (30) appear in the category column. No FSR, Network, Tax Services or Benefits Services category row is present.
