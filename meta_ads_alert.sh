#!/bin/bash
# Meta Ads Alert Monitor — cuma output kalau ada masalah
TOKEN=$(cat ${AUTOPILOT_DIR:-~/.autopilot}/meta_token 2>/dev/null)
[ -z "$TOKEN" ] && exit 0

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

ALERTS=""
TODAY=$(date '+%d %b %Y, %H:%M')

for act_id in "${ACCOUNTS[@]}"; do
  resp=$(curl -s "https://graph.facebook.com/v21.0/${act_id}/insights?fields=spend,impressions,clicks,actions,action_values,cost_per_action_type&date_preset=today&level=account&access_token=${TOKEN}" 2>/dev/null)
  
  vals=$(echo "$resp" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    data = d.get('data',[])
    if not data: sys.exit(0)
    i = data[0]
    spend = float(i.get('spend',0))
    if spend < 10000: sys.exit(0)  # skip kalau spend < 10rb
    
    purchases = 0
    for a in i.get('actions',[]):
        if a.get('action_type')=='purchase':
            purchases = int(a.get('value',0)); break
    revenue = 0
    for av in i.get('action_values',[]):
        if av.get('action_type')=='purchase':
            revenue = float(av.get('value',0)); break
    cpa = 0
    for cp in i.get('cost_per_action_type',[]):
        if cp.get('action_type')=='purchase':
            cpa = float(cp.get('value',0)); break
    
    roas = revenue/spend if spend > 0 and revenue > 0 else 0
    print(f'{spend},{purchases},{revenue},{cpa},{roas}')
except:
    sys.exit(0)
" 2>/dev/null)
  
  [ -z "$vals" ] && continue
  
  IFS=',' read -r spend purchases revenue cpa roas <<< "$vals"
  
  alert=""
  roas_ok=$(python3 -c "print(1 if float($roas) < 2.0 else 0)" 2>/dev/null)
  cpa_high=$(python3 -c "print(1 if float($cpa) > 80000 else 0)" 2>/dev/null)
  
  if [ "$roas_ok" = "1" ] && [ "$purchases" -gt 0 ]; then
    alert="🔴 ROAS DROP: ${roas}x (under 2.0)"
  fi
  if [ "$cpa_high" = "1" ]; then
    alert="${alert} 💸 CPA SPIKE: Rp $(printf "%'.0f" $cpa) (above 80rb)"
  fi
  
  if [ -n "$alert" ]; then
    ALERTS+="\n$alert"
    ALERTS+="\n   ${act_id} | Spend: Rp $(printf "%'.0f" $spend) | Orders: $purchases | Rev: Rp $(printf "%'.0f" $revenue)"
  fi
done

if [ -n "$ALERTS" ]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🚨 META ADS ALERT — ${TODAY}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo -e "$ALERTS"
  echo ""
  echo "💡 SARAN: Cek Ads Manager → filter by ROAS rendah → evaluasi creative/targeting"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi
# Silent exit kalau semua aman = no delivery
