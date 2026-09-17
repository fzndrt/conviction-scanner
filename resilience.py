"""
resilience.py — Modul resilience untuk Conviction Scanner
Circuit breaker, rate limiter multi-tier, cache, time budget,
safe_call wrapper, resilient worker loop, watchdog.
"""

import os
import time
import threading
import random
import hashlib
import json
import traceback
import logging
from collections import defaultdict
from functools import wraps
from typing import Any, Optional, Callable

# ============================================================
# LOGGER TERSTRUKTUR
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger("scanner")


# ============================================================
# 1. CIRCUIT BREAKER
# ============================================================
class CircuitBreaker:
    """
    Stop panggil API setelah N kegagalan berturut-turut.
    Setelah recovery_time, coba lagi (half-open).
    """
    def __init__(self, name: str, failure_threshold: int = 5, recovery_time: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self.failures = 0
        self.state = "CLOSED"
        self.last_failure_time = 0
        self.lock = threading.Lock()
        self.stats = {"success": 0, "failure": 0, "blocked": 0}

    def can_call(self) -> bool:
        with self.lock:
            if self.state == "CLOSED":
                return True
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_time:
                    self.state = "HALF_OPEN"
                    log.warning(f"Circuit {self.name} → HALF_OPEN (testing)")
                    return True
                self.stats["blocked"] += 1
                return False
            return True

    def record_success(self):
        with self.lock:
            self.failures = 0
            self.state = "CLOSED"
            self.stats["success"] += 1

    def record_failure(self):
        with self.lock:
            self.failures += 1
            self.stats["failure"] += 1
            self.last_failure_time = time.time()
            if self.failures >= self.failure_threshold and self.state != "OPEN":
                self.state = "OPEN"
                log.error(f"Circuit {self.name} → OPEN (stop {self.recovery_time}s)")

    def status(self) -> dict:
        with self.lock:
            return {
                "state": self.state,
                "failures": self.failures,
                "stats": dict(self.stats),
            }


BREAKERS: dict[str, CircuitBreaker] = {}

def get_breaker(name: str, failure_threshold: int = 5, recovery_time: int = 60) -> CircuitBreaker:
    if name not in BREAKERS:
        BREAKERS[name] = CircuitBreaker(name, failure_threshold, recovery_time)
    return BREAKERS[name]


# ============================================================
# 2. RATE LIMITER MULTI-TIER
# ============================================================
class RateLimiter:
    """
    Rate limiter yang mendukung batas per menit, jam, dan bulan sekaligus.
    Menggunakan sliding window counter per tier.

    Contoh:
        limiter = RateLimiter("santiment", per_minute=100, per_hour=500, per_month=1000)
        if limiter.acquire(timeout=2.0):
            # boleh panggil API
    """
    def __init__(self, name: str, per_minute: int = 0, per_hour: int = 0,
                 per_month: int = 0, burst: int = 0):
        self.name = name
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.per_month = per_month
        self.burst = burst  # opsional: izinkan burst kecil
        self.lock = threading.Lock()

        # Sliding window: deque of timestamps
        self._minute_window: list[float] = []
        self._hour_window: list[float] = []
        self._month_window: list[float] = []

        self.stats = {"acquired": 0, "rejected": 0, "waited": 0}

    def _cleanup(self, now: float):
        """Buang timestamp yang sudah di luar window."""
        # Window menit
        if self.per_minute:
            cutoff = now - 60
            while self._minute_window and self._minute_window[0] < cutoff:
                self._minute_window.pop(0)
        # Window jam
        if self.per_hour:
            cutoff = now - 3600
            while self._hour_window and self._hour_window[0] < cutoff:
                self._hour_window.pop(0)
        # Window bulan (30 hari)
        if self.per_month:
            cutoff = now - (30 * 86400)
            while self._month_window and self._month_window[0] < cutoff:
                self._month_window.pop(0)

    def _can_acquire(self, now: float) -> bool:
        self._cleanup(now)
        if self.per_minute and len(self._minute_window) >= self.per_minute:
            return False
        if self.per_hour and len(self._hour_window) >= self.per_hour:
            return False
        if self.per_month and len(self._month_window) >= self.per_month:
            return False
        return True

    def _record(self, now: float):
        self._minute_window.append(now)
        self._hour_window.append(now)
        self._month_window.append(now)

    def acquire(self, timeout: float = 5.0) -> bool:
        """
        Coba ambil slot. Block sampai timeout.
        Return True kalau berhasil, False kalau timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                now = time.time()
                if self._can_acquire(now):
                    self._record(now)
                    self.stats["acquired"] += 1
                    return True

            # Hitung waktu tunggu minimal
            with self.lock:
                now = time.time()
                waits = []
                if self.per_minute and len(self._minute_window) >= self.per_minute:
                    waits.append(self._minute_window[0] + 60 - now)
                if self.per_hour and len(self._hour_window) >= self.per_hour:
                    waits.append(self._hour_window[0] + 3600 - now)
                if self.per_month and len(self._month_window) >= self.per_month:
                    waits.append(self._month_window[0] + 30 * 86400 - now)
                wait_time = max(waits) if waits else 0.1

            wait_time = max(0.1, min(wait_time, 1.0))
            time.sleep(wait_time)

        self.stats["rejected"] += 1
        self.stats["waited"] += 1
        return False

    def remaining(self) -> dict:
        with self.lock:
            now = time.time()
            self._cleanup(now)
            return {
                "per_minute": max(0, self.per_minute - len(self._minute_window)) if self.per_minute else None,
                "per_hour": max(0, self.per_hour - len(self._hour_window)) if self.per_hour else None,
                "per_month": max(0, self.per_month - len(self._month_window)) if self.per_month else None,
            }

    def status(self) -> dict:
        return {"name": self.name, "remaining": self.remaining(), "stats": dict(self.stats)}


# ============================================================
# 3. RESPONSE CACHE DENGAN TTL
# ============================================================
class ResponseCache:
    """
    Cache sederhana dengan TTL per key.
    Menghemat request API dengan menyimpan hasil yang jarang berubah.
    """
    def __init__(self, default_ttl: int = 60, max_size: int = 5000):
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._cache: dict[str, tuple[float, Any]] = {}
        self.lock = threading.Lock()
        self.stats = {"hits": 0, "misses": 0, "evictions": 0}

    @staticmethod
    def make_key(*args, **kwargs) -> str:
        raw = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            if key in self._cache:
                expiry, value = self._cache[key]
                if time.time() < expiry:
                    self.stats["hits"] += 1
                    return value
                else:
                    del self._cache[key]
            self.stats["misses"] += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        with self.lock:
            if len(self._cache) >= self.max_size:
                # Buang entry paling lama (FIFO sederhana)
                oldest = min(self._cache.items(), key=lambda x: x[1][0])
                del self._cache[oldest[0]]
                self.stats["evictions"] += 1
            self._cache[key] = (time.time() + (ttl or self.default_ttl), value)

    def clear_expired(self):
        with self.lock:
            now = time.time()
            expired = [k for k, (exp, _) in self._cache.items() if now >= exp]
            for k in expired:
                del self._cache[k]
            return len(expired)

    def status(self) -> dict:
        with self.lock:
            return {"size": len(self._cache), "stats": dict(self.stats)}


# ============================================================
# 4. TIME BUDGET
# ============================================================
class TimeBudget:
    """Batasi total waktu eksekusi per token."""
    def __init__(self, seconds: float, name: str = "op"):
        self.seconds = seconds
        self.name = name
        self.start = time.time()

    def remaining(self) -> float:
        return max(0, self.seconds - (time.time() - self.start))

    def exhausted(self) -> bool:
        return self.remaining() <= 0.5

    def check(self, label: str = "") -> bool:
        if self.exhausted():
            log.warning(f"⏱️ Budget exhausted for {self.name} {label}")
            return True
        return False


# ============================================================
# 5. SAFE CALL — Universal API Wrapper
# ============================================================
def safe_call(
    fn: Callable,
    default: Any = None,
    breaker_name: Optional[str] = None,
    limiter_name: Optional[str] = None,
    cache: Optional[ResponseCache] = None,
    cache_key: Optional[str] = None,
    cache_ttl: Optional[int] = None,
    retries: int = 2,
    timeout: float = 10.0,
) -> Any:
    """
    Bungkus fungsi API dengan:
    - Circuit breaker check
    - Rate limiter acquire
    - Cache lookup (kalau cache + key diberikan)
    - Retry dengan exponential backoff + jitter
    - Return default kalau semua gagal
    - TIDAK pernah raise exception ke caller
    """
    breaker = get_breaker(breaker_name) if breaker_name else None
    limiter = LIMITERS.get(limiter_name) if limiter_name else None

    # Cache hit?
    if cache and cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Circuit breaker open?
    if breaker and not breaker.can_call():
        return default

    for attempt in range(retries + 1):
        # Rate limiter
        if limiter and not limiter.acquire(timeout=timeout):
            log.warning(f"[{limiter_name}] rate limit timeout")
            return default

        try:
            result = fn()
            if result is not None:
                if breaker:
                    breaker.record_success()
                if cache and cache_key:
                    cache.set(cache_key, result, ttl=cache_ttl)
                return result
        except Exception as e:
            log.warning(f"[{breaker_name or 'call'}] attempt {attempt+1} failed: {e}")
            if attempt < retries:
                sleep = (2 ** attempt) + random.uniform(0, 0.5)
                time.sleep(sleep)

    if breaker:
        breaker.record_failure()
    return default


# ============================================================
# 6. RESILIENT WORKER LOOP
# ============================================================
def resilient_loop(name: str, interval: float):
    """
    Decorator untuk worker loop: catch semua exception, log, lanjut.
    Loop akan terus jalan meskipun fungsi di dalamnya crash.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            log.info(f"Worker '{name}' started (interval={interval}s)")
            while True:
                try:
                    fn(*args, **kwargs)
                except KeyboardInterrupt:
                    log.info(f"Worker '{name}' interrupted")
                    break
                except Exception as e:
                    log.error(f"Worker '{name}' crashed: {e}\n{traceback.format_exc()}")
                time.sleep(interval)
        return wrapper
    return decorator


# ============================================================
# 7. WATCHDOG
# ============================================================
WATCHED_THREADS: dict[str, threading.Thread] = {}
WATCHED_TARGETS: dict[str, Callable] = {}

def register_watch(name: str, target: Callable):
    WATCHED_TARGETS[name] = target

def start_watchdog():
    def _watchdog_loop():
        log.info("🐕 Watchdog active")
        while True:
            try:
                time.sleep(30)
                for name, thread in list(WATCHED_THREADS.items()):
                    if not thread.is_alive():
                        log.error(f"💀 [WATCHDOG] {name} dead — restarting...")
                        new_t = threading.Thread(
                            target=WATCHED_TARGETS[name],
                            daemon=True,
                            name=name,
                        )
                        new_t.start()
                        WATCHED_THREADS[name] = new_t
            except Exception as e:
                log.error(f"[WATCHDOG ERROR] {e}\n{traceback.format_exc()}")

    wd = threading.Thread(target=_watchdog_loop, daemon=True, name="watchdog")
    wd.start()
    WATCHED_THREADS["watchdog"] = wd


# ============================================================
# 8. GLOBAL INSTANCES — DIKONFIGURASI DI SCRIPT UTAMA
# ============================================================
LIMITERS: dict[str, RateLimiter] = {}
CACHES: dict[str, ResponseCache] = {}


def init_limiters(config: dict):
    """
    Inisialisasi rate limiter dari config.
    config = {
        "santiment": {"per_minute": 100, "per_hour": 500, "per_month": 1000},
        "gecko": {"per_minute": 30},
        ...
    }
    """
    for name, limits in config.items():
        LIMITERS[name] = RateLimiter(name, **limits)


def init_caches(config: dict):
    """
    config = {
        "security": {"default_ttl": 1800, "max_size": 2000},
        "market": {"default_ttl": 30, "max_size": 5000},
        ...
    }
    """
    for name, opts in config.items():
        CACHES[name] = ResponseCache(**opts)