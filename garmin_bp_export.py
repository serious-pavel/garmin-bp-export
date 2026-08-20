#!/usr/bin/env python3
"""
Export blood pressure readings from Garmin Connect to CSV.

The script uses the `garminconnect` package and tries the known blood-pressure
API method names in a defensive order so it remains usable across minor library
version differences.
"""

from __future__ import annotations

import argparse
import csv
import json
import getpass
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional fallback
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


@dataclass(frozen=True)
class Reading:
    timestamp: datetime
    systolic: Any
    diastolic: Any
    comment: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Garmin Connect blood pressure readings to CSV."
    )
    parser.add_argument(
        "--email",
        default=os.getenv("GARMIN_EMAIL"),
        help="Garmin Connect email address. Defaults to GARMIN_EMAIL.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("GARMIN_PASSWORD"),
        help="Garmin Connect password. Defaults to GARMIN_PASSWORD.",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="End date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="Output CSV path. Use - for stdout.",
    )
    parser.add_argument(
        "--output-dir",
        default="files",
        help="Directory for CSV output when --output is not an explicit path.",
    )
    parser.add_argument(
        "--token-cache",
        default=str(Path.home() / ".garminconnect"),
        help="Path for persisted Garmin tokens.",
    )
    parser.add_argument(
        "--default-comment",
        default="",
        help="Fallback comment value when Garmin does not provide one.",
    )
    parser.add_argument(
        "--debug-json",
        action="store_true",
        help="Write the raw Garmin response to a JSON file next to the CSV output.",
    )
    return parser.parse_args()


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def prompt_credentials(args: argparse.Namespace) -> tuple[str, str]:
    email = args.email or input("Garmin email: ").strip()
    password = args.password or getpass.getpass("Garmin password: ")
    if not email:
        raise SystemExit("missing Garmin email")
    if not password:
        raise SystemExit("missing Garmin password")
    return email, password


def local_timezone():
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=local_timezone())
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=local_timezone())
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1_000_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        seconds = int(text) / 1000.0 if len(text) > 10 else int(text)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=local_timezone())


def as_rows(payload: Any, default_comment: str) -> list[Reading]:
    records: list[Any]
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = extract_records(payload)
    else:
        records = []

    rows: list[Reading] = []
    for item in records:
        if not isinstance(item, dict):
            continue

        timestamp = first_timestamp(item)
        if timestamp is None:
            continue

        systolic = first_nested_value(
            item,
            "systolic",
            "sys",
            "systolicValue",
            "systolicPressure",
            "high",
            "upper",
            "sbp",
        )
        diastolic = first_nested_value(
            item,
            "diastolic",
            "dia",
            "diastolicValue",
            "diastolicPressure",
            "low",
            "lower",
            "dbp",
        )
        comment = first_nested_text(
            item,
            "comment",
            "notes",
            "note",
            "userComment",
            "measurementComment",
            "text",
        ) or default_comment

        rows.append(
            Reading(
                timestamp=timestamp,
                systolic=systolic,
                diastolic=diastolic,
                comment=comment,
            )
        )

    rows.sort(key=lambda row: row.timestamp)
    return rows


def extract_records(payload: dict[str, Any]) -> list[Any]:
    records: list[Any] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if "measurementTimestampLocal" in value or "systolic" in value or "diastolic" in value:
                records.append(value)
                return

            if "measurementSummaries" in value:
                summaries = value.get("measurementSummaries")
                if isinstance(summaries, list):
                    for summary in summaries:
                        if isinstance(summary, dict):
                            collect(summary.get("measurements"))
                return

            for key in (
                "measurements",
                "items",
                "data",
                "readings",
                "bloodPressure",
                "bloodPressureMeasurements",
                "result",
                "results",
            ):
                nested = value.get(key)
                if nested is not None:
                    collect(nested)

            for nested in value.values():
                collect(nested)
            return

        if isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    return records


def first_timestamp(item: dict[str, Any]) -> datetime | None:
    timestamp_keys = (
        "measurementTimestampLocal",
        "measurementTimestampGMT",
        "timestamp",
        "dateTime",
        "datetime",
        "measurementDate",
        "calendarDate",
        "date",
        "recordTime",
        "createdDate",
        "created",
    )
    timestamp_hints = ("date", "time", "timestamp", "calendar", "record", "created")
    return first_nested_timestamp(item, timestamp_keys, timestamp_hints)


def first_nested_timestamp(
    value: Any, timestamp_keys: tuple[str, ...], timestamp_hints: tuple[str, ...]
) -> datetime | None:
    if isinstance(value, dict):
        for key in timestamp_keys:
            raw = value.get(key)
            if raw not in (None, ""):
                parsed = parse_timestamp(raw)
                if parsed is not None:
                    return parsed

        for key, raw in value.items():
            key_lower = key.lower()
            if any(hint in key_lower for hint in timestamp_hints):
                parsed = parse_timestamp(raw)
                if parsed is not None:
                    return parsed

        for raw in value.values():
            parsed = first_nested_timestamp(raw, timestamp_keys, timestamp_hints)
            if parsed is not None:
                return parsed
        return None

    if isinstance(value, list):
        for raw in value:
            parsed = first_nested_timestamp(raw, timestamp_keys, timestamp_hints)
            if parsed is not None:
                return parsed
        return None

    return parse_timestamp(value)


def first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return ""


def first_nested_value(item: Any, *keys: str) -> Any:
    if not keys:
        return ""
    key_set = {key.lower() for key in keys}
    found = walk_for_keys(item, key_set)
    return normalize_scalar(found)


def first_nested_text(item: Any, *keys: str) -> str:
    value = first_nested_value(item, *keys)
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def first_text(item: dict[str, Any], *keys: str) -> str:
    value = first_value(item, *keys)
    if isinstance(value, str):
        return value.strip()
    if value in (None, ""):
        return ""
    return str(value)


def walk_for_keys(value: Any, key_set: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in key_set and item not in (None, ""):
                return item
        for item in value.values():
            found = walk_for_keys(item, key_set)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = walk_for_keys(item, key_set)
            if found not in (None, ""):
                return found
    return ""


def normalize_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "text", "amount", "reading", "number", "val"):
            nested = value.get(key)
            if nested not in (None, ""):
                return nested
        for nested in value.values():
            scalar = normalize_scalar(nested)
            if scalar not in (None, ""):
                return scalar
        return ""
    if isinstance(value, list):
        for nested in value:
            scalar = normalize_scalar(nested)
            if scalar not in (None, ""):
                return scalar
        return ""
    return value


def target_range(start_value: str, end_value: str) -> tuple[date, date]:
    start = parse_date(start_value)
    end = parse_date(end_value)
    if end < start:
        raise argparse.ArgumentTypeError("--end must be on or after --start")
    return start, end


def coerce_output_handle(path: str):
    if path == "-":
        return sys.stdout
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path.open("w", newline="", encoding="utf-8")


def resolve_output_path(output: str, output_dir: str) -> str:
    if output == "-":
        return output

    output_path = Path(output)
    if output_path.parent == Path("."):
        output_path = Path(output_dir) / output_path.name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return str(output_path)


def debug_json_path(output_path: str) -> Path:
    if output_path == "-":
        raise SystemExit("--debug-json requires --output to be a file path")
    return Path(output_path).with_suffix(".json")


def write_debug_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def format_timestamp(value: datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")


def format_date_for_api(value: date) -> str:
    return value.isoformat()


def invoke_candidate(method: Any, start: date, end: date) -> Any:
    start_date = format_date_for_api(start)
    end_date = format_date_for_api(end)
    attempts = (
        lambda: method(start_date, end_date),
        lambda: method(start=start_date, end=end_date),
        lambda: method(start_date=start_date, end_date=end_date),
        lambda: method(startDate=start_date, endDate=end_date),
        lambda: method(),
    )
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc
            continue
    raise RuntimeError("unable to call Garmin blood-pressure method") from last_error


def fetch_blood_pressure(client: Any, start: date, end: date) -> Any:
    for name in (
        "get_blood_pressure",
        "get_blood_pressure_data",
        "get_blood_pressure_measurements",
        "get_bp_data",
    ):
        method = getattr(client, name, None)
        if callable(method):
            return invoke_candidate(method, start, end)
    raise AttributeError(
        "This garminconnect version does not expose a blood-pressure method."
    )


def login(client: Any, token_cache: str) -> None:
    from garminconnect.exceptions import GarminConnectAuthenticationError

    cache_path = Path(token_cache)
    try:
        client.login(token_cache)
        return
    except TypeError:
        pass
    except GarminConnectAuthenticationError:
        # Cached token is expired or invalid — wipe it and fall through to fresh login.
        for f in cache_path.glob("*") if cache_path.is_dir() else [cache_path]:
            f.unlink(missing_ok=True)

    client.login()


def main() -> int:
    load_dotenv()
    args = parse_args()
    try:
        start, end = target_range(args.start, args.end)
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(str(exc)) from exc

    email, password = prompt_credentials(args)

    try:
        from garminconnect import Garmin
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: install `garminconnect` in your environment first."
        ) from exc

    client = Garmin(
        email,
        password,
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    login(client, args.token_cache)

    output_path = resolve_output_path(args.output, args.output_dir)

    payload = fetch_blood_pressure(client, start, end)
    if args.debug_json:
        write_debug_json(debug_json_path(output_path), payload)
    rows = [
        row
        for row in as_rows(payload, args.default_comment)
        if start <= row.timestamp.date() <= end
    ]

    handle = coerce_output_handle(output_path)
    try:
        writer = csv.DictWriter(
            handle,
            fieldnames=("datetime", "systolic", "diastolic", "comment"),
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "datetime": format_timestamp(row.timestamp),
                    "systolic": row.systolic,
                    "diastolic": row.diastolic,
                    "comment": row.comment,
                }
            )
    finally:
        if handle is not sys.stdout:
            handle.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
