"""Collect readable Bilibili danmaku through the connected Chrome and store it locally."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter
from contextlib import closing
from pathlib import Path

from probe_opencli import run_opencli


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "data" / "danmaku.sqlite3"
UTC = dt.timezone.utc
FETCH_EXPRESSION = r'''(async () => {
  const video = window.__INITIAL_STATE__?.videoData;
  if (!video?.cid || !video?.aid || !video?.bvid) throw new Error('Video metadata unavailable');
  const duration = Number(video.duration || 0);
  const total = Math.ceil(duration / 360);
  if (total < 1 || total > 100) throw new Error('Unexpected segment count');
  const decoder = new TextDecoder('utf-8');
  function varint(bytes, start) {
    let value = 0n, shift = 0n, p = start;
    for (let i = 0; i < 10; i++) {
      if (p >= bytes.length) throw new Error('Truncated varint');
      const b = bytes[p++]; value |= BigInt(b & 127) << shift;
      if (!(b & 128)) return [value, p];
      shift += 7n;
    }
    throw new Error('Invalid varint');
  }
  function fields(bytes, callback) {
    let p = 0;
    while (p < bytes.length) {
      const [key, next] = varint(bytes, p); p = next;
      const field = Number(key >> 3n), wire = Number(key & 7n);
      if (wire === 0) {
        const [value, end] = varint(bytes, p); callback(field, wire, value); p = end;
      } else if (wire === 2) {
        const [length, end] = varint(bytes, p); p = end;
        const size = Number(length);
        if (size < 0 || p + size > bytes.length) throw new Error('Invalid field length');
        callback(field, wire, bytes.subarray(p, p + size)); p += size;
      } else if (wire === 1 || wire === 5) {
        p += wire === 1 ? 8 : 4;
        if (p > bytes.length) throw new Error('Truncated field');
      } else throw new Error('Unsupported wire type ' + wire);
    }
  }
  function element(bytes) {
    const row = {id: null, progress_ms: null, content: null, sent_at: null};
    fields(bytes, (field, wire, value) => {
      if (field === 1 && wire === 0) row.id = value.toString();
      if (field === 2 && wire === 0) row.progress_ms = Number(value);
      if (field === 7 && wire === 2) row.content = decoder.decode(value);
      if (field === 8 && wire === 0) row.sent_at = Number(value);
    });
    return row;
  }
  const entries = [], segments = [];
  for (let index = 1; index <= total; index++) {
    const url = `https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid=${video.cid}&pid=${video.aid}&segment_index=${index}`;
    try {
      const response = await fetch(url, {credentials: 'include'});
      if (!response.ok) { segments.push({index, status: response.status, count: 0}); continue; }
      const bytes = new Uint8Array(await response.arrayBuffer());
      let count = 0;
      fields(bytes, (field, wire, value) => {
        if (field === 1 && wire === 2) { entries.push(element(value)); count++; }
      });
      segments.push({index, status: response.status, count});
    } catch (error) {
      segments.push({index, status: 0, count: 0, error: String(error).slice(0, 160)});
    }
  }
  return {bvid: video.bvid, cid: video.cid, aid: video.aid,
          duration_seconds: duration, page_count: Number(video.stat?.danmaku ?? 0),
          segments, entries};
})()'''


def utc_now() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="microseconds")


def connect(path: Path = DATABASE) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS danmaku (
            bvid TEXT NOT NULL,
            id TEXT NOT NULL,
            content TEXT NOT NULL,
            progress_ms INTEGER,
            sent_at INTEGER,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (bvid, id)
        );
        CREATE INDEX IF NOT EXISTS danmaku_sent ON danmaku (bvid, sent_at);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS video_runs (
            run_id TEXT NOT NULL,
            bvid TEXT NOT NULL,
            cid INTEGER,
            duration_seconds INTEGER,
            expected_segments INTEGER NOT NULL,
            successful_segments INTEGER NOT NULL,
            readable_count INTEGER NOT NULL,
            new_count INTEGER NOT NULL,
            page_count INTEGER,
            error TEXT,
            PRIMARY KEY (run_id, bvid)
        );
    """)
    return connection


def fetch_video(bvid: str) -> dict:
    session = f"danmaku-sync-{uuid.uuid4().hex[:8]}"
    try:
        run_opencli("browser", session, "open", f"https://www.bilibili.com/video/{bvid}", "--window", "background")
        result = run_opencli("browser", session, "eval", FETCH_EXPRESSION)
    finally:
        try:
            run_opencli("browser", session, "close")
        except Exception:
            pass
    if not isinstance(result, dict) or result.get("bvid") != bvid:
        raise RuntimeError(f"Unexpected danmaku response for {bvid}")
    return result


def save_video(connection: sqlite3.Connection, run_id: str, bvid: str, result: dict | None,
               error: str | None = None) -> dict:
    segments = result.get("segments", []) if result else []
    expected = int((result or {}).get("duration_seconds", 0) + 359) // 360 if result else 0
    successful = sum(item.get("status") == 200 for item in segments)
    entries = (result or {}).get("entries", [])
    seen: set[str] = set()
    rows = []
    for item in entries:
        id_value, content = item.get("id"), item.get("content")
        if not isinstance(id_value, str) or not id_value or not isinstance(content, str):
            continue
        if id_value in seen:
            continue
        seen.add(id_value)
        progress, sent_at = item.get("progress_ms"), item.get("sent_at")
        rows.append((bvid, id_value, content,
                     progress if isinstance(progress, int) and progress >= 0 else None,
                     sent_at if isinstance(sent_at, int) and sent_at > 0 else None, run_id))
    with connection:
        before = connection.total_changes
        connection.executemany(
            "INSERT OR IGNORE INTO danmaku (bvid,id,content,progress_ms,sent_at,first_seen_at) VALUES (?,?,?,?,?,?)",
            rows,
        )
        new_count = connection.total_changes - before
        connection.execute("""
            INSERT OR REPLACE INTO video_runs
            (run_id,bvid,cid,duration_seconds,expected_segments,successful_segments,
             readable_count,new_count,page_count,error) VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (run_id, bvid, (result or {}).get("cid"), (result or {}).get("duration_seconds"),
              expected, successful, len(rows), new_count, (result or {}).get("page_count"), error))
    return {"bvid": bvid, "expected_segments": expected, "successful_segments": successful,
            "readable_count": len(rows), "new_count": new_count, "error": error}


def collect_videos(videos: list[dict], path: Path = DATABASE) -> dict:
    """Collect a list of current videos. A failed video does not erase other results."""
    run_id = utc_now()
    results = []
    with closing(connect(path)) as connection:
        connection.execute("INSERT INTO runs (id,started_at,status) VALUES (?,?,?)", (run_id, run_id, "running"))
        connection.commit()
        for video in videos[:10]:
            bvid = str(video.get("bvid", ""))
            if not re.fullmatch(r"BV[A-Za-z0-9]+", bvid):
                continue
            try:
                result = fetch_video(bvid)
                report = save_video(connection, run_id, bvid, result)
            except Exception as exc:
                report = save_video(connection, run_id, bvid, None, f"{type(exc).__name__}: {exc}")
            results.append(report)
        complete = bool(results) and len(results) == min(len(videos), 10) and all(
            item["expected_segments"] > 0
            and item["expected_segments"] == item["successful_segments"]
            and not item["error"] for item in results
        )
        connection.execute("UPDATE runs SET finished_at=?,status=? WHERE id=?",
                           (utc_now(), "complete" if complete else "partial", run_id))
        connection.commit()
    return {"run_id": run_id, "status": "complete" if complete else "partial", "videos": results}


def normalized_text(content: str) -> str:
    text = unicodedata.normalize("NFKC", content).casefold()
    return "".join(char for char in text if not char.isspace()
                   and not unicodedata.category(char).startswith(("P", "S")))


def is_reaction(key: str) -> bool:
    return not key or len(key) == 1 or key.isdecimal() or bool(re.fullmatch(r"哈{2,}|呵{2,}|嘿{2,}|[w1]{2,}|6{2,}", key))


def hot_phrases(rows: list[dict], limit: int = 10) -> dict[str, list[dict]]:
    groups: dict[str, dict] = {}
    for row in rows:
        content = row["content"].strip()
        key = normalized_text(content)
        if not key:
            continue
        group = groups.setdefault(key, {"count": 0, "variants": Counter(), "minutes": Counter()})
        group["count"] += 1
        group["variants"][content] += 1
        if row["progress_ms"] is not None:
            group["minutes"][row["progress_ms"] // 60000] += 1
    ranked = {"phrases": [], "reactions": []}
    for key, group in groups.items():
        content, exact = group["variants"].most_common(1)[0]
        peak = group["minutes"].most_common(1)
        bucket = "reactions" if is_reaction(key) else "phrases"
        ranked[bucket].append({"content": content, "count": group["count"],
                               "exact_count": exact, "variants": len(group["variants"]),
                               "peak_minute": peak[0][0] if peak else None,
                               "peak_count": peak[0][1] if peak else 0})
    for bucket in ranked:
        ranked[bucket].sort(key=lambda item: (-item["count"], -item["exact_count"], item["content"]))
        ranked[bucket] = ranked[bucket][:limit]
    return ranked


def _latest_runs(connection: sqlite3.Connection, bvid: str, complete_only: bool = False) -> list[sqlite3.Row]:
    condition = "AND v.expected_segments > 0 AND v.successful_segments = v.expected_segments" if complete_only else ""
    return connection.execute(f"""
        SELECT r.started_at, v.* FROM video_runs v JOIN runs r ON r.id=v.run_id
        WHERE v.bvid=? {condition} ORDER BY r.started_at DESC LIMIT 2
    """, (bvid,)).fetchall()


def _count_buckets(rows: list[dict], duration: int, bins: int = 50) -> list[int]:
    result = [0] * bins
    if duration <= 0:
        return result
    for row in rows:
        progress = row["progress_ms"]
        if progress is None or progress < 0:
            continue
        index = min(bins - 1, int(progress / (duration * 1000) * bins))
        result[index] += 1
    return result


def _hotspots(rows: list[dict], duration: int) -> list[dict]:
    minutes = Counter(row["progress_ms"] // 60000 for row in rows
                      if row["progress_ms"] is not None and row["progress_ms"] >= 0)
    selected = []
    for minute, count in minutes.most_common():
        if all(abs(minute - other["minute"]) > 1 for other in selected):
            selected.append({"minute": minute, "count": count,
                             "share": round(count / len(rows) * 100, 1) if rows else 0})
        if len(selected) == 3:
            break
    return selected


def summary(videos: list[dict], window: str = "24h", bvid: str | None = None,
            path: Path = DATABASE, now: dt.datetime | None = None) -> dict:
    """Return a small aggregate payload; full raw text stays in local SQLite."""
    if window not in ("24h", "latest"):
        raise ValueError("Unknown window")
    present = [str(video["bvid"]) for video in videos[:10] if video.get("bvid")]
    selected = bvid if bvid in present else (present[0] if present else None)
    current_time = now or dt.datetime.now(UTC)
    cutoff = int((current_time - dt.timedelta(hours=24)).timestamp())
    cards = []
    details = None
    shared: dict[str, dict] = {}
    with closing(connect(path)) as connection:
        for video in videos[:10]:
            video_id = str(video.get("bvid", ""))
            runs = _latest_runs(connection, video_id)
            run = runs[0] if runs else None
            complete_runs = _latest_runs(connection, video_id, True)
            if window == "latest":
                cutoff_video = (int(dt.datetime.fromisoformat(complete_runs[1]["started_at"]).timestamp())
                                if len(complete_runs) > 1 else None)
            else:
                cutoff_video = cutoff
            records = [dict(row) for row in connection.execute(
                "SELECT id,content,progress_ms,sent_at,first_seen_at FROM danmaku WHERE bvid=?",
                (video_id,),
            )]
            scoped = [row for row in records if cutoff_video is not None and row["sent_at"] is not None
                      and row["sent_at"] >= cutoff_video]
            for row in records:
                key = normalized_text(row["content"])
                if not key or is_reaction(key):
                    continue
                group = shared.setdefault(key, {"variants": Counter(), "videos": Counter()})
                group["variants"][row["content"].strip()] += 1
                group["videos"][video_id] += 1
            duration = int(run["duration_seconds"] or 0) if run else 0
            card = {"bvid": video_id, "title": video.get("title"), "cover": video.get("cover"),
                    "duration_seconds": duration, "stored_count": len(records),
                    "window_count": len(scoped) if cutoff_video is not None else None,
                    "page_count": run["page_count"] if run else None,
                    "readable_count": run["readable_count"] if run else None,
                    "successful_segments": run["successful_segments"] if run else 0,
                    "expected_segments": run["expected_segments"] if run else 0,
                    "latest_collected_at": run["started_at"] if run else None,
                    "latest_new_count": run["new_count"] if run else None,
                    "latest_error": run["error"] if run else None,
                    "has_baseline": len(complete_runs) >= 2,
                    "heatmap_all": _count_buckets(records, duration),
                    "heatmap_window": _count_buckets(scoped, duration)}
            cards.append(card)
            if video_id == selected:
                hourly = Counter()
                for row in scoped:
                    hourly[(row["sent_at"] // 3600) * 3600] += 1
                hotspots = _hotspots(records, duration)
                for hotspot in hotspots:
                    local = [row for row in records if row["progress_ms"] is not None
                             and row["progress_ms"] // 60000 == hotspot["minute"]]
                    hotspot["phrases"] = hot_phrases(local, 2)["phrases"]
                details = {"bvid": video_id, "hot_all": hot_phrases(records),
                           "hot_window": hot_phrases(scoped),
                           "hotspots": hotspots, "window_start": cutoff_video,
                           "timeline": [{"hour": key, "count": hourly[key]} for key in sorted(hourly)],
                           "density_all": _count_buckets(records, duration),
                           "density_window": _count_buckets(scoped, duration)}
    common = []
    for group in shared.values():
        if len(group["videos"]) < 2 or sum(group["videos"].values()) < 10:
            continue
        common.append({"content": group["variants"].most_common(1)[0][0],
                       "count": sum(group["videos"].values()),
                       "video_count": len(group["videos"])})
    common.sort(key=lambda item: (-item["video_count"], -item["count"]))
    return {"window": window, "generated_at": current_time.isoformat(timespec="seconds"),
            "videos": cards, "selected": details, "shared_phrases": common[:8]}


def search(bvid: str, query: str, path: Path = DATABASE, limit: int = 50) -> list[dict]:
    if not re.fullmatch(r"BV[A-Za-z0-9]+", bvid) or not query.strip():
        return []
    with closing(connect(path)) as connection:
        # Escaped LIKE keeps a user's percent/underscore as literal text.
        pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = connection.execute("""
            SELECT content,progress_ms,sent_at FROM danmaku
            WHERE bvid=? AND content LIKE ? ESCAPE '\\'
            ORDER BY sent_at DESC LIMIT ?
        """, (bvid, pattern, min(limit, 100))).fetchall()
    return [dict(row) for row in rows]
