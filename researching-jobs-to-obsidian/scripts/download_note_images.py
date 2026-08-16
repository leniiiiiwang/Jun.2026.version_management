"""Download evidence images only from selected saved note-detail records."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import re
import tempfile
from collections.abc import Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


NOTE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
RESERVED_IDS = {"run_summary", "download_manifest"}


def _safe_note_id(value):
    if (
        not isinstance(value, str)
        or not NOTE_ID_PATTERN.fullmatch(value)
        or value in {".", ".."}
        or value.casefold() in RESERVED_IDS
    ):
        raise ValueError("note ID must be a safe filename component")
    return value


def _safe_http_url(value):
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def _detail_error(path):
    return ValueError(f"malformed detail file: {Path(path).name}")


def collect_jobs(details_dir: Path, selected_ids: set[str]) -> list[dict]:
    """Collect ordered, unique http(s) image URLs from selected successful details."""
    selected_ids = set(selected_ids)
    seen_ids = set()
    for note_id in selected_ids:
        safe_id = _safe_note_id(note_id)
        folded = safe_id.casefold()
        if folded in seen_ids:
            raise ValueError("selected note IDs must not have case collisions")
        seen_ids.add(folded)

    directory = Path(details_dir)
    if not directory.is_dir():
        raise ValueError("details directory must be a directory")
    jobs = []
    seen_urls = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "run_summary.json":
            continue
        try:
            path_key = _safe_note_id(path.stem)
        except ValueError as exc:
            raise _detail_error(path) from exc
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _detail_error(path) from exc
        if not isinstance(record, Mapping):
            raise _detail_error(path)
        try:
            record_key = _safe_note_id(record.get("key"))
        except ValueError as exc:
            raise _detail_error(path) from exc
        if record_key != path_key or record.get("tool") != "get_note_detail":
            raise _detail_error(path)
        arguments = record.get("arguments")
        if not isinstance(arguments, Mapping):
            raise _detail_error(path)
        try:
            argument_note_id = _safe_note_id(arguments.get("note_id"))
        except ValueError as exc:
            raise _detail_error(path) from exc
        envelope = record.get("envelope")
        if not isinstance(envelope, Mapping) or type(envelope.get("ok")) is not bool:
            raise _detail_error(path)
        if not envelope["ok"]:
            continue
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise _detail_error(path)
        if "id" in data:
            try:
                note_id = _safe_note_id(data["id"])
            except ValueError as exc:
                raise _detail_error(path) from exc
            if note_id != argument_note_id:
                raise _detail_error(path)
        else:
            note_id = argument_note_id
        if note_id not in selected_ids and record_key not in selected_ids:
            continue
        image_urls = data.get("image_urls")
        if not isinstance(image_urls, list):
            continue
        note_seen = seen_urls.setdefault(note_id, set())
        for source_url in image_urls:
            if not _safe_http_url(source_url) or source_url in note_seen:
                continue
            note_seen.add(source_url)
            jobs.append({"note_id": note_id, "source_url": source_url})
    return jobs


def _prepare_output_dir(output_dir):
    path = Path(output_dir)
    descriptor = None
    probe = None
    try:
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_dir():
                raise ValueError("output directory must be a real directory")
            if any(path.iterdir()):
                raise ValueError("output directory must be new or empty")
        else:
            path.mkdir(parents=True)
        path = path.resolve(strict=True)
        descriptor, probe = tempfile.mkstemp(prefix=".download-probe.", suffix=".tmp", dir=path)
        os.close(descriptor)
        descriptor = None
    except OSError as exc:
        raise ValueError("output directory is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if probe is not None and os.path.exists(probe):
            os.unlink(probe)
    return path


def _write_bytes_atomically(path, content, *, no_overwrite=False):
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if no_overwrite:
            if path.exists() or path.is_symlink():
                raise FileExistsError("output file already exists")
            os.link(temporary, path)
            os.unlink(temporary)
            temporary = None
        else:
            os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _write_json_atomically(path, value, *, no_overwrite=False):
    _write_bytes_atomically(
        path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), no_overwrite=no_overwrite,
    )


def _extension(source_url, content_type):
    suffix = Path(urlsplit(source_url).path).suffix.lower()
    if suffix in SAFE_EXTENSIONS:
        return suffix
    media_type = content_type.split(";", 1)[0].strip().lower() if isinstance(content_type, str) else ""
    return CONTENT_TYPE_EXTENSIONS.get(media_type, ".jpg")


def _validated_jobs(jobs):
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list")
    validated = []
    seen_outputs = {"download_manifest.json".casefold()}
    note_counts = {}
    casefolded_notes = {}
    for job in jobs:
        if not isinstance(job, Mapping):
            raise ValueError("each job must be an object")
        note_id = _safe_note_id(job.get("note_id"))
        folded_note_id = note_id.casefold()
        previous_note_id = casefolded_notes.setdefault(folded_note_id, note_id)
        if previous_note_id != note_id:
            raise ValueError("job note IDs must not have case collisions")
        source_url = job.get("source_url")
        if not _safe_http_url(source_url):
            raise ValueError("job URL must use http or https")
        index = note_counts.get(note_id, 0) + 1
        note_counts[note_id] = index
        filename = f"{note_id}-{index:02d}{_extension(source_url, '')}"
        folded = filename.casefold()
        if folded in seen_outputs:
            raise ValueError("job output filenames must not collide")
        seen_outputs.add(folded)
        validated.append((note_id, source_url, index))
    return validated


async def default_fetcher(url):
    """Fetch a single image with a bounded timeout from a direct image URL."""
    def fetch():
        request = Request(url, headers={"User-Agent": "selected-note-image-downloader/1.0"})
        with urlopen(request, timeout=20) as response:  # nosec B310 - URL is allowlisted by caller
            return response.read(), response.headers.get("Content-Type", "")
    return await asyncio.to_thread(fetch)


def _failure_error(category):
    return f"{category}: image download failed"


async def download_jobs(jobs, output_dir, delay_seconds, fetcher, sleeper=asyncio.sleep) -> dict:
    """Download prepared image jobs once each and atomically persist a manifest."""
    if not isinstance(delay_seconds, (int, float)) or isinstance(delay_seconds, bool) or not math.isfinite(delay_seconds) or delay_seconds < 0:
        raise ValueError("delay_seconds must be finite and non-negative")
    validated = _validated_jobs(jobs)
    output_dir = _prepare_output_dir(output_dir)
    summary = {"total": len(validated), "attempted": 0, "succeeded": 0, "failed": 0, "records": []}
    for position, (note_id, source_url, index) in enumerate(validated):
        summary["attempted"] += 1
        try:
            content, content_type = await fetcher(source_url)
            if not isinstance(content, bytes):
                raise TypeError("fetcher returned non-bytes")
            filename = f"{note_id}-{index:02d}{_extension(source_url, content_type)}"
            target = output_dir / filename
            if target.exists() or target.is_symlink():
                raise FileExistsError("output file already exists")
            _write_bytes_atomically(target, content, no_overwrite=True)
        except Exception:
            summary["failed"] += 1
            summary["records"].append({
                "note_id": note_id, "source_url": source_url, "output_file": None,
                "status": "failed", "error": _failure_error("fetch_or_write_failed"),
            })
        else:
            summary["succeeded"] += 1
            summary["records"].append({
                "note_id": note_id, "source_url": source_url, "output_file": filename,
                "status": "succeeded", "error": None,
            })
        if position < len(validated) - 1:
            await sleeper(delay_seconds)
    _write_json_atomically(output_dir / "download_manifest.json", summary, no_overwrite=True)
    return summary


def load_selected_ids(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("selected IDs file must be readable UTF-8 text") from exc
    selected = set()
    folded = set()
    for raw in lines:
        note_id = raw.strip()
        if not note_id:
            continue
        _safe_note_id(note_id)
        key = note_id.casefold()
        if note_id in selected or key in folded:
            raise ValueError("selected note IDs must be unique without case collisions")
        selected.add(note_id)
        folded.add(key)
    return selected


def _parse_delay(value):
    try:
        delay = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("delay must be a finite number of at least 2 seconds") from exc
    if not math.isfinite(delay) or delay < 2:
        raise ValueError("delay must be a finite number of at least 2 seconds")
    return delay


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details-dir", required=True)
    parser.add_argument("--selected-ids", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--delay", default=2)
    args = parser.parse_args(argv)
    delay = _parse_delay(args.delay)
    selected_ids = load_selected_ids(args.selected_ids)
    jobs = collect_jobs(Path(args.details_dir), selected_ids)
    summary = asyncio.run(download_jobs(jobs, Path(args.output_dir), delay, default_fetcher))
    print(f"allowlisted_notes={len(selected_ids)} image_jobs={len(jobs)} succeeded={summary['succeeded']} failed={summary['failed']}")
    return summary


if __name__ == "__main__":
    main()
