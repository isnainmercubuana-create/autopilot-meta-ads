#!/usr/bin/env python3
"""
Meta Ads Automation Engine v3 — Rate-Limit Aware
=================================================
15-min campaign optimizer optimized for Meta Development tier.
Uses max 3 API calls per account (campaigns, adset-insights, campaign-insights).
No adset listing needed — INSIGHTS already contain adset-level data.

Strategy:
- 1 call: ACTIVE campaigns (no pagination, max 200)
- 1 call: Campaign-level insights (for CBO campaigns)
- 1 call: Adset-level insights (for ABO campaigns)
- Rate limit? Skip account, process next.

Usage:
  python3 meta_ads_automation.py                    # Dry-run ALL accounts
  python3 meta_ads_automation.py --live              # Live mode
  python3 meta_ads_automation.py --account act_123   # Single account
"""

import json, os, os, sys, time, re
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── CONFIG ──
TOKEN_FILE = Path(os.environ.get("AUTOPILOT_DIR", Path.home() / ".autopilot")) / "meta_token"
STATE_FILE = Path(os.environ.get("AUTOPILOT_DIR", Path.home() / ".autopilot")) / "meta_automation_state.json"
API_BASE = "https://graph.facebook.com/v21.0"
WIB = timezone(timedelta(hours=7))

# Thresholds (all in IDR)
CPR_PAUSE = 50_000
# RULES-V2 (24 Okt 2026): belanja tanpa hasil — CBO 35rb, ABO 25rb.
SPEND_NO_PURCHASE_CBO = 35_000
SPEND_NO_PURCHASE_ABO = 25_000
SPEND_NO_PURCHASE = 35_000          # legacy alias (CBO)
CPR_REACTIVATE = 35_000
COOLDOWN_SEC = 15 * 60

# RULES-V2: skala turun mulai CPR 40rb, cooldown 15 menit.
CPR_SCALE_DOWN = 40_000
SCALEDOWN_COOLDOWN_SEC = 15 * 60
# RULES-V2: tangga pengali CPR (di atas 35rb = tidak scale).
CPR_T1, CPR_T2, CPR_T3 = 25_000, 30_000, 35_000
MAX_BUDGET = 1_500_000
TIER_SCALE = {1: 0.10, 2: 0.20, 3: 0.20, 4: 0.30, 5: 0.30, 6: 0.30, 7: 0.40}  # delta order → %
MIN_BUDGET = 10_000
NEW_CAMPAIGN_GRACE_SEC = 0  # disabled — user prefers standard rules, no special treatment

RATE_LIMIT_COOLDOWN = {}  # account_id -> cooldown_until timestamp

# ── HELPERS ──
def load_token():
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return os.environ.get("META_TOKEN", "")

def api_call(path, token, params=None, method="GET", data=None):
    """Single API call. Returns (data_dict, rate_limited_bool)."""
    sep = "&" if "?" in path else "?"
    url = f"{API_BASE}/{path}{sep}access_token={token}"
    if params:
        for k, v in params.items():
            url += f"&{k}={urllib.parse.quote(str(v))}"
    
    try:
        req = urllib.request.Request(url)
        if method == "POST" and data:
            req = urllib.request.Request(url, 
                data=urllib.parse.urlencode(data).encode(), method="POST")
        
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode()), False
    
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.code >= 400 else {}
        err_msg = body.get("error", {}).get("message", "")
        err_code = body.get("error", {}).get("code", 0)
        
        if err_code == 17 or "request limit" in err_msg.lower():
            print(f"  ⚠️ Rate limit hit for {path[:50]}", file=sys.stderr)
            return None, True
        
        print(f"  API {e.code} [{path[:50]}]: {err_msg[:120]}", file=sys.stderr)
        return None, False
    
    except Exception as e:
        print(f"  API ERR [{path[:50]}]: {e}", file=sys.stderr)
        return None, False

def now_wib():
    return datetime.now(WIB)

def today_wib():
    return now_wib().strftime("%Y-%m-%d")

def is_operational_hours():
    """
    Engine only runs 04:00 - 20:00 WIB.
    Outside these hours: skip all processing.
    """
    now = now_wib()
    hour = now.hour
    # Active: 4 AM (04:00) to 8 PM (20:00)
    return 4 <= hour < 20

def can_scale():
    """Scale up only allowed before 19:00 WIB. Other rules still apply."""
    return now_wib().hour < 19

# ── STATE ──
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            pass
    return {}

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

def reset_daily(acct_state, today):
    if acct_state.get("date") != today:
        acct_state.clear()
        acct_state["date"] = today
        acct_state["campaigns"] = {}
    return acct_state

# ── DATA FETCH (batch, rate-limit aware) ──
def fetch_account_data(account_id, token):
    """
    Fetch campaigns + insights in max 3 calls.
    Returns: {campaigns: {}, cbo_ids: set, abo_ids: set, adset_insights: {}, campaign_insights: {}}
    Returns None on rate limit.
    """
    # Check rate limit cooldown
    if account_id in RATE_LIMIT_COOLDOWN:
        if now_wib() < RATE_LIMIT_COOLDOWN[account_id]:
            return None
    
    result = {"campaigns": {}, "cbo_ids": set(), "abo_ids": set(),
              "adset_insights": {}, "campaign_insights": {}}
    
    # 1. Get ACTIVE + PAUSED campaigns (ONE call, no pagination, limit 200)
    data, rl = api_call(f"{account_id}/campaigns", token, {
        "fields": "id,name,status,daily_budget,objective",
        "limit": "200"
    })
    
    if rl:
        RATE_LIMIT_COOLDOWN[account_id] = now_wib() + timedelta(minutes=30)
        return None
    if not data:
        return result
    
    for c in data.get("data", []):
        cid = c["id"]
        budget = int(c.get("daily_budget", 0) or 0)
        result["campaigns"][cid] = {
            "name": c.get("name", "?"),
            "status": c.get("status", "PAUSED"),
            "daily_budget": budget,
            "objective": c.get("objective", "?"),
            "is_cbo": budget > 0
        }
        if budget > 0:
            result["cbo_ids"].add(cid)
        else:
            result["abo_ids"].add(cid)
    
    # 2. Get campaign-level insights (for CBO evaluation)
    # Only if there are CBO or ABO campaigns with spend
    ci, rl = api_call(f"{account_id}/insights", token, {
        "fields": "campaign_id,spend,actions,cost_per_action_type",
        "date_preset": "today",
        "level": "campaign",
        "limit": "200"
    })
    
    if rl:
        RATE_LIMIT_COOLDOWN[account_id] = now_wib() + timedelta(minutes=30)
        return None
    
    if ci and ci.get("data"):
        for row in ci["data"]:
            cid = row.get("campaign_id", "")
            if cid:
                result["campaign_insights"][cid] = parse_insight_row(row)
    
    # 3. Get adset-level insights (for ABO evaluation)
    ai, rl = api_call(f"{account_id}/insights", token, {
        "fields": "adset_id,spend,actions,cost_per_action_type",
        "date_preset": "today",
        "level": "adset",
        "limit": "500"
    })
    
    if rl:
        RATE_LIMIT_COOLDOWN[account_id] = now_wib() + timedelta(minutes=30)
        # If we already have campaign data, still return (can evaluate CBO)
        if result["cbo_ids"]:
            print(f"  ⚠️ Adset insights rate-limited — only CBO evaluation for {account_id}", file=sys.stderr)
            return result
        return None
    
    if ai and ai.get("data"):
        for row in ai["data"]:
            aid = row.get("adset_id", "")
            if aid:
                result["adset_insights"][aid] = parse_insight_row(row)
    
    return result

def parse_insight_row(row):
    spend = float(row.get("spend", 0))
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
    return {"spend": spend, "purchases": purchases, "cpr": cpr}

# ── MUTATIONS ──
def api_mutate(obj_id, token, dry_run, params):
    if dry_run:
        return True
    r, _ = api_call(obj_id, token, method="POST", data=params)
    return r is not None and "error" not in (r or {})

def pause_obj(oid, token, dry_run):
    return api_mutate(oid, token, dry_run, {"status": "PAUSED"})

def activate_obj(oid, token, dry_run):
    return api_mutate(oid, token, dry_run, {"status": "ACTIVE"})

def set_budget(oid, token, dry_run, budget):
    budget = max(MIN_BUDGET, min(int(budget), MAX_BUDGET))
    return api_mutate(oid, token, dry_run, {"daily_budget": str(budget)})

# ── RULE ENGINE ──
def evaluate(name, obj_id, current_status, current_budget, spend, purchases, cpr,
             camp_state, token, dry_run, now, level="CBO"):
    """Apply 5 rules in priority order. Returns (action, reason, new_state).
    level: "CBO" (campaign) atau "ABO" (adset) — beda ambang belanja tanpa hasil."""
    
    paused_by_system = camp_state.get("paused_by_system", False)
    paused_at = None
    if camp_state.get("paused_at"):
        try:
            paused_at = datetime.fromisoformat(camp_state["paused_at"])
        except:
            pass
    
    last_purchases = camp_state.get("last_purchases", 0)
    last_cpr = camp_state.get("last_cpr", 0)
    budget = current_budget or 100000
    baseline = camp_state.get("baseline_budget") or budget
    
    new_state = {
        "last_purchases": purchases, "last_cpr": cpr,
        "current_budget": budget, "baseline_budget": baseline,
        "name": name, "paused_by_system": paused_by_system,
        "paused_at": camp_state.get("paused_at"),
        "last_scale_down": camp_state.get("last_scale_down"),
        "first_seen_at": camp_state.get("first_seen_at"),
    }
    
    # ── Auto-heal: kalau campaign dipause sistem tapi user unpause manual ──
    if paused_by_system and current_status == "ACTIVE":
        paused_by_system = False
        new_state["paused_by_system"] = False
        new_state["paused_at"] = None
        new_state["first_seen_at"] = now.isoformat()  # kasih grace period baru
        # NOTE: gak return — lanjut evaluasi normal
    
    # RULE 1: PAUSE
    if current_status == "ACTIVE":
        reason = ""
        should = False
        
        # Grace period: jangan pause campaign baru yg belum pernah dapat order
        # Track first_seen_at untuk campaign yg baru pertama kali diproses
        if camp_state.get("first_seen_at") is None:
            camp_state["first_seen_at"] = now.isoformat()
            new_state["first_seen_at"] = camp_state["first_seen_at"]
        else:
            new_state["first_seen_at"] = camp_state["first_seen_at"]
        
        in_grace = False
        if camp_state.get("first_seen_at"):
            try:
                first_seen = datetime.fromisoformat(camp_state["first_seen_at"])
                elapsed = (now - first_seen).total_seconds()
                if elapsed < NEW_CAMPAIGN_GRACE_SEC and purchases == 0 and last_purchases == 0:
                    in_grace = True
            except:
                pass
        
        # Tighter thresholds for BID COST CAP campaigns — detect from name containing "BID"
        is_bid = bool(re.search(r"\bBID\b", name.upper()))
        pause_cpr = 35_000 if is_bid else CPR_PAUSE
        if is_bid:
            pause_spend = 30_000
        elif level == "ABO":
            pause_spend = SPEND_NO_PURCHASE_ABO    # 25rb
        else:
            pause_spend = SPEND_NO_PURCHASE_CBO    # 35rb
        
        if cpr >= pause_cpr and purchases > 0:
            should = True
            reason = f"CPR Rp{cpr:,.0f} ≥ Rp{pause_cpr:,}"
        elif spend >= pause_spend and purchases == 0 and not in_grace:
            should = True
            reason = f"Spend Rp{spend:,.0f} ≥ Rp{pause_spend:,} • 0 purchases"
        
        if should:
            pause_obj(obj_id, token, dry_run)
            new_state["paused_by_system"] = True
            new_state["paused_at"] = now.isoformat()
            return ("PAUSE", reason, new_state)
    
    # RULE 2: REACTIVATE
    if paused_by_system and current_status == "PAUSED":
        if paused_at:
            elapsed = (now - paused_at).total_seconds()
            if elapsed >= COOLDOWN_SEC:
                # Reactivate jika: CPR bagus DAN (ada order baru, atau sebelumnya 0 skrg >0)
                has_new_order = purchases > last_purchases
                is_first_order = (last_purchases == 0 and purchases > 0)
                if cpr > 0 and cpr <= CPR_REACTIVATE and (has_new_order or is_first_order):
                    activate_obj(obj_id, token, dry_run)
                    new_state["paused_by_system"] = False
                    new_state["paused_at"] = None
                    return ("REACTIVATE",
                            f"CPR Rp{cpr:,.0f} ≤ Rp{CPR_REACTIVATE:,} + ↑{purchases}>{last_purchases}",
                            new_state)
            rem = max(0, COOLDOWN_SEC - elapsed)
            return ("COOLDOWN", f"{rem:.0f}s cooldown", new_state)
        return ("COOLDOWN", "cooldown", new_state)
    
    if current_status != "ACTIVE":
        return ("SKIP", "Not active", new_state)
    
    # ── BID campaigns: NO scale up/down, budget dikontrol manual ──
    is_bid = bool(re.search(r"\bBID\b", name.upper()))
    
    if is_bid:
        return ("NO_ACTION", "BID COST CAP — manual budget control", new_state)
    
    # RULE 3: SCALE UP — GASPOL tiered (10%→40% by order delta) + CPR gate
    if can_scale() and purchases > last_purchases and purchases > 0:
        order_delta = purchases - last_purchases
        
        # CPR trending guard: kalau CPR naik >50% dari siklus sebelumnya, capped +10%
        cpr_surging = last_cpr > 0 and cpr > last_cpr * 1.5
        
        # CPR gate + gradient tier
        if cpr >= CPR_PAUSE:
            return ("NO_ACTION", f"CPR Rp{cpr:,.0f} ≥ Rp{CPR_PAUSE:,} — no scale", new_state)
        
        if cpr >= CPR_SCALE_DOWN:
            # FALLTHROUGH: jangan return di sini. Kalau order naik tapi CPR
            # sudah >=40rb, RULE 3 harus MELEWATI supaya RULE 4 (scale down)
            # yang jalan. Dulu `pct=0.0` lalu return NO_ACTION, jadi campaign
            # mahal yang masih dapat order tidak pernah diturunkan.
            pct = 0.0
        else:
            # RULES-V2: ≤25rb=100%, 25-30rb=75%, 30-35rb=50%, 35-40rb=0%
            if cpr <= CPR_T1:
                multiplier = 1.0
            elif cpr <= CPR_T2:
                multiplier = 0.75
            elif cpr <= CPR_T3:
                multiplier = 0.50
            else:
                multiplier = 0.0
            
            if cpr_surging:
                multiplier = min(multiplier, 0.25)  # trending up → max 25% even if full zone
            
            base_pct = 0.40 if order_delta >= 7 else TIER_SCALE.get(order_delta, 0.20)
            pct = base_pct * multiplier
        
        if pct == 0.0:
            # 35–40rb: tidak scale, tapi JANGAN return — biar RULE 4 menilai.
            # Kalau CPR <40rb, RULE 4 juga tidak kena → jatuh ke NO_ACTION.
            pass
        
        new_budget = min(int(budget * (1 + pct)), MAX_BUDGET)
        if new_budget > budget:
            set_budget(obj_id, token, dry_run, new_budget)
            new_state["current_budget"] = new_budget
            return ("SCALE_UP",
                    f"+{pct*100:.0f}% ({order_delta:+d} ord, CPR Rp{cpr:,.0f}): Rp{budget:,}→Rp{new_budget:,}",
                    new_state)
    
    # RULE 4: SCALE DOWN — RULES-V2: mulai CPR 40rb, cooldown 15 menit
    if purchases > 0 and cpr >= CPR_SCALE_DOWN and cpr < CPR_PAUSE and budget > MIN_BUDGET:
        last_scale_down = camp_state.get("last_scale_down")
        if last_scale_down:
            try:
                last_sd = datetime.fromisoformat(last_scale_down)
                if (now - last_sd).total_seconds() < SCALEDOWN_COOLDOWN_SEC:
                    return ("COOLDOWN", "Scale down cooldown", new_state)
            except:
                pass
        
        new_budget = max(int(budget * 0.80), MIN_BUDGET)
        set_budget(obj_id, token, dry_run, new_budget)
        new_state["current_budget"] = new_budget
        new_state["last_scale_down"] = now.isoformat()
        return ("SCALE_DOWN",
                f"CPR Rp{cpr:,.0f} ≥40rb (-20%): Rp{budget:,}→Rp{new_budget:,}",
                new_state)
    
    # RULE 5: NO ACTION
    return ("NO_ACTION", "OK", new_state)

# ── PROCESS ACCOUNT ──
def process_account(account_id, token, state, dry_run, now):
    """Process all campaigns. Returns list of actionable decisions."""
    decisions = []
    
    data = fetch_account_data(account_id, token)
    if data is None:
        return None  # rate-limited → main loop counts skip
    
    camps = data["campaigns"]
    adset_ins = data["adset_insights"]
    camp_ins = data["campaign_insights"]
    
    if not camps:
        return decisions
    
    acct_state = state.setdefault(account_id, {})
    reset_daily(acct_state, today_wib())
    
    # ── CBO campaigns (campaign level evaluation) ──
    for cid in list(data["cbo_ids"]):
        camp = camps.get(cid)
        if not camp:
            # Campaign deleted from API but still in state — skip
            continue
        if camp["status"] != "ACTIVE":
            # Still track paused-by-system CBOs for reactivation
            camp_state = acct_state["campaigns"].setdefault(cid, {})
            if not camp_state.get("paused_by_system"):
                continue
        
        ins = camp_ins.get(cid, {})
        spend = ins.get("spend", 0)
        purchases = ins.get("purchases", 0)
        cpr = ins.get("cpr", 0)
        budget = camp.get("daily_budget", 0)
        camp_name = camp.get("name", "?")
        camp_status = camp.get("status", "ACTIVE")
        
        camp_state = acct_state["campaigns"].setdefault(cid, {})
        action, reason, new_state = evaluate(
            camp_name, cid, camp_status, budget, spend, purchases, cpr,
            camp_state, token, dry_run, now, level="CBO"
        )
        
        acct_state["campaigns"][cid] = new_state
        
        if action not in ("NO_ACTION", "SKIP", "COOLDOWN"):
            decisions.append({
                "account": account_id, "id": cid, "name": camp_name,
                "type": "CBO", "action": action, "reason": reason,
                "budget": new_state.get("current_budget", 0),
                "purchases": purchases, "cpr": cpr
            })
            emoji = {"PAUSE": "🔴", "REACTIVATE": "🟢", "SCALE_UP": "🚀", "SCALE_DOWN": "⬇️"}
            b = f" → Rp{new_state.get('current_budget',0):,.0f}" if action in ("SCALE_UP","SCALE_DOWN") else ""
            print(f"  {emoji.get(action,'⚪')} [{action}] {camp_name[:50]}{b}")
            print(f"      Rp{spend:,.0f} | {purchases} orders | CPR: Rp{cpr:,.0f} | {reason}")
    
    # ── ABO campaigns (adset level evaluation) ──
    # Build adset → campaign mapping and filter to ABO-only adsets
    abo_campaign_ids = data["abo_ids"]
    if abo_campaign_ids or adset_ins:
        # Batch-query adset details
        adset_details = {}
        adset_ids_batch = list(adset_ins.keys())
        # Also include tracked ABO adsets from state (for paused/reactivation)
        for aid in acct_state.get("ad_sets", {}):
            if aid not in adset_ids_batch:
                adset_ids_batch.append(aid)
        # Limit batch queries to avoid rate limits (max 50 IDs)
        max_batch_ids = min(len(adset_ids_batch), 50)
        
        for batch_start in range(0, max_batch_ids, 25):
            batch = adset_ids_batch[batch_start:batch_start+25]
            ids_str = ",".join(batch)
            adsets_data, rl = api_call("", token, {
                "ids": ids_str,
                "fields": "id,name,status,daily_budget,campaign_id"
            })
            if adsets_data and not rl:
                for aid, info in adsets_data.items():
                    if isinstance(info, dict):
                        adset_details[aid] = {
                            "name": info.get("name", f"AdSet {aid[:8]}"),
                            "status": info.get("status", "ACTIVE"),
                            "daily_budget": int(info.get("daily_budget", 0) or 0),
                            "campaign_id": info.get("campaign_id", ""),
                        }
        
        for aid, ins in adset_ins.items():
            ad_detail = adset_details.get(aid, {})
            ad_campaign_id = ad_detail.get("campaign_id", "")
            
            # Only evaluate ABO adsets (must have campaign_id in abo_ids)
            if ad_campaign_id and ad_campaign_id not in abo_campaign_ids:
                continue
            # Check state first — ad_state defined before use
            _tmp_ad_state = acct_state.setdefault("ad_sets", {}).get(aid, {})
            if not ad_campaign_id and not _tmp_ad_state.get("name"):
                continue  # Unknown adset without details, skip
            
            spend = ins.get("spend", 0)
            purchases = ins.get("purchases", 0)
            cpr = ins.get("cpr", 0)
            
            ad_state = acct_state.setdefault("ad_sets", {}).setdefault(aid, {})
            ad_name = ad_detail.get("name") or ad_state.get("name") or f"AdSet {aid[:8]}"
            # Deteksi BID juga dari nama parent campaign (bukan cuma nama adset)
            if ad_campaign_id:
                _pname = camps.get(ad_campaign_id, {}).get("name", "")
                if re.search(r"\bBID\b", _pname.upper()):
                    ad_name = ad_name + " [BID]"
            ad_budget = ad_detail.get("daily_budget") or ad_state.get("current_budget") or 100000
            ad_status = ad_detail.get("status") or "ACTIVE"
            
            # Store metadata for future runs
            if ad_detail.get("name"):
                ad_state["name"] = ad_detail["name"]
            ad_state.setdefault("first_seen_at", now.isoformat())
            
            # Skip inactive adsets with no prior state (brand new, never spent)
            if spend == 0 and ad_status == "ACTIVE" and not ad_state.get("paused_by_system"):
                if ad_state.get("last_purchases", 0) == 0:
                    continue
            
            action, reason, new_state = evaluate(
                ad_name, aid, ad_status, ad_budget, spend, purchases, cpr,
                ad_state, token, dry_run, now, level="ABO"
            )
            
            acct_state["ad_sets"][aid] = new_state
            
            if action not in ("NO_ACTION", "SKIP", "COOLDOWN"):
                decisions.append({
                    "account": account_id, "id": aid, "name": ad_name,
                    "type": "ABO", "action": action, "reason": reason,
                    "budget": new_state.get("current_budget", 0),
                    "purchases": purchases, "cpr": cpr
                })
                emoji = {"PAUSE": "🔴", "REACTIVATE": "🟢", "SCALE_UP": "🚀", "SCALE_DOWN": "⬇️"}
                b = f" → Rp{new_state.get('current_budget',0):,.0f}" if action in ("SCALE_UP","SCALE_DOWN") else ""
                print(f"  {emoji.get(action,'⚪')} [{action}] {ad_name[:45]}{b}")
                print(f"      Rp{spend:,.0f} | {purchases} ord | CPR: Rp{cpr:,.0f} | {reason}")
        
        # Also evaluate tracked ABO adsets that had no insights (paused/reactivation candidates)
        for aid, ad_state in acct_state.get("ad_sets", {}).items():
            if aid in adset_ins:
                continue  # Already evaluated above
            if not ad_state.get("paused_by_system"):
                continue
            
            ad_detail = adset_details.get(aid, {})
            ad_name = ad_detail.get("name") or ad_state.get("name") or f"AdSet {aid[:8]}"
            ad_budget = ad_detail.get("daily_budget") or ad_state.get("current_budget", 100000)
            ad_status = ad_detail.get("status") or "PAUSED"
            
            # Check if parent campaign is BID COST CAP
            ad_campaign_id = ad_detail.get("campaign_id", "")
            if ad_campaign_id:
                parent_camp = camps.get(ad_campaign_id, {})
                if "BID COST CAP" in parent_camp.get("name", "").upper():
                    ad_name = ad_name + " [BID COST CAP]"
            
            # Use last known CPR from state for reactivation evaluation
            last_cpr = ad_state.get("last_cpr", 0)
            last_purch = ad_state.get("last_purchases", 0)
            
            action, reason, new_state = evaluate(
                ad_name, aid, ad_status, ad_budget, 0, last_purch, last_cpr,
                ad_state, token, dry_run, now, level="ABO"
            )
            
            acct_state["ad_sets"][aid] = new_state
            
            if action not in ("NO_ACTION", "SKIP", "COOLDOWN"):
                decisions.append({
                    "account": account_id, "id": aid, "name": ad_name,
                    "type": "ABO", "action": action, "reason": reason,
                    "budget": new_state.get("current_budget", 0),
                    "purchases": ad_state.get("last_purchases", 0), "cpr": 0
                })
                emoji = {"PAUSE": "🔴", "REACTIVATE": "🟢", "SCALE_UP": "🚀", "SCALE_DOWN": "⬇️"}
                print(f"  {emoji.get(action,'⚪')} [{action}] {ad_name[:45]}")
                print(f"      {reason}")
    
    return decisions

# ── MAIN ──
def main():
    dry_run = "--live" not in sys.argv
    # Also check env var or flag file for cron jobs that can't pass args
    if os.environ.get("META_AUTOPILOT_LIVE", "").lower() in ("true", "1", "yes"):
        dry_run = False
    LIVE_FLAG = Path(os.environ.get("AUTOPILOT_DIR", Path.home() / ".autopilot")) / "meta_autopilot_live"
    if LIVE_FLAG.exists():
        dry_run = False
    only_account = None
    for i, arg in enumerate(sys.argv):
        if arg == "--account" and i + 1 < len(sys.argv):
            only_account = sys.argv[i + 1]
    
    token = load_token()
    if not token:
        print("ERROR: No token", file=sys.stderr)
        sys.exit(1)
    
    state = load_state()
    now = now_wib()
    
    # Time gate: silent exit outside 04:00-20:00 WIB
    if not is_operational_hours():
        return
    
    mode = "🔶 DRY-RUN" if dry_run else "🟢 LIVE"
    
    if only_account:
        accounts = [only_account]
    else:
        # ── DIKELUARKAN 24 Okt 2026 (akun dimatikan/ditahan Meta) ──
        #   G - 16 - BRAND 6     -> account_status=2, disable_reason=1 (ADS_INTEGRITY_POLICY)
        #   G - 13 - BRAND 3     -> account_status=2, disable_reason=1, tulis ditolak 4841021
        #   PED - 1190 - BRAND 7 -> account_status=3 (UNSETTLED, belum bayar tagihan)
        #   Masukkan lagi setelah status akun pulih.
        # ── TIDAK TERJANGKAU TOKEN MASNAH (dikeluarkan 25 Okt 2026) ──
        #   G - 14 - BRAND 4                  -> hanya bisa diakses token Sadam (arsip)
        #   PED - 1191 - BRAND 8              -> hanya bisa diakses token Sadam (arsip)
        #   Owner W                      -> hanya bisa diakses token Sadam (arsip)
        #   ADD ACC OWNER_W 3                    -> hanya bisa diakses token Sadam (arsip)
        #   LK - ADD ACC - MS 01               -> hanya bisa diakses token Sadam (arsip)
        #   ADD ACC OWNER_W 02                   -> hanya bisa diakses token Sadam (arsip)
        #   ADD ACC OWNER_W 01                   -> hanya bisa diakses token Sadam (arsip)
        #   AB - 1080 - ACCOUNT_X 3             -> hanya bisa diakses token Sadam (arsip)
        #   AB - 1081 - ACCOUNT_X 4             -> hanya bisa diakses token Sadam (arsip)
        #   AB - 1052 - BRAND - ACCOUNT_X 2    -> hanya bisa diakses token Sadam (arsip)
        #   ACCOUNT_Z - Owner Z - 2       -> hanya bisa diakses token Sadam (arsip)
        #   ACCOUNT_Z - Owner Z - 3       -> hanya bisa diakses token Sadam (arsip)
        #   ACCOUNT_Z - Owner Z - 4       -> hanya bisa diakses token Sadam (arsip)
        #   ACCOUNT_Z - Owner Z - 5       -> hanya bisa diakses token Sadam (arsip)
        #   Kembalikan ke list setelah token Sadam dipasang ke pool autopilot.
        accounts = [f"act_{a}" for a in [
            "ACCOUNT_01", "ACCOUNT_02", "ACCOUNT_03",
            "ACCOUNT_04", "ACCOUNT_05",
            "ACCOUNT_06", "ACCOUNT_07",
            "ACCOUNT_08", "ACCOUNT_09", 
            "ACCOUNT_10", "ACCOUNT_11",
            "ACCOUNT_12", "ACCOUNT_13",
            "ACCOUNT_14", "ACCOUNT_15", "ACCOUNT_16",
            "ACCOUNT_17", 



 # AB - 1080 - ACCOUNT_X 3 (akun baru 21 Sep)
 # AB - 1081 - ACCOUNT_X 4 (akun baru 21 Sep)
            "ACCOUNT_18",    # AB - 1139 - ACCOUNT_X 1 (nyusul 21 Sep)
 # AB - 1052 - ACCOUNT_X 2 (nyusul 21 Sep)
 # ACCOUNT_Z - Owner Z - 2 (masuk 03 Okt 2026)
 # ACCOUNT_Z - Owner Z - 3 (masuk 03 Okt 2026)
 # ACCOUNT_Z - Owner Z - 4 (masuk 03 Okt 2026)
                # ACCOUNT_Z - Owner Z - 5 (masuk 03 Okt 2026)
        ]]
    
    all_decisions = []
    skipped_rate_limit = 0
    
    for i, act_id in enumerate(accounts):
        try:
            decs = process_account(act_id, token, state, dry_run, now)
            if decs is not None:
                all_decisions.extend(decs)
            else:
                skipped_rate_limit += 1
        except Exception as e:
            print(f"  ⚠️ Error processing {act_id}: {e}", file=sys.stderr)
            pass  # continue to next account
        
        time.sleep(1.0)
    
    # ── SUMMARY ──
    if not all_decisions:
        # No decisions = silent exit, no Telegram notification
        state["last_run"] = now.isoformat()
        state["total_decisions"] = state.get("total_decisions", 0)
        state["stats"] = {"last_total": 0, "rate_limited": skipped_rate_limit, "accounts_processed": len(accounts) - skipped_rate_limit}
        save_state(state)
        return
    
    print(f"\n{'═'*60}")
    print(f"📋 AUTO-PILOT — {len(all_decisions)} tindakan")
    print(f"{'═'*60}")
    
    counts = {}
    for d in all_decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1
    
    for act in ["PAUSE", "REACTIVATE", "SCALE_UP", "SCALE_DOWN"]:
        n = counts.get(act, 0)
        if n > 0:
            e = {"PAUSE": "🔴", "REACTIVATE": "🟢", "SCALE_UP": "🚀", "SCALE_DOWN": "⬇️"}[act]
            print(f"  {e} {act}: {n}")
    
    if skipped_rate_limit:
        print(f"\n  ⛔ {skipped_rate_limit} akun skip (rate limit)")
    
    print(f"\n  🟢 LIVE · {now.strftime('%H:%M WIB')}")
    
    state["last_run"] = now.isoformat()
    state["total_decisions"] = state.get("total_decisions", 0) + len(all_decisions)
    state["stats"] = {
        "last_total": len(all_decisions),
        "rate_limited": skipped_rate_limit,
        "accounts_processed": len(accounts) - skipped_rate_limit
    }
    save_state(state)
    print(f"  💾 State saved")

if __name__ == "__main__":
    main()
