"""
================================================================
CONVICTION SCANNER v7 — Final Render Edition
================================================================
"""

# ================================================================
# BAGIAN 1: KONFIGURASI (isi di Render → Environment)
# ================================================================
import os

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

CABALSPY_API_KEY = os.environ.get("CABALSPY_API_KEY", "")
ADANOS_API_KEY = os.environ.get("ADANOS_API_KEY", "")
SANTIMENT_API_KEY = os.environ.get("SANTIMENT_API_KEY", "")
VYBE_API_KEY = os.environ.get("VYBE_API_KEY", "")
MOBULA_API_KEY = os.environ.get("MOBULA_API_KEY", "")
MADEONSOL_API_KEY = os.environ.get("MADEONSOL_API_KEY", "")
TNT_RISK_API_KEY = os.environ.get("TNT_RISK_API_KEY", "")
GOPLUS_API_KEY = os.environ.get("GOPLUS_API_KEY", "")
CIELO_API_KEY = os.environ.get("CIELO_API_KEY", "")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

MIN_LIQ_DISCOVERY = float(os.environ.get("MIN_LIQ_DISCOVERY", "8000"))
MIN_LIQ_VALIDATION = float(os.environ.get("MIN_LIQ_VALIDATION", "25000"))
MIN_VOL_1H = float(os.environ.get("MIN_VOL_1H", "15000"))
MAX_VOL_LIQ_RATIO = float(os.environ.get("MAX_VOL_LIQ_RATIO", "6.0"))
MIN_CONVICTION_ALERT = float(os.environ.get("MIN_CONVICTION_ALERT", "50"))

SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "30"))
DISCOVERY_INTERVAL = int(os.environ.get("DISCOVERY_INTERVAL", "120"))
MAX_TRACKED = int(os.environ.get("MAX_TRACKED", "100"))
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "480"))

DB_FILE = os.environ.get("DB_FILE", "/tmp/conviction_v7.db")

# ================================================================
# BAGIAN 2: IMPORT
# ================================================================
import time
import json
import sqlite3
import threading
import traceback
import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict

from flask import Flask, request, jsonify

from resilience import (
    safe_call, resilient_loop, TimeBudget,
    init_limiters, init_caches, LIMITERS, CACHES,
    register_watch, start_watchdog, WATCHED_THREADS, log,
)

# ================================================================
# BAGIAN 3: CEK KONFIGURASI
# ================================================================
def mask(v):
    if not v: return "(kosong)"
    if len(v) <= 8: return "***"
    return v[:4] + "..." + v[-4:]

log.info("=" * 65)
log.info("STATUS KONFIGURASI")
log.info("=" * 65)
log.info(f"  WAJIB:")
log.info(f"    TG_BOT_TOKEN    : {mask(TG_BOT_TOKEN)}")
log.info(f"    TG_CHAT_ID      : {TG_CHAT_ID or '(kosong)'}")
log.info(f"  OPSIONAL:")
log.info(f"    CABALSPY        : {mask(CABALSPY_API_KEY)}")
log.info(f"    ADANOS          : {mask(ADANOS_API_KEY)}")
log.info(f"    SANTIMENT       : {mask(SANTIMENT_API_KEY)}")
log.info(f"    VYBE            : {mask(VYBE_API_KEY)}")
log.info(f"    MOBULA          : {mask(MOBULA_API_KEY)}")
log.info(f"    MADEONSOL       : {mask(MADEONSOL_API_KEY)}")
log.info(f"    TNT_RISK        : {mask(TNT_RISK_API_KEY)}")
log.info(f"    GOPLUS          : {mask(GOPLUS_API_KEY)}")
log.info(f"    CIELO           : {mask(CIELO_API_KEY)}")
log.info("=" * 65)

if not TG_BOT_TOKEN or not TG_CHAT_ID:
    log.warning("TG_BOT_TOKEN atau TG_CHAT_ID KOSONG!")
    log.warning("Bot tidak bisa kirim alert.")

# ================================================================
# BAGIAN 4: ENDPOINT
# ================================================================
GECKO_PUBLIC = "https://api.geckoterminal.com/api/v2"  # fallback (tidak dipakai)
DEXSCREENER_API = "https://api.dexscreener.com"
DEXSCREENER_CHAIN = "solana"
RUGCHECK_API = "https://api.rugcheck.xyz/v1"
TNT_RISK_API = "https://www.tnt-audit.com/api/v1"
MADEONSOL_API = "https://api.madeonsol.com/v1"
CABALSPY_API = "https://api.cabalspy.xyz/v1"
VYBE_API = "https://api.vybenetwork.com/v1"
MOBULA_API = "https://api.mobula.io/api/1"
ADANOS_API = "https://api.adanos.org/v1"
SANTIMENT_API = "https://api.santiment.net/graphql"
GOPLUS_API = "https://api.gopluslabs.io/api/v1"
BINANCE_WEB3_API = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money"
FREE_CRYPTO_NEWS = "https://fcn.dev/api"
NETWORK = "solana"

LIMITER_CONFIG = {
    "dexscreener":           {"per_minute": 200, "per_hour": 5000},
    "dexscreener_profiles":  {"per_minute": 50, "per_hour": 500},
    "dexscreener_pairs":     {"per_minute": 150, "per_hour": 3000},
    "gecko":                 {"per_minute": 8, "per_hour": 200},  # fallback saja
    "rugcheck":              {"per_minute": 8, "per_hour": 400},
    "goplus":                {"per_minute": 20, "per_hour": 500},
    "tnt":                   {"per_minute": 1, "per_hour": 10},
    "madeonsol":             {"per_minute": 8, "per_hour": 150},
    "binance":               {"per_minute": 20, "per_hour": 500},
    "cabalspy":              {"per_minute": 4, "per_hour": 200},
    "vybe":                  {"per_minute": 20, "per_hour": 500},
    "mobula":                {"per_minute": 20, "per_hour": 500},
    "adanos":                {"per_minute": 80, "per_hour": 500, "per_month": 230},
    "santiment":             {"per_minute": 90, "per_hour": 450, "per_month": 900},
    "fcn":                   {"per_minute": 30, "per_hour": 500},
}

CACHE_CONFIG = {
    "security":  {"default_ttl": 1800, "max_size": 2000},
    "market":    {"default_ttl": 25, "max_size": 5000},
    "whale":     {"default_ttl": 120, "max_size": 2000},
    "narrative": {"default_ttl": 600, "max_size": 1000},
    "insider":   {"default_ttl": 900, "max_size": 1000},
}

# ================================================================
# BAGIAN 5: DATA MODEL
# ================================================================
@dataclass
class TokenSnapshot:
    token: str
    name: str = "Unknown"
    symbol: str = ""
    pool_id: str = ""
    price: float = 0.0
    liquidity: float = 0.0
    volume_1h: float = 0.0
    buys_1h: int = 0
    sells_1h: int = 0
    buyers_1h: int = 0
    sellers_1h: int = 0
    price_change_1h: float = 0.0
    price_change_5m: float = 0.0
    txns_1h: int = 0
    honeypot: bool = False
    mint_authority: bool = True
    freeze_authority: bool = True
    lp_burned: bool = False
    rugcheck_score: int = 100
    insider_clusters: int = 0
    same_first_funder: bool = False
    sniper_share: float = 0.0
    whale_count: int = 0
    top10_holder_pct: float = 0.0
    sm_direction: str = "neutral"
    social_volume_change: float = 0.0
    sentiment_score: float = 0.0
    unique_authors: int = 0
    buzz_score: float = 0.0
    narrative_stage: str = "unknown"
    first_seen: int = 0
    last_updated: int = 0

# ================================================================
# BAGIAN 6: DATABASE
# ================================================================
db_lock = threading.Lock()

def init_db():
    if os.path.dirname(DB_FILE):
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY, name TEXT, pool_id TEXT,
            snapshot_json TEXT, conviction REAL,
            alert_sent INTEGER DEFAULT 0,
            first_seen INTEGER, last_updated INTEGER
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT, name TEXT, conviction REAL,
            snapshot_json TEXT, sent_at INTEGER
        )""")
        conn.commit()
        conn.close()
    log.info(f"DB initialized: {DB_FILE}")

def upsert_token(snap, conviction):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("""
        INSERT INTO tokens (token, name, pool_id, snapshot_json, conviction, first_seen, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
            snapshot_json=excluded.snapshot_json,
            conviction=excluded.conviction,
            last_updated=excluded.last_updated
        """, (snap.token, snap.name, snap.pool_id, json.dumps(asdict(snap)),
              conviction, snap.first_seen or int(time.time()), int(time.time())))
        conn.commit()
        conn.close()

def log_alert(snap, conviction):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("""
        INSERT INTO alerts (token, name, conviction, snapshot_json, sent_at)
        VALUES (?, ?, ?, ?, ?)
        """, (snap.token, snap.name, conviction, json.dumps(asdict(snap)), int(time.time())))
        conn.commit()
        conn.close()

def get_token_row(token):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tokens WHERE token=?", (token,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

# ================================================================
# BAGIAN 7: HTTP HELPER
# ================================================================
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ConvictionScanner/7.0)",
    "Accept": "application/json",
})
def http_get(url, params=None, headers=None, timeout=10):
    try:
        return session.get(url, params=params, headers=headers, timeout=timeout)
    except Exception:
        return None

def http_post(url, json_data=None, headers=None, timeout=10):
    try:
        return session.post(url, json=json_data, headers=headers, timeout=timeout)
    except Exception:
        return None

def send_telegram(msg):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log.info(f"[TG DISABLED] {msg[:80]}")
        return

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"

    # Percobaan 1: Markdown
    try:
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code == 200:
            return
        # Kalau 400 (Bad Request — biasanya Markdown error), fallback ke plain text
        if r.status_code == 400:
            log.warning(f"[TG] Markdown failed, retry as plain text")
        else:
            log.warning(f"[TG] HTTP {r.status_code}: {r.text[:200]}")
            return
    except Exception as e:
        log.warning(f"[TG] {e}")

    # Percobaan 2: Plain text (tanpa Markdown)
    try:
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": msg,
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code == 200:
            log.info("[TG] Sent as plain text")
        else:
            log.warning(f"[TG] Plain text also failed: {r.status_code}")
    except Exception as e:
        log.warning(f"[TG] Fallback error: {e}")

# ================================================================
# BAGIAN 8: SNIFFER
# ================================================================
def sniffer_rugcheck(token):
    def _call():
        r = http_get(f"{RUGCHECK_API}/tokens/{token}/report", timeout=8)
        if r and r.status_code == 200: return r
        return None
    r = safe_call(_call, default=None, breaker_name="rugcheck", limiter_name="rugcheck",
                  cache=CACHES["security"], cache_key=f"rugcheck:{token}",
                  cache_ttl=1800, retries=1)
    if not r:
        return {"safe": False, "score": 100}
    try:
        risks = r.json().get("risks", [])
        danger = sum(1 for x in risks if str(x.get("level", "")).lower() == "danger")
        if danger == 0: return {"safe": True, "score": 20}
        elif danger == 1: return {"safe": False, "score": 60}
        else: return {"safe": False, "score": 95}
    except Exception:
        return {"safe": False, "score": 100}

def sniffer_goplus(token):
    result = {"honeypot": False, "tax": 0}
    params = {"contract_addresses": token, "chain_id": "501"}
    headers = {"Authorization": GOPLUS_API_KEY} if GOPLUS_API_KEY else {}
    def _call():
        r = http_get(f"{GOPLUS_API}/token_security/501",
                     params=params, headers=headers, timeout=8)
        if r and r.status_code == 200: return r.json().get("result", {})
        return None
    data = safe_call(_call, default=None, breaker_name="goplus", limiter_name="goplus",
                     cache=CACHES["security"], cache_key=f"goplus:{token}",
                     cache_ttl=1800, retries=1)
    if data and token in data:
        info = data[token]
        result["honeypot"] = info.get("is_honeypot", "0") == "1"
        result["tax"] = float(info.get("buy_tax", 0)) + float(info.get("sell_tax", 0))
    return result

def sniffer_tnt_risk(token):
    result = {"insider_clusters": 0, "same_first_funder": False, "sniper_share": 0.0}
    if not TNT_RISK_API_KEY: return result
    def _call():
        r = http_get(f"{TNT_RISK_API}/token/{token}/risk",
                     headers={"X-API-KEY": TNT_RISK_API_KEY}, timeout=15)
        if r and r.status_code == 200: return r.json()
        return None
    data = safe_call(_call, default=None, breaker_name="tnt", limiter_name="tnt",
                     cache=CACHES["insider"], cache_key=f"tnt:{token}",
                     cache_ttl=1800, retries=1)
    if data:
        clusters = data.get("insider_clusters", [])
        result["insider_clusters"] = len(clusters) if isinstance(clusters, list) else int(clusters)
        result["same_first_funder"] = data.get("same_first_funder", False)
        result["sniper_share"] = float(data.get("sniper_share", 0))
    return result

def run_sniffer(snap):
    rc = sniffer_rugcheck(snap.token)
    snap.rugcheck_score = rc["score"]
    snap.lp_burned = rc["safe"]
    gp = sniffer_goplus(snap.token)
    snap.honeypot = gp["honeypot"]
    tnt = sniffer_tnt_risk(snap.token)
    snap.insider_clusters = tnt["insider_clusters"]
    snap.same_first_funder = tnt["same_first_funder"]
    snap.sniper_share = tnt["sniper_share"]
    return snap

# ================================================================
# BAGIAN 9: INSIDER TRACKER
# ================================================================
def fetch_insider_tracker(token):
    result = {"kol_entries": 0, "bundle_held_pct": 0.0,
              "sniper_wallets": 0, "insider_wallets": 0, "bundler_wallets": 0}
    if MADEONSOL_API_KEY:
        def _kol():
            r = http_get(f"{MADEONSOL_API}/token/{token}/kol-entries",
                         headers={"Authorization": f"Bearer {MADEONSOL_API_KEY}"}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_kol, default=None, breaker_name="madeonsol", limiter_name="madeonsol",
                         cache=CACHES["insider"], cache_key=f"mad_kol:{token}",
                         cache_ttl=600, retries=1)
        if data:
            result["kol_entries"] = int(data.get("count", 0))
            result["bundle_held_pct"] = float(data.get("bundle_held_pct", 0))
    if MOBULA_API_KEY:
        def _mob():
            r = http_get(f"{MOBULA_API}/wallet/labels", params={"tokenAddress": token},
                         headers={"Authorization": f"Bearer {MOBULA_API_KEY}"}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_mob, default=None, breaker_name="mobula", limiter_name="mobula",
                         cache=CACHES["insider"], cache_key=f"mob:{token}",
                         cache_ttl=900, retries=1)
        if data:
            for lbl in data.get("labels", []):
                t = lbl.get("label", "").lower()
                if "sniper" in t: result["sniper_wallets"] += 1
                elif "insider" in t: result["insider_wallets"] += 1
                elif "bundler" in t: result["bundler_wallets"] += 1
    return result

# ================================================================
# BAGIAN 10: WHALE FLOW
# ================================================================
def fetch_binance_smart_money(token):
    result = {"sm_count": 0, "sm_direction": "neutral"}
    def _call():
        payload = {"page": 1, "pageSize": 100, "chainId": "CT_501"}
        headers = {"Content-Type": "application/json",
                   "Accept-Encoding": "identity",
                   "User-Agent": "binance-web3/1.1"}
        r = http_post(BINANCE_WEB3_API, json_data=payload, headers=headers, timeout=8)
        if r and r.status_code == 200: return r.json()
        return None
    data = safe_call(_call, default=None, breaker_name="binance", limiter_name="binance",
                     cache=CACHES["whale"], cache_key=f"binance_sm:{token}",
                     cache_ttl=120, retries=1)
    if data:
        try:
            for item in data.get("data", []):
                if item.get("contractAddress", "").lower() == token.lower():
                    result["sm_count"] = int(item.get("smartMoneyCount", 0) or 0)
                    result["sm_direction"] = item.get("direction", "neutral")
                    break
        except Exception:
            pass
    return result

def fetch_whale_flow(token):
    result = {"whale_count": 0, "top10_pct": 0.0, "bundle_detected": False,
              "sm_direction": "neutral"}
    bsm = fetch_binance_smart_money(token)
    result["whale_count"] = bsm["sm_count"]
    result["sm_direction"] = bsm["sm_direction"]

    if CABALSPY_API_KEY:
        def _cabal():
            r = http_get(f"{CABALSPY_API}/token/{token}/bundles",
                         headers={"X-API-KEY": CABALSPY_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_cabal, default=None, breaker_name="cabalspy", limiter_name="cabalspy",
                         cache=CACHES["whale"], cache_key=f"cabal:{token}",
                         cache_ttl=120, retries=1)
        if data:
            result["bundle_detected"] = len(data.get("bundles", [])) > 0

    if VYBE_API_KEY:
        def _vybe():
            r = http_get(f"{VYBE_API}/token/{token}/top-holders", params={"limit": 10},
                         headers={"X-API-KEY": VYBE_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_vybe, default=None, breaker_name="vybe", limiter_name="vybe",
                         cache=CACHES["whale"], cache_key=f"vybe:{token}",
                         cache_ttl=300, retries=1)
        if data:
            holders = data.get("holders", [])
            if holders:
                total = sum(float(h.get("amount", 0)) for h in holders)
                supply = float(data.get("total_supply", 1))
                result["top10_pct"] = (total / max(supply, 1)) * 100
    return result

# ================================================================
# BAGIAN 11: NARRATIVE
# ================================================================
def fetch_fcn_sentiment(symbol):
    def _call():
        r = http_get(f"{FREE_CRYPTO_NEWS}/social/x/sentiment",
                     params={"token": symbol}, timeout=8)
        if r and r.status_code == 200: return r.json()
        return None
    return safe_call(_call, default=None, breaker_name="fcn", limiter_name="fcn",
                     cache=CACHES["narrative"], cache_key=f"fcn:{symbol}",
                     cache_ttl=600, retries=1)

def fetch_narrative(token, symbol=""):
    result = {"social_volume": 0.0, "volume_change": 0.0, "sentiment": 0.0,
              "unique_authors": 0, "buzz_score": 0.0,
              "narrative_stage": "unknown", "narrative_score": 0.0}
    if ADANOS_API_KEY:
        def _adanos():
            r = http_get(f"{ADANOS_API}/asset",
                         params={"ticker": symbol or token, "days": 7},
                         headers={"X-API-KEY": ADANOS_API_KEY}, timeout=8)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_adanos, default=None, breaker_name="adanos", limiter_name="adanos",
                         cache=CACHES["narrative"], cache_key=f"adanos:{symbol or token}",
                         cache_ttl=600, retries=1)
        if data:
            result["buzz_score"] = float(data.get("buzz_score", 0))
            result["sentiment"] = float(data.get("sentiment", 0))
            result["unique_authors"] = int(data.get("unique_authors", 0))

    fcn = fetch_fcn_sentiment(symbol or token)
    if fcn:
        try:
            x_sent = float(fcn.get("sentiment", 0) or 0)
            if result["sentiment"] == 0:
                result["sentiment"] = x_sent
            elif x_sent != 0:
                result["sentiment"] = (result["sentiment"] + x_sent) / 2
        except Exception:
            pass

    if SANTIMENT_API_KEY:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yest = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        query = """
        { getMetric(metric: "social_volume_total") {
            timeseriesData(slug: "%s", from: "%s", to: "%s", interval: "1d") {
              datetime value } } }
        """ % (token.lower(), yest, today)
        def _sant():
            r = http_get(SANTIMENT_API, params={"query": query},
                         headers={"Authorization": f"Bearer {SANTIMENT_API_KEY}"},
                         timeout=12)
            return r.json() if r and r.status_code == 200 else None
        data = safe_call(_sant, default=None, breaker_name="santiment", limiter_name="santiment",
                         cache=CACHES["narrative"], cache_key=f"sant:{token}",
                         cache_ttl=600, retries=1)
        if data:
            ts = data.get("data", {}).get("getMetric", {}).get("timeseriesData", [])
            if len(ts) >= 2:
                prev = ts[-2]["value"]
                if prev > 0:
                    result["volume_change"] = ((ts[-1]["value"] - prev) / prev) * 100

    result["narrative_stage"] = classify_narrative_stage(result)
    result["narrative_score"] = calculate_narrative_score(result)
    return result

def classify_narrative_stage(d):
    vc = d.get("volume_change", 0)
    au = d.get("unique_authors", 0)
    se = d.get("sentiment", 0)
    if vc < 20 and se > 0: return "pre-narrative"
    if vc > 50 and au > 10 and se > 0.3: return "emergence"
    if vc > 150 and au > 50: return "acceleration"
    if vc > 500 and au > 100: return "peak"
    if vc < 0: return "declining"
    return "unknown"

def calculate_narrative_score(d):
    s = 0.0
    vc, se, au = d.get("volume_change", 0), d.get("sentiment", 0), d.get("unique_authors", 0)
    stage = d.get("narrative_stage", "unknown")
    if vc > 200: s += 30
    elif vc > 100: s += 20
    elif vc > 50: s += 10
    if se > 0.7: s += 25
    elif se > 0.4: s += 15
    elif se > 0: s += 5
    elif se < -0.3: s -= 10
    s += min(20, (au / max(d.get("total_mentions", 1), 1)) * 40)
    s += {"pre-narrative": 5, "emergence": 15, "acceleration": 10,
          "peak": 0, "declining": -10}.get(stage, 0)
    s += min(10, d.get("buzz_score", 0) / 10)
    return max(0, min(100, s))

# ================================================================
# BAGIAN 12: EUPHORIA
# ================================================================
def detect_euphoria(snap):
    result = {"euphoria_score": 0.0, "is_healthy": True, "reasons": []}
    if snap.liquidity > 0:
        vlr = snap.volume_1h / snap.liquidity
        if vlr > MAX_VOL_LIQ_RATIO:
            result["euphoria_score"] += 30
            result["reasons"].append(f"Volume/Liq {vlr:.1f}x")
        elif vlr > 3.0:
            result["euphoria_score"] += 15
    total = snap.buys_1h + snap.sells_1h
    if total > 0:
        br = snap.buys_1h / total
        if br > 0.85:
            result["euphoria_score"] += 20
            result["reasons"].append(f"Buy ratio {br:.0%} FOMO")
        elif br < 0.35:
            result["euphoria_score"] += 25
            result["reasons"].append(f"Buy ratio {br:.0%} dump")
    if snap.price_change_5m > 40 and snap.liquidity < 50000:
        result["euphoria_score"] += 25
    if snap.top10_holder_pct > 35:
        result["euphoria_score"] += 20
    if snap.insider_clusters > 3:
        result["euphoria_score"] += 25
    result["euphoria_score"] = min(100, result["euphoria_score"])
    result["is_healthy"] = result["euphoria_score"] < 50
    return result

# ================================================================
# BAGIAN 13: CONVICTION
# ================================================================
def calculate_conviction(snap, insider, whale, narrative, euphoria):
    """
    Hitung conviction score dengan breakdown detail per layer.
    Return: (total_score, breakdown_dict)
    """
    breakdown = {
        "security":    {"score": 0.0, "max": 25, "reasons": []},
        "insider":     {"score": 0.0, "max": 20, "reasons": []},
        "whale":       {"score": 0.0, "max": 15, "reasons": []},
        "narrative":   {"score": 0.0, "max": 20, "reasons": []},
        "smart_money": {"score": 0.0, "max": 10, "reasons": []},
        "momentum":    {"score": 0.0, "max": 5,  "reasons": []},
        "euphoria":    {"score": 0.0, "max": 5,  "reasons": []},
    }

    # --- SECURITY (25) ---
    if snap.honeypot:
        breakdown["security"]["reasons"].append("HONEYPOT terdeteksi — auto-reject")
        return 0.0, breakdown

    if not snap.mint_authority and not snap.freeze_authority:
        breakdown["security"]["score"] += 20
        breakdown["security"]["reasons"].append("Mint & Freeze authority tidak aktif (+20)")
    else:
        if snap.mint_authority:
            breakdown["security"]["reasons"].append("Mint authority aktif (0)")
        if snap.freeze_authority:
            breakdown["security"]["reasons"].append("Freeze authority aktif (0)")

    if snap.lp_burned:
        breakdown["security"]["score"] += 5
        breakdown["security"]["reasons"].append("LP sudah burn (+5)")
    else:
        breakdown["security"]["reasons"].append("LP belum burn (0)")

    # --- INSIDER (20) ---
    ins = 0
    if snap.insider_clusters == 0:
        ins += 10
        breakdown["insider"]["reasons"].append("Tidak ada insider cluster (+10)")
    else:
        breakdown["insider"]["reasons"].append(f"{snap.insider_clusters} insider cluster terdeteksi (0)")

    if not snap.same_first_funder:
        ins += 5
        breakdown["insider"]["reasons"].append("Tidak ada same-first-funder (+5)")
    else:
        breakdown["insider"]["reasons"].append("Ada same-first-funder / sniper (0)")

    if snap.sniper_share < 0.05:
        ins += 3
        breakdown["insider"]["reasons"].append(f"Sniper share rendah ({snap.sniper_share:.2%}) (+3)")
    else:
        breakdown["insider"]["reasons"].append(f"Sniper share tinggi ({snap.sniper_share:.2%}) (0)")

    if insider.get("sniper_wallets", 0) == 0:
        ins += 2
        breakdown["insider"]["reasons"].append("Tidak ada sniper wallet (+2)")
    else:
        breakdown["insider"]["reasons"].append(f"{insider['sniper_wallets']} sniper wallet (0)")

    breakdown["insider"]["score"] = min(20, ins)

    # --- WHALE (15) ---
    wf = 0
    wc = whale.get("whale_count", 0)
    if wc >= 3:
        wf += 8
        breakdown["whale"]["reasons"].append(f"{wc} smart money wallet (+8)")
    elif wc >= 1:
        wf += 4
        breakdown["whale"]["reasons"].append(f"{wc} smart money wallet (+4)")
    else:
        breakdown["whale"]["reasons"].append("Tidak ada smart money wallet (0)")

    if not whale.get("bundle_detected"):
        wf += 3
        breakdown["whale"]["reasons"].append("Tidak ada bundle terdeteksi (+3)")
    else:
        breakdown["whale"]["reasons"].append("Bundle terdeteksi (0)")

    top10 = whale.get("top10_pct", 0)
    if top10 < 20:
        wf += 4
        breakdown["whale"]["reasons"].append(f"Top10 holder {top10:.1f}% — tersebar (+4)")
    elif top10 > 40:
        wf -= 6
        breakdown["whale"]["reasons"].append(f"Top10 holder {top10:.1f}% — terkonsentrasi (-6)")
    else:
        breakdown["whale"]["reasons"].append(f"Top10 holder {top10:.1f}% (0)")

    breakdown["whale"]["score"] = max(0, min(15, wf))

    # --- NARRATIVE (20) ---
    nar = narrative.get("narrative_score", 0)
    st = narrative.get("narrative_stage", "unknown")
    if st == "emergence":
        nar = min(100, nar * 1.3)
        breakdown["narrative"]["reasons"].append(f"Stage emergence — boost 1.3× ({nar:.0f}/100)")
    elif st == "peak":
        nar *= 0.3
        breakdown["narrative"]["reasons"].append(f"Stage peak — potong 0.3× ({nar:.0f}/100)")
    elif st == "declining":
        nar = 0
        breakdown["narrative"]["reasons"].append("Stage declining — 0")
    elif st == "acceleration":
        breakdown["narrative"]["reasons"].append(f"Stage acceleration ({nar:.0f}/100)")
    elif st == "pre-narrative":
        breakdown["narrative"]["reasons"].append(f"Stage pre-narrative ({nar:.0f}/100)")
    else:
        breakdown["narrative"]["reasons"].append(f"Stage unknown ({nar:.0f}/100)")

    sent = narrative.get("sentiment", 0)
    if sent > 0:
        breakdown["narrative"]["reasons"].append(f"Sentiment +{sent:.2f}")
    elif sent < 0:
        breakdown["narrative"]["reasons"].append(f"Sentiment {sent:.2f}")

    breakdown["narrative"]["score"] = (nar / 100) * 20

    # --- SMART MONEY (10) ---
    sm = 0
    if wc >= 2:
        sm += 5
        breakdown["smart_money"]["reasons"].append(f"{wc} wallet smart money aktif (+5)")
    else:
        breakdown["smart_money"]["reasons"].append("Kurang dari 2 wallet smart money (0)")

    if top10 < 25:
        sm += 5
        breakdown["smart_money"]["reasons"].append(f"Top10 {top10:.1f}% — distribusi sehat (+5)")
    else:
        breakdown["smart_money"]["reasons"].append(f"Top10 {top10:.1f}% (0)")

    breakdown["smart_money"]["score"] = min(10, sm)

    # --- MOMENTUM (5) ---
    mom = 0
    if snap.liquidity >= MIN_LIQ_VALIDATION:
        mom += 2
        breakdown["momentum"]["reasons"].append(f"Likuiditas ${snap.liquidity:,.0f} >= ${MIN_LIQ_VALIDATION:,.0f} (+2)")
    else:
        breakdown["momentum"]["reasons"].append(f"Likuiditas ${snap.liquidity:,.0f} < ${MIN_LIQ_VALIDATION:,.0f} (0)")

    if snap.volume_1h >= MIN_VOL_1H:
        mom += 2
        breakdown["momentum"]["reasons"].append(f"Volume 1H ${snap.volume_1h:,.0f} >= ${MIN_VOL_1H:,.0f} (+2)")
    else:
        breakdown["momentum"]["reasons"].append(f"Volume 1H ${snap.volume_1h:,.0f} < ${MIN_VOL_1H:,.0f} (0)")

    if snap.buyers_1h >= 15:
        mom += 1
        breakdown["momentum"]["reasons"].append(f"{snap.buyers_1h} buyer unik >= 15 (+1)")
    else:
        breakdown["momentum"]["reasons"].append(f"{snap.buyers_1h} buyer unik < 15 (0)")

    breakdown["momentum"]["score"] = min(5, mom)

    # --- EUPHORIA (5, inverse) ---
    euph_score = euphoria.get("euphoria_score", 0)
    euph_pts = max(0, 5 * (1 - euph_score / 100))
    breakdown["euphoria"]["score"] = euph_pts
    if euph_score < 30:
        breakdown["euphoria"]["reasons"].append(f"Euphoria rendah ({euph_score:.0f}/100) — belum FOMO (+{euph_pts:.1f})")
    elif euph_score < 60:
        breakdown["euphoria"]["reasons"].append(f"Euphoria sedang ({euph_score:.0f}/100) (+{euph_pts:.1f})")
    else:
        breakdown["euphoria"]["reasons"].append(f"Euphoria tinggi ({euph_score:.0f}/100) — risiko FOMO (+{euph_pts:.1f})")

    # --- TOTAL ---
    total = sum(v["score"] for v in breakdown.values())
    return round(min(100, max(0, total)), 1), breakdown

# ================================================================
# BAGIAN 14: GECKOTERMINAL
# ================================================================
def fetch_new_pools():
    """
    Discovery dari 4 sumber:
      1. Token profiles terbaru (baru < 24 jam)
      2. Token boosted (trending via boost)
      3. Token trending via search endpoint (bisa berumur hari/minggu)
      4. Top gainers 24h (bisa berumur berapa pun)
    """
    all_pools = []
    seen_pairs = set()

    # ============================================
    # SUMBER 1: Token profiles terbaru
    # ============================================
    def _get_profiles():
        r = http_get(f"{DEXSCREENER_API}/token-profiles/latest/v1", timeout=10)
        return r.json() if r and r.status_code == 200 else None

    profiles = safe_call(_get_profiles, default=None,
                         breaker_name="ds_profiles", limiter_name="dexscreener",
                         cache=CACHES["market"], cache_key="ds_profiles",
                         cache_ttl=60, retries=1)

    new_tokens = []
    if profiles:
        for p in profiles:
            if p.get("chainId") == DEXSCREENER_CHAIN:
                addr = p.get("tokenAddress")
                if addr:
                    new_tokens.append(addr)
    log.info(f"[DISCOVERY-1] {len(new_tokens)} new profile tokens")

    # ============================================
    # SUMBER 2: Token boosted (trending)
    # ============================================
    def _get_boosts():
        r = http_get(f"{DEXSCREENER_API}/token-boosts/top/v1", timeout=10)
        return r.json() if r and r.status_code == 200 else None

    boosts = safe_call(_get_boosts, default=None,
                       breaker_name="ds_boosts", limiter_name="dexscreener",
                       cache=CACHES["market"], cache_key="ds_boosts",
                       cache_ttl=120, retries=1)

    boost_tokens = []
    if boosts:
        for b in boosts:
            if b.get("chainId") == DEXSCREENER_CHAIN:
                addr = b.get("tokenAddress")
                if addr and addr not in new_tokens:
                    boost_tokens.append(addr)
    log.info(f"[DISCOVERY-2] {len(boost_tokens)} boosted tokens")

    # ============================================
    # SUMBER 3: Search trending pairs (volume tinggi)
    # Sumber ini bisa mencakup token berumur hari/minggu
    # ============================================
    def _search_pairs():
        r = http_get(f"{DEXSCREENER_API}/latest/dex/search",
                     params={"q": "SOL"}, timeout=10)
        return r.json() if r and r.status_code == 200 else None

    search_data = safe_call(_search_pairs, default=None,
                            breaker_name="ds_search", limiter_name="dexscreener",
                            cache=CACHES["market"], cache_key="ds_search_sol",
                            cache_ttl=180, retries=1)

    if search_data:
        for pair in search_data.get("pairs", []) or []:
            if pair.get("chainId") != DEXSCREENER_CHAIN:
                continue
            liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            if liq >= MIN_LIQ_DISCOVERY:
                pair_addr = pair.get("pairAddress")
                if pair_addr and pair_addr not in seen_pairs:
                    all_pools.append(pair)
                    seen_pairs.add(pair_addr)
    log.info(f"[DISCOVERY-3] {len(all_pools)} pairs from search")

    # ============================================
    # SUMBER 4: Ambil pairs untuk setiap token (new + boost)
    # ============================================
    all_token_addrs = new_tokens[:30] + boost_tokens[:20]  # total max 50

    for token_addr in all_token_addrs:
        def _get_pairs(addr=token_addr):
            r = http_get(f"{DEXSCREENER_API}/token-pairs/v1/{DEXSCREENER_CHAIN}/{addr}",
                         timeout=8)
            return r.json() if r and r.status_code == 200 else None

        data = safe_call(_get_pairs, default=None,
                         breaker_name="ds_pairs", limiter_name="dexscreener",
                         cache=CACHES["market"], cache_key=f"ds_pairs:{token_addr}",
                         cache_ttl=30, retries=1)

        if data and isinstance(data, list):
            for pair in data:
                pair_addr = pair.get("pairAddress")
                if pair_addr and pair_addr not in seen_pairs:
                    all_pools.append(pair)
                    seen_pairs.add(pair_addr)

    log.info(f"[DISCOVERY] Total {len(all_pools)} unique pairs dari 4 sumber")
    return all_pools

def fetch_pool_live(pair_address):
    """Live pair data via DexScreener. Return single pair dict."""
    def _call():
        r = http_get(f"{DEXSCREENER_API}/latest/dex/pairs/{DEXSCREENER_CHAIN}/{pair_address}",
                     timeout=8)
        if r and r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs", [])
            return pairs[0] if pairs else None
        return None
    return safe_call(_call, default=None, breaker_name="dexscreener",
                     limiter_name="dexscreener", cache=CACHES["market"],
                     cache_key=f"ds_pair:{pair_address}", cache_ttl=25, retries=1)

def parse_pool(pool):
    """
    Parse DexScreener pair response ke TokenSnapshot.
    DexScreener response format berbeda dari GeckoTerminal.
    """
    try:
        # DexScreener: baseToken.address, pairAddress, liquidity.usd, volume.h1, dll
        base_token = pool.get("baseToken", {})
        token = base_token.get("address", "")
        if not token:
            return None

        txns_h1 = pool.get("txns", {}).get("h1", {})
        volume_h1 = float(pool.get("volume", {}).get("h1", 0) or 0)
        price_change = pool.get("priceChange", {})
        liquidity = float(pool.get("liquidity", {}).get("usd", 0) or 0)

        return TokenSnapshot(
            token=token,
            name=base_token.get("name", "Unknown"),
            symbol=base_token.get("symbol", ""),
            pool_id=pool.get("pairAddress", ""),
            price=float(pool.get("priceUsd", 0) or 0),
            liquidity=liquidity,
            volume_1h=volume_h1,
            buys_1h=int(txns_h1.get("buys", 0) or 0),
            sells_1h=int(txns_h1.get("sells", 0) or 0),
            buyers_1h=int(txns_h1.get("buys", 0) or 0),
            sellers_1h=int(txns_h1.get("sells", 0) or 0),
            price_change_1h=float(price_change.get("h1", 0) or 0),
            price_change_5m=float(price_change.get("m5", 0) or 0),
            txns_1h=int(txns_h1.get("buys", 0) or 0) + int(txns_h1.get("sells", 0) or 0),
            first_seen=int(time.time()),
        )
    except Exception as e:
        log.warning(f"[PARSE] error: {e}")
        return None

# ================================================================
# BAGIAN 15: PROCESS TOKEN
# ================================================================
tracked = {}
tracked_lock = threading.Lock()

def process_token(snap):
    budget = TimeBudget(20, f"token:{snap.name[:20]}")
    snap = run_sniffer(snap)
    if snap.honeypot:
        log.info(f"HONEYPOT: {snap.name} ({snap.token})")
        return
    if budget.check("sniffer"): return
    insider = fetch_insider_tracker(snap.token)
    if budget.check("insider"): return
    whale = fetch_whale_flow(snap.token)
    if budget.check("whale"): return
    narrative = fetch_narrative(snap.token, snap.symbol)
    if budget.check("narrative"): return
    euphoria = detect_euphoria(snap)

    # PENTING: unpack tuple (conviction, breakdown)
    conviction, breakdown = calculate_conviction(snap, insider, whale, narrative, euphoria)
    upsert_token(snap, conviction)
    log.info(f"[SCORE] {snap.name}: {conviction}/100")

    row = get_token_row(snap.token)
    if row and row["alert_sent"] == 0 and conviction >= MIN_CONVICTION_ALERT:
        send_alert(snap, conviction, breakdown, insider, whale, narrative, euphoria)
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            conn.execute("UPDATE tokens SET alert_sent=1 WHERE token=?", (snap.token,))
            conn.commit()
            conn.close()

def _progress_bar(score, max_score, width=10):
    """Buat progress bar unicode."""
    filled = int((score / max_score) * width) if max_score > 0 else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _fmt_layer(breakdown, key, label):
    """Format satu layer breakdown."""
    d = breakdown[key]
    score = d["score"]
    maxs = d["max"]
    bar = _progress_bar(score, maxs)
    reasons = "\n".join(f"    • {r}" for r in d["reasons"])
    return f"  {label}: {score:.1f}/{maxs} {bar}\n{reasons}"


def _escape_md(text):
    """Escape karakter Markdown Telegram."""
    for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(ch, f"\\{ch}")
    return text


def send_alert(snap, conviction, breakdown, insider, whale, narrative, euphoria):
    dex_url = f"https://dexscreener.com/solana/{snap.token}"

    # --- Grade & Emoji ---
    if conviction >= 80:
        grade = "SANGAT KUAT"
        emoji = "🔥🔥🔥"
    elif conviction >= 70:
        grade = "KUAT"
        emoji = "🔥🔥"
    elif conviction >= 60:
        grade = "BAGUS"
        emoji = "🔥"
    else:
        grade = "WASPADA"
        emoji = "⚠️"

    # --- Breakdown per layer ---
    breakdown_text = "\n".join([
        _fmt_layer(breakdown, "security",    "🛡️ Security"),
        _fmt_layer(breakdown, "insider",     "🕵️ Insider"),
        _fmt_layer(breakdown, "whale",       "🐳 Whale"),
        _fmt_layer(breakdown, "narrative",   "📰 Narrative"),
        _fmt_layer(breakdown, "smart_money", "🧠 SmartMoney"),
        _fmt_layer(breakdown, "momentum",    "📈 Momentum"),
        _fmt_layer(breakdown, "euphoria",    "🌡️ Euphoria"),
    ])

    # --- Red flags ---
    flags = []
    if snap.mint_authority: flags.append("Mint authority aktif")
    if snap.freeze_authority: flags.append("Freeze authority aktif")
    if not snap.lp_burned: flags.append("LP belum burn")
    if snap.insider_clusters > 3: flags.append(f"{snap.insider_clusters} insider clusters")
    if snap.same_first_funder: flags.append("Same first funder (sniper)")
    if whale.get("bundle_detected"): flags.append("Bundle detected")
    if euphoria["euphoria_score"] > 50:
        flags.append(f"Euphoria tinggi {euphoria['euphoria_score']:.0f}/100")
    if flags:
        flag_text = "\n".join(f"  ⚠️ {f}" for f in flags)
    else:
        flag_text = "  ✅ Tidak ada red flag utama"

    # --- Penjelasan naratif ---
    narrative_explain = f"Skor *{conviction:.1f}/100* masuk kategori *{grade}*. "

    if conviction >= 70:
        narrative_explain += "Token ini punya kombinasi keamanan, akumulasi whale, dan sentimen yang kuat. "
    elif conviction >= 60:
        narrative_explain += "Token ini punya fondasi bagus tapi belum semua layer konfirmasi. "
    else:
        narrative_explain += "Token ini masih early — banyak data belum tersedia. Wajib DYOR lebih dalam. "

    # Tambahkan insight per layer
    if breakdown["whale"]["score"] >= 8:
        narrative_explain += "🐳 Whale terdeteksi akumulasi. "
    if breakdown["narrative"]["score"] >= 10:
        narrative_explain += "📰 Narasi sosial mulai bergerak. "
    if breakdown["security"]["score"] >= 25:
        narrative_explain += "🛡️ Keamanan on-chain bersih. "
    if breakdown["smart_money"]["score"] >= 8:
        narrative_explain += "🧠 Smart money aktif. "
    if breakdown["euphoria"]["score"] >= 4:
        narrative_explain += "🌡️ Belum FOMO — masih early. "

    # --- Susun pesan ---
    msg = (
        f"{emoji} *CONVICTION ALERT* {emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"*{snap.name}* (`{snap.symbol}`)\n"
        f"`{snap.token}`\n\n"
        f"🎯 *SKOR: {conviction:.1f}/100* — {grade}\n\n"
        f"💰 Harga: `${snap.price:.8f}`\n"
        f"💧 Likuiditas: `${snap.liquidity:,.0f}`\n"
        f"📊 Volume 1H: `${snap.volume_1h:,.0f}`\n"
        f"📈 Perubahan 1H: `{snap.price_change_1h:+.1f}%`\n"
        f"👥 Buyer/Seller 1H: `{snap.buyers_1h}/{snap.sellers_1h}`\n\n"
        f"━━━ 📊 *BREAKDOWN SKOR* ━━━\n"
        f"{breakdown_text}\n\n"
        f"━━━ ⚠️ *RED FLAGS* ━━━\n"
        f"{flag_text}\n\n"
        f"━━━ 💡 *PENJELASAN* ━━━\n"
        f"{narrative_explain}\n\n"
        f"🔎 [DEXScreener]({dex_url})"
    )

    # --- Telegram limit: 4096 chars. Potong kalau perlu. ---
    if len(msg) > 4000:
        msg = msg[:3950] + "...\n\n(terpotong karena limit Telegram)"

    send_telegram(msg)
    log_alert(snap, conviction)

# ================================================================
# BAGIAN 16: WORKERS
# ================================================================
def check_resurrect():
    """
    Cek token yang sudah lewat TTL, tapi volume 24h tinggi.
    Kalau ada, tambahkan kembali ke tracked.
    """
    def _search():
        r = http_get(f"{DEXSCREENER_API}/latest/dex/search",
                     params={"q": "SOL"}, timeout=10)
        return r.json() if r and r.status_code == 200 else None

    data = safe_call(_search, default=None,
                     breaker_name="ds_search", limiter_name="dexscreener",
                     cache=CACHES["market"], cache_key="ds_resurrect",
                     cache_ttl=600, retries=1)

    if not data:
        return 0

    resurrected = 0
    for pair in data.get("pairs", []) or []:
        if pair.get("chainId") != DEXSCREENER_CHAIN:
            continue

        token_addr = pair.get("baseToken", {}).get("address")
        pair_addr = pair.get("pairAddress")
        if not token_addr or not pair_addr:
            continue

        with tracked_lock:
            if token_addr in tracked:
                continue

        liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
        price_change_24h = float(pair.get("priceChange", {}).get("h24", 0) or 0)

        if (liq >= MIN_LIQ_VALIDATION and
            vol_24h > liq * 3 and
            price_change_24h > 20):

            with tracked_lock:
                if len(tracked) < MAX_TRACKED:
                    tracked[token_addr] = {
                        "pool_id": pair_addr,
                        "first_seen": int(time.time()),
                        "resurrected": True,
                    }
                    resurrected += 1
                    log.info(f"[RESURRECT] {pair.get('baseToken', {}).get('symbol', '?')} "
                             f"(liq ${liq:,.0f}, vol24h ${vol_24h:,.0f}, "
                             f"+{price_change_24h:.1f}%)")

    return resurrected


@resilient_loop("discovery", DISCOVERY_INTERVAL)
def discovery_loop():
    """Discovery loop — cari token baru dari 4 sumber."""
    try:
        pools = fetch_new_pools()
        count = 0
        for pool in pools:
            snap = parse_pool(pool)
            if not snap or not snap.token:
                continue
            if snap.liquidity < MIN_LIQ_DISCOVERY:
                continue
            with tracked_lock:
                if len(tracked) >= MAX_TRACKED:
                    break
                if snap.token not in tracked:
                    tracked[snap.token] = {
                        "pool_id": snap.pool_id,
                        "first_seen": int(time.time()),
                    }
                    count += 1
        log.info(f"[DISCOVERY] +{count} new | total: {len(tracked)}")

        # Cek token yang bangkit kembali
        try:
            resurrected = check_resurrect()
            if resurrected > 0:
                log.info(f"[DISCOVERY] {resurrected} tokens resurrected")
        except Exception as e:
            log.warning(f"[RESURRECT] error: {e}")

    except Exception as e:
        log.error(f"[DISCOVERY] error: {e}")


@resilient_loop("monitoring", SCAN_INTERVAL)
def monitoring_loop():
    """Monitoring loop — scan token dengan interval adaptif per umur."""
    try:
        with tracked_lock:
            items = list(tracked.items())

        now = int(time.time())
        now_minute = int(now / 60)

        # Cleanup token kadaluarsa
        expired = [t for t, v in items if now - v["first_seen"] > TOKEN_TTL_HOURS * 3600]
        for t in expired:
            with tracked_lock:
                tracked.pop(t, None)
        if expired:
            log.info(f"[CLEANUP] Removed {len(expired)} expired tokens")

        scanned_count = 0
        for token, meta in items:
            age_hours = (now - meta["first_seen"]) / 3600

            # Scan adaptif berdasarkan umur
            if age_hours < 6:
                skip_mod = 1
            elif age_hours < 24:
                skip_mod = 2
            elif age_hours < 72:
                skip_mod = 4
            else:
                skip_mod = 10

            token_index = hash(token) % skip_mod
            if (now_minute % skip_mod) != token_index:
                continue

            pool_data = fetch_pool_live(meta["pool_id"])
            if not pool_data:
                continue
            snap = parse_pool(pool_data)
            if not snap:
                continue
            if snap.liquidity < MIN_LIQ_DISCOVERY:
                continue

            with tracked_lock:
                if token in tracked:
                    tracked[token]["last_updated"] = now

            process_token(snap)
            scanned_count += 1

        log.info(f"[MONITOR] Scanned {scanned_count}/{len(items)} tokens this cycle")

    except Exception as e:
        log.error(f"[MONITOR] error: {e}")


@resilient_loop("memory_guard", 300)
def memory_guard_loop():
    """Bersihkan token lama kalau tracked > MAX_TRACKED."""
    try:
        with tracked_lock:
            if len(tracked) > MAX_TRACKED:
                sorted_items = sorted(tracked.items(), key=lambda x: x[1].get("first_seen", 0))
                excess = len(tracked) - MAX_TRACKED
                for token, _ in sorted_items[:excess]:
                    tracked.pop(token, None)
                log.info(f"Memory guard: removed {excess} tokens")
    except Exception as e:
        log.error(f"[MEMORY_GUARD] error: {e}")


@resilient_loop("cache_cleanup", 600)
def cache_cleanup_loop():
    """Bersihkan cache expired."""
    try:
        for name, cache in CACHES.items():
            n = cache.clear_expired()
            if n:
                log.info(f"Cache {name}: cleared {n} expired")
    except Exception as e:
        log.error(f"[CACHE_CLEANUP] error: {e}")
# ================================================================
# BAGIAN 17: FLASK — dengan route "/"
# ================================================================
app = Flask(__name__)

@app.errorhandler(404)
def handle_404(e):
    return jsonify({
        "status": "not_found",
        "hint": "Gunakan /health atau /test-alert",
    }), 404

@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f"[FLASK ERROR] {e}")
    return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/")
def root():
    with tracked_lock:
        n = len(tracked)
    return jsonify({
        "name": "Conviction Scanner v7",
        "status": "ONLINE",
        "tg_configured": bool(TG_BOT_TOKEN and TG_CHAT_ID),
        "tracked_tokens": n,
        "endpoints": {
            "health": "/health",
            "test_alert": "/test-alert",
        },
    }), 200
@app.route("/debug/tokens")
def debug_tokens():
    """Lihat semua tracked token dan conviction score-nya."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT token, name, conviction, alert_sent FROM tokens ORDER BY conviction DESC LIMIT 50")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    with tracked_lock:
        tracked_count = len(tracked)
    return jsonify({
        "tracked_count": tracked_count,
        "threshold": MIN_CONVICTION_ALERT,
        "tokens": rows,
    }), 200


@app.route("/debug/force-alert")
def force_alert():
    """Force kirim alert untuk test — abaikan threshold."""
    send_telegram(
        "FORCE ALERT TEST\n\n"
        "Kalau kamu lihat pesan ini, Telegram API bekerja.\n"
        f"Threshold saat ini: {MIN_CONVICTION_ALERT}/100\n"
        f"Tracked tokens: {len(tracked)}"
    )
    return jsonify({"status": "sent", "threshold": MIN_CONVICTION_ALERT}), 200
@app.route("/health")
def health():
    with tracked_lock:
        n = len(tracked)
    return jsonify({
        "status": "ONLINE",
        "version": "7.0",
        "tg_configured": bool(TG_BOT_TOKEN and TG_CHAT_ID),
        "tracked_tokens": n,
    }), 200

# ================================================================
# BAGIAN 18: MAIN
# ================================================================
def main():
    init_db()
    init_limiters(LIMITER_CONFIG)
    init_caches(CACHE_CONFIG)
    log.info("Conviction Scanner v7 starting...")

    register_watch("discovery", discovery_loop)
    register_watch("monitoring", monitoring_loop)
    register_watch("memory_guard", memory_guard_loop)
    register_watch("cache_cleanup", cache_cleanup_loop)

    for name, target in [
        ("discovery", discovery_loop),
        ("monitoring", monitoring_loop),
        ("memory_guard", memory_guard_loop),
        ("cache_cleanup", cache_cleanup_loop),
    ]:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        WATCHED_THREADS[name] = t

    start_watchdog()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

if __name__ == "__main__":
    main()
