import unittest
from datetime import datetime, timezone

from analytics import (
    AnalyticsStore,
    SessionRow,
    calculate_month_stats,
    format_stats_report,
    month_key_from_started_at,
    monthly_pseudonym,
    parse_admin_ids,
    vilnius_month_bounds,
)


def vilnius_time(year, month, day, hour):
    from analytics import VILNIUS_TZ

    return datetime(year, month, day, hour, tzinfo=VILNIUS_TZ).astimezone(timezone.utc)


class AnalyticsTests(unittest.TestCase):
    def test_parse_admin_ids_ignores_invalid_values(self):
        self.assertEqual(parse_admin_ids("6517854385, bad, ,123"), {6517854385, 123})
        self.assertEqual(parse_admin_ids(None), set())

    def test_admin_detection_does_not_require_database(self):
        store = AnalyticsStore(database_url=None, hmac_secret=None, admin_ids={6517854385})

        self.assertTrue(store.is_admin(6517854385))
        self.assertFalse(store.is_admin(111))
        self.assertIsNone(store.start_session(6517854385))

    def test_multiple_admin_ids_are_excluded_from_sessions(self):
        admin_ids = parse_admin_ids("6517854385,6117764683")
        store = AnalyticsStore(database_url="postgres://example", hmac_secret="secret", admin_ids=admin_ids)
        store._psycopg = object()

        def fail_connect():
            self.fail("Admin calculations must not attempt analytics database writes.")

        store._connect = fail_connect

        self.assertTrue(store.is_admin(6517854385))
        self.assertTrue(store.is_admin(6117764683))
        self.assertFalse(store.is_admin(111))
        self.assertIsNone(store.start_session(6517854385))
        self.assertIsNone(store.start_session(6117764683))

    def test_monthly_pseudonym_changes_between_months(self):
        september = monthly_pseudonym("secret", 123456789, "2026-09")
        october = monthly_pseudonym("secret", 123456789, "2026-10")
        other_user = monthly_pseudonym("secret", 987654321, "2026-10")

        self.assertEqual(september, monthly_pseudonym("secret", 123456789, "2026-09"))
        self.assertNotEqual(september, october)
        self.assertNotEqual(october, other_user)

    def test_month_key_uses_vilnius_timezone(self):
        started = datetime(2026, 9, 30, 21, 30, tzinfo=timezone.utc)
        self.assertEqual(month_key_from_started_at(started), "2026-10")

    def test_vilnius_month_bounds_are_dst_aware(self):
        march_start, march_end = vilnius_month_bounds(2026, 3)
        october_start, october_end = vilnius_month_bounds(2026, 10)

        self.assertEqual((march_end - march_start).total_seconds() / 3600, 743)
        self.assertEqual((october_end - october_start).total_seconds() / 3600, 745)

    def test_calculate_month_stats(self):
        rows = [
            SessionRow("s1", vilnius_time(2026, 10, 2, 10), vilnius_time(2026, 10, 2, 10), "u1", "Vilnius"),
            SessionRow("s2", vilnius_time(2026, 10, 8, 16), vilnius_time(2026, 10, 8, 16), "u1", "Kaunas"),
            SessionRow("s3", vilnius_time(2026, 10, 18, 18), vilnius_time(2026, 10, 18, 18), "u2", "Klaipėda"),
            SessionRow("s4", vilnius_time(2026, 10, 25, 22), None, "u3", "Vilnius"),
        ]

        stats = calculate_month_stats(rows, 2026, 10)

        self.assertEqual(stats["started"], 4)
        self.assertEqual(stats["completed"], 3)
        self.assertEqual(stats["completion_rate"], 75)
        self.assertEqual(stats["active_users"], 2)
        self.assertEqual(stats["median"], 1.5)
        self.assertEqual(round(stats["average"], 1), 1.5)
        self.assertEqual(stats["top_user_count"], 2)
        self.assertEqual(stats["top_user_share"], 67)
        self.assertEqual(stats["top_three_count"], 3)
        self.assertEqual(stats["top_three_share"], 100)
        self.assertEqual(stats["periods"]["1–7"], 1)
        self.assertEqual(stats["periods"]["8–14"], 1)
        self.assertEqual(stats["periods"]["15–21"], 1)
        self.assertEqual(stats["periods"]["22–31"], 0)
        self.assertEqual(stats["cities"]["Vilnius"], 1)
        self.assertEqual(stats["cities"]["Kaunas"], 1)
        self.assertEqual(stats["cities"]["Klaipėda"], 1)
        self.assertEqual(stats["times"]["09–12"], 1)
        self.assertEqual(stats["times"]["15–18"], 1)
        self.assertEqual(stats["times"]["18–21"], 1)
        self.assertEqual(sum(stats["times"].values()), 3)

    def test_zero_stats_format_without_division_errors(self):
        stats = calculate_month_stats([], 2026, 10)
        report = format_stats_report(stats)

        self.assertEqual(stats["completion_rate"], 0)
        self.assertIn("No statistics for this month yet.", report)

    def test_single_user_and_less_than_three_users(self):
        rows = [
            SessionRow("s1", vilnius_time(2026, 10, 22, 15), vilnius_time(2026, 10, 22, 15), "u1", "Vilnius"),
            SessionRow("s2", vilnius_time(2026, 10, 23, 16), vilnius_time(2026, 10, 23, 16), "u1", "Vilnius"),
        ]

        stats = calculate_month_stats(rows, 2026, 10)

        self.assertEqual(stats["active_users"], 1)
        self.assertEqual(stats["median"], 2)
        self.assertEqual(stats["top_user_count"], 2)
        self.assertEqual(stats["top_user_share"], 100)
        self.assertEqual(stats["top_three_count"], 2)
        self.assertEqual(stats["top_three_share"], 100)


if __name__ == "__main__":
    unittest.main()
