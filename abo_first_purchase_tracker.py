#!/usr/bin/env python3
"""ABO First Purchase Tracker — records spend threshold for first purchase."""
import json, os, os, re, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

_DIR = Path(os.environ.get("AUTOPILOT_DIR", Path.home() / ".autopilot"))
TOKEN = (_DIR / "meta_token").read_text().strip()
API = 'https://graph.facebook.com/v21.0'
WIB = timezone(timedelta(hours=7))
TRACK_FILE = _DIR / "abo_first_purchase.json"
now = datetime.now(WIB)
today = now.strftime('%Y-%m-%d')

# Daftar akun = IKUT sumber tunggal: daftar akun di `meta_ads_automation.py`.
# Dulu di-hardcode 16 akun → 13 akun nggak pernah dipantau (insiden 21 Sep 2026).
# Jangan di-hardcode lagi: biar akun baru otomatis ikut kapan pun ditambahin ke autopilot.
_acct_src = (Path(__file__).with_name("meta_ads_automation.py")).read_text()
ACTS = sorted({"act_" + m for m in re.findall(r'"(\d{15,17})"', _acct_src)})

# Load existing
data = {}
if TRACK_FILE.exists():
    data = json.loads(TRACK_FILE.read_text())

if today not in data:
    data = {today: {}}  # reset daily

records = data[today]
new_count = 0

for act in ACTS:
    # Get CBO IDs
    try:
        cr = urllib.request.urlopen(f'{API}/{act}/campaigns?fields=id,daily_budget&limit=200&access_token={TOKEN}',timeout=10)
        camps = json.loads(cr.read())
    except: continue
    cbo_ids = {c['id'] for c in camps['data'] if int(c.get('daily_budget',0)or 0)>0}
    
    # Get ABO adsets
    try:
        ar = urllib.request.urlopen(f'{API}/{act}/adsets?fields=id,name,campaign_id&limit=500&access_token={TOKEN}',timeout=10)
        adsets = json.loads(ar.read())
    except: continue
    
    abo_map = {}
    for a in adsets['data']:
        if a.get('campaign_id','') not in cbo_ids:
            abo_map[a['id']] = a.get('name','?')
    
    if not abo_map: continue
    
    # Get insights
    try:
        ir = urllib.request.urlopen(f'{API}/{act}/insights?fields=adset_id,spend,actions&date_preset=today&level=adset&limit=500&access_token={TOKEN}',timeout=10)
        ins = json.loads(ir.read())
    except: continue
    
    for row in ins.get('data',[]):
        aid = row.get('adset_id','')
        if aid not in abo_map: continue
        spend = float(row.get('spend',0))
        purchases = sum(int(a['value']) for a in row.get('actions',[]) if 'purchase' in a.get('action_type',''))
        
        if purchases > 0 and spend > 0:
            if aid not in records:
                records[aid] = {
                    'name': abo_map[aid],
                    'account': act,
                    'first_purchase_spend': int(spend),
                    'purchases': purchases,
                    'time': now.strftime('%H:%M WIB')
                }
                new_count += 1
                print(f'  🆕 {abo_map[aid][:45]} — Rp {spend:,.0f}')
            else:
                # Update if more purchases
                old = records[aid]
                if purchases > old.get('purchases',0):
                    old['purchases'] = purchases
                    old['spend_now'] = int(spend)

data[today] = records
TRACK_FILE.parent.mkdir(parents=True, exist_ok=True)
TRACK_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# Summary
count = len(records)
if count > 0:
    spends = [r['first_purchase_spend'] for r in records.values()]
    spends.sort()
    print(f'\n━━━━━━━━━━━━━━━━━━━━')
    print(f'📊 {today} — {count} ABO dapet order')
    print(f'   Median first spend: Rp {spends[len(spends)//2]:,}')
    print(f'   Range: Rp {min(spends):,} - Rp {max(spends):,}')
    print(f'   New today: {new_count}')
    print(f'💾 {TRACK_FILE}')
else:
    print(f'Belum ada ABO yg dapet order hari ini')
