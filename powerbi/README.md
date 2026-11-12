# Power BI Dashboard — Garmin Activities (PostgreSQL)

## Prerequisites

- Tailscale installed on both the Azure VM and your local machine
- Both devices in the same Tailscale network (`tailscale up` on both)
- PostgreSQL running on the VM (`docker compose up -d`)
- PostgreSQL ODBC driver installed locally (Power BI uses it for PostgreSQL)

---

## Connecting to PostgreSQL via Tailscale

1. On the VM: `tailscale ip -4` → note the IP (e.g. `100.64.0.5`)
2. Open **Power BI Desktop**
3. **Get Data → Database → PostgreSQL database**
4. Fill in:
   - **Server:** `100.64.0.5`  (your VM's Tailscale IP)
   - **Database:** `garmin`
5. **Data Connectivity mode:** DirectQuery (live) or Import (snapshot)
   - Recommended: **Import** — faster dashboards, manual refresh when needed
6. Enter credentials: Username / Password (from `.env` → `POSTGRES_USER` / `POSTGRES_PASSWORD`)
7. Select the **activities** table → **Load**

> If the connection fails, verify Tailscale is connected: `tailscale status` on both machines.

---

## Column Data Types

Power BI infers types from PostgreSQL — these should be set automatically:

| Column | PostgreSQL type | Power BI type |
|---|---|---|
| `activity_date` | DATE | Date |
| `activity_type` | VARCHAR | Text |
| `duration_min` | NUMERIC | Decimal Number |
| `distance_km` | NUMERIC | Decimal Number |
| `avg_heart_rate` | INTEGER | Whole Number |
| `max_heart_rate` | INTEGER | Whole Number |
| `calories` | INTEGER | Whole Number |
| `elevation_gain_m` | NUMERIC | Decimal Number |
| `avg_pace_per_km` | VARCHAR | Text |
| `created_at` | TIMESTAMPTZ | Date/Time |

---

## Recommended Dashboard Pages

### Page 1 — Overview
- **Card**: Total Activities
- **Card**: Total Distance (km)
- **Bar chart**: Activity count by type
- **Line chart**: Weekly distance over time

### Page 2 — Heart Rate & Effort
- **Scatter plot**: Distance vs Avg Heart Rate (color by activity_type)
- **Line chart**: Avg Heart Rate trend
- **Card**: Personal best (max of max_heart_rate)

### Page 3 — Monthly Summary
- **Matrix**: Month × Activity Type → Total Distance
- **Bar chart**: Monthly calories burned
- **KPI card**: This month vs last month distance

---

## DAX Measures

```dax
Total Distance (km) =
SUM(activities[distance_km])
```

```dax
Avg Heart Rate =
AVERAGEX(
    FILTER(activities, NOT(ISBLANK(activities[avg_heart_rate]))),
    activities[avg_heart_rate]
)
```

```dax
This Month Distance =
CALCULATE(
    SUM(activities[distance_km]),
    DATESMTD(activities[activity_date])
)
```

```dax
Weekly Distance =
CALCULATE(
    SUM(activities[distance_km]),
    DATESINPERIOD(
        activities[activity_date],
        LASTDATE(activities[activity_date]),
        -7,
        DAY
    )
)
```

---

## Refreshing Data

After sending a new CSV to the Telegram bot (n8n writes directly to PostgreSQL):

- **Import mode:** Home → Refresh in Power BI Desktop
- **DirectQuery mode:** Dashboard auto-queries live data — no manual refresh needed
- No file locking issues (unlike Excel) — refresh anytime, even while the pipeline is running
