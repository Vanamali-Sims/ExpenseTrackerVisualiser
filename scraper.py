#!/usr/bin/env python3
"""
Everyday Rewards Receipt Scraper (GraphQL edition)
====================================================
Downloads every in-store Woolworths e-receipt from your Everyday Rewards
account and saves them as JSON + CSV for analysis.

Built around the real GraphQL API at apigee-prod.api-wr.com.

HOW TO GET YOUR TOKEN (30 seconds):
  1. Open Chrome → https://www.everyday.com.au/ → log in
  2. Go to: https://www.everyday.com.au/my-account/activity
  3. Press F12 → Network tab → filter by "graphql"
  4. Click any GraphQL request → Headers tab
  5. Copy the value after "authorization: Bearer "
  6. Paste it when this script asks for it

Token usually starts with a short opaque string (e.g. RsmenFLjY26j...).
Tokens expire after ~30 min — run promptly after copying.

DISCLAIMER: This uses Woolies' unofficial GraphQL API. It may technically
breach their T&C — you're accessing your own personal data. Use at your
own risk. Your credentials never leave your machine.
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("📦 Installing 'requests' library...")
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "requests", "--break-system-packages", "-q"]
    )
    import requests


# ── CONSTANTS ────────────────────────────────────────────────────────────────
GRAPHQL_URL  = "https://apigee-prod.api-wr.com/wx/v1/bff/graphql"
CLIENT_ID    = "8h41mMOiDULmlLT28xKSv5ITpp3XBRvH"  # public web client id

# ── GraphQL queries (copied verbatim from the Everyday Rewards web app) ─────
ACTIVITY_LIST_QUERY = """
query RewardsActivityHomeFirstPage($featureFlags: ActivityHomeFeatureFlags) {
  activityHome(featureFlags: $featureFlags) {
    __typename
    ...on ActivityHomePage {
      results {
        sections {
          ...on ActivityHomePageSection {
            sectionTitle
            sectionItems {
              __typename
              ...on RewardsActivityItem {
                id
                activityDetailsId
                description
                displayDate
                pointsValue
                receipt { receiptId receiptSource analytics { partnerName } }
                transactionType
                transaction { amountAsDollars origin }
              }
            }
          }
        }
        nextPageToken
      }
    }
  }
}
"""

# Same shape, but accepts a page token for pagination
ACTIVITY_LIST_NEXT_QUERY = """
query RewardsActivityHomeNextPage($pageToken: String!, $featureFlags: ActivityHomeFeatureFlags) {
  activityHomeNextPage(pageToken: $pageToken, featureFlags: $featureFlags) {
    __typename
    ...on ActivityHomePage {
      results {
        sections {
          ...on ActivityHomePageSection {
            sectionTitle
            sectionItems {
              __typename
              ...on RewardsActivityItem {
                id
                activityDetailsId
                description
                displayDate
                pointsValue
                receipt { receiptId receiptSource analytics { partnerName } }
                transactionType
                transaction { amountAsDollars origin }
              }
            }
          }
        }
        nextPageToken
      }
    }
  }
}
"""

ACTIVITY_DETAIL_QUERY = """
query ActivityDetails($id: String!, $featureFlags: ActivityDetailsFeatureFlags!) {
  activityDetails(id: $id, featureFlags: $featureFlags) {
    __typename
    tabs {
      label
      page {
        __typename
        ...on ReceiptDetails {
          download { url filename }
          details {
            __typename
            ...on ReceiptDetailsHeader { title content storeNo division }
            ...on ReceiptDetailsTotal  { total }
            ...on ReceiptDetailsItems  {
              items { prefixChar description amount }
            }
            ...on ReceiptDetailsSummary {
              receiptTotal { description amount }
              gst { description amount }
            }
            ...on ReceiptDetailsSavings { savings }
            ...on ReceiptDetailsFooter  { transactionDetails abnAndStore }
          }
        }
      }
    }
  }
}
"""

FEATURE_FLAGS_DETAIL = {"isGiftingStoreEnabled": True}
FEATURE_FLAGS_LIST   = {"isGiftingStoreEnabled": True}


def make_headers(token: str) -> dict:
    """Headers that mimic what the website sends."""
    return {
        "Authorization": f"Bearer {token}",
        "client_id":     CLIENT_ID,
        "api-version":   "2",
        "Content-Type":  "application/json;charset=UTF-8",
        "Accept":        "application/json, text/plain, */*",
        "Origin":        "https://www.everyday.com.au",
        "Referer":       "https://www.everyday.com.au/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
    }


def gql(token: str, operation_name: str, query: str, variables: dict) -> dict:
    """Send a GraphQL request and return the parsed JSON body."""
    body = {
        "operationName": operation_name,
        "query":         query,
        "variables":     variables,
    }
    resp = requests.post(GRAPHQL_URL, headers=make_headers(token), json=body, timeout=20)

    if resp.status_code == 401:
        print("\n❌ 401 Unauthorised — your bearer token has expired or is invalid.")
        print("   Refresh https://www.everyday.com.au/, grab a fresh token from")
        print("   DevTools → Network → any graphql request, and try again.")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"\n⚠️  HTTP {resp.status_code} from {operation_name}")
        print(f"   Response: {resp.text[:400]}")
        return {}

    data = resp.json()
    if "errors" in data:
        print(f"\n⚠️  GraphQL errors in {operation_name}: {data['errors']}")
    return data


def fetch_activity_list(token: str, max_pages: int = 50) -> list[dict]:
    """Walk every page of the activity list, return flat list of items."""
    print("\n📋 Fetching shopping activity list...")
    all_items = []
    page_num = 1

    # First page
    data = gql(token, "RewardsActivityHomeFirstPage", ACTIVITY_LIST_QUERY,
               {"featureFlags": FEATURE_FLAGS_LIST})
    home = (data.get("data") or {}).get("activityHome") or {}
    results = home.get("results") or {}

    items, next_token = _extract_items(results)
    all_items.extend(items)
    print(f"  Page {page_num}: +{len(items)} activities (total: {len(all_items)})")

    # Subsequent pages
    while next_token and page_num < max_pages:
        page_num += 1
        data = gql(token, "RewardsActivityHomeNextPage", ACTIVITY_LIST_NEXT_QUERY,
                   {"pageToken": next_token, "featureFlags": FEATURE_FLAGS_LIST})
        next_home = (data.get("data") or {}).get("activityHomeNextPage") or {}
        next_results = next_home.get("results") or {}
        items, next_token = _extract_items(next_results)
        if not items:
            break
        all_items.extend(items)
        print(f"  Page {page_num}: +{len(items)} activities (total: {len(all_items)})")
        time.sleep(0.3)

    return all_items


def _extract_items(results: dict) -> tuple[list[dict], str | None]:
    """Pull RewardsActivityItem entries out of the section list."""
    items = []
    for section in results.get("sections") or []:
        section_title = section.get("sectionTitle") or ""
        for it in section.get("sectionItems") or []:
            if it.get("__typename") != "RewardsActivityItem":
                continue
            it["_section"] = section_title  # e.g. "This Month", "April 2026"
            items.append(it)
    return items, results.get("nextPageToken")


def fetch_receipt_detail(token: str, activity_details_id: str) -> dict | None:
    """Fetch the full receipt (items + total) for a single activity."""
    data = gql(token, "ActivityDetails", ACTIVITY_DETAIL_QUERY, {
        "id":           activity_details_id,
        "featureFlags": FEATURE_FLAGS_DETAIL,
    })
    return (data.get("data") or {}).get("activityDetails")


def parse_receipt_into_rows(activity: dict, details: dict) -> list[dict]:
    """
    Flatten a receipt's items into CSV-ready rows. Handles weight-priced
    items (e.g. bananas) and uses the `prefixChar` markers from Woolies:
       '#'  → promotional / often snack-y item (filled flag)
       '^'  → on special (reduced price)
    """
    if not details:
        return []

    # Find the 'eReceipt' tab (skip the points-breakdown tab)
    receipt_page = None
    for tab in details.get("tabs") or []:
        page = tab.get("page") or {}
        if page.get("__typename") == "ReceiptDetails":
            receipt_page = page
            break
    if not receipt_page:
        return []

    # Pull out store info + total + line items
    store_name = ""
    store_no   = ""
    total      = ""
    raw_items  = []
    transaction_details = ""

    for block in receipt_page.get("details") or []:
        tn = block.get("__typename")
        if tn == "ReceiptDetailsHeader":
            store_name = block.get("title") or ""
            store_no   = block.get("storeNo") or ""
        elif tn == "ReceiptDetailsTotal":
            total = (block.get("total") or "").replace("$", "")
        elif tn == "ReceiptDetailsItems":
            raw_items = block.get("items") or []
        elif tn == "ReceiptDetailsFooter":
            transaction_details = block.get("transactionDetails") or ""

    # Date — try to pull DD/MM/YYYY out of "POS  063  TRANS  8299   20:03   05/06/2026"
    date_iso = _parse_transaction_date(transaction_details, activity.get("displayDate") or "")

    # Walk the items, pairing each product with its next weight/qty line
    rows = []
    i = 0
    while i < len(raw_items):
        line   = raw_items[i]
        desc   = (line.get("description") or "").strip()
        amount = (line.get("amount") or "").strip()
        prefix = line.get("prefixChar") or ""

        if not desc:
            i += 1
            continue

        # Strip the leading '#' that Woolies sometimes bakes into the description
        clean_desc = desc.lstrip("#").strip()

        qty        = 1
        unit_price = ""
        weight_kg  = ""

        # If this line has no price, it's likely a "header" — the next line
        # has the qty/weight + price
        if not amount and i + 1 < len(raw_items):
            next_line = raw_items[i + 1]
            next_desc = (next_line.get("description") or "").lower()
            next_amt  = (next_line.get("amount") or "").strip()

            # "0.937 kg NET @ $4.90/kg"
            m_weight = re.search(r"([\d.]+)\s*kg.*?@\s*\$?([\d.]+)\s*/\s*kg", next_desc)
            # "Qty 3 @ $2.95 each"
            m_qty    = re.search(r"qty\s*(\d+)\s*@\s*\$?([\d.]+)", next_desc)

            if m_weight:
                weight_kg  = m_weight.group(1)
                unit_price = m_weight.group(2)
                amount     = next_amt
                i += 1
            elif m_qty:
                qty        = int(m_qty.group(1))
                unit_price = m_qty.group(2)
                amount     = next_amt
                i += 1

        # Classify based on prefix char
        is_promo   = "#" in prefix or desc.lstrip().startswith("#")
        is_special = "^" in prefix

        rows.append({
            "date":           date_iso,
            "store":          store_name,
            "store_no":       store_no,
            "section":        activity.get("_section") or "",
            "item":           clean_desc,
            "qty":            qty,
            "weight_kg":      weight_kg,
            "unit_price":     unit_price,
            "line_total":     amount.replace("$", ""),
            "receipt_total":  total,
            "is_promo_item":  is_promo,        # # marker — often snack/junk
            "is_on_special":  is_special,      # ^ marker — reduced price
            "prefix_char":    prefix,
            "activity_id":    activity.get("id") or "",
            "receipt_source": (activity.get("receipt") or {}).get("receiptSource") or "",
            "category":       "",              # filled later by the dashboard
        })
        i += 1

    return rows


def _parse_transaction_date(transaction_details: str, fallback_display: str) -> str:
    """Try to extract a DD/MM/YYYY date and return as YYYY-MM-DD."""
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", transaction_details or "")
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Fall back to the displayDate from the activity list (e.g. "Sun 14 Jun")
    if fallback_display:
        try:
            # Assume current year
            return datetime.strptime(
                f"{fallback_display} {datetime.now().year}", "%a %d %b %Y"
            ).strftime("%Y-%m-%d")
        except ValueError:
            return fallback_display
    return ""


def save_outputs(activities: list[dict], all_rows: list[dict],
                 raw_details: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── trips_summary.csv ────────────────────────────────────────────────
    trips_csv = out_dir / "trips_summary.csv"
    with open(trips_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date_display", "section", "description",
                           "amount", "origin", "type", "activity_id",
                           "receipt_source"]
        )
        writer.writeheader()
        for act in activities:
            writer.writerow({
                "date_display":   act.get("displayDate") or "",
                "section":        act.get("_section") or "",
                "description":    act.get("description") or "",
                "amount":         (act.get("transaction") or {}).get("amountAsDollars") or "",
                "origin":         (act.get("transaction") or {}).get("origin") or "",
                "type":           act.get("transactionType") or "",
                "activity_id":    act.get("id") or "",
                "receipt_source": (act.get("receipt") or {}).get("receiptSource") or "",
            })
    print(f"\n🗓️  Trips summary  → {trips_csv}  ({len(activities)} entries)")

    # ── items.csv ─── the important one for the dashboard ────────────────
    if all_rows:
        items_csv = out_dir / "items.csv"
        fieldnames = ["date", "store", "store_no", "section", "item", "qty",
                      "weight_kg", "unit_price", "line_total", "receipt_total",
                      "is_promo_item", "is_on_special", "prefix_char",
                      "activity_id", "receipt_source", "category"]
        with open(items_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"📊 Items CSV       → {items_csv}  ({len(all_rows)} line items)")
    else:
        print("⚠️  No item-level receipt data was extracted.")

    # ── raw_receipts.json — full fidelity backup ─────────────────────────
    raw_json = out_dir / "raw_receipts.json"
    with open(raw_json, "w", encoding="utf-8") as f:
        json.dump({"activities": activities, "details": raw_details},
                  f, indent=2, ensure_ascii=False)
    print(f"💾 Raw JSON backup → {raw_json}")


def main():
    parser = argparse.ArgumentParser(
        description="Download Everyday Rewards e-receipts via GraphQL API"
    )
    parser.add_argument("--token", "-t",
                        help="Bearer token from DevTools (or paste when prompted)")
    parser.add_argument("--out", "-o", default="./everyday_rewards_data",
                        help="Output folder (default: ./everyday_rewards_data)")
    parser.add_argument("--max-pages", type=int, default=50,
                        help="Max activity pages to fetch (default: 50)")
    parser.add_argument("--no-receipts", action="store_true",
                        help="Skip per-receipt detail fetch — only trip summaries")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only fetch this many receipts (useful for testing)")
    args = parser.parse_args()

    print("=" * 64)
    print("  🛒  Everyday Rewards Receipt Scraper")
    print("=" * 64)

    # Token
    token = args.token
    if not token:
        print("\nPaste your bearer token (from DevTools → Network → graphql request):")
        token = input("Token: ").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
    if not token:
        print("❌ No token provided. Exiting.")
        sys.exit(1)

    # Fetch all activities
    activities = fetch_activity_list(token, max_pages=args.max_pages)

    # Filter down to ones that actually have receipts (in-store shops)
    shoppable = [a for a in activities
                 if a.get("receipt") and a.get("activityDetailsId")
                 and a.get("transactionType") == "purchase"]
    print(f"\n✅ Total activities: {len(activities)}")
    print(f"   Of those, {len(shoppable)} have receipts (in-store shops)")

    if args.limit:
        shoppable = shoppable[:args.limit]
        print(f"   (limited to first {args.limit} for this run)")

    all_rows: list[dict] = []
    raw_details: dict = {}

    if not args.no_receipts and shoppable:
        print(f"\n🧾 Fetching item-level receipts for {len(shoppable)} shops...")
        for idx, act in enumerate(shoppable):
            details_id = act.get("activityDetailsId") or ""
            store = (act.get("transaction") or {}).get("origin") or "?"
            date  = act.get("displayDate") or "?"
            print(f"  [{idx+1:3d}/{len(shoppable)}] {date}  {store:35s}", end="")

            details = fetch_receipt_detail(token, details_id)
            if details:
                rows = parse_receipt_into_rows(act, details)
                all_rows.extend(rows)
                raw_details[act.get("id") or details_id[:24]] = details
                print(f"  → {len(rows)} items")
            else:
                print("  (no receipt available)")
            time.sleep(0.25)

    # Save outputs
    save_outputs(activities, all_rows, raw_details, Path(args.out))

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  🎉  Done!")
    print("=" * 64)
    if all_rows:
        total_spend = sum(float(r["line_total"] or 0) for r in all_rows
                          if r["line_total"])
        promo_count = sum(1 for r in all_rows if r["is_promo_item"])
        special_count = sum(1 for r in all_rows if r["is_on_special"])
        print(f"  Items captured:    {len(all_rows)}")
        print(f"  Total line spend:  ${total_spend:.2f}")
        print(f"  Promo items (#):   {promo_count}")
        print(f"  On-special (^):    {special_count}")
    print(f"\n  Output folder:     {Path(args.out).resolve()}")
    print("  Next: load items.csv into the dashboard for AI categorisation.")


if __name__ == "__main__":
    main()