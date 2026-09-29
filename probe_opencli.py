"""One-shot Bilibili page sampling through the user's OpenCLI Chrome bridge.

This probe reads one creator page, a short video list, and a few video details.
It does not schedule requests, handle login, or bypass access restrictions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


UID = "3546619609876957"
PROFILE_URL = f"https://space.bilibili.com/{UID}"
PROFILE_EXPRESSION = r'''(() => {
  const label = [...document.querySelectorAll('span')]
    .find(node => node.textContent.trim() === '粉丝数');
  const count = label?.parentElement?.querySelector('.nav-statistics__item-num');
  return {
    url: location.href,
    title: document.title,
    followers_exact: count?.getAttribute('title') ?? null,
    followers_display: count?.textContent.trim() ?? null
  };
})()'''


def run_opencli(*arguments: str) -> object:
    executable = shutil.which("opencli.cmd" if sys.platform == "win32" else "opencli")
    if executable is None:
        raise RuntimeError("OpenCLI is not installed or not on PATH")
    command = [executable, *arguments]
    if sys.platform == "win32":
        # Calling the npm .cmd shim mangles JS expression arguments through cmd.exe.
        node = shutil.which("node")
        entry = Path(executable).parent / "node_modules/@jackwener/opencli/dist/src/main.js"
        if node is None or not entry.is_file():
            raise RuntimeError("OpenCLI's Node entry point is unavailable")
        command = [node, str(entry), *arguments]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=35,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"OpenCLI {' '.join(arguments[:3])} failed: "
            f"{(completed.stderr or completed.stdout).strip()[:500]}"
        )
    output = completed.stdout.strip()
    try:
        result, _ = json.JSONDecoder().raw_decode(output)
        return result
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenCLI returned unexpected output: {output[:500]}") from exc


def sample(limit: int) -> dict[str, object]:
    session = f"bili-probe-{uuid.uuid4().hex[:8]}"
    try:
        run_opencli("browser", session, "open", PROFILE_URL, "--window", "background")
        profile = run_opencli("browser", session, "eval", PROFILE_EXPRESSION)
    finally:
        try:
            run_opencli("browser", session, "close")
        except (RuntimeError, subprocess.TimeoutExpired):
            pass

    if not isinstance(profile, dict) or not profile.get("followers_exact"):
        raise RuntimeError("The creator page did not expose an exact follower count")
    profile["followers_exact"] = int(str(profile["followers_exact"]).replace(",", ""))

    videos = run_opencli(
        "bilibili", "user-videos", UID,
        "--limit", str(limit), "--page", "1", "-f", "json", "--window", "background",
    )
    if not isinstance(videos, list) or not videos:
        raise RuntimeError("OpenCLI returned no creator videos")

    details = []
    for item in videos:
        match = re.search(r"/video/(BV[\w]+)", str(item.get("url", "")))
        if not match:
            raise RuntimeError(f"Video has no BVID: {item!r}")
        rows = run_opencli(
            "bilibili", "video", match.group(1), "-f", "json", "--window", "background"
        )
        if not isinstance(rows, list):
            raise RuntimeError(f"Video detail has unexpected shape: {match.group(1)}")
        detail = {str(row["field"]): row.get("value") for row in rows}
        if UID not in str(detail.get("author", "")):
            raise RuntimeError(f"Video does not belong to target UID: {match.group(1)}")
        details.append(detail)

    return {
        "sampled_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "OpenCLI connected Chrome browser",
        "creator_uid": UID,
        "profile": profile,
        "videos": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 11))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = sample(args.limit)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    # ASCII escapes keep output readable in Windows terminals with legacy encodings.
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
