"""
=============================================================================
CONVICTION SCANNER V7.0 - ULTIMATE COMPREHENSIVE EDITION
- Multi-Feed Discovery Radar (DexScreener + GeckoTerminal Solana)
- Turbo Batching Scanner (30 tokens/batch, 15s scan cycle)
- Smart AMM Pool & LP Locked/Burned Audit (RugCheck V1 + DEX Vaults)
- Triple Green Lock Adaptif (Early Gems $25k s/d Mega Runners $2.5M)
- Anti-Dump & Pure Buyer Dominance Engine
- Flask Keep-Alive Webhook for 24/7 Cloud Hosting
=============================================================================
"""

import os
import sys
import time
import json
import logging
import threading
import urllib.request
from typing import Dict, Any, List, Set, Tuple, Optional

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ConvictionV7")

# Webhook Server
try:
    from flask import Flask, jsonify
    app = Flask(__name__)
except ImportError:
    app = None

# Telegram Bot Setup
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
bot = None

if TG_TOKEN:
    try:
        import telebot
        bot = telebot.TeleBot(TG_TOKEN)
        logger.info("Telegram Bot berhasil diinisialisasi.")
    except Exception as e:
        logger.error(f"Gagal inisialisasi telebot: {e}")

# ============================================================================
# 👑 KONFIGURASI "50x RUNNER / BUY & HOLD" (ULTRA CONVICTION FILTER)
# ============================================================================
MIN_TOKEN_AGE_HOURS = 0.12          # Usia minimal ~7 menit (Lolos fase sniping bot)
MIN_LIQUIDITY_USD = 18000.0         # Minimal Likuiditas $18.000 USD (Kolam tebal & aman ditinggal)
MIN_MARKET_CAP = 35000.0            # ⭐ Min MC $35.000 USD (Dasar breakout sehat)
MAX_MARKET_CAP = 180000.0           # ⭐ MAKSIMAL MC $180.000 USD! (Wajib beli di bawah agar ruang 50x terbuka!)
MIN_VOL_H1 = 20000.0                # Volume 1 jam wajib deras minimal $20,000 USD

# 🟢 TRIPLE GREEN LOCK & RUNNER MOMENTUM
MIN_PC_M5 = 3.0                     # Menit ini (M5) WAJIB HIJAU >= +3.0% (Tolak koin lesu/dump)
MIN_PC_H1 = 15.0                    # 1 Jam (H1) WAJIB HIJAU >= +15.0%
MAX_PC_H1 = 350.0                   # Plafon H1 Max +350%
MAX_PC_H24_INITIAL = 800.0          # Batas awal H24 +800%

# 🛡️ PINTU DOMINASI PEMBELI (MUTLAK ANTI-PISAU JATUH)
MIN_BUYERS_H1 = 50                  # Wajib minimal 50 pembeli unik di H1 (Komunitas riil)
MIN_BUY_SELL_RATIO_H1 = 1.75        # ⭐ Minimal Pembeli H1: 1.75x Penjual (Dominasi pembeli mutlak!)
MIN_BUY_SELL_RATIO_M5 = 1.60        # ⭐ Minimal Pembeli M5: 1.60x Penjual (Tolak tekanan jual!)
MIN_AVG_TX_USD = 25.0               # Transaksi riil rata-rata >= $25 USD (Anti-wash bot)

# 🔒 KEAMANAN ON-CHAIN (STANDAR RUNNER KOMUNITAS)
MAX_DEV_HOLDING_PCT = 1.8           # Dev maksimal 1.8% (Aman dari dev rug/dump)
MAX_SINGLE_HOLDER_PCT = 5.5         # 1 Dompet perorangan maksimal 5.5%
MAX_TOP10_HOLDING_PCT = 22.0        # Total Top 10 Dompet maksimal 22.0% (Distribusi merata)

alerted_mints = set()
stats = {
    "scans_completed": 0,
    "breakouts_detected": 0,
    "continuation_rallies": 0,
    "golden_buyers_found": 0,
    "dumps_blocked": 0,
    "sell_ratio_blocked": 0,
    "bundles_blocked": 0,
    "last_scan_time": 0
}


def get_json(url: str, timeout: int = 5) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def audit_onchain_security(mint: str) -> Dict[str, Any]:
    """
    🛡️ AUDIT ON-CHAIN REAL-TIME CERDAS:
    1. Deteksi & Pisahkan Kolam Likuiditas DEX (AMM: PumpSwap, Raydium, Meteora, Orca).
    2. Audit Keamanan Kolam: Wajib Terkunci / Dibakar (LP Locked / Burned >= 80%).
    3. Audit Dompet Manusia (Whale): Hitung hanya dompet perorangan asli!
    4. Cek Freeze & Mint Authority (Anti-Honeypot).
    """
    res = {
        "top_holder": 0.0,
        "top10_cumulative": 0.0,
        "dev_holding": 0.0,
        "lp_locked_pct": 0.0,
        "is_safe": True,
        "score": 0,
        "rejection_reason": "",
        "audit_source": "RugCheck-SmartPool"
    }
    
    try:
        data = get_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=4)
        
        if data:
            score = data.get("score", 0)
            res["score"] = score
            if score > 750:
                res["is_safe"] = False
                res["rejection_reason"] = f"RugCheck Score Bahaya: {score}"
                return res

            token = data.get("token", {})
            if token.get("freezeAuthority") is not None:
                res["is_safe"] = False
                res["rejection_reason"] = "Freeze Authority Aktif (Potensi Honeypot)!"
                return res

            if token.get("mintAuthority") is not None:
                res["is_safe"] = False
                res["rejection_reason"] = "Mint Authority Aktif (Bisa Cetak Koin Gratis)!"
                return res

            risks = [rk.get("name", "").lower() for rk in data.get("risks", [])]
            for r_name in risks:
                if "correlation" in r_name or "insider" in r_name:
                    res["is_safe"] = False
                    res["rejection_reason"] = f"Risiko Sindikat: {r_name}"
                    return res

            # Identifikasi Kolam Likuiditas DEX
            markets = data.get("markets", [])
            known_pool_addresses = set()
            max_lp_locked = 0.0

            for m in markets:
                pub = m.get("pubkey")
                if pub: 
                    known_pool_addresses.add(pub)
                lp = m.get("lp", {})
                lp_mint = lp.get("lpMint")
                if lp_mint: 
                    known_pool_addresses.add(lp_mint)

                locked = float(lp.get("lpLockedPct", 0) or 0)
                if locked > max_lp_locked:
                    max_lp_locked = locked

            res["lp_locked_pct"] = max_lp_locked

            if markets and max_lp_locked < 80.0:
                res["is_safe"] = False
                res["rejection_reason"] = f"Likuiditas Belum Terkunci! LP Lock: {max_lp_locked:.1f}% (< 80%)"
                return res

            # Filter Dompet Manusia Asli
            creator_addr = data.get("creator")
            top_holders = data.get("topHolders", [])
            cumulative_pct = 0.0
            counted_holders = 0

            for h in top_holders:
                addr = h.get("address", "")
                owner = h.get("owner", "")
                pct = float(h.get("pct", 0.0))

                is_dex_pool = (
                    addr in known_pool_addresses or 
                    owner in known_pool_addresses or
                    "pool" in addr.lower() or 
                    "raydium" in addr.lower() or 
                    "meteora" in addr.lower() or
                    "pump" in addr.lower()
                )

                if addr == creator_addr or owner == creator_addr:
                    if not is_dex_pool:
                        res["dev_holding"] = pct

                if not is_dex_pool:
                    if pct > res["top_holder"]:
                        res["top_holder"] = pct
                    if counted_holders < 10:
                        cumulative_pct += pct
                        counted_holders += 1

            res["top10_cumulative"] = round(cumulative_pct, 1)

            if res["dev_holding"] > MAX_DEV_HOLDING_PCT:
                res["is_safe"] = False
                res["rejection_reason"] = f"Dev memegang {res['dev_holding']:.1f}% suplai"
                return res

            if res["top_holder"] > MAX_SINGLE_HOLDER_PCT:
                res["is_safe"] = False
                res["rejection_reason"] = f"Top 1 Paus Manusia {res['top_holder']:.1f}% > {MAX_SINGLE_HOLDER_PCT}%"
                return res

            if res["top10_cumulative"] > MAX_TOP10_HOLDING_PCT:
                res["is_safe"] = False
                res["rejection_reason"] = f"Top 10 Dompet memegang {res['top10_cumulative']}% (> {MAX_TOP10_HOLDING_PCT}%)"
                return res

            return res

        res["is_safe"] = False
        res["rejection_reason"] = "Audit On-Chain Timeout"
        return res

    except Exception as e:
        res["is_safe"] = False
        res["rejection_reason"] = f"Audit Error: {e}"
        return res


def extract_social_sentiment(pair: Dict[str, Any]) -> Dict[str, Any]:
    info = pair.get("info", {})
    websites = info.get("websites", [])
    socials = info.get("socials", [])

    has_website = len(websites) > 0
    twitter_handle = None
    telegram_handle = None

    for s in socials:
        plat = s.get("type", "").lower()
        url = s.get("url", "")
        if plat == "twitter":
            twitter_handle = url
        elif plat == "telegram":
            telegram_handle = url

    score_bonus = 0
    labels = []
    if twitter_handle:
        score_bonus += 3
        labels.append("Twitter (X)")
    if telegram_handle:
        score_bonus += 3
        labels.append("Telegram")
    if has_website:
        score_bonus += 2
        labels.append("Website")

    return {
        "has_socials": bool(twitter_handle or telegram_handle),
        "has_website": has_website,
        "twitter_url": twitter_handle,
        "telegram_url": telegram_handle,
        "score_bonus": score_bonus,
        "summary": ", ".join(labels) if labels else "None"
    }


def fetch_all_solana_candidates() -> List[str]:
    """
    🌐 RADAR MULTI-SUMBER KOMPREHENSIF (150 - 250+ Koin Simultan):
    Menjaring dari DexScreener Profiles, Boosts (Latest & Top), Search (SOL, Raydium, Pump),
    serta GeckoTerminal Solana New & Trending Pools. Runner emas tidak akan terlewat lagi!
    """
    candidates = set()
    endpoints = [
        "https://api.dexscreener.com/token-profiles/latest/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1",
        "https://api.dexscreener.com/latest/dex/search?q=SOL",
        "https://api.dexscreener.com/latest/dex/search?q=Raydium",
        "https://api.dexscreener.com/latest/dex/search?q=Pump",
        "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1",
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1"
    ]

    for ep in endpoints:
        data = get_json(ep, timeout=3)
        if isinstance(data, list):
            for item in data:
                if item.get("chainId") == "solana":
                    addr = item.get("tokenAddress")
                    if addr:
                        candidates.add(addr)
        elif isinstance(data, dict):
            # Format DexScreener search
            for pair in data.get("pairs", []):
                if pair.get("chainId") == "solana":
                    addr = pair.get("baseToken", {}).get("address")
                    if addr:
                        candidates.add(addr)
            # Format GeckoTerminal pools
            for it in data.get("data", []):
                rel = it.get("relationships", {}).get("base_token", {}).get("data", {})
                tid = rel.get("id", "")
                if tid.startswith("solana_"):
                    candidates.add(tid.replace("solana_", ""))

    return list(candidates)


def evaluate_pair(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = pair.get("baseToken", {})
    mint = base.get("address")
    created_at = pair.get("pairCreatedAt")

    if not mint or not created_at:
        return None

    now_ms = time.time() * 1000
    age_hours = (now_ms - created_at) / 3600000.0

    if age_hours < MIN_TOKEN_AGE_HOURS:
        return None

    liq_usd = float(pair.get("liquidity", {}).get("usd") or 0.0)
    market_cap = float(pair.get("marketCap") or 0.0)

    # 1. PINTU LIKUIDITAS ($12k+) & RENTANG MC ($25k s/d $2.5M)
    if liq_usd < MIN_LIQUIDITY_USD:
        return None
    if market_cap < MIN_MARKET_CAP or market_cap > MAX_MARKET_CAP:
        return None

    vol = pair.get("volume", {})
    vol_h1 = float(vol.get("h1") or 0.0)
    if vol_h1 < MIN_VOL_H1:
        return None

    # 2. TRIPLE GREEN LOCK (HANYA KENAIKAN / TOLAK PENURUNAN)
    price_change = pair.get("priceChange", {})
    pc_h1 = float(price_change.get("h1") or 0.0)
    pc_m5 = float(price_change.get("m5") or 0.0)
    pc_h24 = float(price_change.get("h24") or 0.0)

    if pc_m5 < MIN_PC_M5:
        stats["dumps_blocked"] += 1
        return None

    if pc_h1 < MIN_PC_H1:
        stats["dumps_blocked"] += 1
        return None

    if pc_h24 <= 0.0:
        stats["dumps_blocked"] += 1
        return None

    is_continuation = False
    if pc_h1 > MAX_PC_H1 or pc_h24 > MAX_PC_H24_INITIAL:
        is_continuation = True

    # 3. PINTU MUTLAK DOMINASI PEMBELI
    txns = pair.get("txns", {})
    txns_h1 = txns.get("h1", {})
    h1_buys = txns_h1.get("buys", 0)
    h1_sells = txns_h1.get("sells", 0)
    total_tx_h1 = h1_buys + h1_sells

    if h1_buys < MIN_BUYERS_H1:
        return None

    required_ratio_h1 = 1.15 if is_continuation else MIN_BUY_SELL_RATIO_H1
    if h1_buys < (h1_sells * required_ratio_h1):
        stats["sell_ratio_blocked"] += 1
        return None

    txns_m5 = txns.get("m5", {})
    m5_buys = txns_m5.get("buys", 0)
    m5_sells = txns_m5.get("sells", 0)

    # 🛡️ PINTU ANTI-PISAU JATUH (M5):
    # Tolak jika pembeli M5 tidak mencapai 1.60x penjual
    if m5_buys < (m5_sells * MIN_BUY_SELL_RATIO_M5) or m5_buys < 8:
        stats["sell_ratio_blocked"] += 1
        return None

    # Tolak jika aksi jual di 5 menit terakhir melampaui 38% dari total order!
    total_tx_m5 = m5_buys + m5_sells
    if total_tx_m5 > 0 and (m5_sells / total_tx_m5) > 0.38:
        stats["sell_ratio_blocked"] += 1
        return None

    # 4. FILTER ANTI-MICRO WASH BOT ($25 USD Avg/Tx)
    avg_tx_usd = vol_h1 / max(1, total_tx_h1)
    if avg_tx_usd < MIN_AVG_TX_USD:
        return None

    # 5. AUDIT MEDIA SOSIAL & KOMUNITAS (WAJIB ADA TWITTER ATAU TELEGRAM UNTUK RUNNER 50x)
    social = extract_social_sentiment(pair)
    if not social["has_socials"]:
        # Koin tanpa Twitter / Telegram TIDAK BISA terbang 50x karena tidak ada komunitas
        return None

    # 6. AUDIT KEAMANAN ON-CHAIN
    security = audit_onchain_security(mint)
    if not security["is_safe"]:
        if "Top 10" in security["rejection_reason"]:
            stats["bundles_blocked"] += 1
        return None

    if is_continuation:
        buyer_badge = f"🔥 <b>MEGA CONTINUATION RUNNER ({h1_ratio}x BUYERS)</b>"
        buyer_desc = "Koin breakout kuat & terus mencetak Higher High! Gelombang reli aktif!"
        base_score = 96
        stats["continuation_rallies"] += 1
    elif h1_ratio >= 2.5:
        buyer_badge = f"💎 <b>DIAMOND BUYERS TIER ({h1_ratio}x SUPER GOD MODE)</b>"
        buyer_desc = "Pembeli 2.5x lipat lebih banyak dari penjual! Dorongan beli luar biasa masif."
        base_score = 97
        stats["golden_buyers_found"] += 1
    elif h1_ratio >= 1.8:
        buyer_badge = f"🏆 <b>GOLDEN BUYERS TIER ({h1_ratio}x PEMBELI EMAS)</b>"
        buyer_desc = "Pembeli 1.8x+ mendominasi orderbook! Tekanan beli sangat bersih dan kuat."
        base_score = 94
        stats["golden_buyers_found"] += 1
    else:
        buyer_badge = f"🟢 <b>STRONG BULLISH TIER ({h1_ratio}x ORGANIK)</b>"
        buyer_desc = "Rasio pembeli sehat di atas 1.30x. Aliran dana masuk stabil & murni hijau."
        base_score = 90

    final_score = min(100, base_score + social["score_bonus"])

    # 🎯 TARGET TRADING PLAN "50x RUNNER STRATEGY" (BUY & LEAVE 1 JAM)
    entry_mc = market_cap
    tp1_mc = entry_mc * 3.0    # 3x (+200%): TARIK SEMUA MODAL AWAL (POSISI FREE RIDE 100%)
    tp2_mc = entry_mc * 10.0   # 10x (+900%): AMBIL 50% PROFIT (BANKING GAINS)
    tp3_mc = entry_mc * 50.0   # 50x (+4,900%): RUNNER BAG HIT (PULUHAN RIBU PERSEN)
    sl_mc = entry_mc * 0.75    # -25% Hard Stop Loss

    return {
        "mint": mint,
        "name": base.get("name", "Solana Gem"),
        "symbol": base.get("symbol", "SOL"),
        "dex_id": pair.get("dexId", "raydium").upper(),
        "age_hours": age_hours,
        "market_cap": market_cap,
        "liquidity_usd": liq_usd,
        "vol_h1": vol_h1,
        "avg_tx_usd": avg_tx_usd,
        "pc_h1": pc_h1,
        "pc_m5": pc_m5,
        "pc_h24": pc_h24,
        "h1_buys": h1_buys,
        "h1_sells": h1_sells,
        "h1_ratio": h1_ratio,
        "m5_ratio": m5_ratio,
        "buyer_badge": buyer_badge,
        "buyer_desc": buyer_desc,
        "top_holder_pct": security["top_holder"],
        "top10_cumulative": security["top10_cumulative"],
        "dev_holding_pct": security["dev_holding"],
        "lp_locked_pct": security["lp_locked_pct"],
        "social": social,
        "entry_mc": entry_mc,
        "tp1_mc": tp1_mc,
        "tp2_mc": tp2_mc,
        "tp3_mc": tp3_mc,
        "sl_mc": sl_mc,
        "score": final_score
    }


def format_telegram_message(d: Dict[str, Any]) -> str:
    mint = d["mint"]
    soc = d["social"]
    social_text = f"🌐 <b>Official Links:</b> {soc['summary']}"
    if soc["twitter_url"]:
        social_text += f"\n🐦 <b>Twitter/X:</b> {soc['twitter_url']}"
    if soc["telegram_url"]:
        social_text += f"\n✈️ <b>Telegram:</b> {soc['telegram_url']}"

    msg = f"""
🚀 <b>SIGNAL BULLISH BREAKOUT TERVERIFIKASI</b>

🪙 <b>Token:</b> {d['name']} (<b>${d['symbol']}</b>)
🏷️ <b>DEX:</b> {d['dex_id']}
⏱️ <b>Usia Token:</b> {d['age_hours']:.1f} Jam ({d['age_hours']*60:.0f} Menit)
🛡️ <b>Conviction Score:</b> <b>{d['score']}/100</b>

{d['buyer_badge']}
<i>{d['buyer_desc']}</i>

📊 <b>METRIK ORDERBOOK & TEKANAN BELI:</b>
• <b>Rasio Pembeli (1 Jam):</b> <b>{d['h1_ratio']}x</b> ({d['h1_buys']} Beli vs {d['h1_sells']} Jual)
• <b>Momentum (5 Menit):</b> <b>+{d['pc_m5']:.1f}%</b> ({d['m5_ratio']}x Pembeli)
• <b>Tren 1 Jam:</b> <b>+{d['pc_h1']:.1f}% Hijau Murni</b>
• <b>Volume 1 Jam:</b> ${d['vol_h1']:,.0f} USD
• <b>Rata-rata Order:</b> ${d['avg_tx_usd']:.1f} USD (Anti-Bot)

💰 <b>VALUASI & LIKUIDITAS:</b>
• <b>Market Cap Saat Ini:</b> <b>${d['market_cap']:,.0f} USD</b>
• <b>Likuiditas Pool:</b> ${d['liquidity_usd']:,.0f} USD (LP Lock: {d['lp_locked_pct']:.0f}%)

🔒 <b>AUDIT KEAMANAN ON-CHAIN:</b>
• <b>Top 1 Whale:</b> {d['top_holder_pct']:.1f}% (Aman & Terdistribusi)
• <b>Top 10 Dompet Gabungan:</b> {d['top10_cumulative']:.1f}% (Anti-Bundle)
• <b>Dev / Creator Holding:</b> {d['dev_holding_pct']:.1f}% (Anti-Dev Dump)

{social_text}

🎯 <b>TRADING PLAN "50x RUNNER" (BUY & HOLD 1 JAM):</b>
🟢 <b>Entry Area:</b> ${d['entry_mc']:,.0f} MC
💰 <b>TP 1 (3x / +200%):</b> ${d['tp1_mc']:,.0f} MC <i>(Tarik Modal Awal -> Free Ride)</i>
💎 <b>TP 2 (10x / +900%):</b> ${d['tp2_mc']:,.0f} MC <i>(Amankan 50% Keuntungan)</i>
🚀 <b>TP 3 (50x RUNNER):</b> ${d['tp3_mc']:,.0f} MC <i>(Target Puluhan Ribu Persen)</i>
🛑 <b>Proteksi SL (-25%):</b> ${d['sl_mc']:,.0f} MC

📋 <b>Mint Address (Klik untuk Salin):</b>
<code>{mint}</code>

🔗 <b>Direct Trading Links (1-Click Buy):</b>
<a href="https://t.me/solana_trojanbot?start=r-alpha_{mint}">[⚡ Trojan Bot]</a> · <a href="https://photon-sol.tinyastro.io/en/r/@alpha/{mint}">[Photon SOL]</a> · <a href="https://dexscreener.com/solana/{mint}">[DexScreener]</a> · <a href="https://neo.bullx.io/terminal?chainId=1399811149&address={mint}">[BullX]</a>"""
    return msg.strip()


def run_breakout_scanner_loop():
    logger.info("Radar Multi-Feed Pure Bullish Scanner V7.0 Aktif...")
    while True:
        try:
            candidates = fetch_all_solana_candidates()
            unalerted = [m for m in candidates if m not in alerted_mints]
            
            # ⚡ TURBO BATCHING: Ambil 30 token per request (Cepat & Anti-Rate Limit)
            chunk_size = 30
            for i in range(0, len(unalerted), chunk_size):
                chunk = unalerted[i:i + chunk_size]
                chunk_str = ",".join(chunk)
                pair_res = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{chunk_str}", timeout=6) or {}
                pairs = pair_res.get("pairs", [])
                
                seen_in_chunk = set()
                for pair in pairs:
                    base_addr = pair.get("baseToken", {}).get("address")
                    if not base_addr or base_addr in seen_in_chunk or base_addr in alerted_mints:
                        continue
                    seen_in_chunk.add(base_addr)

                    eval_res = evaluate_pair(pair)
                    if eval_res:
                        alerted_mints.add(base_addr)
                        stats["breakouts_detected"] += 1
                        logger.info(f"💎 BULLISH GEM: {eval_res['name']} (${eval_res['symbol']}) | MC: ${eval_res['market_cap']:,.0f} | Ratio: {eval_res['h1_ratio']}x")

                        if bot and TG_CHAT_ID:
                            text = format_telegram_message(eval_res)
                            try:
                                bot.send_message(TG_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
                            except Exception as e:
                                logger.error(f"Gagal kirim Telegram: {e}")

                time.sleep(0.5)

            stats["scans_completed"] += 1
            stats["last_scan_time"] = int(time.time())

        except Exception as e:
            logger.error(f"[Loop Error]: {e}")

        time.sleep(15)  # Siklus cepat 15 detik


if app:
    @app.route("/")
    def health():
        return jsonify({
            "status": "online",
            "bot_name": "Conviction Scanner V7.0 Comprehensive",
            "version": "7.0.0-PRO",
            "stats": stats,
            "monitored_unique_alerts": len(alerted_mints)
        })

    @app.route("/test-alert")
    def test_alert():
        if bot and TG_CHAT_ID:
            try:
                bot.send_message(TG_CHAT_ID, "🔔 <b>Test Notifikasi Conviction Scanner V7.0 Aktif!</b>", parse_mode="HTML")
                return jsonify({"status": "success", "message": "Test alert terkirim"})
            except Exception as e:
                return jsonify({"status": "error", "error": str(e)}), 500
        return jsonify({"status": "error", "message": "Bot token / chat ID belum dikonfigurasi"}), 400


def start_bot_and_background_worker():
    if bot and TG_CHAT_ID:
        try:
            bot.send_message(
                TG_CHAT_ID,
                "👑 <b>SOLANA CONVICTION SCANNER v7.0 ONLINE DI RENDER!</b>\n"
                "• Radar Multi-Feed: Aktif (PumpSwap, Raydium, Meteora)\n"
                "• Triple Green Lock: Aktif (Hanya Deteksi Naik)\n"
                "• Strict Buy Ratio: >= 1.50x\n"
                "• Smart DEX Pool Exclusion: Aktif (Top 1 Whale Murni Manusia)\n"
                "<i>Scanner siap menjaring permata breakout...</i>",
                parse_mode="HTML"
            )
            logger.info("Startup message sent successfully to Telegram!")
        except Exception as e:
            logger.error(f"Gagal kirim pesan startup: {e}")

    t = threading.Thread(target=run_breakout_scanner_loop, daemon=True)
    t.start()
    logger.info("Background scanner thread started successfully!")

# Dijalankan otomatis saat di-import oleh Gunicorn di Render!
start_bot_and_background_worker()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    if app:
        app.run(host="0.0.0.0", port=port)
    else:
        while True:
            time.sleep(1)
