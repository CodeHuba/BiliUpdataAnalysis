"""Local dashboard for the latest ten public Bilibili video snapshots."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import danmaku
from probe_opencli import sample


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
FIELDS = ("view", "like", "coin", "favorite", "reply", "danmaku", "share")
collection_lock = threading.Lock()
collection_state = {"running": False, "kind": None, "last_error": None,
                    "danmaku_error": None, "last_finished": None}
BEIJING = dt.timezone(dt.timedelta(hours=8))


def next_slot(now: dt.datetime | None = None) -> dt.datetime:
    """Next 00:00/06:00/12:00/18:00 Beijing-time collection slot."""
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(BEIJING)
    hour = (local.hour // 6 + 1) * 6
    return local.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(hours=hour)


def status() -> dict:
    return {**collection_state, "next_scheduled": next_slot().isoformat()}


def snapshots() -> list[dict]:
    files = sorted((ROOT / "samples").glob("*.json"))
    files += sorted((ROOT / "data" / "snapshots").glob("*.json"))
    found = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("creator_uid") != "3546619609876957":
                continue
            if not payload.get("sampled_at_utc") or not payload.get("videos"):
                continue
            found.append(payload)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(found, key=lambda value: dt.datetime.fromisoformat(value["sampled_at_utc"]))


def number(value: object) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def cover_url(value: object) -> str | None:
    parsed = urlparse(str(value or ""))
    hostname = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not hostname.endswith(".hdslb.com"):
        return None
    return parsed._replace(scheme="https").geturl()


def compare_videos(current: dict, baseline: dict | None) -> list[dict]:
    baseline_by_bvid = {
        video.get("bvid"): video for video in baseline["videos"]
        if video.get("bvid")
    } if baseline else {}
    videos = []
    for video in current["videos"][:10]:
        bvid = video.get("bvid")
        prior_video = baseline_by_bvid.get(bvid)
        metrics = {field: number(video.get(field)) for field in FIELDS}
        deltas = {}
        for field in FIELDS:
            prior = number(prior_video.get(field)) if prior_video else None
            deltas[field] = metrics[field] - prior if metrics[field] is not None and prior is not None else None
        videos.append({
            "bvid": bvid,
            "title": video.get("title", "未命名视频"),
            "published": video.get("publish_time"),
            "url": f"https://www.bilibili.com/video/{bvid}",
            "cover": cover_url(video.get("thumbnail")),
            "metrics": metrics,
            "deltas": deltas,
            "baseline_at": baseline["sampled_at_utc"] if prior_video else None,
        })
    return videos


def growth_totals(videos: list[dict]) -> tuple[int, int | None, dict[str, int | None]]:
    compared = [video for video in videos if video["deltas"]["view"] is not None]
    breakdown = {}
    for field in FIELDS[1:]:
        values = [video["deltas"][field] for video in compared if video["deltas"][field] is not None]
        breakdown[field] = sum(values) if values else None
    return (
        len(compared),
        sum(video["deltas"]["view"] for video in compared) if compared else None,
        breakdown,
    )


def make_summary(window: str = "24h") -> dict:
    if window not in ("24h", "latest"):
        raise ValueError("Unknown comparison window")
    all_snapshots = snapshots()
    if not all_snapshots:
        return {"empty": True, "status": status()}

    latest = all_snapshots[-1]
    latest_time = dt.datetime.fromisoformat(latest["sampled_at_utc"])
    prior_full = [item for item in all_snapshots[:-1] if len(item["videos"]) >= 10]
    if window == "24h":
        cutoff = latest_time - dt.timedelta(hours=24)
        candidates = [item for item in prior_full if dt.datetime.fromisoformat(item["sampled_at_utc"]) >= cutoff]
        baseline = candidates[0] if candidates else None
    else:
        baseline = prior_full[-1] if prior_full else None

    baseline_time = dt.datetime.fromisoformat(baseline["sampled_at_utc"]) if baseline else None
    visible = [
        item for item in all_snapshots
        if baseline_time is not None and dt.datetime.fromisoformat(item["sampled_at_utc"]) >= baseline_time
    ] if baseline_time else [latest]
    history = [
        {"at": item["sampled_at_utc"], "followers": number(item["profile"].get("followers_exact"))}
        for item in visible if isinstance(item.get("profile"), dict)
    ]
    current_followers = number(latest.get("profile", {}).get("followers_exact"))
    previous_followers = number(baseline.get("profile", {}).get("followers_exact")) if baseline else None
    videos = compare_videos(latest, baseline)
    for video in videos:
        video["history"] = []
        for item in visible:
            observed = next((row for row in item["videos"] if row.get("bvid") == video["bvid"]), None)
            if observed:
                video["history"].append({
                    "at": item["sampled_at_utc"],
                    "metrics": {field: number(observed.get(field)) for field in FIELDS},
                })
    matched, view_growth, breakdown = growth_totals(videos)
    interaction_growth = sum(value for value in breakdown.values() if value is not None) if matched else None

    interval_snapshots = [item for item in visible if len(item["videos"]) >= 10]
    intervals = []
    for before, after in zip(interval_snapshots, interval_snapshots[1:]):
        rows = compare_videos(after, before)
        interval_matched, interval_views, interval_breakdown = growth_totals(rows)
        before_fans = number(before.get("profile", {}).get("followers_exact"))
        after_fans = number(after.get("profile", {}).get("followers_exact"))
        intervals.append({
            "from": before["sampled_at_utc"],
            "to": after["sampled_at_utc"],
            "hours": round((dt.datetime.fromisoformat(after["sampled_at_utc"]) - dt.datetime.fromisoformat(before["sampled_at_utc"])).total_seconds() / 3600, 2),
            "followers": after_fans - before_fans if before_fans is not None and after_fans is not None else None,
            "views": interval_views,
            "interactions": sum(value for value in interval_breakdown.values() if value is not None) if interval_matched else None,
            "matched_videos": interval_matched,
        })
    return {
        "empty": False,
        "window": window,
        "uid": latest["creator_uid"],
        "sampled_at": latest["sampled_at_utc"],
        "baseline_at": baseline["sampled_at_utc"] if baseline else None,
        "coverage_hours": round((latest_time - baseline_time).total_seconds() / 3600, 2) if baseline_time else None,
        "snapshot_count": len(all_snapshots),
        "followers": current_followers,
        "follower_delta": current_followers - previous_followers
        if current_followers is not None and previous_followers is not None else None,
        "follower_history": history,
        "videos": videos,
        "matched_videos": matched,
        "view_growth": view_growth,
        "interaction_growth": interaction_growth,
        "interaction_breakdown": breakdown,
        "intervals": intervals,
        "status": status(),
    }


def collect_once(kind: str = "all") -> None:
    error = None
    danmaku_error = None
    try:
        if kind == "all":
            result = sample(10)
            if len(result["videos"]) != 10:
                raise RuntimeError(f"Expected 10 videos, got {len(result['videos'])}")
            folder = ROOT / "data" / "snapshots"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.fromisoformat(result["sampled_at_utc"]).strftime("%Y%m%dT%H%M%SZ")
            destination = folder / f"{stamp}.json"
            temporary = folder / f"{stamp}.json.tmp"
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)
        else:
            existing = snapshots()
            if not existing:
                raise RuntimeError("Collect video metrics before collecting danmaku")
            result = existing[-1]
        danmaku_result = danmaku.collect_videos(result["videos"])
        if danmaku_result["status"] != "complete":
            failed = [item["bvid"] for item in danmaku_result["videos"]
                      if item["error"] or item["successful_segments"] != item["expected_segments"]]
            danmaku_error = "部分弹幕分段读取失败：" + ", ".join(failed)
    except Exception as exc:  # Keep the server available when a browser read fails.
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        with collection_lock:
            collection_state["running"] = False
            collection_state["kind"] = None
            collection_state["last_error"] = error
            collection_state["danmaku_error"] = danmaku_error
            collection_state["last_finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def begin_collection(kind: str = "all") -> bool:
    with collection_lock:
        if collection_state["running"]:
            return False
        collection_state["running"] = True
        collection_state["kind"] = kind
        collection_state["last_error"] = None
        collection_state["danmaku_error"] = None
    threading.Thread(target=collect_once, args=(kind,), daemon=True).start()
    return True


def schedule_collections() -> None:
    while True:
        target = next_slot()
        while True:
            remaining = (target - dt.datetime.now(BEIJING)).total_seconds()
            if remaining <= 0:
                break
            threading.Event().wait(min(remaining, 60))
        begin_collection()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            window = parse_qs(urlparse(self.path).query).get("window", ["24h"])[0]
            if window not in ("24h", "latest"):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            self.send_json(make_summary(window))
            return
        if path == "/api/status":
            self.send_json(status())
            return
        if path == "/api/danmaku":
            query = parse_qs(urlparse(self.path).query)
            window = query.get("window", ["24h"])[0]
            if window not in ("24h", "latest"):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            existing = snapshots()
            videos = compare_videos(existing[-1], None) if existing else []
            self.send_json(danmaku.summary(videos, window, query.get("bvid", [None])[0]))
            return
        if path == "/api/danmaku/search":
            query = parse_qs(urlparse(self.path).query)
            bvid = query.get("bvid", [""])[0]
            phrase = query.get("q", [""])[0]
            existing = snapshots()
            valid = {item.get("bvid") for item in existing[-1]["videos"][:10]} if existing else set()
            if bvid not in valid or len(phrase) > 100:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"results": danmaku.search(bvid, phrase)})
            return
        static = {"/": "index.html", "/styles.css": "styles.css", "/app.js": "app.js",
                  "/danmaku.css": "danmaku.css", "/danmaku.js": "danmaku.js"}
        filename = static.get(path)
        if filename is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = (WEB / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mimetypes.guess_type(filename)[0] or "text/plain") + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/api/collect", "/api/danmaku/collect"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not begin_collection("danmaku" if path == "/api/danmaku/collect" else "all"):
            self.send_json({"started": False, "reason": "already_running"}, HTTPStatus.CONFLICT)
            return
        self.send_json({"started": True}, HTTPStatus.ACCEPTED)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=schedule_collections, daemon=True).start()
    print(f"Dashboard: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
