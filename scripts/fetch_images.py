#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from rich import print

MANIFEST_NAME = "manifest.jsonl"
USER_AGENT = "sf311-labeler/0.1 (https://example.com)"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Download and cache SF311 report images")
    ap.add_argument("--input", required=True, help="Transformed JSONL file that includes image URLs")
    ap.add_argument("--out-dir", default="data/images", help="Where to store cached images")
    ap.add_argument(
        "--manifest",
        default=None,
        help="Manifest path (defaults to <out_dir>/manifest.jsonl)",
    )
    ap.add_argument("--max-workers", type=int, default=8, help="Concurrent download workers")
    ap.add_argument(
        "--rewrite",
        action="store_true",
        help="Force re-download even if manifest entry exists and file is present",
    )
    return ap.parse_args()


def read_jsonl(path: Path) -> Iterable[Dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            yield json.loads(s)


def load_manifest(path: Path) -> Dict[str, Dict]:
    if not path.exists():
        return {}
    records: Dict[str, Dict] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                continue
            url = rec.get("url")
            if not url:
                continue
            records[url] = rec
    return records


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def filename_for(url: str, index: int) -> str:
    parsed = urlparse(url)
    name = os.path.basename(parsed.path.rstrip("/"))
    if not name:
        name = f"image_{index:02d}.jpg"
    return f"{index:02d}_{name}"


def fetch_image(url: str) -> Tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    return data, digest


def write_manifest_entry(path: Path, entry: Dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest) if args.manifest else out_dir / MANIFEST_NAME

    ensure_dir(out_dir)
    seen_manifest = load_manifest(manifest_path)

    jobs: List[Tuple[str, str, str]] = []  # (request_id, url, local_path)
    for row in read_jsonl(input_path):
        request_id = row.get("request_id")
        urls = row.get("image_urls") or []
        if not request_id or not isinstance(urls, list):
            continue
        target_dir = out_dir / str(request_id)
        ensure_dir(target_dir)
        for idx, url in enumerate(urls):
            if not isinstance(url, str) or not url.strip():
                continue
            url = url.strip()
            filename = filename_for(url, idx)
            local_path = target_dir / filename
            manifest_entry = seen_manifest.get(url)
            if (
                manifest_entry
                and not args.rewrite
                and manifest_entry.get("status") == "ok"
                and Path(manifest_entry.get("local_path", "")).exists()
            ):
                # Ensure path matches; if moved, fall back to download
                if Path(manifest_entry["local_path"]) != local_path:
                    manifest_entry = None
                else:
                    continue
            jobs.append((request_id, url, str(local_path)))

    if not jobs:
        print("[green][ok][/green] No new images to fetch.")
        return

    print(f"[bold]Fetching {len(jobs)} image(s)...[/bold]")
    manifest_entries: List[Dict] = []

    def worker(request_id: str, url: str, local_path: str) -> Dict:
        started = datetime.utcnow().isoformat()
        entry: Dict[str, str] = {
            "request_id": request_id,
            "url": url,
            "local_path": local_path,
            "fetched_at": started,
        }
        try:
            data, digest = fetch_image(url)
            Path(local_path).write_bytes(data)
            entry["status"] = "ok"
            entry["sha256"] = digest
        except Exception as exc:  # noqa: BLE001
            entry["status"] = "error"
            entry["error"] = str(exc)
        return entry

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_map = {executor.submit(worker, *job): job for job in jobs}
        for future in as_completed(future_map):
            entry = future.result()
            manifest_entries.append(entry)
            if entry.get("status") == "ok":
                print(f"[green]saved[/green] {entry['local_path']}")
            else:
                print(f"[red]failed[/red] {entry['url']} → {entry.get('error')}")

    for entry in manifest_entries:
        write_manifest_entry(manifest_path, entry)

    ok_count = sum(1 for e in manifest_entries if e.get("status") == "ok")
    err_count = len(manifest_entries) - ok_count
    print(f"[green][ok][/green] Downloads complete: {ok_count} ok, {err_count} failed")


if __name__ == "__main__":
    main()
