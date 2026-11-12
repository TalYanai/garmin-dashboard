"""Write Garmin activity data to Excel with deduplication.

CLI usage:
  python excel_writer.py --json '[...]'      # small payloads only
  python excel_writer.py --file /tmp/g.json  # recommended for n8n / large data
"""
import argparse
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

SCHEMA_COLUMNS = [
    "activity_date",
    "activity_type",
    "duration_min",
    "distance_km",
    "avg_heart_rate",
    "max_heart_rate",
    "calories",
    "elevation_gain_m",
    "avg_pace_per_km",
]

DEDUP_KEYS = ["activity_date", "activity_type", "duration_min"]

EXCEL_PATH = Path(os.environ.get("EXCEL_PATH", "data/activities.xlsx"))


def load_existing() -> pd.DataFrame:
    if EXCEL_PATH.exists():
        return pd.read_excel(EXCEL_PATH, engine="openpyxl")
    return pd.DataFrame(columns=SCHEMA_COLUMNS)


def append_activities(activities: list[dict]) -> int:
    """Append new activities to Excel, deduplicating by (date, type, duration).

    Returns the number of new rows actually written.
    Raises PermissionError if the file is locked (e.g. open in Power BI).
    """
    existing = load_existing()
    new_df = pd.DataFrame(activities, columns=SCHEMA_COLUMNS)

    if existing.empty:
        combined = new_df
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=DEDUP_KEYS, keep="first")

    new_rows = len(combined) - len(existing)

    EXCEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        combined.to_excel(EXCEL_PATH, index=False, engine="openpyxl")
    except PermissionError:
        raise PermissionError(
            f"Cannot write to {EXCEL_PATH} — the file may be open in Power BI or Excel. "
            "Close it and try again. Your data is saved in data/backup_latest.json."
        )

    return new_rows


def get_summary_stats(activities: list[dict]) -> dict:
    if not activities:
        return {}

    df = pd.DataFrame(activities)
    total_distance = df["distance_km"].fillna(0).sum()
    avg_hr_values = df["avg_heart_rate"].dropna()
    avg_hr = round(avg_hr_values.mean()) if not avg_hr_values.empty else None
    total_calories = df["calories"].fillna(0).sum()
    activity_counts = df["activity_type"].value_counts().to_dict()

    return {
        "total_activities": len(activities),
        "total_distance_km": round(total_distance, 2),
        "avg_heart_rate": avg_hr,
        "total_calories": int(total_calories),
        "activity_breakdown": activity_counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write Garmin activities to Excel")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="JSON array of activity dicts (small payloads only)")
    group.add_argument("--file", help="Path to a JSON file containing the activity array")
    args = parser.parse_args()

    if args.file:
        activities = json.loads(Path(args.file).read_text(encoding="utf-8"))
    else:
        activities = json.loads(args.json)

    new_rows = append_activities(activities)
    stats = get_summary_stats(activities)

    print(f"New rows added: {new_rows}")
    print(json.dumps(stats, ensure_ascii=False))
