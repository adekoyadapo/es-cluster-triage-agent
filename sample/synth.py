"""
synth.py — stdlib-only fake-data generator.
No third-party dependencies (faker intentionally absent).
"""
from __future__ import annotations

import math
import random
import string
import uuid
from datetime import datetime, timezone, timedelta


class SynthGen:
    """Seeded synthetic data generator. All methods are reproducible for a given seed."""

    # ── vocabulary tables ──────────────────────────────────────────────────────
    _CURRENCIES   = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "SGD"]
    _CURRENCY_W   = [0.50,  0.20,  0.10,  0.06,  0.05,  0.04,  0.03,  0.02]
    _MERCHANTS    = [
        "AcmeRetail", "GlobalMart", "TechHub", "FreshGrocer", "QuickFuel",
        "UrbanDiner", "CloudStore", "FitnessPro", "TravelDeal", "HomeGoods",
        "DigitalWorld", "CafeBlend", "AutoParts", "MedSupply", "BookNook",
        "GardenPlus", "PetCare", "SportZone", "LuxuryShop", "EduCenter",
    ]
    _STATUSES     = ["approved", "approved", "approved", "declined", "pending", "reversed"]
    _TX_TYPES     = ["purchase", "purchase", "purchase", "refund", "adjustment", "reward_redemption"]
    _TX_SUBTYPES  = ["chip", "contactless", "swipe", "online", "recurring", "manual"]
    _CATEGORIES   = ["retail", "grocery", "fuel", "dining", "travel", "entertainment",
                     "healthcare", "utility", "subscription", "government"]
    _SERVICES     = ["payment-gateway", "rewards-engine", "fraud-detector",
                     "auth-service", "settlement-service"]
    _EVENT_TYPES  = ["debit", "credit", "authorization", "reversal", "adjustment"]
    _SOURCES      = ["pos", "ecommerce", "atm", "p2p", "recurring"]

    _LOG_LEVELS   = ["INFO", "INFO", "INFO", "INFO", "WARN", "WARN", "ERROR", "DEBUG"]
    _LOGGERS      = [
        "com.example.payments.TransactionService",
        "com.example.auth.SecurityFilter",
        "com.example.db.ConnectionPool",
        "com.example.cache.RedisClient",
        "com.example.api.RestController",
        "com.example.batch.JobRunner",
        "com.example.messaging.KafkaConsumer",
        "org.springframework.web.servlet.DispatcherServlet",
        "org.hibernate.SQL",
        "com.zaxxer.hikari.HikariPool",
    ]
    _SERVICES_SVC = ["payment-api", "auth-service", "data-processor", "batch-worker",
                     "notification-svc", "audit-logger", "metrics-exporter"]
    _HOSTS        = [f"app-node-{i:02d}" for i in range(1, 9)]
    _THREADS      = [f"http-nio-8080-exec-{i}" for i in range(1, 11)] + \
                   [f"scheduled-{i}" for i in range(1, 5)]
    _EXC_TYPES    = [
        "java.lang.NullPointerException",
        "java.sql.SQLException",
        "java.util.concurrent.TimeoutException",
        "org.springframework.dao.DataAccessException",
        "java.io.IOException",
        "com.example.payments.PaymentException",
    ]
    _INFO_MSGS    = [
        "Processing transaction {id}",
        "Request received: POST /api/v2/transactions",
        "Cache hit for key {key}",
        "Database query completed in {ms}ms",
        "Scheduled job started: {job}",
        "User {user} authenticated successfully",
        "Event published to topic {topic}",
        "Health check: OK",
        "Connection pool size: {n}/{max}",
    ]
    _WARN_MSGS    = [
        "Slow query detected: {ms}ms for {sql}",
        "Retry attempt {n}/3 for transaction {id}",
        "High memory usage: {pct}%",
        "Rate limit approaching for client {client}",
        "Deprecated API endpoint called: {path}",
        "Connection pool near capacity: {n}/{max}",
    ]
    _BULK_WORDS   = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
        "omicron pi rho sigma tau upsilon phi chi psi omega lorem ipsum dolor sit "
        "amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore "
        "et dolore magna aliqua enim ad minim veniam quis nostrud exercitation ullamco "
        "laboris nisi aliquip commodo consequat duis aute irure reprehenderit voluptate "
        "velit esse cillum eu fugiat nulla pariatur excepteur sint occaecat cupidatat "
        "proident sunt culpa deserunt mollit anim id est laborum"
    ).split()

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    # ── low-level helpers ──────────────────────────────────────────────────────

    def _choice(self, seq):
        return self._rng.choice(seq)

    def _weighted(self, seq, weights):
        return self._rng.choices(seq, weights=weights, k=1)[0]

    def _int(self, lo, hi):
        return self._rng.randint(lo, hi)

    def _float(self, lo, hi):
        return round(self._rng.uniform(lo, hi), 2)

    def _hex(self, n=8):
        return "".join(self._rng.choices(string.hexdigits[:16], k=n))

    def uid(self) -> str:
        return str(uuid.UUID(int=self._rng.getrandbits(128)))

    def short_id(self, prefix="", n=8) -> str:
        return prefix + self._hex(n).upper()

    # ── timestamp helpers ──────────────────────────────────────────────────────

    def ts_now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def ts_jitter_iso(self, base_dt: datetime, max_jitter_s: int = 2) -> str:
        jitter = timedelta(seconds=self._rng.uniform(-max_jitter_s, max_jitter_s))
        dt = base_dt + jitter
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # ── transaction field generators ──────────────────────────────────────────

    def account_ref(self) -> str:
        return f"ACC-{self._int(100000, 999999)}"

    def instrument_token(self) -> str:
        return f"TKN-{self._hex(4).upper()}-{self._hex(4).upper()}-{self._int(1000, 9999)}"

    def party_ref(self) -> str:
        return f"PTY-{self._int(100000, 999999)}"

    def merchant_ref(self) -> str:
        return f"MCH-{self._int(10000, 99999)}"

    def merchant_name(self) -> str:
        return self._choice(self._MERCHANTS)

    def currency(self) -> str:
        return self._weighted(self._CURRENCIES, self._CURRENCY_W)

    def amount(self, lo=0.5, hi=5000.0) -> float:
        # log-normal for realistic spend distribution
        raw = math.exp(self._rng.gauss(2.5, 1.5))
        return round(max(lo, min(hi, raw)), 2)

    def monetary(self) -> dict:
        c = self.currency()
        a = self.amount()
        return {"amount": a, "currency_code": c, "multiplier": 1.0}

    def reward_monetary(self) -> dict:
        c = self.currency()
        a = round(self.amount(0, 50) * 0.02, 4)
        return {"amount": a, "currency_code": c, "multiplier": 0.02}

    def tx_status(self) -> str:
        return self._choice(self._STATUSES)

    def tx_type(self) -> str:
        return self._choice(self._TX_TYPES)

    def tx_subtype(self) -> str:
        return self._choice(self._TX_SUBTYPES)

    def category(self) -> str:
        return self._choice(self._CATEGORIES)

    def event_source(self) -> str:
        return self._choice(self._SERVICES)

    def event_type(self) -> str:
        return self._choice(self._EVENT_TYPES)

    # ── java log generators ───────────────────────────────────────────────────

    def log_level(self) -> str:
        return self._choice(self._LOG_LEVELS)

    def logger_name(self) -> str:
        return self._choice(self._LOGGERS)

    def service_name(self) -> str:
        return self._choice(self._SERVICES_SVC)

    def host_name(self) -> str:
        return self._choice(self._HOSTS)

    def thread_name(self) -> str:
        return self._choice(self._THREADS)

    def log_message(self, level: str) -> str:
        templates = self._INFO_MSGS if level in ("INFO", "DEBUG") else self._WARN_MSGS
        t = self._choice(templates)
        return t.format(
            id=self.short_id("TXN-"),
            key=f"cache:{self.short_id()}",
            ms=self._int(1, 5000),
            job=self._choice(["cleanup", "report", "sync", "archive"]),
            user=f"user_{self._int(1, 9999)}",
            topic=self._choice(["events", "transactions", "alerts"]),
            n=self._int(1, 50),
            max=50,
            pct=self._int(70, 95),
            client=f"client_{self._int(1, 99)}",
            path=f"/api/v{self._int(1, 2)}/legacy/{self._hex(4)}",
            sql=f"SELECT * FROM transactions WHERE id='{self.short_id()}'",
        )

    def java_stacktrace(self, exc_type: str | None = None) -> str:
        if exc_type is None:
            exc_type = self._choice(self._EXC_TYPES)
        lines = [
            f"{exc_type}: {self._choice(['Unexpected null', 'Connection refused', 'Timeout after 5000ms', 'Constraint violation', 'Read timed out'])}",
            f"\tat {self._choice(self._LOGGERS)}.process(Unknown Source)",
            f"\tat {self._choice(self._LOGGERS)}.execute({self._choice(self._LOGGERS).split('.')[-1]}.java:{self._int(50, 400)})",
            f"\tat org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:{self._int(800, 1000)})",
            f"\tat javax.servlet.http.HttpServlet.service(HttpServlet.java:764)",
        ]
        if self._rng.random() < 0.3:
            lines.append(f"\t... {self._int(3, 25)} more")
        return "\n".join(lines)

    # ── bulk/padding generators ────────────────────────────────────────────────

    def lorem_words(self, n: int) -> str:
        words = [self._choice(self._BULK_WORDS) for _ in range(n)]
        return " ".join(words)

    def random_field_name(self) -> str:
        """For mapping explosion: random dynamic field name."""
        return f"dyn_{self._hex(8)}"

    def random_tags(self) -> list[str]:
        tags = [self._choice(["important", "archived", "processed", "pending",
                               "reviewed", "flagged", "auto-tagged"]) for _ in range(self._int(1, 4))]
        return list(set(tags))
