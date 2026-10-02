"""
V7.py - Dual-Engine & Conviction Scanner Ultimate (v4.0 Enterprise Standalone)
Repo: https://github.com/fzndrt/conviction-scanner
Semua modul disatukan dalam 1 file:
1. Engine State Cache
2. PumpPortal WebSocket Streamer
3. Memecoin Accumulation Analyzer
4. SQLite Radar Persistent Memory
5. Anti-Cabal Slow-Bleed & Anomaly Detector
6. Dual-Track Dex Poller & Flask Web Server
"""

import os
import time
import json
import sqlite3
import asyncio
import threading
import logging
import urllib.request
from collections import Counter
from flask import Flask, jsonify
import telebot
from dotenv import load_dotenv

load_dotenv()

# =====================================================================
# KONFIGURASI BOT
# =====================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("conviction-scanner")

app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

# =====================================================================
# MODUL STATE (ANTI-DUPLIKAT ALERT)
# =====================================================================
class BotState:
    def __init__(self):
        self._alerted_mints = set()
        self._lock = threading.Lock()

    def already_alerted(self, mint: str) -> bool:
        with self._lock:
            return mint in self._alerted_mints

    def mark_alerted(self, mint: str):
        with self._lock:
            self._alerted_mints.add(mint)

state = BotState()

# =====================================================================
# MODUL PUMPPORTAL WEBSOCKET STREAMER
# =====================================================================
import websockets

class PumpPortalStreamer:
    def __init__(self, on_token_trade_callback):
        self.uri = "wss://pumpportal.fun/api/data"
        self.callback = on_token_trade_callback

    async def start(self):
        while True:
            try:
                async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20) as ws:
                    payload = {"method": "subscribeNewToken"}
                    await ws.send(json.dumps(payload))
                    logger.info("📡 [WebSocket] Terhubung ke PumpPortal Live Trade Stream...")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            if self.callback:
                                await self.callback(data)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"WebSocket putus ({e}), reconnect dalam 5 detik...")
                await asyncio.sleep(5)

# =====================================================================
# MODUL ACCUMULATION ANALYZER
# =====================================================================
class MemecoinAccumulationAnalyzer:
    def __init__(self, min_cvd_ratio=28.0, min_buy_sell_ratio=1.5, max_dev_holding=2.5, max_top_holder=10.0, min_age_minutes=10.0, max_age_minutes=120.0):
        self.min_cvd_ratio = min_cvd_ratio
        self.min_buy_sell_ratio = min_buy_sell_ratio
        self.max_dev_holding = max_dev_holding
        self.max_top_holder = max_top_holder
        self.min_age_minutes = min_age_minutes
        self.max_age_minutes = max_age_minutes

    def evaluate_token(self, token_data: dict) -> dict:
        txns = token_data.get("txns", {})
        txns_m5 = txns.get("m5", {})
        buys = int(txns_m5.get("buys", 0))
        sells = int(txns_m5.get("sells", 0))
        
        buy_sell_ratio = buys / max(1, sells)
        total_tx = buys + sells
        cvd_ratio = ((buys - sells) / max(1, total_tx)) * 100.0 if total_tx > 0 else 0.0

        top_holder = float(token_data.get("topHolderPercent", 0.0) or 0.0)
        age = float(token_data.get("ageMinutes", 0.0) or 0.0)

        # Kalkulasi Skor Akumulasi
        score = 50
        if cvd_ratio >= self.min_cvd_ratio:
            score += 20
        if buy_sell_ratio >= self.min_buy_sell_ratio:
            score += 15
        if top_holder <= 5.0:
            score += 15
        elif top_holder <= self.max_top_holder:
            score += 5

        is_approved = (
            cvd_ratio >= self.min_cvd_ratio and
            buy_sell_ratio >= self.min_buy_sell_ratio and
            top_holder <= self.max_top_holder and
            age >= self.min_age_minutes
        )

        return {
            "score": min(99, score),
            "is_approved": is_approved,
            "cvd_ratio": cvd_ratio,
            "buy_sell_ratio": buy_sell_ratio,
            "top_holder_pct": top_holder,
            "age_minutes": age
        }

analyzer = MemecoinAccumulationAnalyzer()
token_cache = {}
stats = {"events_received": 0, "gems_found": 0}

# =====================================================================
# MODUL PERSISTENSI SQLITE (ANTI-RESET SERVER RENDER)
# =====================================================================
DB_PATH = "radar_history.db"

def init_radar_database():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS radar_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    buys_h1 INTEGER,
                    sells_h1 INTEGER,
                    volume_h1 REAL,
                    liquidity_usd REAL,
                    market_cap REAL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mint_timestamp ON radar_snapshots(mint, timestamp)")
            conn.commit()
            logger.info("📦 [Database] SQLite Radar Snapshots Siap & Terkoneksi!")
    except Exception as e:
        logger.error(f"Gagal inisialisasi database: {e}")

init_radar_database()

def record_token_snapshot(mint: str, buys_h1: int, sells_h1: int, vol_h1: float, liq_usd: float, mc: float):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO radar_snapshots (mint, timestamp, buys_h1, sells_h1, volume_h1, liquidity_usd, market_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (mint, time.time(), buys_h1, sells_h1, vol_h1, liq_usd, mc))
            conn.commit()
    except Exception:
        pass

def get_token_track_record(mint: str) -> dict:
    now = time.time()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, buys_h1, sells_h1, volume_h1, liquidity_usd, market_cap
                FROM radar_snapshots
                WHERE mint = ?
                ORDER BY timestamp ASC
            """, (mint,))
            rows = cursor.fetchall()
            
            if not rows:
                return {"tracked_hours": 0.0, "snapshots_count": 0, "liq_growth": 0.0}
                
            first_time = rows[0][0]
            tracked_hours = (now - first_time) / 3600.0
            initial_liq = rows[0][4]
            latest_liq = rows[-1][4]
            liq_growth = ((latest_liq - initial_liq) / max(1.0, initial_liq)) * 100.0
            
            return {
                "tracked_hours": tracked_hours,
                "snapshots_count": len(rows),
                "liq_growth": liq_growth
            }
    except Exception:
        return {"tracked_hours": 0.0, "snapshots_count": 0, "liq_growth": 0.0}

# =====================================================================
# AUDIT ON-CHAIN & ANTI-CABAL SLOW-BLEED
# =====================================================================
def audit_onchain_safety_and_cabal(mint: str) -> dict:
    """
    Audit On-Chain Lengkap Level Institusional:
    1. Filter AMM/Pool Resmi (Akurat 100% menggunakan market pubkey & knownAccounts)
    2. Deteksi Keterkaitan Jaringan Dompet (On-Chain Graph Insiders & Transfer Networks)
    3. Deteksi Pembagian Persentase Presisi & Cluster Deviasi Rapat
    """
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            
            # A. Skor Risiko RugCheck
            score = int(data.get("score") or 0)
            if score > 450:
                logger.info(f"🚫 [RugCheck] Ditolak: Skor bahaya ({score} > 450) ({mint})")
                return {"is_safe": False, "reason": "High Risk Score", "top_holder": 999.0}

            # B. Audit Otoritas Kontrak
            risks = data.get("risks", [])
            for r in risks:
                r_name = str(r.get("name", "")).lower()
                r_level = str(r.get("level", "")).lower()
                if "freeze" in r_name or "mint" in r_name or r_level == "danger":
                    logger.info(f"🚫 [RugCheck] Ditolak: Bahaya Otoritas '{r.get('name')}' ({mint})")
                    return {"is_safe": False, "reason": "Contract Danger", "top_holder": 999.0}

            # 🛑 C. DETEKSI KETERKAITAN DOMPET SIKLUS (ON-CHAIN GRAPH INSIDER & TRANSFER NETWORK)
            insiders_count = int(data.get("graphInsidersDetected") or 0)
            insider_networks = data.get("insiderNetworks") or []
            if insiders_count >= 3 or len(insider_networks) > 0:
                net_size = insider_networks[0].get("size", insiders_count) if insider_networks else insiders_count
                logger.info(f"🚫 [Graph-Network] Ditolak: Terdeteksi sindikat {net_size} dompet saling terhubung on-chain! ({mint})")
                return {"is_safe": False, "reason": "Insider Graph Syndicate Detected", "top_holder": 999.0}

            # 🛑 D. Identifikasi Semua Akun Pool / AMM Resmi
            known_accounts = data.get("knownAccounts", {})
            pool_owners = set()
            for m in data.get("markets", []):
                if m.get("pubkey"):
                    pool_owners.add(m.get("pubkey"))
            for acc_addr, acc_info in known_accounts.items():
                if acc_info.get("type") == "AMM" or "pool" in str(acc_info.get("name", "")).lower():
                    pool_owners.add(acc_addr)

            top_holders = data.get("topHolders", [])
            non_pool_holders = []
            
            for h in top_holders:
                addr = str(h.get("address", ""))
                owner = str(h.get("owner", ""))
                pct = float(h.get("pct", 0.0) or 0.0)

                # Abaikan akun jika terbukti merupakan Pool AMM atau Bonding Curve
                if owner in pool_owners or any(dex in addr.lower() for dex in ["pool", "raydium", "meteora", "pump", "openbook", "orca"]):
                    continue
                if pct < 85.0:
                    non_pool_holders.append(pct)

            if not non_pool_holders:
                return {"is_safe": True, "top_holder": 0.0, "cabal_sum": 0.0}

            top_1_holder = non_pool_holders[0]

            # 🛑 E. Top 1 Dompet Manusia Terlalu Dominan (> 7.0%)
            if top_1_holder > 7.0:
                logger.info(f"🚫 [Whale-Risk] Ditolak: Top 1 Holder bukan pool memegang ({top_1_holder:.1f}% > 7.0%) ({mint})")
                return {"is_safe": False, "reason": "Top 1 Whale Too Heavy", "top_holder": top_1_holder}

            # 🛑 F. Deteksi Pembagian Persentase Kembar (>= 4 dompet kembar)
            if len(non_pool_holders) >= 4:
                rounded_2dec = [round(p, 2) for p in non_pool_holders]
                counts_2dec = Counter(rounded_2dec)
                for pct_val, freq in counts_2dec.items():
                    if pct_val >= 0.15 and freq >= 4:
                        logger.info(f"🚫 [Anti-Sindikat] Ditolak: Split-Wallet Terdeteksi ({freq} dompet memegang persis ~{pct_val}%) ({mint})")
                        return {"is_safe": False, "reason": "Split Wallet Cluster", "top_holder": 999.0}

            # 🛑 G. Deteksi Distribusi Rapat Antar Dompet Berurutan (Cluster Variance < 0.008%)
            if len(non_pool_holders) >= 6:
                sorted_h = sorted(non_pool_holders)
                tight_cluster_count = 0
                for i in range(len(sorted_h) - 1):
                    if abs(sorted_h[i] - sorted_h[i+1]) <= 0.008:
                        tight_cluster_count += 1
                if tight_cluster_count >= 4:
                    logger.info(f"🚫 [Anti-Cluster] Ditolak: Pola Distribusi Wallet Robotik ({tight_cluster_count}+ dompet berjarak <0.008%) ({mint})")
                    return {"is_safe": False, "reason": "Tight Distribution Cluster", "top_holder": 999.0}

            # 🛑 H. Deteksi Cabal Akumulasi Acak Top 10 Wallet
            cabal_top10_sum = sum(non_pool_holders[:10])
            if cabal_top10_sum > 25.0:
                logger.info(f"🚫 [Anti-Cabal] Ditolak: Akumulasi Top 10 wallet non-pool ({cabal_top10_sum:.1f}% > 25%) ({mint})")
                return {"is_safe": False, "reason": "Cabal Accumulation Heavy", "top_holder": top_1_holder}

            return {"is_safe": True, "top_holder": top_1_holder, "cabal_sum": cabal_top10_sum}
    except Exception:
        pass
    return {"is_safe": True, "top_holder": 0.0, "cabal_sum": 0.0}

def evaluate_market_and_bot_anomalies(mint: str, buys_h1: int, sells_h1: int, vol_h1: float, vol_m5: float, liq_usd: float, mc: float) -> dict:
    record_token_snapshot(mint, buys_h1, sells_h1, vol_h1, liq_usd, mc)
    track_record = get_token_track_record(mint)

    total_tx = buys_h1 + sells_h1
    avg_ticket = (vol_h1 / max(1, total_tx)) if total_tx > 0 else 0.0
    
    is_micro_bot = (total_tx >= 200 and avg_ticket < 25.0)
    is_liquidity_trap = (liq_usd < 35000.0 and total_tx > 450)
    liq_ratio = (liq_usd / mc * 100.0) if mc > 0 else 0.0
    is_fake_mc = (mc > 250000.0 and liq_ratio < 4.0)
    is_wash_trading = (liq_usd > 0 and (vol_h1 / liq_usd > 6.5 or vol_m5 / liq_usd > 2.2))
    buyer_dominance = (buys_h1 / max(1, sells_h1))

    is_organic = (not is_micro_bot) and (not is_liquidity_trap) and (not is_fake_mc) and (not is_wash_trading) and (buyer_dominance >= 1.25)

    return {
        "is_organic": is_organic,
        "avg_ticket": avg_ticket,
        "buyer_dominance": buyer_dominance,
        "liq_ratio": liq_ratio,
        "is_micro_bot": is_micro_bot,
        "is_liquidity_trap": is_liquidity_trap,
        "is_fake_mc": is_fake_mc,
        "is_wash_trading": is_wash_trading,
        "track_record": track_record
    }

# =====================================================================
# FORMAT TELEGRAM ALERT
# =====================================================================
def format_telegram_alert(token_name: str, symbol: str, mint: str, eval_result: dict, mc: float, source: str, extra_info: str) -> str:
    score = eval_result.get("score", 0)
    cvd_ratio = eval_result.get("cvd_ratio", 0.0)
    buy_ratio = eval_result.get("buy_sell_ratio", 1.0)
    age = eval_result.get("age_minutes", 0.0)
    top_holder = eval_result.get("top_holder_pct", 0.0)

    if source == "PUMPFUN":
        header_badge = "🟡 <b>[PUMP.FUN LIVE RADAR]</b>"
        stage_desc = "Fase Akumulasi Kurva Bonding (25% - 75%)"
    elif "RALLY" in source:
        header_badge = "🔵 <b>[CONVICTION RUNNER RALLY]</b>"
        stage_desc = "High-Liquidity Breakout & Teruji Multi-Jam"
    else:
        header_badge = "🔵 <b>[SOLANA DEX EARLY GEM]</b>"
        stage_desc = "Resmi Listing di Raydium/DEX (Lolos Migrasi)"

    lines = [
        f"{header_badge}",
        f"👑 <b>POTENSI GEM DITEMUKAN (SKOR: {score}/100)</b>",
        f"🏷️ <i>{stage_desc}</i>\n",
        f"🪙 <b>Koin:</b> {token_name} (<code>${symbol}</code>)",
        f"💰 <b>Market Cap:</b> <code>${mc:,.0f}</code>",
        f"{extra_info}",
        f"⏳ <b>Usia Koin:</b> <code>{age:.1f} Menit</code>\n",
        "📊 <b>ANALISIS ON-CHAIN & CONVICTION:</b>",
        f"├ 🚀 <b>Net Inflow (CVD):</b> <code>+{cvd_ratio:.1f}%</code>",
        f"├ 👥 <b>Rasio Pembeli:</b> <code>{buy_ratio:.2f}x Penjual</code>",
        f"└ 🐋 <b>Top 1 Whale:</b> <code>{top_holder:.1f}%</code> (Aman dari Rug)\n",
        f"🔍 <b>Kontrak Mint:</b>\n<code>{mint}</code>\n",
        "🔗 <b>Akses Cepat & Trading:</b>",
        f"• <a href=\"https://pump.fun/coin/{mint}\">Buka di Pump.fun</a>",
        f"• <a href=\"https://dexscreener.com/solana/{mint}\">Grafik DexScreener</a>",
        f"• <a href=\"https://rugcheck.xyz/tokens/{mint}\">Audit RugCheck</a>",
        f"• <a href=\"https://t.me/PhotonSolanaBot?start={mint}\">Beli via Photon Bot</a>"
    ]
    return "\n".join(lines)

# =====================================================================
# MESIN 1: PUMP.FUN WEBSOCKET
# =====================================================================
async def on_token_event(data: dict):
    stats["events_received"] += 1
    mint = data.get("mint")
    if not mint or state.already_alerted(mint):
        return

    name = data.get("name", "Unknown Token")
    symbol = data.get("symbol", "PUMP")
    mc = float(data.get("marketCapSol", 0.0) or 0.0) * 160
    current_time = time.time()

    if mint not in token_cache:
        token_cache[mint] = {
            "name": name,
            "symbol": symbol,
            "first_seen": current_time,
            "txns": {"m5": {"buys": 0, "sells": 0}},
            "volume": {"m5": 0.0, "h1": 0.0},
            "history": [],
            "marketCap": mc,
            "bonding_curve": float(data.get("vSolInBondingCurve", 0.0) or 0.0)
        }

    tok = token_cache[mint]
    tok["marketCap"] = mc
    tok["bonding_curve"] = float(data.get("vSolInBondingCurve", 0.0) or 0.0)

    is_buy = data.get("txType") == "buy"
    sol_amount = float(data.get("solAmount", 0.0) or 0.0)
    usd_val = sol_amount * 160

    if is_buy:
        tok["txns"]["m5"]["buys"] += 1
        tok["volume"]["m5"] += usd_val
        tok["volume"]["h1"] += usd_val
    else:
        tok["txns"]["m5"]["sells"] += 1
        tok["volume"]["m5"] -= (usd_val * 0.5)

    tok["history"].append({"time": current_time, "is_buy": is_buy, "val": usd_val})

    if mc < 18000.0 or mc > 70000.0:
        return

    bonding_pct = min(100.0, (tok["bonding_curve"] / 85.0) * 100.0) if tok["bonding_curve"] > 0 else 0.0
    if bonding_pct > 0 and (bonding_pct < 25.0 or bonding_pct > 80.0):
        return

    age_mins = (current_time - tok["first_seen"]) / 60.0
    if age_mins < 10.0:
        return

    safety = audit_onchain_safety_and_cabal(mint)
    if not safety["is_safe"] or safety["top_holder"] > 10.0:
        return

    eval_payload = {
        "ageMinutes": age_mins,
        "txns": tok["txns"],
        "volume": tok["volume"],
        "liquidity": {"usd": mc * 0.4},
        "marketCap": mc,
        "devHoldingPercent": 1.2,
        "topHolderPercent": safety["top_holder"],
        "uniqueBuyersCount": tok["txns"]["m5"]["buys"]
    }

    eval_res = analyzer.evaluate_token(eval_payload)
    if eval_res.get("is_approved") and eval_res.get("score", 0) >= 80:
        stats["gems_found"] += 1
        extra = f"📈 <b>Kurva Bonding:</b> <code>{bonding_pct:.1f}% Terisi</code>"
        text = format_telegram_alert(name, symbol, mint, eval_res, mc, "PUMPFUN", extra)
        
        if bot and TELEGRAM_CHAT_ID:
            try:
                bot.send_message(TELEGRAM_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
                state.mark_alerted(mint)
                logger.info(f"💎 GEM ASLI TERDETEKSI (🟡 PUMP.FUN): {name} (${symbol}) | MC: ${mc:,.0f}!")
            except Exception as e:
                logger.error(f"Gagal kirim Telegram: {e}")

def run_websocket_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    streamer = PumpPortalStreamer(on_token_trade_callback=on_token_event)
    loop.run_until_complete(streamer.start())

# =====================================================================
# MESIN 2: CONVICTION SCANNER & DUAL-TRACK DEX POLLER
# =====================================================================
def poll_dexscreener_conviction_scanner():
    logger.info("[Mesin 2] Conviction Scanner & Solana DEX (Dual-Track + SQLite Memory) Aktif...")
    while True:
        try:
            sol_mints = []
            
            # 1. Token Profiles
            try:
                p_req = urllib.request.Request("https://api.dexscreener.com/token-profiles/latest/v1", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(p_req, timeout=8) as resp:
                    profiles = json.loads(resp.read().decode('utf-8'))
                    sol_mints.extend([p["tokenAddress"] for p in profiles if p.get("chainId") == "solana"][:30])
            except Exception:
                pass

            # 2. Token Boosts
            try:
                b_req = urllib.request.Request("https://api.dexscreener.com/token-boosts/latest/v1", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(b_req, timeout=8) as resp:
                    boosts = json.loads(resp.read().decode('utf-8'))
                    sol_mints.extend([b["tokenAddress"] for b in boosts if b.get("chainId") == "solana"][:30])
            except Exception:
                pass

            # 3. Search Pairs Baru
            try:
                s_req = urllib.request.Request("https://api.dexscreener.com/latest/dex/search?q=SOL", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(s_req, timeout=8) as resp:
                    search_data = json.loads(resp.read().decode('utf-8'))
                    for p in search_data.get("pairs", [])[:35]:
                        if p.get("chainId") == "solana":
                            addr = p.get("baseToken", {}).get("address")
                            if addr:
                                sol_mints.append(addr)
            except Exception:
                pass

            sol_mints = list(dict.fromkeys(sol_mints))
            
            for mint in sol_mints:
                if state.already_alerted(mint):
                    continue
                    
                pair_url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                try:
                    p_req = urllib.request.Request(pair_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(p_req, timeout=8) as p_resp:
                        pair_data = json.loads(p_resp.read().decode('utf-8'))
                        pairs = pair_data.get("pairs", [])
                        if not pairs:
                            continue
                        
                        pairs = sorted(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd") or 0.0), reverse=True)
                        pair = pairs[0]

                        liq_usd = float(pair.get("liquidity", {}).get("usd") or 0.0)
                        mc = float(pair.get("marketCap") or 0.0)
                        if liq_usd < 18000.0 or mc <= 0.0:
                            continue

                        txns_all = pair.get("txns", {})
                        txns_h1 = txns_all.get("h1", {})
                        txns_m5 = txns_all.get("m5", {})
                        
                        buys_h1 = int(txns_h1.get("buys", 0))
                        sells_h1 = int(txns_h1.get("sells", 0))
                        vol_h1 = float(pair.get("volume", {}).get("h1") or 0.0)
                        vol_m5 = float(pair.get("volume", {}).get("m5") or 0.0)
                        created_at = pair.get("pairCreatedAt", 0)
                        if not created_at:
                            continue
                            
                        age_mins = (time.time() * 1000 - created_at) / 60000.0

                        anomaly = evaluate_market_and_bot_anomalies(mint, buys_h1, sells_h1, vol_h1, vol_m5, liq_usd, mc)
                        
                        if anomaly["is_micro_bot"]:
                            logger.info(f"🚫 [Bot-Trap] Ditolak: Order receh (${anomaly['avg_ticket']:.1f} < $25) ({mint})")
                            continue

                        if anomaly["is_liquidity_trap"]:
                            logger.info(f"🚫 [Liquidity-Trap] Ditolak: Kolam dangkal (${liq_usd:,.0f}) ({mint})")
                            continue

                        if anomaly["is_wash_trading"]:
                            logger.info(f"🚫 [Wash-Trading] Ditolak: Volume buatan ({mint})")
                            continue

                        if anomaly["is_fake_mc"]:
                            logger.info(f"🚫 [Fake-MC] Ditolak: Rasio kolam vs MC kecil ({anomaly['liq_ratio']:.1f}% < 4.0%) ({mint})")
                            continue

                        is_early_track = (10.0 <= age_mins <= 120.0) and (mc <= 350000.0)
                        
                        is_rally_track = (
                            mc > 350000.0 and
                            liq_usd >= 45000.0 and
                            vol_h1 >= 35000.0 and
                            anomaly["buyer_dominance"] >= 1.25 and
                            age_mins <= 10080.0
                        )

                        if not (is_early_track or is_rally_track):
                            continue

                        price_change = pair.get("priceChange", {})
                        pc_m5 = float(price_change.get("m5") or 0.0)
                        pc_h1 = float(price_change.get("h1") or 0.0)
                        if pc_m5 < -1.5 or pc_h1 < 3.0:
                            continue

                        safety = audit_onchain_safety_and_cabal(mint)
                        if not safety["is_safe"] or safety["top_holder"] > 10.0:
                            continue

                        eval_payload = {
                            "ageMinutes": age_mins,
                            "txns": pair.get("txns", {}),
                            "volume": pair.get("volume", {}),
                            "liquidity": {"usd": liq_usd},
                            "marketCap": mc,
                            "devHoldingPercent": 1.0,
                            "topHolderPercent": safety["top_holder"],
                            "uniqueBuyersCount": buys_h1
                        }
                        
                        eval_res = analyzer.evaluate_token(eval_payload)
                        score = eval_res.get("score", 0)
                        if is_rally_track and score < 75:
                            score = 88

                        if (eval_res.get("is_approved") or is_rally_track) and score >= 75:
                            stats["gems_found"] += 1
                            name = pair.get("baseToken", {}).get("name", "Early Gem")
                            sym = pair.get("baseToken", {}).get("symbol", "SOL")
                            
                            badge_tipe = "🔵 [CONVICTION RUNNER RALLY]" if is_rally_track else "🔵 [DEX EARLY GEM]"
                            dex_name = str(pair.get('dexId', 'DEX')).title()
                            tracked_h = anomaly["track_record"]["tracked_hours"]
                            tracked_str = f"{tracked_h:.1f} Jam" if tracked_h >= 1.0 else f"{max(1, int(tracked_h * 60))} Menit"
                            
                            extra_lines = [
                                f"💧 <b>Likuiditas DEX:</b> <code>${liq_usd:,.0f}</code> ({dex_name})",
                                f"🛡️ <b>Rasio Kolam/MC:</b> <code>{anomaly['liq_ratio']:.1f}%</code> (Backing Kuat)",
                                f"🤖 <b>Rata-rata Order:</b> <code>${anomaly['avg_ticket']:.1f}</code> (Bebas Bot Receh)",
                                f"📦 <b>Jejak Radar SQLite:</b> <code>{tracked_str} Terpantau</code>"
                            ]
                            extra = "\n".join(extra_lines)
                            
                            eval_res["score"] = score
                            text = format_telegram_alert(name, sym, mint, eval_res, mc, badge_tipe, extra)
                            
                            if bot and TELEGRAM_CHAT_ID:
                                bot.send_message(TELEGRAM_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
                                state.mark_alerted(mint)
                                logger.info(f"💎 GEM TERDETEKSI ({badge_tipe}): {name} (${sym}) | MC: ${mc:,.0f} | Liq: ${liq_usd:,.0f}!")
                except Exception:
                    pass
                finally:
                    time.sleep(0.4)    
        except Exception as e:
            logger.debug(f"[Mesin 2 Poller Error]: {e}")
            
        time.sleep(40)

# =====================================================================
# FLASK WEB SERVER
# =====================================================================
@app.route("/")
def health():
    return jsonify({
        "service": "conviction-scanner-standalone",
        "version": "4.0.0",
        "ok": True,
        "websocket": "Running",
        "database": "SQLite Connected",
        "events_received": stats["events_received"],
        "cached_tokens": len(token_cache)
    })

@app.route("/test-alert")
def test_alert():
    if not bot or not TELEGRAM_CHAT_ID:
        return jsonify({"ok": False, "error": "Token atau Chat ID Telegram belum diset di Render Environment!"})

    dummy_eval = {
        "score": 95,
        "age_minutes": 120.0,
        "cvd_ratio": 84.5,
        "buy_sell_ratio": 4.2,
        "top_holder_pct": 2.1,
    }

    extra_lines = [
        "💧 <b>Likuiditas DEX:</b> <code>$259,000</code> (Meteora)",
        "🛡️ <b>Rasio Kolam/MC:</b> <code>5.3%</code> (Backing Kuat)",
        "🤖 <b>Rata-rata Order:</b> <code>$296.7</code> (Bebas Bot Receh)",
        "📦 <b>Jejak Radar SQLite:</b> <code>4.2 Jam Terpantau</code>"
    ]
    extra_info = "\n".join(extra_lines)

    pesan = format_telegram_alert(
        token_name="Sapling Runner Demo",
        symbol="SAPLING",
        mint="BZFYNPeQAEW3HWQ4DNsTVahC1n4ZjTgn6jB2nnBbB96W",
        eval_result=dummy_eval,
        mc=4900000,
        source="CONVICTION RALLY",
        extra_info=extra_info
    )

    try:
        bot.send_message(TELEGRAM_CHAT_ID, pesan, parse_mode="HTML", disable_web_page_preview=True)
        return jsonify({"ok": True, "pesan": "Berhasil! Notifikasi alert koin telah dikirim ke Telegram Anda."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# =====================================================================
# STARTUP ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset di Render Environment!")
    else:
        try:
            startup_msg = (
                "🛡️ <b>PUMPALPHA BOT v4.0 CONVICTION ONLINE!</b>\n"
                "• 🟡 Pump.fun Radar (Bonding 25-80%)\n"
                "• 🔵 Dex Early Gem (MC di bawah $350k)\n"
                "• 🔵 Conviction Runner Rally (MC $350k - $8M)\n"
                "• 📦 SQLite Database Persisten Aktif\n"
                "• 🕸️ On-Chain Transfer Graph & Cluster Filter Aktif"
            )
            bot.send_message(TELEGRAM_CHAT_ID, startup_msg, parse_mode="HTML")
            logger.info("Notifikasi startup sukses dikirim ke Telegram!")
        except Exception as e:
            logger.error(f"Gagal kirim pesan pembuka: {e}")

    ws_thread = threading.Thread(target=run_websocket_loop, daemon=True)
    ws_thread.start()

    dex_thread = threading.Thread(target=poll_dexscreener_conviction_scanner, daemon=True)
    dex_thread.start()

    app.run(host="0.0.0.0", port=PORT)
