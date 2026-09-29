"""Coverage for local danmaku deduplication and analysis windows."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import danmaku


class DanmakuTests(unittest.TestCase):
    def test_normalization_and_reaction_split(self):
        rows = [
            {"content": "懂你意思", "progress_ms": 60000},
            {"content": "懂你意思！", "progress_ms": 62000},
            {"content": "哈哈哈", "progress_ms": 30000},
            {"content": "哈哈哈哈", "progress_ms": 32000},
        ]
        ranked = danmaku.hot_phrases(rows)
        self.assertEqual(ranked["phrases"][0]["count"], 2)
        self.assertEqual(ranked["phrases"][0]["variants"], 2)
        self.assertTrue(all(item["content"].startswith("哈") for item in ranked["reactions"]))

    def test_collection_deduplicates_and_preserves_first_baseline(self):
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        first = {"bvid": "BVtest123", "cid": 1, "duration_seconds": 400, "page_count": 5,
                 "segments": [{"index": 1, "status": 200}, {"index": 2, "status": 200}],
                 "entries": [
                     {"id": "1", "content": "懂你意思", "progress_ms": 1000, "sent_at": now},
                     {"id": "2", "content": "懂你意思！", "progress_ms": 2000, "sent_at": now},
                 ]}
        second = {**first, "entries": first["entries"] + [
            {"id": "3", "content": "青旅", "progress_ms": 3000, "sent_at": now},
        ]}
        video = {"bvid": "BVtest123", "title": "Test", "cover": None}
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "danmaku.sqlite3"
            with patch.object(danmaku, "fetch_video", side_effect=[first, second]):
                first_run = danmaku.collect_videos([video], db)
                second_run = danmaku.collect_videos([video], db)
            self.assertEqual(first_run["videos"][0]["new_count"], 2)
            self.assertEqual(second_run["videos"][0]["new_count"], 1)
            result = danmaku.summary([video], "latest", path=db)
            self.assertEqual(result["videos"][0]["stored_count"], 3)
            self.assertTrue(result["videos"][0]["has_baseline"])
            self.assertEqual(result["selected"]["hot_all"]["phrases"][0]["count"], 2)
            self.assertEqual(len(danmaku.search("BVtest123", "懂", path=db)), 2)

    def test_partial_segment_remains_visible(self):
        result = {"bvid": "BVtest123", "cid": 1, "duration_seconds": 400, "page_count": 9,
                  "segments": [{"index": 1, "status": 200}, {"index": 2, "status": 429}],
                  "entries": [{"id": "1", "content": "hello", "progress_ms": 1000, "sent_at": 1}]}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(danmaku, "fetch_video", return_value=result):
                run = danmaku.collect_videos([{"bvid": "BVtest123"}], Path(directory) / "dm.sqlite3")
        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["videos"][0]["new_count"], 1)


if __name__ == "__main__":
    unittest.main()
