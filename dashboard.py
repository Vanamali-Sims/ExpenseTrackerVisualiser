"""
Grocery Spend Dashboard
=======================
Interactive analysis of your Everyday Rewards e-receipts.
Categorises items in memory (no intermediate CSV) and visualises spend,
discretionary breakdown, and "what if I cut X" savings projections.

USAGE
-----
1.  Install dependencies (one-off):
        pip install streamlit pandas plotly

2.  Run it:
        streamlit run dashboard.py

3.  Drops you in a browser at http://localhost:8501

By default it looks for `items.csv` in the current directory. If not found,
use the file uploader in the sidebar.
"""

from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ═════════════════════════════════════════════════════════════════════════════
#  CATEGORISATION RULES
#  Same keyword lists as the standalone pipeline. Edit these to retune.
#  Rules apply IN ORDER — first match wins. Put more specific BEFORE broader.
# ═════════════════════════════════════════════════════════════════════════════
CATEGORIES = [
    # ── DISCRETIONARY ───────────────────────────────────────────────────────
    ("Alcohol", "discretionary", [
        r"\blagers?\b", r"\bbeers?\b", r"\bales?\b", r"\bstouts?\b", r"\bipa\b",
        r"\bcervezas?\b",
        r"\bvodkas?\b", r"\bgins?\b", r"\bwhiskys?\b", r"\bwhiskeys?\b",
        r"\brums?\b", r"\btequilas?\b", r"\bbourbons?\b", r"\bliqueurs?\b",
        r"\bbrandy\b",
        r"\bwines?\b", r"\bshiraz\b", r"\bmerlot\b", r"\bsauv(?:ignon)?\b",
        r"\bchardonnay\b", r"\bpinot\b", r"\brose\b", r"\bprosecco\b",
        r"\bchampagne\b", r"\bciders?\b", r"\bspirits?\b", r"\bvermouth\b",
        r"carlton", r"coopers", r"sapporo", r"corona", r"heineken", r"asahi",
        r"jagermeister", r"jameson", r"smirnoff", r"absolut", r"bundaberg",
        r"\bbws\b", r"\b(?:btl|btle|bttle)\b.*(?:\d+x|\d+pk)",
    ]),
    ("Soft Drinks & Energy", "discretionary", [
        r"\bcokes?\b", r"coca[\s-]?cola", r"\bpepsis?\b", r"\bfanta\b",
        r"\bsprite\b", r"\bsolo\b", r"\bmountain dew\b", r"\b7[\s-]?up\b",
        r"\bdr pepper\b",
        r"\bmonster\b", r"\bred bull\b", r"\bv energy\b", r"\bv\b.*energy",
        r"energy drinks?", r"\bmother\b.*energy",
        r"\bschweppes\b",
        r"soft drinks?", r"\bcolas?\b",
        r"lemonade", r"iced teas?", r"sports? drinks?", r"powerade",
        r"gatorade", r"\bkombucha\b", r"\bcoconut\s+water\b",
    ]),
    ("Ice Cream & Frozen Desserts", "discretionary", [
        r"ice[\s-]?creams?", r"\bi/cream\b", r"\bicecream\b",
        r"\bgelato\b", r"\bsorbets?\b",
        r"cheesecakes?", r"\bcreamy classics\b",
        r"\bpeters\b.*\b(?:dstck|mini|stick|cup)",
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
        r"\bcrinkle\s+cut\b",
        r"\bpopcorn\b", r"\bpretzels?\b",
        r"\bnobby'?s\b", r"\bsnacks?\b.*\d+g",
    ]),
    ("Biscuits & Sweet Snacks", "discretionary", [
        r"\bbiscuits?\b", r"\bcookies?\b", r"\bwafers?\b", r"\bdonuts?\b",
        r"\bdoughnuts?\b", r"\bbrownies?\b", r"\bcake\b(?!\s*tin)",
        r"\bmuffins?\b", r"\bcroissants?\b", r"\bpastry\b", r"\bpastries\b",
        r"\bdanish\b",
        r"\barnott'?s\b", r"\btiny teddy\b", r"\bmini cookies\b",
        r"\bshapes\b.*\d+g",
        r"\bglamingtons?\b", r"\blamingtons?\b",
        r"\bfunfetti\b", r"\bsprinkles?\b",
    ]),
    ("Snack Bars", "discretionary", [
        r"muesli bars?", r"\bnut bars?\b", r"\bprotein bars?\b",
        r"\bnice & natural\b", r"\bbe\s*natural\b", r"\bcarman'?s\b",
        r"\buncle toby'?s\b.*bar", r"\bmearth\b",
    ]),

    # ── ESSENTIALS ──────────────────────────────────────────────────────────
    ("Coffee, Tea & Hot Drinks", "essential", [
        r"\bcoffee\b", r"\bnescafe\b", r"\bmoccona\b", r"\binstant\s+coffee\b",
        r"\bespresso\b", r"\bteas?\b\b", r"\btwinings\b", r"\bdilmah\b",
        r"\bcocoa\b", r"\bmilo\b", r"\bovaltine\b", r"\bdrinking chocolate\b",
        r"\bfreeze[\s-]?dried\b.*coffee",
    ]),
    ("Personal Care & Health", "essential", [
        r"\brazors?\b", r"\bshaving\b", r"\bsanitary\b", r"\btampon",
        r"\bpads?\b\s+\d", r"\btoothbrush\b", r"\btoothpaste\b", r"\bfloss\b",
        r"\bmouthwash\b", r"\bdeodorant\b", r"\bbody\s*wash\b", r"\bshampoo\b",
        r"\bconditioners?\b", r"\bmoisturiser\b", r"\bsunscreen\b",
        r"\bcosmetic", r"\blipstick\b", r"\bnappy\b", r"\bnappies\b",
        r"\bbaby\s+wipes?\b",
        r"\bpanadol\b", r"\bnurofen\b", r"\bvitamin", r"\bsupplement",
        r"\bde[\s-]?gas\b", r"\bcapsules?\b.*(?:pepperm|gas|relief|aid)",
        r"\bband[\s-]?aid\b", r"\bbandage\b",
    ]),
    ("Pantry Staples", "essential", [
        r"\boils?\b(?!.*essential)", r"\bvinegars?\b", r"\bsalts?\b(?!.*chip)",
        r"\bpeppers?\b(?!\s*rings|\s*max)", r"\bspice", r"\bseasoning\b",
        r"\bflours?\b", r"\bsugars?\b(?!.*free.*drink)", r"\bhoneys?\b",
        r"\bsyrups?\b", r"\bbaking\s+(?:powder|soda)\b",
        r"\bcoat\s*&\s*cook\b", r"\bcoating\b",
        r"\bdry\b\s*\d+\s*g$", r"\b(?:dried|dry)\s+(?:chilli|herb|spice)",
        r"\brice\b(?!.*pudding|.*crackers)", r"\bpastas?\b(?!.*sauce)",
        r"\bnoodles?\b", r"\bindomie\b", r"\bspaghetti\b", r"\bpenne\b",
        r"\bquinoa\b", r"\bcous[\s-]?cous\b", r"\boats?\b(?!.*bar)",
        r"\bcereals?\b", r"\bmuesli\b(?!\s+bars?)",
        r"\btomato\s+(?:puree|paste|sauce|past)\b", r"\bpasta\s+sauce\b",
        r"\bsauces?\b(?!\s+(?:apple|chocolate|caramel))",
        r"\bketchup\b", r"\bmustard\b", r"\bmayo(?:nnaise)?\b",
        r"\bperinaise\b", r"\bperi[\s-]?peri\b", r"\bnandos?\b",
        r"\bdressings?\b", r"\bstocks?\b",
        r"\bdiced\s+(?:italian\s+)?tomato(?:es)?\b",
        r"\btinned\s+", r"\bcanned\s+",
        r"\bcan\b\s+(?:beans|tomato|tuna|corn)",
        r"\bbaked\s+beans\b", r"\bbeans?\b\s+\d+g",
        r"\blegumes?\b", r"\blentils?\b", r"\bchickpeas?\b",
        r"\bpeanut\s+butter\b", r"\bjams?\b\b", r"\bvegemite\b", r"\bnutella\b",
        r"\btrident\b",
        r"\bhoyts?\b",
    ]),
    ("Bakery & Bread", "essential", [
        r"\bbreads?\b", r"\bbuns?\b", r"\brolls?\b(?!\s+up)", r"\bbagels?\b",
        r"\bsandwich\b(?!.*biscuit)",
        r"\btoast\b", r"\bwraps?\b", r"\bpita\b", r"\bnaan\b",
        r"\bflatbreads?\b",
        r"\bsourdough\b", r"\bbaguettes?\b", r"\bcrumpets?\b",
        r"\benglish muffin",
        r"\bmultigrain\b", r"\bwholemeal\b",
    ]),
    ("Dairy & Eggs", "essential", [
        r"\bmilks?\b(?!\s+chocolate|.*shake)",
        r"\byoghurts?\b", r"\byogurts?\b",
        r"\bcreams?\b(?!.*classics|.*ice)",
        r"\bbutters?\b(?!.*chicken|.*peanut)", r"\bbtr\b",
        r"\bcheeses?\b(?!.*chip|.*puff|.*cracker)",
        r"\bfeta\b", r"\bricotta\b", r"\bcottage cheese\b", r"\bhalloumi\b",
        r"\bmozzarella\b", r"\bparmesan\b", r"\bcheddar\b",
        r"\beggs?\b(?!plant)",
        r"\bchobani\b", r"\bgreek (?:style|yogurt|yoghurt)\b",
        r"\bwestern star\b",
    ]),
    ("Meat & Poultry", "essential", [
        r"\bchickens?\b(?!.*(?:tender.*swt|nugget|tender.*chli))",
        r"\bbeef\b", r"\bsteaks?\b", r"\bmince\b", r"\blambs?\b", r"\bpork\b",
        r"\bhams?\b", r"\bbacon\b", r"\bsausages?\b", r"\bsalami\b",
        r"\bturkey\b", r"\bduck\b", r"\bfillets?\b", r"\bschnitzels?\b",
        r"\bbreasts?\b.*chicken|chicken.*breasts?", r"\brspca\b",
        r"\bdeli\b.*meat", r"\bprosciutto\b", r"\bpepperoni\b",
        r"\bsalmon\b", r"\btunas?\b(?!\s+oil)", r"\bprawns?\b",
        r"\bshrimps?\b",
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
        r"\bsultanas?\b", r"\bdried\s+(?:fruit|apricot)",
        # Veg
        r"\blettuces?\b",
        r"\btomato(?:es)?\b(?!\s+(?:puree|paste|sauce|soup|juice|past|diced))",
        r"\bcucumbers?\b", r"\bcapsicums?\b", r"\bcarrots?\b", r"\bonions?\b",
        r"\bpotato(?:es)?\b(?!.*chip)", r"\bspudlite\b",
        r"\bgarlic\b", r"\bginger\b",
        r"\bbroccoli\b", r"\bcauliflower\b", r"\bspinach\b", r"\bkale\b",
        r"\bcelery\b", r"\bzucchinis?\b", r"\bpumpkins?\b",
        r"\bcorn\b(?!\s+chip)", r"\beggplants?\b",
        r"\bpeas\b", r"\basparagus\b", r"\bmushrooms?\b",
        r"\bsweet\s+potato(?:es)?\b", r"\bbeetroot\b", r"\bradish\b",
        r"\bcabbages?\b",
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
        r"\btissues?\b", r"\btisss?\b",
        r"\bbin liners?\b", r"\bgarbage bags?\b", r"\bscented\s+tidy\b",
        r"\bcleaners?\b", r"\bbleach\b", r"\bdisinfect",
        r"\bspray\s*n'?\s*wipe\b",
        r"\bajax\b", r"\bpine o cleen\b", r"\bdettol\b", r"\bjif\b",
        r"\bwipes?\b", r"\bsponges?\b", r"\bdishwash", r"\bdishliquid\b",
        r"\bwashing\s+(?:powder|liquid)\b",
        r"\blaundry\s+(?:powder|liquid|detergent)\b",
        r"\bfabric softener\b", r"\bomo\b", r"\bcold power\b",
        r"\bglad\b\s+wrap", r"\bcling\s+wrap\b", r"\bfoil\b",
        r"\bbaking paper\b",
        r"\bbatter(?:y|ies)\b", r"\blight\s*bulbs?\b",
        r"\bbowls?\b", r"\bplates?\b", r"\bcups?\b\s+\d+pk", r"\bcutlery\b",
        r"\bair\s+freshener\b", r"\bcandles?\b",
        r"\bskewers?\b", r"\bbamboo\b",
    ]),
]

NOISE_PATTERNS = [
    re.compile(r"^price reduced", re.I),
    re.compile(r"^\*", re.I),
    re.compile(r"\bcoupon\b", re.I),
    re.compile(r"\boffer\b", re.I),
    re.compile(r"\boffr\b", re.I),
    re.compile(r"\bdiscount\b", re.I),
    re.compile(r"\brebate\b", re.I),
]

# Pre-compile category patterns once for speed
_COMPILED = [
    (cat, tier, [re.compile(p, re.I) for p in pats])
    for cat, tier, pats in CATEGORIES
]


# ═════════════════════════════════════════════════════════════════════════════
#  CORE LOGIC: classification & filtering
# ═════════════════════════════════════════════════════════════════════════════
def is_noise(item: str, line_total) -> bool:
    if pd.isna(line_total) or line_total == "" or line_total is None:
        return True
    try:
        if float(line_total) <= 0:
            return True
    except (ValueError, TypeError):
        return True
    item_str = str(item or "")
    return any(p.search(item_str) for p in NOISE_PATTERNS)


def classify(name: str) -> tuple[str, str]:
    """Return (category, tier). First-match-wins."""
    name = str(name or "")
    for cat, tier, patterns in _COMPILED:
        if any(p.search(name) for p in patterns):
            return cat, tier
    return "Other / Uncategorised", "review"


# ═════════════════════════════════════════════════════════════════════════════
#  DATA LOAD & TRANSFORM
# ═════════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False)
def load_and_categorise(csv_data) -> pd.DataFrame:
    """
    Read items.csv (path or file-like) → cleaned, categorised DataFrame.
    Held entirely in memory. No intermediate CSV written.
    """
    df = pd.read_csv(csv_data)

    # Filter out noise rows
    mask = df.apply(lambda r: not is_noise(r.get("item"), r.get("line_total")),
                    axis=1)
    df = df[mask].copy()

    # Coerce types
    df["line_total"]    = pd.to_numeric(df["line_total"], errors="coerce")
    df["receipt_total"] = pd.to_numeric(df["receipt_total"], errors="coerce")
    df["qty"]           = pd.to_numeric(df["qty"], errors="coerce").fillna(1)
    df["unit_price"]    = pd.to_numeric(df["unit_price"], errors="coerce")
    df["weight_kg"]     = pd.to_numeric(df["weight_kg"], errors="coerce")
    df["date"]          = pd.to_datetime(df["date"], errors="coerce")

    df = df.dropna(subset=["line_total", "date"])
    df = df[df["line_total"] > 0]

    # Apply our classifier
    cats_tiers = df["item"].apply(classify)
    df["category"] = cats_tiers.apply(lambda x: x[0])
    df["tier"]     = cats_tiers.apply(lambda x: x[1])
    df["month"]    = df["date"].dt.to_period("M").astype(str)
    df["weekday"]  = df["date"].dt.day_name()
    df["spend"]    = df["line_total"]   # alias

    return df.reset_index(drop=True)


# ═════════════════════════════════════════════════════════════════════════════
#  PLOTTING HELPERS
# ═════════════════════════════════════════════════════════════════════════════
TIER_COLORS = {
    "essential":     "#10b981",   # green
    "discretionary": "#ef4444",   # red
    "review":        "#94a3b8",   # grey
}

# Per-category palette so charts are consistent
CATEGORY_COLORS = {
    "Alcohol":                      "#7c3aed",
    "Soft Drinks & Energy":         "#f59e0b",
    "Chocolate & Confectionery":    "#d97706",
    "Ice Cream & Frozen Desserts":  "#ec4899",
    "Chips & Salty Snacks":         "#f97316",
    "Biscuits & Sweet Snacks":      "#fb923c",
    "Snack Bars":                   "#fbbf24",
    "Coffee, Tea & Hot Drinks":     "#92400e",
    "Personal Care & Health":       "#06b6d4",
    "Pantry Staples":               "#84cc16",
    "Bakery & Bread":               "#a16207",
    "Dairy & Eggs":                 "#fde047",
    "Meat & Poultry":               "#dc2626",
    "Fresh Produce":                "#22c55e",
    "Frozen Foods":                 "#3b82f6",
    "Ready Meals & Heat-and-Eat":   "#0ea5e9",
    "Household & Cleaning":         "#64748b",
    "Other / Uncategorised":        "#94a3b8",
}


def style_fig(fig, height: int | None = None):
    """Apply consistent dark-friendly styling."""
    fig.update_layout(
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, sans-serif", size=13),
        hoverlabel=dict(font_size=13),
    )
    if height:
        fig.update_layout(height=height)
    return fig


# ═════════════════════════════════════════════════════════════════════════════
#  STREAMLIT UI
# ═════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Grocery Spend Dashboard",
    page_icon="🛒",
    layout="wide",
)

st.markdown(
    """
    <style>
      .main .block-container { padding-top: 1.6rem; padding-bottom: 2rem; }
      [data-testid="stMetricValue"] { font-size: 1.65rem; }
      [data-testid="stMetricLabel"] { font-size: 0.85rem; opacity: 0.7; }
      h1, h2, h3 { letter-spacing: -0.02em; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar: data source ────────────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Data Source")

    default_path = Path("items.csv")
    uploaded = st.file_uploader("Upload items.csv", type=["csv"])

    if uploaded is not None:
        df = load_and_categorise(uploaded)
        st.success(f"Loaded {len(df)} items from upload")
    elif default_path.exists():
        df = load_and_categorise(default_path)
        st.success(f"Loaded {len(df)} items from {default_path}")
    else:
        st.warning("Drop your items.csv above to begin.")
        st.stop()

    # Date range filter
    st.divider()
    st.subheader("🗓️ Date Range")
    min_date, max_date = df["date"].min(), df["date"].max()
    date_range = st.date_input(
        "Filter",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        label_visibility="collapsed",
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        df = df[(df["date"] >= pd.Timestamp(date_range[0]))
                & (df["date"] <= pd.Timestamp(date_range[1]))]

    # Store filter
    st.subheader("🏪 Stores")
    all_stores = sorted(df["store"].dropna().unique())
    chosen_stores = st.multiselect(
        "Filter", all_stores, default=all_stores, label_visibility="collapsed"
    )
    df = df[df["store"].isin(chosen_stores)]

    if df.empty:
        st.warning("No data after filters.")
        st.stop()

    st.divider()
    st.caption(
        f"**{len(df)}** line items · "
        f"**${df['spend'].sum():,.2f}** total"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  HEADER + KPIs
# ═════════════════════════════════════════════════════════════════════════════
st.title("🛒 Grocery Spend Dashboard")

n_months    = max(df["month"].nunique(), 1)
total_spend = df["spend"].sum()
disc_spend  = df.loc[df["tier"] == "discretionary", "spend"].sum()
ess_spend   = df.loc[df["tier"] == "essential",     "spend"].sum()
monthly_avg = total_spend / n_months
disc_pct    = (disc_spend / total_spend * 100) if total_spend else 0
monthly_disc = disc_spend / n_months

st.caption(
    f"Analysing **{n_months} months** of data "
    f"({df['date'].min():%d %b %Y} → {df['date'].max():%d %b %Y})"
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total tracked spend",    f"${total_spend:,.0f}")
k2.metric("Average per month",      f"${monthly_avg:,.0f}")
k3.metric("Spent on discretionary", f"${disc_spend:,.0f}",
          delta=f"{disc_pct:.0f}% of total", delta_color="inverse")
k4.metric("Projected yearly junk-spend",
          f"${monthly_disc * 12:,.0f}",
          delta=f"${monthly_disc:,.0f}/mo", delta_color="inverse")

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
#  TIER + CATEGORY BREAKDOWN
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("Where it goes")

col_donut, col_bars = st.columns([1, 2])

with col_donut:
    tier_agg = (df.groupby("tier", as_index=False)["spend"].sum()
                  .sort_values("spend", ascending=False))
    fig = go.Figure(go.Pie(
        labels=tier_agg["tier"].str.title(),
        values=tier_agg["spend"],
        hole=0.6,
        marker_colors=[TIER_COLORS[t] for t in tier_agg["tier"]],
        textinfo="label+percent",
        textfont_size=14,
        hovertemplate="<b>%{label}</b><br>$%{value:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="Essential vs Discretionary",
        showlegend=False,
        annotations=[dict(
            text=f"<b>${total_spend:,.0f}</b><br><span style='font-size:12px'>total</span>",
            x=0.5, y=0.5, font_size=20, showarrow=False,
        )],
    )
    st.plotly_chart(style_fig(fig, height=380), use_container_width=True)

with col_bars:
    cat_agg = (df.groupby(["category", "tier"], as_index=False)["spend"].sum()
                 .sort_values("spend", ascending=True))
    fig = px.bar(
        cat_agg, x="spend", y="category", orientation="h",
        color="tier",
        color_discrete_map=TIER_COLORS,
        text=cat_agg["spend"].map(lambda v: f"${v:,.0f}"),
        title="By Category",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        yaxis_title=None, xaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    title_text=""),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False),
    )
    st.plotly_chart(style_fig(fig, height=380), use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
#  "WHAT IF I CUT..." SIMULATOR
# ═════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("💸 What if I cut...")
st.caption(
    "Drag the sliders. Each one represents what % of that category you'd cut. "
    "Savings update instantly."
)

disc_cats = (df[df["tier"] == "discretionary"]
             .groupby("category")["spend"].sum()
             .sort_values(ascending=False))

if disc_cats.empty:
    st.info("No discretionary spend to simulate.")
else:
    # Two columns of sliders
    cuts: dict[str, float] = {}
    slider_cols = st.columns(2)
    for i, (cat, amt) in enumerate(disc_cats.items()):
        with slider_cols[i % 2]:
            monthly = amt / n_months
            cuts[cat] = st.slider(
                f"**{cat}** — currently ${monthly:,.2f}/mo",
                min_value=0, max_value=100, value=0, step=5,
                format="%d%%",
                key=f"cut_{cat}",
            )

    # Compute savings
    saved_monthly = sum(
        (amt / n_months) * (cuts[cat] / 100)
        for cat, amt in disc_cats.items()
    )

    st.markdown(" ")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Saved per month",  f"${saved_monthly:,.2f}")
    s2.metric("Saved per 6 months", f"${saved_monthly * 6:,.2f}")
    s3.metric("Saved per year",   f"${saved_monthly * 12:,.2f}")
    s4.metric("Saved per 5 years", f"${saved_monthly * 60:,.2f}")

    # Comparison bar
    current_monthly_total = total_spend / n_months
    projected_monthly_total = current_monthly_total - saved_monthly
    compare_df = pd.DataFrame({
        "Scenario": ["Current", "After cuts"],
        "Spend":    [current_monthly_total, projected_monthly_total],
    })
    fig = px.bar(
        compare_df, x="Scenario", y="Spend",
        text=compare_df["Spend"].map(lambda v: f"${v:,.0f}"),
        color="Scenario",
        color_discrete_map={"Current": "#64748b", "After cuts": "#10b981"},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        title="Projected monthly spend after cuts",
        showlegend=False, yaxis_title=None, xaxis_title=None,
        yaxis=dict(showgrid=False, zeroline=False),
    )
    st.plotly_chart(style_fig(fig, height=320), use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
#  MONTHLY TREND
# ═════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📈 Monthly Trend")

trend_view = st.radio(
    "View by", ["Tier", "Category"], horizontal=True, label_visibility="collapsed"
)

if trend_view == "Tier":
    monthly = (df.groupby(["month", "tier"], as_index=False)["spend"].sum())
    fig = px.bar(
        monthly, x="month", y="spend", color="tier",
        color_discrete_map=TIER_COLORS,
        labels={"spend": "$", "month": ""},
        text=monthly["spend"].map(lambda v: f"${v:,.0f}"),
    )
else:
    monthly = (df.groupby(["month", "category"], as_index=False)["spend"].sum())
    fig = px.bar(
        monthly, x="month", y="spend", color="category",
        color_discrete_map=CATEGORY_COLORS,
        labels={"spend": "$", "month": ""},
    )

fig.update_layout(
    barmode="stack",
    legend=dict(title_text="", orientation="h", yanchor="bottom", y=-0.3),
    yaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)"),
    xaxis=dict(showgrid=False),
)
fig.update_traces(textposition="inside")
st.plotly_chart(style_fig(fig, height=420), use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
#  CATEGORY DRILL-DOWN
# ═════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("🔍 Drill Down: What am I actually buying?")

cat_choices = sorted(df["category"].unique(),
                     key=lambda c: -df.loc[df["category"] == c, "spend"].sum())
chosen_cat = st.selectbox("Pick a category", cat_choices)

cat_df = df[df["category"] == chosen_cat].copy()
top_items = (
    cat_df.groupby("item")
          .agg(total_spend=("spend", "sum"),
               times_bought=("spend", "count"),
               avg_price=("spend", "mean"))
          .sort_values("total_spend", ascending=False)
          .head(15)
          .reset_index()
)

col_l, col_r = st.columns([2, 1])

with col_l:
    fig = px.bar(
        top_items.sort_values("total_spend"),
        x="total_spend", y="item", orientation="h",
        text=top_items.sort_values("total_spend")["total_spend"]
            .map(lambda v: f"${v:,.2f}"),
        hover_data={"times_bought": True, "avg_price": ":.2f"},
        color_discrete_sequence=[CATEGORY_COLORS.get(chosen_cat, "#6366f1")],
        title=f"Top items in {chosen_cat}",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        yaxis_title=None, xaxis_title=None, showlegend=False,
        yaxis=dict(showgrid=False),
        xaxis=dict(showgrid=False, showticklabels=False),
    )
    st.plotly_chart(style_fig(fig, height=450), use_container_width=True)

with col_r:
    st.markdown(f"### {chosen_cat}")
    c_total = cat_df["spend"].sum()
    c_count = len(cat_df)
    c_avg   = cat_df["spend"].mean()
    c_unique = cat_df["item"].nunique()
    c_monthly = c_total / n_months
    st.metric("Total spent",       f"${c_total:,.2f}")
    st.metric("Avg per month",     f"${c_monthly:,.2f}")
    st.metric("Line items",        f"{c_count}")
    st.metric("Unique products",   f"{c_unique}")
    st.metric("Avg price/line",    f"${c_avg:,.2f}")

    if cat_df["tier"].iloc[0] == "discretionary":
        st.error(
            f"📌 Cutting all {chosen_cat.lower()} saves "
            f"**${c_monthly * 12:,.0f}/year**"
        )


# ═════════════════════════════════════════════════════════════════════════════
#  STORE BREAKDOWN
# ═════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("🏪 By Store")

store_agg = (
    df.groupby("store")
      .agg(total=("spend", "sum"),
           trips=("activity_id", "nunique"),
           items=("spend", "count"))
      .sort_values("total", ascending=False)
      .reset_index()
)
store_agg["avg_per_trip"] = store_agg["total"] / store_agg["trips"]

col_pie, col_table = st.columns([1, 1])
with col_pie:
    fig = px.pie(
        store_agg, names="store", values="total", hole=0.4,
        title="Spend share by store",
    )
    fig.update_traces(textinfo="label+percent")
    fig.update_layout(showlegend=False)
    st.plotly_chart(style_fig(fig, height=340), use_container_width=True)
with col_table:
    st.dataframe(
        store_agg.style.format({
            "total":        "${:,.2f}",
            "avg_per_trip": "${:,.2f}",
        }),
        hide_index=True, use_container_width=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  RAW DATA EXPLORER
# ═════════════════════════════════════════════════════════════════════════════
with st.expander("🗂️  Raw item data (filterable)"):
    search = st.text_input("Search items", "")
    view_df = df.copy()
    if search:
        view_df = view_df[view_df["item"].str.contains(search, case=False,
                                                       na=False)]
    view_df = view_df[[
        "date", "store", "item", "category", "tier",
        "qty", "unit_price", "spend",
    ]].sort_values("date", ascending=False)

    st.dataframe(
        view_df.style.format({
            "spend":      "${:,.2f}",
            "unit_price": "${:,.2f}",
            "date":       lambda d: d.strftime("%Y-%m-%d"),
        }),
        hide_index=True, use_container_width=True, height=400,
    )


st.divider()
st.caption(
    "💡 Tip: edit the `CATEGORIES` keyword lists at the top of `dashboard.py` "
    "to refine classifications, then re-run."
)