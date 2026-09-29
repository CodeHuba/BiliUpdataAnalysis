"""Checks for the snapshot ordering and comparison windows used in the dashboard."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard


UID = "3546619609876957"


def snapshot(when: dt.datetime, fans: int, views: int, count: int = 10) -> dict:
    return {
        "sampled_at_utc": when.isoformat(),
        "creator_uid": UID,
        "profile": {"followers_exact": fans},
        "videos": [
            {"bvid": f"BV{i}", "title": f"Video {i}", "view": views + i,
             "like": 100 + i, "coin": 10, "favorite": 10, "reply": 10,
             "danmaku": 10, "share": 10}
            for i in range(count)
        ],
    }


class DashboardSummaryTests(unittest.TestCase):
    def test_cover_url_uses_https_and_ignores_unexpected_hosts(self):
        self.assertEqual(
            dashboard.cover_url("http://i2.hdslb.com/bfs/archive/example.jpg"),
            "https://i2.hdslb.com/bfs/archive/example.jpg",
        )
        self.assertIsNone(dashboard.cover_url("https://hdslb.com.evil.example/image.jpg"))

    def test_snapshots_sort_mixed_time_zone_offsets_by_instant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "samples").mkdir()
            earlier = snapshot(dt.datetime(2026, 9, 29, 10, 12, tzinfo=dt.timezone(dt.timedelta(hours=8))), 100, 100)
            later = snapshot(dt.datetime(2026, 9, 29, 2, 37, tzinfo=dt.timezone.utc), 110, 120)
            (root / "samples" / "a.json").write_text(json.dumps(earlier), encoding="utf-8")
            (root / "samples" / "b.json").write_text(json.dumps(later), encoding="utf-8")
            with patch.object(dashboard, "ROOT", root):
                self.assertEqual(dashboard.snapshots()[-1]["profile"]["followers_exact"], 110)

    def test_24_hour_and_latest_windows_use_different_baselines(self):
        now = dt.datetime(2026, 9, 29, 12, tzinfo=dt.timezone.utc)
        rows = [
            snapshot(now - dt.timedelta(hours=20), 1000, 100),
            snapshot(now - dt.timedelta(hours=2), 1090, 190),
            snapshot(now, 1100, 210),
        ]
        with patch.object(dashboard, "snapshots", return_value=rows):
            day = dashboard.make_summary("24h")
            latest = dashboard.make_summary("latest")
        self.assertEqual(day["coverage_hours"], 20)
        self.assertEqual(day["follower_delta"], 100)
        self.assertEqual(day["view_growth"], 1100)
        self.assertEqual(latest["coverage_hours"], 2)
        self.assertEqual(latest["follower_delta"], 10)
        self.assertEqual(latest["view_growth"], 200)
        self.assertEqual(len(day["intervals"]), 2)

    def test_missing_24_hour_baseline_does_not_reuse_stale_data(self):
        now = dt.datetime(2026, 9, 29, 12, tzinfo=dt.timezone.utc)
        rows = [snapshot(now - dt.timedelta(hours=30), 1000, 100), snapshot(now, 1100, 210)]
        with patch.object(dashboard, "snapshots", return_value=rows):
            day = dashboard.make_summary("24h")
        self.assertIsNone(day["baseline_at"])
        self.assertIsNone(day["follower_delta"])
        self.assertIsNone(day["view_growth"])
        self.assertEqual(len(day["follower_history"]), 1)

    def test_new_video_has_only_observed_history_and_no_invented_growth(self):
        now = dt.datetime(2026, 9, 29, 12, tzinfo=dt.timezone.utc)
        before = snapshot(now - dt.timedelta(hours=6), 1000, 100)
        after = snapshot(now, 1010, 120)
        after["videos"][0]["bvid"] = "BVnew"
        with patch.object(dashboard, "snapshots", return_value=[before, after]):
            result = dashboard.make_summary("24h")
        new_video = result["videos"][0]
        self.assertIsNone(new_video["deltas"]["view"])
        self.assertEqual(len(new_video["history"]), 1)
        self.assertEqual(result["matched_videos"], 9)


if __name__ == "__main__":
    unittest.main()
