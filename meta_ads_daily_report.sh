#!/bin/bash
# Meta Ads Daily Report v3 — Campaign-Level + IDR Format
TOKEN=$(cat ${AUTOPILOT_DIR:-~/.autopilot}/meta_token 2>/dev/null)
[ -z "$TOKEN" ] && exit 1

ACCOUNTS=(
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT act_ACCOUNT act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
  act_ACCOUNT
)

TODAY=$(date '+%d %b %Y')

# Use Python for all IDR formatting + processing
python3 << PYEOF
import json, urllib.request, sys
from datetime import datetime

TOKEN = "$TOKEN"
TODAY = "$TODAY"
ACCOUNTS = """${ACCOUNTS[@]}""".split()

def idr(n):
    """Format as Indonesian Rupiah with dots: 1500000 -> 1.500.000"""
    return f"{int(n):,}".replace(",", ".")

def api_get(path):
    sep = "&" if "?" in path else "?"
    url = f"https://graph.facebook.com/v21.0/{path}{sep}access_token={TOKEN}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except:
        return None

# Collect all account data
all_data = {}
total_spend = 0
total_purchases = 0
total_revenue = 0
total_cpr_sum = 0
cpr_count = 0

for act_id in ACCOUNTS:
    # Get campaign-level insights for yesterday
    data = api_get(f"{act_id}/insights?fields=campaign_id,campaign_name,spend,actions,cost_per_action_type&date_preset=yesterday&level=campaign&limit=200")
    if not data or not data.get("data"):
        continue
    
    campaigns = []
    act_spend = 0
    act_purchases = 0
    
    for row in data["data"]:
        cid = row.get("campaign_id", "")
        cname = row.get("campaign_name", "Unknown")
        spend = float(row.get("spend", 0))
        if spend == 0:
            continue
        
        purchases = 0
        for a in row.get("actions", []):
            if a.get("action_type") in ("purchase", "omni_purchase"):
                purchases = max(purchases, int(a.get("value", 0)))
        
        cpr = 0
        for cp in row.get("cost_per_action_type", []):
            if cp.get("action_type") in ("purchase", "omni_purchase"):
                cpr = float(cp.get("value", 0))
                break
        if not cpr and purchases > 0 and spend > 0:
            cpr = spend / purchases
        
        roas = (purchases * 123000 / spend) if spend > 0 and purchases > 0 else 0  # estimate
        # Better: use actual revenue if available, else estimate
        # Actually we have CPR = spend/purchase, so use that for ROAS classification
        if cpr > 0:
            # ROAS estimate based on CPR vs avg order value ~123k
            roas = 123000 / cpr
        elif purchases > 0:
            roas = 999  # has purchases, can't calc ROAS
        else:
            roas = 0
        
        emoji = "⚪"
        if purchases > 0:
            if roas >= 4: emoji = "🔥"
            elif roas >= 3: emoji = "✅"
            elif roas >= 2: emoji = "⚠️"
            else: emoji = "🔴"
        elif spend >= 40000:
            emoji = "💀"
        
        campaigns.append({
            "name": cname,
            "spend": int(spend),
            "purchases": purchases,
            "cpr": int(cpr),
            "emoji": emoji
        })
        
        act_spend += spend
        act_purchases += purchases
        if cpr > 0:
            total_cpr_sum += cpr
            cpr_count += 1
    
    if campaigns:
        all_data[act_id] = {
            "spend": int(act_spend),
            "purchases": act_purchases,
            "campaigns": campaigns
        }
        total_spend += act_spend
        total_purchases += act_purchases

# Calculate summary
avg_cpr = int(total_cpr_sum / cpr_count) if cpr_count > 0 else 0
total_roas = 123000 * total_purchases / total_spend if total_spend > 0 else 0

# ── OUTPUT ──
print("╔══════════════════════════════════╗")
print(f"║  📊 META ADS REPORT — {TODAY}  ║")
print("╚══════════════════════════════════╝")
print()
print(f"💰 Total Spend   : Rp {idr(total_spend)}")
print(f"📊 Avg CPR       : Rp {idr(avg_cpr)}")
print(f"📦 Total Orders  : {total_purchases}")
print(f"🎯 ROAS          : {total_roas:.1f}x")
print(f"🏢 Active Acc    : {len(all_data)}")

# Per account
for act_id, adata in sorted(all_data.items()):
    print()
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📁 {act_id}")
    print(f"   Spend: Rp {idr(adata['spend'])} | Orders: {adata['purchases']}")
    print()
    
    # Sort campaigns by spend descending
    camps = sorted(adata["campaigns"], key=lambda c: c["spend"], reverse=True)
    for c in camps:
        name = c["name"][:55]
        if c["purchases"] > 0:
            print(f"  {c['emoji']} {name}")
            print(f"     Rp {idr(c['spend'])} | CPR Rp {idr(c['cpr'])} | {c['purchases']} order")
        elif c["spend"] >= 40000:
            print(f"  {c['emoji']} {name}")
            print(f"     Rp {idr(c['spend'])} | ⚠️ 0 order | burn!")
        else:
            print(f"  {c['emoji']} {name}")
            print(f"     Rp {idr(c['spend'])} | 0 order")

print()
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"Generated · {datetime.now().strftime('%H:%M WIB')}")
PYEOF
