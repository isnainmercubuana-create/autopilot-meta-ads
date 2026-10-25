#!/usr/bin/env python3
"""Meta Ads Budget Reset v3 — Batch-Optimized + BID COST CAP aware.
Resets all CBO campaigns to Rp 250.000 and ABO ad sets to Rp 75.000.
Skips BID COST CAP campaigns (manual budget control).
Uses 2 API calls per account (campaigns + adsets), not per-campaign.
"""
import json, os, urllib.request, urllib.error, sys, urllib.parse, time, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

TOKEN = open(Path(os.environ.get("AUTOPILOT_DIR", Path.home() / ".autopilot")) / "meta_token").read().strip()
API = "https://graph.facebook.com/v21.0"
WIB = timezone(timedelta(hours=7))

ACCOUNTS = [
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT", "act_ACCOUNT", "act_ACCOUNT",
    "act_ACCOUNT",
    "act_ACCOUNT",
    "act_ACCOUNT",
    "act_ACCOUNT",   # AB-1052 YATIM 7
    "act_ACCOUNT",   # AB-1139 YATIM 6
    "act_ACCOUNT",  # AB-1080 ACCOUNT_X 3 (akun baru 21 Sep)
    "act_ACCOUNT",  # AB-1081 ACCOUNT_X 4 (akun baru 21 Sep)
    "act_ACCOUNT",   # ACCOUNT_Z - Owner Z - 2 (masuk 03 Okt 2026)
    "act_ACCOUNT",   # ACCOUNT_Z - Owner Z - 3 (masuk 03 Okt 2026)
    "act_ACCOUNT",  # ACCOUNT_Z - Owner Z - 4 (masuk 03 Okt 2026)
    "act_ACCOUNT"   # ACCOUNT_Z - Owner Z - 5 (masuk 03 Okt 2026)
]
CBO_BASE, ABO_BASE = 250000, 75000
BID_BASE = 5_000_000
MAX_RETRIES = 3

def idr(n): return f"{int(n):,}".replace(",", ".")

def get(path, retries=MAX_RETRIES):
    sep = "&" if "?" in path else "?"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"{API}/{path}{sep}access_token={TOKEN}", timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if attempt < retries - 1:
                time.sleep(3)
    print(f"  ⚠️ GET FAIL [{path[:60]}] after {retries} retries", flush=True)
    return None

def post(obj_id, data, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{API}/{obj_id}?access_token={TOKEN}",
                data=urllib.parse.urlencode(data).encode(), method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None
            if attempt < retries - 1:
                time.sleep(2)
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    print(f"  ⚠️ POST FAIL [{obj_id[:40]}] after {retries} retries", flush=True)
    return None

now = datetime.now(WIB)
total_cbo = total_abo = total_bid = 0

print(f"╔══════════════════════════════════╗")
print(f"║  🔄 BUDGET RESET — {now.strftime('%d %b %Y %H:%M WIB')}  ║")
print(f"╚══════════════════════════════════╝")
print()

# Tunggu network siap (DNS sering belum ready pas laptop baru bangun dari sleep)
test = None
for _ in range(10):  # max ~5 menit
    test = get("me?fields=id")
    if test and "id" in test:
        break
    print("  ⏳ Network belum siap, retry 30s...", flush=True)
    time.sleep(30)
if not test or "id" not in (test or {}):
    print("❌ FATAL: Token tidak valid / network down (retry 5 menit gagal).")
    exit(1)

# Load autopilot state → baseline manual tiap campaign/adset (bukan fixed 250K/75K)
STATE = {}
try:
    STATE = json.loads((Path(os.environ.get("AUTOPILOT_DIR", Path.home() / ".autopilot")) / "meta_automation_state.json").read_text())
except Exception:
    pass

def cbo_baseline(act, cid):
    b = STATE.get(act, {}).get("campaigns", {}).get(cid, {}).get("baseline_budget")
    return int(b) if b else CBO_BASE

def adset_baseline(act, aid, is_bid):
    b = STATE.get(act, {}).get("ad_sets", {}).get(aid, {}).get("baseline_budget")
    if b:
        return int(b)
    return BID_BASE if is_bid else ABO_BASE

for act_id in ACCOUNTS:
    act_cbo = act_abo = act_bid = 0

    camps = get(f"{act_id}/campaigns?fields=id,name,status,daily_budget&limit=200")
    if not camps or not camps.get("data"): continue

    cbo_ids = set()
    bid_campaign_ids = set()

    for c in camps["data"]:
        if c.get("status") not in ("ACTIVE", "PAUSED"): continue
        cid, cname, budget = c["id"], c["name"], int(c.get("daily_budget", 0) or 0)

        # Track BID campaigns — jangan di-reset (detect "BID" sebagai kata utuh, bukan "BIDARA")
        if re.search(r"\bBID\b", cname.upper()):
            bid_campaign_ids.add(cid)

        if budget > 0:
            if cid in bid_campaign_ids:
                continue  # skip BID COST CAP CBO
            base = cbo_baseline(act_id, cid)
            if budget > base:  # HANYA turunin — jangan pernah NAIKIN budget (operator '>' bukan '!=')
                r = post(cid, {"daily_budget": str(base)})
                if r and "error" not in r:
                    act_cbo += 1
                    print(f"  ✅ CBO {cname[:45]}: Rp {idr(budget)} → Rp {idr(base)}")
            cbo_ids.add(cid)

    adsets = get(f"{act_id}/adsets?fields=id,name,daily_budget,campaign_id,status&limit=500")
    if not adsets or not adsets.get("data"): continue

    for a in adsets["data"]:
        if a.get("status") not in ("ACTIVE", "PAUSED"): continue
        acampaign_id = a.get("campaign_id", "")

        # Skip CBO adsets
        if acampaign_id in cbo_ids: continue
        
        aid, aname, abudget = a["id"], a.get("name", "?"), int(a.get("daily_budget", 0) or 0)
        
        # BID campaigns: SKIP TOTAL — anggaran BID = hak mutlak user (aturan keras).
        # JANGAN ada code path post(daily_budget) ke objek BID, termasuk "reset ke 5M".
        if acampaign_id in bid_campaign_ids:
            continue
        
        base = adset_baseline(act_id, aid, False)
        if abudget > 0 and abudget > base:  # HANYA turunin — jangan pernah NAIKIN
            r = post(aid, {"daily_budget": str(base)})
            if r and "error" not in r:
                act_abo += 1
                if act_abo <= 5:
                    print(f"  ✅ ABO {aname[:40]}: Rp {idr(abudget)} → Rp {idr(base)}")

    if act_cbo + act_abo > 0:
        print(f"  📁 {act_id}: {act_cbo} CBO + {act_abo} ABO diturunin (BID: tidak disentuh)")

    total_cbo += act_cbo
    total_abo += act_abo
    total_bid += act_bid

    time.sleep(0.5)  # jeda antar akun, cegah rate limit

total = total_cbo + total_abo
if total > 0:
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"🔄 {total} budget diturunin ({total_cbo} CBO · {total_abo} ABO) — TIDAK ada yang dinaikin")
    print(f"   CBO → baseline (fallback Rp {idr(CBO_BASE)}) | ABO → baseline (fallback Rp {idr(ABO_BASE)}) | BID: TIDAK disentuh")
else:
    print(f"\n✅ Semua sudah di baseline")