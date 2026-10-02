import calendar
import hashlib
import hmac
import os
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable
from zoneinfo import ZoneInfo


VILNIUS_TZ = ZoneInfo("Europe/Vilnius")
ALLOWED_CITIES = ("Vilnius", "Kaunas", "Klaipėda")
TIME_BUCKETS = (
    ("00–06", 0, 6),
    ("06–09", 6, 9),
    ("09–12", 9, 12),
    ("12–15", 12, 15),
    ("15–18", 15, 18),
    ("18–21", 18, 21),
    ("21–24", 21, 24),
)


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    started_at: datetime
    completed_at: datetime | None
    monthly_user_id: str
    city: str | None


def parse_admin_ids(raw_value: str | None) -> set[int]:
    admin_ids: set[int] = set()
    if not raw_value:
        return admin_ids

    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            admin_ids.add(int(item))
        except ValueError:
            continue
    return admin_ids


def get_database_url() -> str | None:
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_DATABASE_URL"):
        value = os.getenv(key)
        if value:
            return value
    return None


def get_current_local_month(now: datetime | None = None) -> tuple[int, int]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(VILNIUS_TZ)
    return local_now.year, local_now.month


def month_key_from_started_at(started_at: datetime) -> str:
    local_started = started_at.astimezone(VILNIUS_TZ)
    return f"{local_started.year:04d}-{local_started.month:02d}"


def monthly_pseudonym(secret: str, telegram_user_id: int, month_key: str) -> str:
    message = f"{telegram_user_id}:{month_key}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def vilnius_month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start_local = datetime(year, month, 1, tzinfo=VILNIUS_TZ)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=VILNIUS_TZ)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=VILNIUS_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def clamp_to_current_month(year: int, month: int, now: datetime | None = None) -> tuple[int, int]:
    current_year, current_month = get_current_local_month(now)
    if (year, month) > (current_year, current_month):
        return current_year, current_month
    return year, month


def percentage(count: int, total: int) -> int:
    if total <= 0:
        return 0
    value = Decimal(count * 100) / Decimal(total)
    return int(value.quantize(Decimal("0"), rounding=ROUND_HALF_UP))


def format_median(value: float | int) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def period_label_for_day(day: int, last_day: int) -> str:
    if day <= 7:
        return "1–7"
    if day <= 14:
        return "8–14"
    if day <= 21:
        return "15–21"
    return f"22–{last_day}"


def time_label_for_hour(hour: int) -> str:
    for label, start, end in TIME_BUCKETS:
        if start <= hour < end:
            return label
    return "21–24"


def calculate_month_stats(rows: Iterable[SessionRow], year: int, month: int) -> dict:
    rows = list(rows)
    completed_rows = [row for row in rows if row.completed_at is not None]
    started_count = len(rows)
    completed_count = len(completed_rows)
    active_user_counts: dict[str, int] = {}
    last_day = calendar.monthrange(year, month)[1]
    periods = {"1–7": 0, "8–14": 0, "15–21": 0, f"22–{last_day}": 0}
    cities = {city: 0 for city in ALLOWED_CITIES}
    times = {label: 0 for label, _, _ in TIME_BUCKETS}

    for row in completed_rows:
        active_user_counts[row.monthly_user_id] = active_user_counts.get(row.monthly_user_id, 0) + 1
        local_started = row.started_at.astimezone(VILNIUS_TZ)
        periods[period_label_for_day(local_started.day, last_day)] += 1
        times[time_label_for_hour(local_started.hour)] += 1
        if row.city in cities:
            cities[row.city] += 1

    active_users = len(active_user_counts)
    user_counts = sorted(active_user_counts.values(), reverse=True)
    top_user_count = user_counts[0] if user_counts else 0
    top_three_count = sum(user_counts[:3])
    median_value = statistics.median(user_counts) if user_counts else 0
    average_value = (completed_count / active_users) if active_users else 0

    return {
        "year": year,
        "month": month,
        "started": started_count,
        "completed": completed_count,
        "completion_rate": percentage(completed_count, started_count),
        "active_users": active_users,
        "median": median_value,
        "average": average_value,
        "top_user_count": top_user_count,
        "top_user_share": percentage(top_user_count, completed_count),
        "top_three_count": top_three_count,
        "top_three_share": percentage(top_three_count, completed_count),
        "periods": periods,
        "cities": cities,
        "times": times,
    }


def format_count_share(count: int, total: int) -> str:
    return f"{count} — {percentage(count, total)}%"


def format_stats_report(stats: dict) -> str:
    year = stats["year"]
    month = stats["month"]
    month_name = datetime(year, month, 1).strftime("%B %Y")
    completed = stats["completed"]

    if stats["started"] == 0:
        return f"📊 Fasoul Bot Statistics\n{month_name}\n\nNo statistics for this month yet."

    lines = [
        "📊 Fasoul Bot Statistics",
        month_name,
        "",
        "🧮 Usage",
        f"Started calculations: {stats['started']}",
        f"Completed calculations: {completed}",
        f"Completion rate: {stats['completion_rate']}%",
        "",
        f"👥 Active users: {stats['active_users']}",
        f"Median: {format_median(stats['median'])} calculations/user",
        f"Average: {stats['average']:.1f} calculations/user",
        "",
        "👤 Usage concentration",
        f"Top user: {stats['top_user_count']} calculations — {stats['top_user_share']}%",
        f"Top 3 users: {stats['top_three_count']} calculations — {stats['top_three_share']}%",
        "",
        "📅 By period of month",
    ]

    for label, count in stats["periods"].items():
        lines.append(f"{label}: {format_count_share(count, completed)}")

    lines.extend(["", "📍 By city"])
    for city, count in stats["cities"].items():
        lines.append(f"{city}: {format_count_share(count, completed)}")

    lines.extend(["", "🕐 By time"])
    for label, count in stats["times"].items():
        lines.append(f"{label}: {format_count_share(count, completed)}")

    return "\n".join(lines)


class AnalyticsStore:
    def __init__(
        self,
        database_url: str | None = None,
        hmac_secret: str | None = None,
        admin_ids: set[int] | None = None,
    ):
        self.database_url = database_url if database_url is not None else get_database_url()
        self.hmac_secret = hmac_secret if hmac_secret is not None else os.getenv("ANALYTICS_HMAC_SECRET")
        self.admin_ids = admin_ids if admin_ids is not None else parse_admin_ids(os.getenv("ADMIN_TELEGRAM_IDS"))
        self._psycopg = None

        if self.database_url:
            try:
                import psycopg

                self._psycopg = psycopg
            except Exception as exc:
                print(f"Analytics disabled: cannot import database driver ({type(exc).__name__}).")

    @property
    def enabled(self) -> bool:
        return bool(self.database_url and self.hmac_secret and self._psycopg)

    def is_admin(self, telegram_user_id: int | None) -> bool:
        return telegram_user_id in self.admin_ids if telegram_user_id is not None else False

    def _connect(self):
        return self._psycopg.connect(self.database_url, autocommit=True, connect_timeout=5)

    def initialize(self) -> None:
        if not self.enabled:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS analytics_sessions (
                        session_id TEXT PRIMARY KEY,
                        started_at TIMESTAMPTZ NOT NULL,
                        completed_at TIMESTAMPTZ NULL,
                        monthly_user_id TEXT NOT NULL,
                        city TEXT NULL CHECK (
                            city IS NULL OR city IN ('Vilnius', 'Kaunas', 'Klaipėda')
                        )
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_analytics_sessions_started_at
                    ON analytics_sessions (started_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_analytics_sessions_completed_at
                    ON analytics_sessions (completed_at)
                    """
                )
        except Exception as exc:
            print(f"Analytics initialization failed: {type(exc).__name__}.")

    def start_session(self, telegram_user_id: int | None, started_at: datetime | None = None) -> str | None:
        if telegram_user_id is None or self.is_admin(telegram_user_id) or not self.enabled:
            return None

        started_at = started_at or datetime.now(timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        started_at = started_at.astimezone(timezone.utc)
        month_key = month_key_from_started_at(started_at)
        monthly_user_id = monthly_pseudonym(self.hmac_secret, telegram_user_id, month_key)
        session_id = str(uuid.uuid4())

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO analytics_sessions
                        (session_id, started_at, completed_at, monthly_user_id, city)
                    VALUES (%s, %s, NULL, %s, NULL)
                    """,
                    (session_id, started_at, monthly_user_id),
                )
            return session_id
        except Exception as exc:
            print(f"Analytics start_session failed: {type(exc).__name__}.")
            return None

    def set_city(self, session_id: str | None, city: str) -> None:
        if not session_id or city not in ALLOWED_CITIES or not self.enabled:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE analytics_sessions SET city = %s WHERE session_id = %s",
                    (city, session_id),
                )
        except Exception as exc:
            print(f"Analytics set_city failed: {type(exc).__name__}.")

    def complete_session(self, session_id: str | None, completed_at: datetime | None = None) -> None:
        if not session_id or not self.enabled:
            return
        completed_at = completed_at or datetime.now(timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        completed_at = completed_at.astimezone(timezone.utc)

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE analytics_sessions
                    SET completed_at = %s
                    WHERE session_id = %s AND completed_at IS NULL
                    """,
                    (completed_at, session_id),
                )
        except Exception as exc:
            print(f"Analytics complete_session failed: {type(exc).__name__}.")

    def rows_for_month(self, year: int, month: int) -> list[SessionRow]:
        if not self.enabled:
            return []
        start_utc, end_utc = vilnius_month_bounds(year, month)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT session_id, started_at, completed_at, monthly_user_id, city
                    FROM analytics_sessions
                    WHERE started_at >= %s AND started_at < %s
                    ORDER BY started_at ASC
                    """,
                    (start_utc, end_utc),
                ).fetchall()
            return [SessionRow(*row) for row in rows]
        except Exception as exc:
            print(f"Analytics rows_for_month failed: {type(exc).__name__}.")
            return []

    def stats_for_month(self, year: int, month: int) -> dict:
        return calculate_month_stats(self.rows_for_month(year, month), year, month)
