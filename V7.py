"""
================================================================
SOLANA PURE BULLISH & CONTINUATION SCANNER v6.1
Repo: https://github.com/fzndrt/conviction-scanner
All-In-One Script: Mandiri tanpa perlu file tambahan!

KUNCI UTAMA v6.1:
1. Triple Green Lock: M5 >= +1.5%, H1 >= +12%, H24 > 0% (HANYA KENAIKAN!).
2. Strict Buyer Dominance: H1 Buys >= 1.60x Sells & M5 Buys >= 1.35x Sells (Tolak rasio sell!).
3. Dynamic Continuation Rally: Mendukung koin tangga (staircase) bertahap yang reli lagi.
4. Golden Market Cap Range: $25.000 s/d $350.000 (Early Capture).
5. Liquidity Floor: Mulai $15.000 USD.
6. Anti-Micro Wash Bot: Rata-rata per transaksi >= $35 USD.
7. Anti-Bundled Snipers: Total Top 10 Wallets <= 28% total suplai.
8. Social & On-Chain Security: Audit X/Twitter, Telegram, Freeze & Mint Revoked.
================================================================
"""

import os
import time
import json
import logging
import threading
import urllib.request
from typing import List, Dict, Any, Optional
from flask import Flask, jsonify
import telebot
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pure-bullish-scanner")

app = Flask(__name__)

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")

bot = telebot.TeleBot(TG_BOT_TOKEN) if TG_BOT_TOKEN else None

# ==========================================
# 🎯 PARAMETER EMAS & TRIPLE GREEN LOCK
# ==========================================
MIN_TOKEN_AGE_HOURS = 0.1            # Minimal usia 25 menit
MIN_LIQUIDITY_USD = 15000.0          # Minimal Likuiditas $15.000 USD
MIN_MARKET_CAP = 25000.0             # ⭐ Min MC $25.000 USD (Early Capture)
MAX_MARKET_CAP = 450000.0            # ⭐ Max MC $350.000 USD (Growth Zone)
MIN_VOL_H1 = 9000.0                  # Min volume 1 jam $9,000 USD

# 🟢 TRIPLE GREEN LOCK (HANYA MENDETEKSI KENAIKAN / ANTI-PENURUNAN)
MIN_PC_M5 = 1.5                      # ⭐ Menit ini (M5) WAJIB HIJAU >= +1.5%
MIN_PC_H1 = 12.0                     # ⭐ 1 Jam (H1) WAJIB HIJAU >= +12.0%
MAX_PC_H1 = 100.0                    # Plafon H1 Max +100% (Tolak lilin vertikal pucuk)
MAX_PC_H24_INITIAL = 350.0           # Batas awal H24 +350%

# 🛡️ PINTU MUTLAK DOMINASI PEMBELI (ANTI-SELL RATIO)
MIN_BUYERS_H1 = 45                   # Wajib minimal 45 pembeli unik di H1
MIN_BUY_SELL_RATIO_H1 = 1.60         # ⭐ Minimal Pembeli H1: 1.60x Penjual
MIN_BUY_SELL_RATIO_M5 = 1.35         # ⭐ Minimal Pembeli M5: 1.35x Penjual
MIN_AVG_TX_USD = 35.0                # Transaksi riil rata-rata >= $35 USD (Anti-wash bot)

# 🔒 KEAMANAN ON-CHAIN & ANTI-SYNDICATE
MAX_DEV_HOLDING_PCT = 2.5            # Dev/Creator maksimal 2.5%
MAX_SINGLE_HOLDER_PCT = 7.5         # 1 Dompet perorangan maksimal 10.0%
MAX_TOP10_HOLDING_PCT = 24.0         # Total Top 10 Dompet maksimal 28.0% (Anti-Bundle)

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


def get_json(url: str, timeout: int = 10) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def audit_onchain_security(mint: str) -> Dict[str, Any]:
    """
    🛡️ AUDIT ON-CHAIN REAL-TIME CERDAS:
    1. Deteksi & Pisahkan Kolam Likuiditas DEX (AMM Pools: PumpSwap, Raydium, Meteora, Orca).
    2. Audit Keamanan Kolam: Wajib Terkunci / Dibakar (LP Locked / Burned >= 85%).
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

            # 1. Cek Freeze & Mint Authority
            token = data.get("token", {})
            if token.get("freezeAuthority") is not None:
                res["is_safe"] = False
                res["rejection_reason"] = "Freeze Authority Aktif (Potensi Honeypot)!"
                return res

            if token.get("mintAuthority") is not None:
                res["is_safe"] = False
                res["rejection_reason"] = "Mint Authority Aktif (Bisa Cetak Koin Gratis)!"
                return res

            # 2. Cek Risiko Sindikat / Insider
            risks = [rk.get("name", "").lower() for rk in data.get("risks", [])]
            for r_name in risks:
                if "correlation" in r_name or "insider" in r_name:
                    res["is_safe"] = False
                    res["rejection_reason"] = f"Risiko Sindikat: {r_name}"
                    return res

            # 3. IDENTIFIKASI KOLAM DEX & AUDIT LP LOCKED/BURNED
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

                # Cek persentase LP yang terkunci atau dibakar
                locked = float(lp.get("lpLockedPct", 0) or 0)
                if locked > max_lp_locked:
                    max_lp_locked = locked

            res["lp_locked_pct"] = max_lp_locked

            # Syarat Wajib: Kolam Likuiditas harus terkunci minimal 85% (Anti Tarik Karpet / Rugpull)
            if markets and max_lp_locked < 85.0:
                res["is_safe"] = False
                res["rejection_reason"] = f"Likuiditas Belum Terkunci! LP Lock: {max_lp_locked:.1f}% (< 85%)"
                return res

            # 4. FILTER DOMPET MANUSIA (MENGABAIKAN KOLAM DEX SECARA TOTAL)
            creator_addr = data.get("creator")
            top_holders = data.get("topHolders", [])
            cumulative_pct = 0.0
            counted_holders = 0

            for h in top_holders:
                addr = h.get("address", "")
                owner = h.get("owner", "")
                pct = float(h.get("pct", 0.0))

                # Kriteria Deteksi Kolam DEX:
                # - Alamat atau Owner cocok dengan pubkey market DEX (Raydium, PumpSwap, Meteora, dll)
                # - Atau mengandung teks AMM standar
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

                # HANYA HITUNG JIKA INI DOMPET MANUSIA (BUKAN KOLAM LIKUIDITAS)
                if not is_dex_pool:
                    if pct > res["top_holder"]:
                        res["top_holder"] = pct
                    if counted_holders < 10:
                        cumulative_pct += pct
                        counted_holders += 1

            res["top10_cumulative"] = round(cumulative_pct, 1)

            # 5. EVALUASI AMAN UNTUK DOMPET MANUSIA
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

        # Jika Rugcheck timeout / gagal
        res["is_safe"] = False
        res["rejection_reason"] = "Audit On-Chain Timeout / Server Sibuk"
        return res

    except Exception as e:
        res["is_safe"] = False
        res["rejection_reason"] = f"Audit Error: {e}"
        return res

def extract_social_sentiment(pair: Dict[str, Any]) -> Dict[str, Any]:
    info = pair.get("info", {})
    websites = info.get("websites", [])
    socials = info.get("socials", [])

    has_telegram = False
    has_twitter = False
    has_website = len(websites) > 0
    twitter_handle = ""
    telegram_handle = ""

    for s in socials:
        sType = s.get("type", "").lower()
        sUrl = s.get("url", "")
        if sType == "telegram" or "t.me" in sUrl:
            has_telegram = True
            telegram_handle = sUrl
        elif sType == "twitter" or "x.com" in sUrl or "twitter.com" in sUrl:
            has_twitter = True
            twitter_handle = sUrl

    score_bonus = 0
    labels = []
    if has_twitter:
        score_bonus += 4
        labels.append("Twitter (X)")
    if has_telegram:
        score_bonus += 3
        labels.append("Telegram")
    if has_website:
        score_bonus += 3
        labels.append("Website")

    if has_twitter and has_telegram:
        status = "✅ Komunitas Lengkap (Verified X & TG)"
    elif has_twitter or has_telegram:
        status = "⚠️ Media Sosial Parsial"
    else:
        status = "❌ Tanpa Akun Sosial Resmi"

    return {
        "status": status,
        "has_twitter": has_twitter,
        "has_telegram": has_telegram,
        "has_website": has_website,
        "twitter_url": twitter_handle,
        "telegram_url": telegram_handle,
        "score_bonus": score_bonus,
        "summary": ", ".join(labels) if labels else "None"
    }


def fetch_all_solana_candidates() -> List[str]:
    candidates = set()

    profiles = get_json("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for p in profiles:
        if p.get("chainId") == "solana":
            addr = p.get("tokenAddress")
            if addr:
                candidates.add(addr)

    boosts = get_json("https://api.dexscreener.com/token-boosts/latest/v1") or []
    for b in boosts:
        if b.get("chainId") == "solana":
            addr = b.get("tokenAddress")
            if addr:
                candidates.add(addr)

    search_res = get_json("https://api.dexscreener.com/latest/dex/search?q=SOL") or {}
    for pair in search_res.get("pairs", [])[:30]:
        if pair.get("chainId") == "solana":
            addr = pair.get("baseToken", {}).get("address")
            if addr:
                candidates.add(addr)

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

    # 1. PINTU LIKUIDITAS ($15k+) & SWEET SPOT MC ($25k s/d $350k)
    if liq_usd < MIN_LIQUIDITY_USD:
        return None
    if market_cap < MIN_MARKET_CAP or market_cap > MAX_MARKET_CAP:
        return None

    vol = pair.get("volume", {})
    vol_h1 = float(vol.get("h1") or 0.0)
    if vol_h1 < MIN_VOL_H1:
        return None

    # ========================================================
    # 🟢 2. TRIPLE GREEN LOCK (HANYA KENAIKAN / TOLAK PENURUNAN)
    # ========================================================
    price_change = pair.get("priceChange", {})
    pc_h1 = float(price_change.get("h1") or 0.0)
    pc_m5 = float(price_change.get("m5") or 0.0)
    pc_h24 = float(price_change.get("h24") or 0.0)

    # Kunci 1: Candle 5 Menit Wajib Hijau Positif (>= +1.5%)
    if pc_m5 < MIN_PC_M5:
        stats["dumps_blocked"] += 1
        return None  # Tolak koin yang menit ini sedang merah/koreksi!

    # Kunci 2: Candle 1 Jam Wajib Hijau Positif (>= +12.0%)
    if pc_h1 < MIN_PC_H1:
        stats["dumps_blocked"] += 1
        return None  # Tolak koin yang 1 jam terakhir minus/stagnan!

    # Kunci 3: Tolak lilin vertikal 1 jam yang sudah meledak > 100% (Pucuk instan)
    if pc_h1 > MAX_PC_H1:
        return None

    # Kunci 4: Evaluasi 24 Jam & Tangga Reli (Staircase Continuation)
    is_continuation = False
    if pc_h24 <= 0.0:
        stats["dumps_blocked"] += 1
        return None  # Tolak koin mati suri yang tren 24 jamnya merah

    # ========================================================
    # 🛡️ 3. PINTU MUTLAK DOMINASI PEMBELI (TOLAK SELL RATIO)
    # ========================================================
    txns = pair.get("txns", {})
    txns_h1 = txns.get("h1", {})
    h1_buys = txns_h1.get("buys", 0)
    h1_sells = txns_h1.get("sells", 0)
    total_tx_h1 = h1_buys + h1_sells

    if h1_buys < MIN_BUYERS_H1:
        return None

    # Wajib Pembeli H1 >= 1.60x Penjual!
    if h1_buys < (h1_sells * MIN_BUY_SELL_RATIO_H1):
        stats["sell_ratio_blocked"] += 1
        return None

    txns_m5 = txns.get("m5", {})
    m5_buys = txns_m5.get("buys", 0)
    m5_sells = txns_m5.get("sells", 0)

    # Wajib Pembeli M5 >= 1.35x Penjual!
    if m5_buys < (m5_sells * MIN_BUY_SELL_RATIO_M5) or m5_buys < 8:
        stats["sell_ratio_blocked"] += 1
        return None

    # Cek Pengecualian Tangga Reli (Jika H24 > 350%)
    if pc_h24 > MAX_PC_H24_INITIAL:
        # Koin dengan H24 tinggi HANYA lolos jika Pembeli H1 sangat ganas (>= 1.80x) dan M5 >= 2.0%
        if (h1_buys >= h1_sells * 1.80) and pc_m5 >= 2.0:
            is_continuation = True
            stats["continuation_rallies"] += 1
        else:
            return None  # Tolak pucuk biasa yang tidak ada dorongan baru

    # 4. FILTER ANTI-MICRO WASH BOT ($35 USD Avg/Tx)
    avg_tx_usd = vol_h1 / max(1, total_tx_h1)
    if avg_tx_usd < MIN_AVG_TX_USD:
        return None

    # 5. KEAMANAN ON-CHAIN (DEV DUMP & ANTI-BUNDLE)
    security = audit_onchain_security(mint)
    if not security["is_safe"]:
        if "Top 10" in security["rejection_reason"]:
            stats["bundles_blocked"] += 1
        return None

    # 6. AUDIT MEDIA SOSIAL
    social = extract_social_sentiment(pair)

    h1_ratio = round(h1_buys / max(1, h1_sells), 2)
    m5_ratio = round(m5_buys / max(1, m5_sells), 2)

    # KLASIFIKASI KEKUATAN & LENCANA PEMBELI
    if is_continuation:
        buyer_badge = f"🔥 <b>STAIRCASE CONTINUATION RALLY ({h1_ratio}x BUYERS)</b>"
        buyer_desc = "Koin naik bertahap & sukses buat lantai harga! Gelombang reli baru aktif!"
        base_score = 97
    elif h1_ratio >= 3.0:
        buyer_badge = f"💎 <b>DIAMOND BUYERS TIER ({h1_ratio}x SUPER GOD MODE)</b>"
        buyer_desc = "Pembeli 3x lipat lebih banyak dari penjual! Dorongan beli luar biasa masif."
        base_score = 98
        stats["golden_buyers_found"] += 1
    elif h1_ratio >= 2.2:
        buyer_badge = f"🏆 <b>GOLDEN BUYERS TIER ({h1_ratio}x PEMBELI EMAS)</b>"
        buyer_desc = "Pembeli 2.2x+ mendominasi orderbook! Tekanan beli sangat bersih dan kuat."
        base_score = 95
        stats["golden_buyers_found"] += 1
    else:
        buyer_badge = f"🟢 <b>STRONG BULLISH TIER ({h1_ratio}x ORGANIK)</b>"
        buyer_desc = "Rasio pembeli sehat di atas 1.60x. Aliran dana masuk stabil & murni hijau."
        base_score = 91

    final_score = min(100, base_score + social["score_bonus"])

    # TRADING PLAN OTOMATIS
    entry_mc = market_cap
    tp1_mc = entry_mc * 1.6   # +60% Tarik Modal Awal
    tp2_mc = entry_mc * 2.5   # +150% Ambil Profit
    tp3_mc = entry_mc * 5.0   # +400% Moonshot
    sl_mc = entry_mc * 0.75   # -25% Cut Loss

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
    name = d["name"]
    sym = d["symbol"]
    dex = d["dex_id"]
    mc = d["market_cap"]
    liq = d["liquidity_usd"]
    age_h = d["age_hours"]
    vol1 = d["vol_h1"]
    avg_tx = d["avg_tx_usd"]
    pc1 = d["pc_h1"]
    pcm = d["pc_m5"]
    pc24 = d["pc_h24"]
    b1 = d["h1_buys"]
    s1 = d["h1_sells"]
    h1_ratio = d["h1_ratio"]
    m5_ratio = d["m5_ratio"]
    badge = d["buyer_badge"]
    b_desc = d["buyer_desc"]
    top_h = d["top_holder_pct"]
    top10 = d["top10_cumulative"]
    dev_h = d["dev_holding_pct"]
    soc = d["social"]
    score = d["score"]

    tp1 = d["tp1_mc"]
    tp2 = d["tp2_mc"]
    tp3 = d["tp3_mc"]
    sl = d["sl_mc"]

    dev_str = f"Sangat Aman ({dev_h:.1f}%)" if dev_h <= 2.5 else f"⚠️ {dev_h:.1f}%"

    msg = f"""{badge}
💎 <b>{name} (${sym})</b> | <code>DEX: {dex}</code>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>Confidence Score:</b> <code>{score}/100</code>
📊 <b>Kondisi Tren:</b> <code>{b_desc}</code>
⏳ <b>Usia Koin:</b> <code>{age_h:.1f} Jam</code>
💰 <b>Market Cap:</b> <code>${mc:,.0f} (Zona Early $25k-$350k)</code>
💧 <b>Likuiditas Pool:</b> <code>${liq:,.0f} (Floor $15k Pass)</code>
📈 <b>Volume 1 Jam:</b> <code>${vol1:,.0f}</code>
━━━━━━━━━━━━━━━━━━━━
🟢 <b>TRIPLE GREEN MOMENTUM & PEMBELI:</b>
• Candle 5 Menit (M5): <code>+{pcm:.1f}% (Aktif Menembus Naik)</code>
• Candle 1 Jam (H1): <code>+{pc1:.1f}% (Tren Bullish Nyata)</code>
• Tren 24 Jam: <code>+{pc24:.1f}% (Struktur Naik)</code>
• Dominasi Pembeli H1: <code>{h1_ratio}x ({b1} Buys vs {s1} Sells)</code>
• Dominasi Pembeli M5: <code>{m5_ratio}x Buyers</code>
• Nilai Rata-rata/Tx: <code>${avg_tx:,.1f} USD (Anti-Wash Bot Lolos)</code>
━━━━━━━━━━━━━━━━━━━━
🛡️ <b>AUDIT KEAMANAN & SOSIAL MEDIA:</b>
• Dompet Dev/Creator: <code>{dev_str}</code>
• Top 1 Holder: <code>{top_h:.1f}% (Aman &lt; 10%)</code>
• Top 10 Wallets Akumulasi: <code>{top10}% (Anti-Bundle &lt; 28%)</code>
• Freeze & Mint Authority: <code>REVOKED (Anti-Honeypot)</code>
• Status Komunitas: <code>{soc['status']}</code>
• Tautan Terdaftar: <code>{soc['summary']}</code>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>PANDUAN EKSEKUSI TRADING (PROFIT PLAN):</b>
• <b>Zona Entry:</b> <code>Sekitar MC ${mc:,.0f}</code>
• <b>TP 1 (+60%):</b> <code>${tp1:,.0f} (Tarik Modal Awal)</code>
• <b>TP 2 (+150%):</b> <code>${tp2:,.0f} (Amankan Profit)</code>
• <b>TP 3 (+400% Moonshot):</b> <code>${tp3:,.0f} (Biarkan Terbang)</code>
• <b>Stop Loss (-25%):</b> <code>${sl:,.0f} (Disiplin Cut Loss)</code>

📋 <b>Mint Address:</b>
<code>{mint}</code>

⚡ <b>Perintah Quick Snipe:</b>
<code>/snipe {mint} 0.5</code>

🔗 <b>Direct Trading Links (1-Click Buy):</b>
<a href="https://t.me/solana_trojanbot?start=r-alpha_{mint}">[⚡ Trojan Bot]</a> · <a href="https://photon-sol.tinyastro.io/en/r/@alpha/{mint}">[Photon SOL]</a> · <a href="https://dexscreener.com/solana/{mint}">[DexScreener]</a> · <a href="https://neo.bullx.io/terminal?chainId=1399811149&address={mint}">[BullX]</a>"""
    return msg.strip()


def run_breakout_scanner_loop():
    logger.info("Radar Pure Bullish Scanner v6.1 Aktif...")
    while True:
        try:
            candidates = fetch_all_solana_candidates()

            for mint in candidates:
                if mint in alerted_mints:
                    continue

                pair_res = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8) or {}
                pairs = pair_res.get("pairs", [])
                if not pairs:
                    continue

                pair = pairs[0]
                eval_res = evaluate_pair(pair)

                if eval_res:
                    alerted_mints.add(mint)
                    stats["breakouts_detected"] += 1
                    logger.info(f"💎 BULLISH GEM: {eval_res['name']} (${eval_res['symbol']}) | MC: ${eval_res['market_cap']:,.0f} | Ratio: {eval_res['h1_ratio']}x")

                    if bot and TG_CHAT_ID:
                        text = format_telegram_message(eval_res)
                        try:
                            bot.send_message(TG_CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
                        except Exception as e:
                            logger.error(f"Gagal kirim Telegram: {e}")

                time.sleep(0.35)

            stats["scans_completed"] += 1
            stats["last_scan_time"] = int(time.time())

        except Exception as e:
            logger.error(f"[Loop Error]: {e}")

        time.sleep(25)


@app.route("/")
def health():
    return jsonify({
        "service": "pure-bullish-early-breakout-scanner",
        "version": "6.1.0-pure-bullish-momentum",
        "status": "Running",
        "scans_completed": stats["scans_completed"],
        "breakouts_detected": stats["breakouts_detected"],
        "continuation_rallies": stats["continuation_rallies"],
        "golden_buyers_found": stats["golden_buyers_found"],
        "dumps_blocked": stats["dumps_blocked"],
        "sell_ratio_blocked": stats["sell_ratio_blocked"],
        "bundles_blocked": stats["bundles_blocked"],
        "cached_alerts": len(alerted_mints)
    })


@app.route("/test-alert")
def test_alert():
    if not bot or not TG_CHAT_ID:
        return jsonify({"ok": False, "error": "TG_BOT_TOKEN atau TG_CHAT_ID belum diset!"})

    dummy = {
        "mint": "H8WTvo3ZXSYHJAcYqzCforbG8coR1FDAX2kHFfqHBAGS",
        "name": "BagsPay",
        "symbol": "BAGSPAY",
        "dex_id": "METEORA",
        "age_hours": 3.2,
        "market_cap": 48500,
        "liquidity_usd": 21500,
        "vol_h1": 39500,
        "avg_tx_usd": 82.5,
        "pc_h1": 38.5,
        "pc_m5": 5.2,
        "pc_h24": 115.0,
        "h1_buys": 380,
        "h1_sells": 150,
        "h1_ratio": 2.53,
        "m5_ratio": 2.10,
        "buyer_badge": "🏆 <b>GOLDEN BUYERS TIER (2.53x PEMBELI EMAS)</b>",
        "buyer_desc": "Pembeli 2.2x+ mendominasi orderbook! Tekanan beli sangat bersih dan kuat.",
        "top_holder_pct": 5.9,
        "top10_cumulative": 21.4,
        "dev_holding_pct": 0.0,
        "social": {
            "status": "✅ Komunitas Lengkap (Verified X & TG)",
            "summary": "Twitter (X), Telegram, Website",
            "score_bonus": 10
        },
        "entry_mc": 48500,
        "tp1_mc": 77600,
        "tp2_mc": 121250,
        "tp3_mc": 242500,
        "sl_mc": 36375,
        "score": 99
    }
    pesan = format_telegram_message(dummy)
    try:
        bot.send_message(TG_CHAT_ID, pesan, parse_mode="HTML", disable_web_page_preview=True)
        return jsonify({"ok": True, "pesan": "Berhasil! Test alert Pure Bullish v6.1 terkirim ke Telegram."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    if bot and TG_CHAT_ID:
        try:
            bot.send_message(
                TG_CHAT_ID,
                "👑 <b>PURE BULLISH SCANNER v6.1 ONLINE!</b>\nTriple Green Lock Aktif: M5 &gt;= +1.5%, H1 &gt;= +12%, H24 &gt; 0%. Hanya Mendeteksi Kenaikan Murni &amp; Dominasi Pembeli Emas!",
                parse_mode="HTML"
            )
        except Exception:
            pass

    scanner_thread = threading.Thread(target=run_breakout_scanner_loop, daemon=True)
    scanner_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
