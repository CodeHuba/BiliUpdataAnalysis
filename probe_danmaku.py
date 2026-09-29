"""One-shot danmaku feasibility probe through the connected Chrome page.

It records counts and a few examples, not a reusable full-text archive.
The probe does not read browser cookies or bypass access restrictions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from pathlib import Path

from probe_opencli import run_opencli


ROOT = Path(__file__).resolve().parent
EXPRESSION = r'''(async () => {
  const video = window.__INITIAL_STATE__?.videoData;
  if (!video?.cid || !video?.aid) throw new Error('Video page did not expose cid and aid');
  const cid = video.cid, aid = video.aid;
  const duration = Number(video.duration || 0);
  const segmentCount = Math.ceil(duration / 360);
  if (!segmentCount || segmentCount > 100) throw new Error('Unexpected video duration');

  const xmlResponse = await fetch(`https://api.bilibili.com/x/v1/dm/list.so?oid=${cid}`, {credentials: 'include'});
  const xmlText = await xmlResponse.text();
  const xml = new DOMParser().parseFromString(xmlText, 'text/xml');
  const xmlRows = [...xml.querySelectorAll('d')];

  function varint(bytes, start) {
    let value = 0n, shift = 0n, position = start;
    for (let count = 0; count < 10; count++) {
      if (position >= bytes.length) throw new Error('Truncated protobuf varint');
      const byte = bytes[position++];
      value |= BigInt(byte & 127) << shift;
      if (!(byte & 128)) return [value, position];
      shift += 7n;
    }
    throw new Error('Protobuf varint is too long');
  }
  function fields(bytes, callback) {
    let position = 0;
    while (position < bytes.length) {
      const [key, next] = varint(bytes, position);
      position = next;
      const field = Number(key >> 3n), wire = Number(key & 7n);
      if (wire === 0) {
        const [value, end] = varint(bytes, position);
        callback(field, wire, value);
        position = end;
      } else if (wire === 2) {
        const [length, end] = varint(bytes, position);
        position = end;
        const size = Number(length);
        if (size < 0 || position + size > bytes.length) throw new Error('Invalid protobuf field length');
        callback(field, wire, bytes.subarray(position, position + size));
        position += size;
      } else if (wire === 1 || wire === 5) {
        position += wire === 1 ? 8 : 4;
        if (position > bytes.length) throw new Error('Truncated protobuf field');
      } else {
        throw new Error(`Unsupported protobuf wire type: ${wire}`);
      }
    }
  }
  const decoder = new TextDecoder('utf-8');
  function parseElement(bytes) {
    const entry = {id: null, progress_ms: null, content: null, sent_at: null};
    fields(bytes, (field, wire, value) => {
      if (field === 1 && wire === 0) entry.id = value.toString();
      if (field === 2 && wire === 0) entry.progress_ms = Number(value);
      if (field === 7 && wire === 2) entry.content = decoder.decode(value);
      if (field === 8 && wire === 0) entry.sent_at = Number(value);
    });
    return entry;
  }

  const segmentResults = [], all = [];
  for (let index = 1; index <= segmentCount; index++) {
    const url = `https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid=${cid}&pid=${aid}&segment_index=${index}`;
    const response = await fetch(url, {credentials: 'include'});
    const bytes = new Uint8Array(await response.arrayBuffer());
    const entries = [];
    if (response.ok) fields(bytes, (field, wire, value) => {
      if (field === 1 && wire === 2) entries.push(parseElement(value));
    });
    segmentResults.push({index, status: response.status, bytes: bytes.length, count: entries.length});
    all.push(...entries);
  }
  const uniqueIds = new Set(all.map(item => item.id).filter(Boolean));
  const progress = all.map(item => item.progress_ms).filter(Number.isFinite);
  const sendTimes = all.map(item => item.sent_at).filter(Number.isFinite);
  const repeated = new Map();
  for (const item of all) {
    const content = item.content?.trim();
    if (!content) continue;
    // Fold width, case, punctuation and spaces; preserve characters and meaning.
    const key = content.normalize('NFKC').toLowerCase().replace(/[\p{P}\p{S}\s]/gu, '');
    if (!key) continue;
    let group = repeated.get(key);
    if (!group) {
      group = {count: 0, variants: new Map(), positions: []};
      repeated.set(key, group);
    }
    group.count++;
    group.variants.set(content, (group.variants.get(content) || 0) + 1);
    if (Number.isFinite(item.progress_ms)) group.positions.push(item.progress_ms);
  }
  const hot = [...repeated.entries()].map(([key, group]) => {
    const [content, exact_count] = [...group.variants.entries()].sort((a, b) => b[1] - a[1])[0];
    const bucket = new Map();
    for (const ms of group.positions) {
      const minute = Math.floor(ms / 60000);
      bucket.set(minute, (bucket.get(minute) || 0) + 1);
    }
    const [peak_minute, peak_count] = [...bucket.entries()].sort((a, b) => b[1] - a[1])[0] || [null, 0];
    return {content: content.slice(0, 120), count: group.count, exact_count,
            variants: group.variants.size, peak_minute, peak_count, key_length: key.length,
            numeric_only: /^\d+$/.test(key)};
  }).sort((a, b) => b.count - a.count || b.exact_count - a.exact_count);
  const topMeaningful = hot.filter(item => !item.numeric_only && item.key_length >= 2);
  return {
    bvid: video.bvid,
    cid, aid, duration_seconds: duration,
    page_danmaku_count: Number(video.stat?.danmaku ?? 0),
    xml: {status: xmlResponse.status, count: xmlRows.length,
          maxlimit: xml.querySelector('maxlimit')?.textContent ?? null,
          parse_error: Boolean(xml.querySelector('parsererror'))},
    segments: segmentResults,
    segment_total: all.length,
    unique_ids: uniqueIds.size,
    with_text: all.filter(item => item.content).length,
    with_progress: progress.length,
    with_send_time: sendTimes.length,
    min_progress_ms: progress.length ? Math.min(...progress) : null,
    max_progress_ms: progress.length ? Math.max(...progress) : null,
    min_send_time: sendTimes.length ? Math.min(...sendTimes) : null,
    max_send_time: sendTimes.length ? Math.max(...sendTimes) : null,
    top_repeated: hot.slice(0, 20),
    top_repeated_non_numeric: topMeaningful.slice(0, 20),
    examples: all.filter(item => item.content).slice(0, 5)
      .map(item => ({progress_ms: item.progress_ms, content: item.content.slice(0, 120)}))
  };
})()'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bvid", nargs="?", default="BV1gqai6iEeQ")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    session = f"danmaku-probe-{uuid.uuid4().hex[:8]}"
    try:
        run_opencli("browser", session, "open", f"https://www.bilibili.com/video/{args.bvid}", "--window", "background")
        result = run_opencli("browser", session, "eval", EXPRESSION)
    finally:
        try:
            run_opencli("browser", session, "close")
        except Exception:
            pass
    if not isinstance(result, dict) or result.get("bvid") != args.bvid:
        raise RuntimeError("Unexpected danmaku probe result")
    result["sampled_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    result["source"] = "OpenCLI connected Chrome video page"
    output = args.output or ROOT / "samples" / f"{result['sampled_at_utc'][:10]}-danmaku-probe.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **result}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
