"""CLI entry point for testing the Garmin → Excel pipeline without Telegram.

Usage: python main.py <path_to_csv>
"""
import sys
from pathlib import Path

from garmin_analyzer import analyze_csv
from excel_writer import append_activities, get_summary_stats


def main():
    if len(sys.argv) != 2:
        print("Usage: python main.py <path_to_csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    print(f"Reading {csv_path}...")
    # utf-8-sig handles Windows BOM
    csv_content = csv_path.read_text(encoding="utf-8-sig")

    print("Sending to Claude for analysis...")
    activities = analyze_csv(csv_content)
    print(f"Parsed {len(activities)} activities")

    print("Writing to Excel...")
    new_rows = append_activities(activities)
    stats = get_summary_stats(activities)

    print(f"\nDone!")
    print(f"  New rows added: {new_rows}")
    print(f"  Total activities in file: {stats.get('total_activities', 0)}")
    print(f"  Total distance: {stats.get('total_distance_km', 0)} km")
    if stats.get("avg_heart_rate"):
        print(f"  Avg heart rate: {stats['avg_heart_rate']} bpm")
    print(f"  Total calories: {stats.get('total_calories', 0)}")

    breakdown = stats.get("activity_breakdown", {})
    if breakdown:
        print("  Activity breakdown:")
        for activity_type, count in breakdown.items():
            print(f"    {activity_type}: {count}")


if __name__ == "__main__":
    main()
