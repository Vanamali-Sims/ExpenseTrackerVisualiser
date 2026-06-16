#!/usr/bin/env python3
"""
Expense Categorisation Pipeline
================================
Reads items.csv (from the Everyday Rewards scraper) and produces a clean,
enriched CSV ready for visualisation.

What it does:
  1. Filters out noise rows (discount labels, offer line items, coupons)
  2. Classifies each item into a category + essentiality tier
  3. Adds month_key column for time-series aggregation
  4. Computes savings projections — "if you cut X, you save $Y/mo, $Z/yr"
  5. Writes items_categorised.csv

Categorisation is keyword-based and deterministic. The keyword lists are
near the top — edit them if you disagree with a classification.

Usage:
    python categorise_pipeline.py [--in items.csv] [--out items_categorised.csv]
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────
# Each category has:
#   - tier: "discretionary" (the stuff to consider cutting) or "essential"
#   - patterns: list of regex/keywords (case-insensitive, matched on item name)
#
# Rules apply IN ORDER — first match wins. So put more specific categories
# BEFORE broader ones (e.g. "Ice Cream" before "Dairy" since ice cream
# contains the word "cream" and we want it classified as junk, not dairy).

CATEGORIES = [
    # ── DISCRETIONARY: drinks ────────────────────────────────────────────────
    ("Alcohol", "discretionary", [
        r"\blagers?\b", r"\bbeers?\b", r"\bales?\b", r"\bstouts?\b", r"\bipa\b",
        r"\bcervezas?\b",  # spanish for beer (Vasto)
        r"\bvodkas?\b", r"\bgins?\b", r"\bwhiskys?\b", r"\bwhiskeys?\b", r"\brums?\b",
        r"\btequilas?\b", r"\bbourbons?\b", r"\bliqueurs?\b", r"\bbrandy\b",
        r"\bwines?\b", r"\bshiraz\b", r"\bmerlot\b", r"\bsauv(?:ignon)?\b",
        r"\bchardonnay\b", r"\bpinot\b", r"\brose\b", r"\bprosecco\b",
        r"\bchampagne\b", r"\bciders?\b", r"\bspirits?\b", r"\bvermouth\b",
        r"carlton", r"coopers", r"sapporo", r"corona", r"heineken", r"asahi",
        r"jagermeister", r"jameson", r"smirnoff", r"absolut", r"bundaberg",
        r"\bbws\b", r"\b(?:btl|btle|bttle)\b.*(?:\d+x|\d+pk)",  # bottle multipacks → almost always grog
    ]),
    ("Soft Drinks & Energy", "discretionary", [
        r"\bcokes?\b", r"coca[\s-]?cola", r"\bpepsis?\b", r"\bfanta\b", r"\bsprite\b",
        r"\bsolo\b", r"\bmountain dew\b", r"\b7[\s-]?up\b", r"\bdr pepper\b",
        r"\bmonster\b", r"\bred bull\b", r"\bv energy\b", r"\bv\b.*energy",
        r"energy drinks?", r"\bmother\b.*energy",
        r"\bschweppes\b",            # flavoured mineral / tonic — discretionary
        r"soft drinks?", r"\bcolas?\b",
        r"lemonade", r"iced teas?", r"sports? drinks?", r"powerade",
        r"gatorade", r"\bkombucha\b", r"\bcoconut\s+water\b",
    ]),

    # ── DISCRETIONARY: junk food ─────────────────────────────────────────────
    ("Ice Cream & Frozen Desserts", "discretionary", [
        r"ice[\s-]?creams?", r"\bi/cream\b", r"\bicecream\b",
        r"\bgelato\b", r"\bsorbets?\b",
        r"cheesecakes?", r"\bcreamy classics\b",            # Bulla
        r"\bpeters\b.*\b(?:dstck|mini|stick|cup)",          # Peters ice cream products
        r"\bmagnum\b", r"\bcornetto\b", r"\bweis\b", r"\bstreets\b",
    ]),
    ("Chocolate & Confectionery", "discretionary", [
        r"\bchocolates?\b", r"\bchoc\b(?!\s*chip\s+muesli)",
        r"\bcadbury\b", r"caramilk", r"dairy milk", r"\bmars\b(?!\s*bar.*nut)",
        r"\bsnickers\b", r"\bbounty\b", r"\btwix\b", r"\bkit[\s-]?kat\b",
        r"\bmaltesers\b", r"\bm&m", r"\boreos?\b", r"\btim[\s-]?tam\b",
        r"\btoblerone\b", r"\blindt\b", r"\bferrero\b", r"breakaway",
        r"\blolly\b", r"\blollies\b", r"\bcandy\b", r"\bgummies?\b",
        r"\bjellybeans?\b", r"\bskittles\b", r"\bstarbur(?:st)?\b",
        r"\bmentos\b", r"\bnerds\b", r"\bnatural confectionery\b",
    ]),
    ("Chips & Salty Snacks", "discretionary", [
        r"\brrd\b", r"red rock", r"\bsmiths\b.*chips?", r"\bdoritos\b",
        r"\bpringles\b", r"\bcheezels\b", r"\bburger rings\b",
        r"\btwisties\b", r"\bcheetos\b",
        r"corn chips?", r"potato chips?", r"\bcrackers?\b.*chips?",
        r"\bcrinkle\s+cut\b",                                # crinkle-cut chips (WW brand)
        r"\bpopcorn\b", r"\bpretzels?\b",
        r"\bnobby'?s\b", r"\bsnacks?\b.*\d+g",              # generic "Snacks NNg"
    ]),
    ("Biscuits & Sweet Snacks", "discretionary", [
        r"\bbiscuits?\b", r"\bcookies?\b", r"\bwafers?\b", r"\bdonuts?\b",
        r"\bdoughnuts?\b", r"\bbrownies?\b", r"\bcake\b(?!\s*tin)",
        r"\bmuffins?\b", r"\bcroissants?\b", r"\bpastry\b", r"\bpastries\b",
        r"\bdanish\b",
        r"\barnott'?s\b", r"\btiny teddy\b", r"\bmini cookies\b",
        r"\bshapes\b.*\d+g",                                 # Arnott's Shapes
        r"\bglamingtons?\b", r"\blamingtons?\b",             # lamington cake
        r"\bfunfetti\b", r"\bsprinkles?\b",
    ]),
    ("Snack Bars", "discretionary", [
        r"muesli bars?", r"\bnut bars?\b", r"\bprotein bars?\b",
        r"\bnice & natural\b", r"\bbe\s*natural\b", r"\bcarman'?s\b",
        r"\buncle toby'?s\b.*bar", r"\bmearth\b",
    ]),

    # ── ESSENTIALS: more specific ones first ─────────────────────────────────
    ("Coffee, Tea & Hot Drinks", "essential", [
        r"\bcoffee\b", r"\bnescafe\b", r"\bmoccona\b", r"\binstant\s+coffee\b",
        r"\bespresso\b", r"\bteas?\b\b", r"\btwinings\b", r"\bdilmah\b",
        r"\bcocoa\b", r"\bmilo\b", r"\bovaltine\b", r"\bdrinking chocolate\b",
        r"\bfreeze[\s-]?dried\b.*coffee",
    ]),
    ("Personal Care & Health", "essential", [
        r"\brazors?\b", r"\bshaving\b", r"\bsanitary\b", r"\btampon", r"\bpads?\b\s+\d",
        r"\btoothbrush\b", r"\btoothpaste\b", r"\bfloss\b", r"\bmouthwash\b",
        r"\bdeodorant\b", r"\bbody\s*wash\b", r"\bshampoo\b", r"\bconditioners?\b",
        r"\bmoisturiser\b", r"\bsunscreen\b", r"\bcosmetic", r"\blipstick\b",
        r"\bnappy\b", r"\bnappies\b", r"\bbaby\s+wipes?\b",
        r"\bpanadol\b", r"\bnurofen\b", r"\bvitamin", r"\bsupplement",
        r"\bde[\s-]?gas\b", r"\bcapsules?\b.*(?:pepperm|gas|relief|aid)",
        r"\bband[\s-]?aid\b", r"\bbandage\b",
    ]),
    ("Pantry Staples", "essential", [
        # Cooking essentials
        r"\boils?\b(?!.*essential)", r"\bvinegars?\b", r"\bsalts?\b(?!.*chip)",
        r"\bpeppers?\b(?!\s*rings|\s*max)", r"\bspice", r"\bseasoning\b",
        r"\bflours?\b", r"\bsugars?\b(?!.*free.*drink)", r"\bhoneys?\b",
        r"\bsyrups?\b", r"\bbaking\s+(?:powder|soda)\b",
        r"\bcoat\s*&\s*cook\b", r"\bcoating\b",              # seasoning mixes
        r"\bdry\b\s*\d+\s*g$", r"\b(?:dried|dry)\s+(?:chilli|herb|spice)",
        # Grains/starch
        r"\brice\b(?!.*pudding|.*crackers)", r"\bpastas?\b(?!.*sauce)",
        r"\bnoodles?\b", r"\bindomie\b", r"\bspaghetti\b", r"\bpenne\b",
        r"\bquinoa\b", r"\bcous[\s-]?cous\b", r"\boats?\b(?!.*bar)",
        r"\bcereals?\b", r"\bmuesli\b(?!\s+bars?)",  # muesli essential, bars not
        # Sauces & condiments
        r"\btomato\s+(?:puree|paste|sauce|past)\b", r"\bpasta\s+sauce\b",
        r"\bsauces?\b(?!\s+(?:apple|chocolate|caramel))",
        r"\bketchup\b", r"\bmustard\b", r"\bmayo(?:nnaise)?\b",
        r"\bperinaise\b", r"\bperi[\s-]?peri\b", r"\bnandos?\b",
        r"\bdressings?\b", r"\bstocks?\b",
        # Canned / tinned (catch BEFORE produce so canned tomato → pantry)
        r"\bdiced\s+(?:italian\s+)?tomato(?:es)?\b",
        r"\btinned\s+", r"\bcanned\s+",
        r"\bcan\b\s+(?:beans|tomato|tuna|corn)",
        r"\bbaked\s+beans\b", r"\bbeans?\b\s+\d+g",   # canned beans
        r"\blegumes?\b", r"\blentils?\b", r"\bchickpeas?\b",
        # Spreads
        r"\bpeanut\s+butter\b", r"\bjams?\b\b", r"\bvegemite\b", r"\bnutella\b",
        r"\btrident\b",
        # Spice / hoyts
        r"\bhoyts?\b",
    ]),
    ("Bakery & Bread", "essential", [
        r"\bbreads?\b", r"\bbuns?\b", r"\brolls?\b(?!\s+up)", r"\bbagels?\b",
        r"\bsandwich\b(?!.*biscuit)",
        r"\btoast\b", r"\bwraps?\b", r"\bpita\b", r"\bnaan\b", r"\bflatbreads?\b",
        r"\bsourdough\b", r"\bbaguettes?\b", r"\bcrumpets?\b", r"\benglish muffin",
        r"\bmultigrain\b", r"\bwholemeal\b",
    ]),
    ("Dairy & Eggs", "essential", [
        r"\bmilks?\b(?!\s+chocolate|.*shake)",
        r"\byoghurts?\b", r"\byogurts?\b",
        r"\bcreams?\b(?!.*classics|.*ice)",
        r"\bbutters?\b(?!.*chicken|.*peanut)", r"\bbtr\b",  # "Btr" abbreviation
        r"\bcheeses?\b(?!.*chip|.*puff|.*cracker)",
        r"\bfeta\b", r"\bricotta\b", r"\bcottage cheese\b", r"\bhalloumi\b",
        r"\bmozzarella\b", r"\bparmesan\b", r"\bcheddar\b",
        r"\beggs?\b(?!plant)",
        r"\bchobani\b", r"\bgreek (?:style|yogurt|yoghurt)\b",
        r"\bwestern star\b",  # butter brand
    ]),
    ("Meat & Poultry", "essential", [
        r"\bchickens?\b(?!.*(?:tender.*swt|nugget|tender.*chli))",
        r"\bbeef\b", r"\bsteaks?\b", r"\bmince\b", r"\blambs?\b", r"\bpork\b",
        r"\bhams?\b", r"\bbacon\b", r"\bsausages?\b", r"\bsalami\b",
        r"\bturkey\b", r"\bduck\b", r"\bfillets?\b", r"\bschnitzels?\b",
        r"\bbreasts?\b.*chicken|chicken.*breasts?", r"\brspca\b",
        r"\bdeli\b.*meat", r"\bprosciutto\b", r"\bpepperoni\b",
        r"\bsalmon\b", r"\btunas?\b(?!\s+oil)", r"\bprawns?\b", r"\bshrimps?\b",
        r"\bfish\b", r"\bcod\b", r"\bbarramundi\b", r"\bsardines?\b",
        r"\boysters?\b", r"\bmussels?\b", r"\bcalamari\b", r"\bsquid\b",
    ]),
    ("Fresh Produce", "essential", [
        # Fruit
        r"\bbananas?\b", r"\bapples?\b", r"\boranges?\b", r"\bmandarins?\b",
        r"\bgrapes?\b", r"\bberry\b", r"\bberries\b",
        r"\bblueberry\b", r"\bblueberries\b",
        r"\bstrawberr", r"\braspberr", r"\bblackberr",
        r"\bmango(?:es)?\b", r"\bpineapples?\b", r"\bkiwis?\b", r"\bavocados?\b",
        r"\blemons?\b", r"\blimes?\b", r"\bpears?\b", r"\bplums?\b",
        r"\bpeach(?:es)?\b", r"\bnectarines?\b", r"\bwatermelon\b",
        r"\brockmelon\b", r"\bhoneydew\b",
        r"\bcherry\b", r"\bcherries\b", r"\bpomegranate\b", r"\bpapaya\b",
        r"\bsultanas?\b", r"\bdried\s+(?:fruit|apricot)",  # dried fruit = produce
        # Veg
        r"\blettuces?\b",
        r"\btomato(?:es)?\b(?!\s+(?:puree|paste|sauce|soup|juice|past|diced))",
        r"\bcucumbers?\b", r"\bcapsicums?\b", r"\bcarrots?\b", r"\bonions?\b",
        r"\bpotato(?:es)?\b(?!.*chip)", r"\bspudlite\b",     # branded baby potato
        r"\bgarlic\b", r"\bginger\b",
        r"\bbroccoli\b", r"\bcauliflower\b", r"\bspinach\b", r"\bkale\b",
        r"\bcelery\b", r"\bzucchinis?\b", r"\bpumpkins?\b",
        r"\bcorn\b(?!\s+chip)", r"\beggplants?\b",
        r"\bpeas\b", r"\basparagus\b", r"\bmushrooms?\b",
        r"\bsweet\s+potato(?:es)?\b", r"\bbeetroot\b", r"\bradish\b", r"\bcabbages?\b",
        r"\bherbs?\b", r"\bparsley\b", r"\bbasil\b", r"\bcoriander\b",
        r"\bchilli(?:es|s)?\b(?!\s+sauce)", r"\bshallots?\b", r"\bleeks?\b",
    ]),
    ("Frozen Foods", "essential", [
        r"\bfrozen\b(?!.*dessert)", r"\bfrozen\s+veg\b",
        r"\bwinter\s+veg\b", r"\bmixed\s+veg\b",
        r"\bsuperfries\b", r"\bmccain\b", r"\bhash\s+browns?\b",
        r"\bdumplings?\b", r"\bgyoza\b", r"\bspring rolls?\b",
        r"\bpizzas?\b(?!\s+shape)",
    ]),
    ("Ready Meals & Heat-and-Eat", "essential", [
        r"\bnuggets?\b", r"\bgoujons?\b", r"\bkievs?\b",
        r"\bingham'?s\b", r"\btenders?\b", r"\bcrumb",
        r"\broast\b.*chicken", r"hot chook",
    ]),
    ("Household & Cleaning", "essential", [
        r"\bdetergents?\b", r"\bsoaps?\b(?!\s+stone)",
        r"\btoilet paper\b", r"\bpaper towels?\b", r"\bpaper\s+bags?\b",
        r"\btissues?\b", r"\btisss?\b",                     # also "Tisss" typo
        r"\bbin liners?\b", r"\bgarbage bags?\b", r"\bscented\s+tidy\b",
        r"\bcleaners?\b", r"\bbleach\b", r"\bdisinfect", r"\bspray\s*n'?\s*wipe\b",
        r"\bajax\b", r"\bpine o cleen\b", r"\bdettol\b", r"\bjif\b",
        r"\bwipes?\b", r"\bsponges?\b", r"\bdishwash", r"\bdishliquid\b",
        r"\bwashing\s+(?:powder|liquid)\b", r"\blaundry\s+(?:powder|liquid|detergent)\b",
        r"\bfabric softener\b", r"\bomo\b", r"\bcold power\b",
        r"\bglad\b\s+wrap", r"\bcling\s+wrap\b", r"\bfoil\b", r"\bbaking paper\b",
        r"\bbatter(?:y|ies)\b", r"\blight\s*bulbs?\b",
        r"\bbowls?\b", r"\bplates?\b", r"\bcups?\b\s+\d+pk", r"\bcutlery\b",
        r"\bair\s+freshener\b", r"\bcandles?\b",
        r"\bskewers?\b", r"\bbamboo\b",                     # bamboo skewers etc
    ]),
]

# Rows we filter out entirely (not real product purchases)
NOISE_PATTERNS = [
    re.compile(r"^price reduced", re.I),
    re.compile(r"^\*", re.I),               # "*CARLTON DRAUG OFFER"
    re.compile(r"\bcoupon\b", re.I),
    re.compile(r"\boffer\b", re.I),
    re.compile(r"\boffr\b", re.I),
    re.compile(r"\bdiscount\b", re.I),
    re.compile(r"\brebate\b", re.I),
]


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def is_noise(item_name: str, line_total: str) -> bool:
    """True if this row isn't a real purchase (discount label, offer line, etc)."""
    if not line_total or line_total.strip() == "":
        return True
    try:
        if float(line_total) <= 0:
            return True
    except ValueError:
        return True
    return any(p.search(item_name or "") for p in NOISE_PATTERNS)


# Compile all category patterns once for speed
_compiled = [
    (cat, tier, [re.compile(p, re.I) for p in pats])
    for cat, tier, pats in CATEGORIES
]


def classify(item_name: str) -> tuple[str, str]:
    """Return (category, tier) for an item name. First-match-wins."""
    for cat, tier, patterns in _compiled:
        if any(p.search(item_name) for p in patterns):
            return cat, tier
    return "Other / Uncategorised", "review"


def month_key(date_str: str) -> str:
    """YYYY-MM from a YYYY-MM-DD date."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m")
    except (ValueError, TypeError):
        return ""


def run_pipeline(input_path: Path, output_path: Path) -> dict:
    """Read, clean, categorise, write. Returns summary stats."""
    with open(input_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"📥 Loaded {len(rows)} rows from {input_path.name}")

    # ── Filter noise ────────────────────────────────────────────────────────
    clean = []
    filtered = []
    for r in rows:
        if is_noise(r.get("item", ""), r.get("line_total", "")):
            filtered.append(r)
        else:
            clean.append(r)
    print(f"🧹 Filtered out {len(filtered)} noise rows "
          f"(discounts, offers, coupons, zero/negative amounts)")
    print(f"   → {len(clean)} real purchase line items")

    # ── Categorise ──────────────────────────────────────────────────────────
    for r in clean:
        cat, tier = classify(r["item"])
        r["category"] = cat
        r["tier"]     = tier
        r["month_key"] = month_key(r.get("date", ""))
        r["spend"]    = r["line_total"]  # alias for clarity in viz

    # ── Write out ───────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date", "month_key", "store", "store_no", "section",
        "item", "category", "tier",
        "qty", "weight_kg", "unit_price", "line_total", "spend",
        "receipt_total", "is_promo_item", "is_on_special",
        "activity_id", "receipt_source",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(clean)
    print(f"💾 Wrote categorised data → {output_path}")

    # ── Build summary ───────────────────────────────────────────────────────
    return summarise(clean)


def summarise(clean_rows: list[dict]) -> dict:
    """Compute per-category totals + savings projections."""
    by_cat   = defaultdict(float)
    by_tier  = defaultdict(float)
    months   = set()
    unmatched = []

    for r in clean_rows:
        amt = float(r["line_total"])
        by_cat[r["category"]] += amt
        by_tier[r["tier"]]    += amt
        if r["month_key"]:
            months.add(r["month_key"])
        if r["category"] == "Other / Uncategorised":
            unmatched.append((r["item"], amt))

    n_months = max(len(months), 1)
    total_spend = sum(by_cat.values())

    # Console report
    print("\n" + "=" * 64)
    print(f"  💰  SPEND SUMMARY  ({n_months} month{'s' if n_months>1 else ''} of data)")
    print("=" * 64)

    print(f"\n  Total tracked spend:  ${total_spend:>10.2f}")
    print(f"  Avg per month:        ${total_spend/n_months:>10.2f}")

    print(f"\n  BY TIER")
    print(f"  {'─'*54}")
    for tier in ["essential", "discretionary", "review"]:
        amt = by_tier.get(tier, 0)
        pct = (amt / total_spend * 100) if total_spend else 0
        bar = "█" * int(pct / 2)
        print(f"  {tier:14s} ${amt:>8.2f}  ({pct:5.1f}%) {bar}")

    print(f"\n  BY CATEGORY  (sorted by spend)")
    print(f"  {'─'*54}")
    sorted_cats = sorted(by_cat.items(), key=lambda x: -x[1])
    for cat, amt in sorted_cats:
        pct = (amt / total_spend * 100) if total_spend else 0
        # Mark discretionary categories with 🔴 for quick visual ID
        tier_of_cat = next((t for c, t, _ in CATEGORIES if c == cat),
                           "review" if cat == "Other / Uncategorised" else "essential")
        marker = "🔴" if tier_of_cat == "discretionary" else \
                 "🟡" if tier_of_cat == "review" else "🟢"
        print(f"  {marker} {cat:32s} ${amt:>8.2f}  ({pct:5.1f}%)")

    # ── Savings projection: cut each discretionary category ────────────────
    print(f"\n  💸 SAVINGS PROJECTION  (if you cut entirely)")
    print(f"  {'─'*54}")
    print(f"  {'Category':30s} {'/month':>9} {'/6mo':>9} {'/year':>9}")
    print(f"  {'─'*54}")
    disc_total = by_tier.get("discretionary", 0)
    for cat, amt in sorted_cats:
        tier_of_cat = next((t for c, t, _ in CATEGORIES if c == cat), "essential")
        if tier_of_cat != "discretionary":
            continue
        monthly = amt / n_months
        print(f"  {cat:30s} ${monthly:>7.2f}  ${monthly*6:>7.2f}  ${monthly*12:>7.2f}")
    print(f"  {'─'*54}")
    monthly_disc = disc_total / n_months
    print(f"  {'CUT ALL DISCRETIONARY':30s} "
          f"${monthly_disc:>7.2f}  ${monthly_disc*6:>7.2f}  ${monthly_disc*12:>7.2f}")

    # ── Items needing review ───────────────────────────────────────────────
    if unmatched:
        print(f"\n  🟡  UNCATEGORISED ({len(unmatched)} items, "
              f"${sum(a for _,a in unmatched):.2f}) — review these:")
        for name, amt in sorted(unmatched, key=lambda x: -x[1])[:15]:
            print(f"     • {name:50s} ${amt:>6.2f}")
        if len(unmatched) > 15:
            print(f"     ... and {len(unmatched)-15} more")

    return {
        "n_months":       n_months,
        "total":          total_spend,
        "by_category":    dict(by_cat),
        "by_tier":        dict(by_tier),
        "monthly_discretionary": monthly_disc,
    }


def main():
    parser = argparse.ArgumentParser(description="Categorise grocery items by spend category")
    parser.add_argument("--in",  dest="input",  default="items.csv",
                        help="Input CSV from the scraper (default: items.csv)")
    parser.add_argument("--out", dest="output", default="items_categorised.csv",
                        help="Output CSV (default: items_categorised.csv)")
    args = parser.parse_args()

    input_path  = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"❌ Input file not found: {input_path}")
        sys.exit(1)

    print(f"\n🥬 GROCERY CATEGORISATION PIPELINE")
    print(f"   {input_path} → {output_path}\n")
    run_pipeline(input_path, output_path)
    print(f"\n✅ Done. Use {output_path} as input to the visualisation step.\n")


if __name__ == "__main__":
    main()