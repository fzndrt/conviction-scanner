"""resilience.py — Circuit breaker, rate limiter, cache, watchdog."""

import time
import threading
import random
import hashlib
import json
import traceback
import logging
from functools import wraps

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger("scanner")


class CircuitBreaker:
    def __init__(self, name, failure_threshold=5, recovery_time=60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self.failures = 0
        self.state = "CLOSED"
        self.last_failure_time = 0
        self.lock = threading.Lock()
        self.stats = {"success": 0, "failure": 0, "blocked": 0}

    def can_call(self):
        with self.lock:
            if self.state == "CLOSED":
                return True
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_time:
                    self.state = "HALF_OPEN"
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
                log.error(f"Circuit {self.name} → OPEN")

    def status(self):
        with self.lock:
            return {"state": self.state, "failures": self.failures, "stats": dict(self.stats)}


BREAKERS = {}

def get_breaker(name):
    if name not in BREAKERS:
        BREAKERS[name] = CircuitBreaker(name)
    return BREAKERS[name]


class RateLimiter:
    def __init__(self, name, per_minute=0, per_hour=0, per_month=0):
        self.name = name
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.per_month = per_month
        self.lock = threading.Lock()
        self._minute = []
        self._hour = []
        self._month = []
        self.stats = {"acquired": 0, "rejected": 0}

    def _cleanup(self, now):
        if self.per_minute:
            c = now - 60
            while self._minute and self._minute[0] < c: self._minute.pop(0)
        if self.per_hour:
            c = now - 3600
            while self._hour and self._hour[0] < c: self._hour.pop(0)
        if self.per_month:
            c = now - (30 * 86400)
            while self._month and self._month[0] < c: self._month.pop(0)

    def _can_acquire(self, now):
        self._cleanup(now)
        if self.per_minute and len(self._minute) >= self.per_minute: return False
        if self.per_hour and len(self._hour) >= self.per_hour: return False
        if self.per_month and len(self._month) >= self.per_month: return False
        return True

    def acquire(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                now = time.time()
                if self._can_acquire(now):
                    self._minute.append(now)
                    self._hour.append(now)
                    self._month.append(now)
                    self.stats["acquired"] += 1
                    return True
            time.sleep(0.2)
        self.stats["rejected"] += 1
        return False

    def status(self):
        return {"name": self.name, "stats": dict(self.stats)}


class ResponseCache:
    def __init__(self, default_ttl=60, max_size=5000):
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._cache = {}
        self.lock = threading.Lock()
        self.stats = {"hits": 0, "misses": 0, "evictions": 0}

    def get(self, key):
        with self.lock:
            if key in self._cache:
                expiry, value = self._cache[key]
                if time.time() < expiry:
                    self.stats["hits"] += 1
                    return value
                del self._cache[key]
            self.stats["misses"] += 1
            return None

    def set(self, key, value, ttl=None):
        with self.lock:
            if len(self._cache) >= self.max_size:
                oldest = min(self._cache.items(), key=lambda x: x[1][0])
                del self._cache[oldest[0]]
                self.stats["evictions"] += 1
            self._cache[key] = (time.time() + (ttl or self.default_ttl), value)

    def clear_expired(self):
        with self.lock:
            now = time.time()
            expired = [k for k, (exp, _) in self._cache.items() if now >= exp]
            for k in expired: del self._cache[k]
            return len(expired)

    def status(self):
        with self.lock:
            return {"size": len(self._cache), "stats": dict(self.stats)}


class TimeBudget:
    def __init__(self, seconds, name="op"):
        self.seconds = seconds
        self.name = name
        self.start = time.time()

    def remaining(self):
        return max(0, self.seconds - (time.time() - self.start))

    def exhausted(self):
        return self.remaining() <= 0.5

    def check(self, label=""):
        if self.exhausted():
            log.warning(f"⏱️ Budget exhausted: {self.name} {label}")
            return True
        return False


LIMITERS = {}
CACHES = {}

def safe_call(fn, default=None, breaker_name=None, limiter_name=None,
              cache=None, cache_key=None, cache_ttl=None, retries=2, timeout=10.0):
    breaker = get_breaker(breaker_name) if breaker_name else None
    limiter = LIMITERS.get(limiter_name) if limiter_name else None

    if cache and cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    if breaker and not breaker.can_call():
        return default

    for attempt in range(retries + 1):
        if limiter and not limiter.acquire(timeout=timeout):
            return default
        try:
            result = fn()
            if result is not None:
                if breaker: breaker.record_success()
                if cache and cache_key:
                    cache.set(cache_key, result, ttl=cache_ttl)
                return result
        except Exception as e:
            log.warning(f"[{breaker_name or 'call'}] attempt {attempt+1}: {e}")
            if attempt < retries:
                time.sleep((2 ** attempt) + random.uniform(0, 0.5))

    if breaker: breaker.record_failure()
    return default


def resilient_loop(name, interval):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            log.info(f"Worker '{name}' started (interval={interval}s)")
            while True:
                try:
                    fn(*args, **kwargs)
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    log.error(f"Worker '{name}' crashed: {e}\n{traceback.format_exc()}")
                time.sleep(interval)
        return wrapper
    return decorator


WATCHED_THREADS = {}
WATCHED_TARGETS = {}

def register_watch(name, target):
    WATCHED_TARGETS[name] = target

def start_watchdog():
    def _loop():
        log.info("🐕 Watchdog active")
        while True:
            try:
                time.sleep(30)
                for name, thread in list(WATCHED_THREADS.items()):
                    if not thread.is_alive():
                        log.error(f"💀 [WATCHDOG] {name} dead — restarting")
                        new_t = threading.Thread(target=WATCHED_TARGETS[name], daemon=True, name=name)
                        new_t.start()
                        WATCHED_THREADS[name] = new_t
            except Exception as e:
                log.error(f"[WATCHDOG] {e}")
    wd = threading.Thread(target=_loop, daemon=True, name="watchdog")
    wd.start()
    WATCHED_THREADS["watchdog"] = wd


def init_limiters(config):
    for name, limits in config.items():
        LIMITERS[name] = RateLimiter(name, **limits)

def init_caches(config):
    for name, opts in config.items():
        CACHES[name] = ResponseCache(**opts)