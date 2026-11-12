"""FastAPI microservice — Garmin analyzer + PostgreSQL writer.

Single endpoint: POST /process
  Receives a raw Garmin CSV string from n8n.
  1. Sends CSV to Claude (tool-use) → list of activity dicts.
  2. Upserts activities to PostgreSQL (ON CONFLICT DO NOTHING).
  3. Returns a summary dict that n8n forwards to Telegram.

Internal to the Docker network — never exposed via nginx.
"""
import logging
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from garmin_analyzer import analyze_csv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Garmin Analyzer", version="2.0.0")

# ── Database ──────────────────────────────────────────────────────────────────

DB_DSN = {
    "host": os.environ["POSTGRES_HOST"],
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

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


@contextmanager
def get_conn():
    conn = psycopg2.connect(**DB_DSN)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    csv_content: str


class ProcessResponse(BaseModel):
    activities_found: int
    inserted: int
    skipped: int          # dedup — already in DB
    total_distance_km: float
    avg_heart_rate: int | None
    total_calories: int
    activity_breakdown: dict[str, int]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/process", response_model=ProcessResponse)
async def process(req: ProcessRequest) -> ProcessResponse:
    """Main pipeline: CSV → Claude → PostgreSQL → summary stats."""

    # ── 1. Analyze with Claude ────────────────────────────────────────────
    try:
        activities = analyze_csv(req.csv_content)
    except Exception as exc:
        logger.exception("Claude analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    logger.info("Claude returned %d activities", len(activities))

    if not activities:
        raise HTTPException(
            status_code=422,
            detail="No valid activities found in CSV. Check the file came from Garmin Connect.",
        )

    # ── 2. Save to PostgreSQL ─────────────────────────────────────────────
    inserted = 0
    skipped = 0
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for activity in activities:
                    cur.execute(INSERT_SQL, activity)
                    if cur.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
    except Exception as exc:
        logger.exception("PostgreSQL write failed")
        raise HTTPException(status_code=503, detail=f"Database error: {exc}") from exc

    logger.info("Inserted: %d  Skipped (dedup): %d", inserted, skipped)

    # ── 3. Compute summary stats ──────────────────────────────────────────
    total_distance = round(sum(a.get("distance_km") or 0 for a in activities), 2)
    hr_values = [a["avg_heart_rate"] for a in activities if a.get("avg_heart_rate")]
    avg_hr = round(sum(hr_values) / len(hr_values)) if hr_values else None
    total_calories = int(sum(a.get("calories") or 0 for a in activities))

    breakdown: dict[str, int] = {}
    for a in activities:
        t = a.get("activity_type", "Unknown")
        breakdown[t] = breakdown.get(t, 0) + 1

    return ProcessResponse(
        activities_found=len(activities),
        inserted=inserted,
        skipped=skipped,
        total_distance_km=total_distance,
        avg_heart_rate=avg_hr,
        total_calories=total_calories,
        activity_breakdown=breakdown,
    )


@app.get("/health")
async def health():
    """Health check — also verifies DB connectivity."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as exc:
        # Return 200 with degraded status (nginx/n8n can still route)
        logger.warning("DB health check failed: %s", exc)
        return {"status": "degraded", "db": str(exc)}
