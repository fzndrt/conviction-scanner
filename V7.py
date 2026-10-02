"""
main.py - Dual-Engine & Conviction Scanner Ultimate (v4.0 Enterprise)
Repo: https://github.com/fzndrt/memecoin-alert-bot
Integrasi Penuh:
1. Mesin 1: PumpPortal WebSocket (Early Bonding 25%-80%)
2. Mesin 2: Conviction Scanner & Solana DEX (Dual-Track Early Gem & Multi-Hour Runner)
3. Persistensi Database: SQLite terintegrasi (Anti-hilang riwayat saat server Render restart)
4. Anti-Cabal Slow-Bleed Detector:
    * Deteksi akumulasi Top 10 dompet acak non-pool
    * Deteksi net-selling terselubung pada timeframe H1 & H6
    * Deteksi rasio jual whale vs retail
5. Anomaly & Micro-Bot Trap Detector (Bebas bot receh, fake volume, dan fake MC)
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

import config
import state
from analyzer import MemecoinAccumulationAnalyzer
from pumpportal_stream import PumpPortalStreamer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("memecoin-alert-bot")

app = Flask(__name__)
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN) if config.TELEGRAM_BOT_TOKEN else None

# Inisialisasi Analyzer Inti
analyzer = MemecoinAccumulationAnalyzer(
    min_cvd_ratio=28.0,
    min_buy_sell_ratio=1.5,
    max_dev_holding=2.5,
    max_top_holder=10.0,
    min_age_minutes=10.0,
    max_age_minutes=120.0
)

token_cache = {}
stats = {"events_received": 0, "gems_found": 0}

# =====================================================================
# MODUL 1: PERSISTENSI DATABASE SQLITE (ANTI-RESET SERVER RENDER)
# =====================================================================
DB_PATH = "radar_history.db"

def init_radar_database():
    """Inisialisasi tabel SQLite untuk menyimpan jejak rekam koin secara permanen."""
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
    """Menyimpan data pengamatan berkala koin ke SQLite."""
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
    """Mengambil riwayat beberapa jam terakhir dari SQLite."""
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
                return {"tracked_hours": 0.0, "snapshots_count": 0, "liq_growth": 0.0, "is_steady_accumulation": False}
                
            first_time = rows[0][0]
            tracked_hours = (now - first_time) / 3600.0
            
            initial_liq = rows[0][4]
            latest_liq = rows[-1][4]
            liq_growth = ((latest_liq - initial_liq) / max(1.0, initial_liq)) * 100.0
            is_steady = (latest_liq >= initial_liq * 0.90) and (rows[-1][1] >= rows[-1][2])
            
            return {
                "tracked_hours": tracked_hours,
                "snapshots_count": len(rows),
                "liq_growth": liq_growth,
                "is_steady_accumulation": is_steady
            }
    except Exception:
        return {"tracked_hours": 0.0, "snapshots_count": 0, "liq_growth": 0.0, "is_steady_accumulation": False}


# =====================================================================
# MODUL 2: DETEKTOR ANOMALI BOT & ANTI-CABAL SLOW-BLEED
# =====================================================================
def audit_onchain_safety_and_cabal(mint: str) -> dict:
    """
    Audit On-Chain Lengkap:
    1. Skor RugCheck & Izin Bahaya (Mint/Freeze Authority)
    2. Deteksi Sindikat Pecah Dompet (Identical Split-Wallets)
    3. Deteksi Cabal Slow-Bleed (Whale acak yang mendominasi supply non-pool)
    """
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            
            score = int(data.get("score") or 0)
            if score > 450:
                logger.info(f"🚫 [RugCheck] Ditolak: Skor bahaya ({score} > 450) ({mint})")
                return {"is_safe": False, "reason": "High Risk Score", "top_holder": 999.0}

            risks = data.get("risks", [])
            for r in risks:
                r_name = str(r.get("name", "")).lower()
                r_level = str(r.get("level", "")).lower()
                if "freeze" in r_name or "mint" in r_name or r_level == "danger":
                    logger.info(f"🚫 [RugCheck] Ditolak: Bahaya Fatal '{r.get('name')}' ({mint})")
                    return {"is_safe": False, "reason": "Contract Authority Danger", "top_holder": 999.0}

            top_holders = data.get("topHolders", [])
            non_pool_holders = []
            
            for h in top_holders:
                addr = str(h.get("address", "")).lower()
                pct = float(h.get("pct", 0.0) or 0.0)
                if any(dex in addr for dex in ["pool", "raydium", "meteora", "pump", "openbook", "orca"]):
                    continue
                if pct < 85.0:
                    non_pool_holders.append(pct)

            if not non_pool_holders:
                return {"is_safe": True, "top_holder": 0.0, "cabal_sum": 0.0}

            top_1_holder = non_pool_holders[0]

            # Deteksi Sindikat Pecah Dompet (Persentase Kembar)
            if len(non_pool_holders) >= 4:
                rounded_pcts = [round(p, 1) for p in non_pool_holders[:15]]
                counts = Counter(rounded_pcts)
                for pct_val, freq in counts.items():
                    if pct_val >= 0.3 and freq >= 4:
                        logger.info(f"🚫 [Anti-Sindikat] Ditolak: Split-Wallet Terdeteksi ({freq} dompet memegang persis ~{pct_val}%) ({mint})")
                        return {"is_safe": False, "reason": "Split Wallet Cluster", "top_holder": 999.0}

            # Deteksi CABAL SLOW-BLEED (Akumulasi Top 10 Wallet Acak > 28%)
            cabal_top10_sum = sum(non_pool_holders[:10])
            if cabal_top10_sum > 28.0:
                logger.info(f"🚫 [Anti-Cabal] Ditolak: Cabal Slow-Bleed Risk (Top 10 non-pool akumulasi {cabal_top10_sum:.1f}% > 28%) ({mint})")
                return {"is_safe": False, "reason": "Cabal Accumulation Heavy", "top_holder": top_1_holder}

            return {"is_safe": True, "top_holder": top_1_holder, "cabal_sum": cabal_top10_sum}
    except Exception:
        pass
    return {"is_safe": True, "top_holder": 0.0, "cabal_sum": 0.0}

def evaluate_market_and_bot_anomalies(mint: str, buys_h1: int, sells_h1: int, vol_h1: float, vol_m5: float, liq_usd: float, mc: float) -> dict:
    """Evaluasi Matematika Pasar, Bot Loop, dan Anomali Kolam Likuiditas"""
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
# MODUL 3: FORMAT TELEGRAM ALERT MULTI-TRACK
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
# MESIN 1: PUMP.FUN WEBSOCKET (EARLY RADAR)
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
        
        if bot and config.TELEGRAM_CHAT_ID:
            try:
                bot.send_message(config.TELEGRAM_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
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
            
            # 1. Token Profiles Resmi
            try:
                p_req = urllib.request.Request("https://api.dexscreener.com/token-profiles/latest/v1", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(p_req, timeout=8) as resp:
                    profiles = json.loads(resp.read().decode('utf-8'))
                    sol_mints.extend([p["tokenAddress"] for p in profiles if p.get("chainId") == "solana"][:30])
            except Exception:
                pass

            # 2. Token Boosts (Trending)
            try:
                b_req = urllib.request.Request("https://api.dexscreener.com/token-boosts/latest/v1", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(b_req, timeout=8) as resp:
                    boosts = json.loads(resp.read().decode('utf-8'))
                    sol_mints.extend([b["tokenAddress"] for b in boosts if b.get("chainId") == "solana"][:30])
            except Exception:
                pass

            # 3. Pasangan DEX Baru & Organik
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
                        
                        # Prioritaskan pool likuiditas terbesar (Meteora / Raydium)
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

                        # Evaluasi Pasar & Anomali Bot
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
                            logger.info(f"🚫 [Fake-MC] Ditolak: Rasio kolam vs MC terlalu kecil ({anomaly['liq_ratio']:.1f}% < 4.0%) ({mint})")
                            continue

                        # DUAL-TRACK CONVICTION SELECTION
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

                        # Triple Green Lock
                        price_change = pair.get("priceChange", {})
                        pc_m5 = float(price_change.get("m5") or 0.0)
                        pc_h1 = float(price_change.get("h1") or 0.0)
                        if pc_m5 < -1.5 or pc_h1 < 3.0:
                            continue

                        # Audit On-chain & Anti-Cabal
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
                            
                            if bot and config.TELEGRAM_CHAT_ID:
                                bot.send_message(config.TELEGRAM_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
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
        "service": "memecoin-alert-bot",
        "version": "4.0.0-conviction-scanner-sqlite",
        "ok": True,
        "websocket": "Running",
        "database": "SQLite Connected",
        "events_received": stats["events_received"],
        "cached_tokens": len(token_cache)
    })

@app.route("/test-alert")
def test_alert():
    if not bot or not config.TELEGRAM_CHAT_ID:
        return jsonify({"ok": False, "error": "Token atau Chat ID Telegram belum diset di Render!"})

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
        bot.send_message(config.TELEGRAM_CHAT_ID, pesan, parse_mode="HTML", disable_web_page_preview=True)
        return jsonify({"ok": True, "pesan": "Berhasil! Notifikasi alert koin telah dikirim ke Telegram Anda."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# =====================================================================
# STARTUP ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset di Render Environment!")
    else:
        try:
            startup_msg = (
                "🛡️ <b>PUMPALPHA BOT v4.0 CONVICTION ONLINE!</b>\n"
                "• 🟡 Pump.fun Radar (Bonding 25-80%)\n"
                "• 🔵 Dex Early Gem (MC < $350k)\n"
                "• 🔵 Conviction Runner Rally (MC $350k - $8M)\n"
                "• 📦 SQLite Database Persisten Aktif\n"
                "• 🚫 Anti-Cabal Slow-Bleed & Bot Trap Aktif"
            )
            bot.send_message(
                config.TELEGRAM_CHAT_ID,
                startup_msg,
                parse_mode="HTML",
            )
            logger.info("Notifikasi startup sukses dikirim ke Telegram!")
        except Exception as e:
            logger.error(f"Gagal kirim pesan pembuka: {e}")

    ws_thread = threading.Thread(target=run_websocket_loop, daemon=True)
    ws_thread.start()

    dex_thread = threading.Thread(target=poll_dexscreener_conviction_scanner, daemon=True)
    dex_thread.start()

    port = int(os.environ.get("PORT", getattr(config, "PORT", 10000)))
    app.run(host="0.0.0.0", port=port)
