"""One-time historical import: Activities.csv → PostgreSQL.

Analyzes the CSV with Claude (tool-use) and upserts all activities.
Safe to re-run — ON CONFLICT DO NOTHING prevents duplicates.

Usage (recommended — runs inside the analyzer container so DB is reachable):

    # Copy your CSV to the VM first, then:
    docker compose run --rm \\
        -v /absolute/path/to/Activities.csv:/data/Activities.csv \\
        analyzer \\
        python initial_load.py /data/Activities.csv

Usage (local machine, after Tailscale is connected):

    POSTGRES_HOST=<tailscale-vm-ip> \\
    POSTGRES_PORT=5432 \\
    POSTGRES_DB=garmin \\
    POSTGRES_USER=garmin_user \\
    POSTGRES_PASSWORD=... \\
    ANTHROPIC_API_KEY=sk-ant-... \\
    python db/initial_load.py Activities.csv
"""
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# Support running from project root (local) or from /app (inside container)
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, "/app")

load_dotenv()

from garmin_analyzer import analyze_csv  # noqa: E402 (needs sys.path set above)

INSERT_SQL = """
    INSERT INTO activities
        (activity_date, activity_type, duration_min, distance_km,
         avg_heart_rate, max_heart_rate, calories, elevation_gain_m, avg_pace_per_km)
    VALUES
        (%(activity_date)s, %(activity_type)s, %(duration_min)s, %(distance_km)s,
         %(avg_heart_rate)s, %(max_heart_rate)s, %(calories)s,
         %(elevation_gain_m)s, %(avg_pace_per_km)s)
    ON CONFLICT ON CONSTRAINT activities_dedup DO NOTHING
"""


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)

    # ── Read CSV ──────────────────────────────────────────────────────────
    print(f"Reading {csv_path} ({csv_path.stat().st_size / 1024:.1f} KB)...")
    csv_content = csv_path.read_text(encoding="utf-8-sig")  # handles Windows BOM
    rows = len(csv_content.splitlines()) - 1  # subtract header
    print(f"  {rows} data rows found in CSV")

    # ── Analyze with Claude ───────────────────────────────────────────────
    print("\nSending to Claude for analysis (this may take 30–90 seconds)...")
    activities = analyze_csv(csv_content)
    print(f"  Claude returned {len(activities)} valid activities")

    if not activities:
        print("\nWARNING: No valid activities found. Check that this is a Garmin Connect export.")
        sys.exit(0)

    # ── Connect to PostgreSQL ─────────────────────────────────────────────
    print("\nConnecting to PostgreSQL...")
    try:
        conn = psycopg2.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ.get("POSTGRES_PORT", 5432)),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
        )
    except psycopg2.OperationalError as exc:
        print(f"ERROR: Cannot connect to PostgreSQL: {exc}")
        print("  → Is the DB running? Is Tailscale connected (if running locally)?")
        sys.exit(1)

    # ── Upsert ────────────────────────────────────────────────────────────
    inserted = 0
    skipped = 0

    print(f"Inserting {len(activities)} activities (ON CONFLICT DO NOTHING)...")
    with conn:
        with conn.cursor() as cur:
            for i, activity in enumerate(activities, 1):
                cur.execute(INSERT_SQL, activity)
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
                if i % 50 == 0:
                    print(f"  {i}/{len(activities)} processed...")

    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'─' * 40}")
    print(f"  Total activities in CSV:  {len(activities)}")
    print(f"  Inserted (new):           {inserted}")
    print(f"  Skipped  (already exist): {skipped}")
    print(f"{'─' * 40}")
    print("Done! Power BI can now connect to the activities table.")


if __name__ == "__main__":
    main()
