"""發電量估算示範資料寫入腳本。

依現有光電案場維度產生近期待估發電量資料，並寫入 mart.fact_generation_estimate。
流程包含資料表欄位偵測、案場資料載入、估算值計算、事實表 upsert 與 ETL 執行紀錄更新。
"""
from __future__ import annotations

import traceback
import hashlib
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from db import get_engine



# =========================
# 參數設定
# =========================
# 回補天數，從執行日往前產生固定天數的估算資料。
DAYS_TO_SEED = 90
# 僅處理目前有效且狀態為 ACTIVE 的案場。
ONLY_ACTIVE_SITES = True


@dataclass(frozen=True)
class SolarSite:
    """光電案場查詢結果模型，承接估算流程需要的維度欄位。"""

    site_sk: int
    site_id: str
    site_name: str
    county_name: str | None
    town_name: str | None
    location_sk: int | None
    capacity_kw: float
    baseline_efficiency_pct: float | None
    site_status: str | None


def stable_noise(key: str, low: float, high: float) -> float:
    """依輸入鍵值產生穩定的偽隨機數，範圍介於 low 與 high。"""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16) / 0xFFFFFFFF
    return low + (high - low) * value


def month_season_factor(month: int) -> float:
    """回傳台灣示範情境的月份季節日照係數。"""
    mapping = {
        1: 0.72,
        2: 0.76,
        3: 0.84,
        4: 0.92,
        5: 1.00,
        6: 1.05,
        7: 1.08,
        8: 1.03,
        9: 0.95,
        10: 0.88,
        11: 0.79,
        12: 0.70,
    }
    return mapping.get(month, 0.85)


def weekday_operation_factor(d: date) -> float:
    """依日期回傳營運係數，週末略降以增加示範資料的真實感。"""
    # weekday(): 週一為 0，週日為 6
    if d.weekday() in (5, 6):
        return 0.98
    return 1.00


def county_weather_factor(county_name: str | None) -> float:
    """依縣市回傳示範用天氣區域係數。"""
    mapping = {
        "臺北市": 0.93,
        "台北市": 0.93,
        "苗栗縣": 1.00,
        "臺中市": 1.03,
        "台中市": 1.03,
        "高雄市": 1.08,
    }
    return mapping.get(county_name or "", 1.00)


def compute_estimated_generation_kwh(site: SolarSite, d: date) -> dict[str, float]:
    """計算單一案場指定日期的示範估算發電量與輔助指標。

    估算公式以裝置容量、基準等效日照時數、季節係數、區域係數、效率係數、天氣調整、營運係數與穩定噪音組成。
    """
    base_sun_hours = 4.2
    season_factor = month_season_factor(d.month)
    area_factor = county_weather_factor(site.county_name)
    operation_factor = weekday_operation_factor(d)

    efficiency_pct = site.baseline_efficiency_pct or 18.0
    performance_ratio = max(0.72, min(0.92, efficiency_pct / 22.0))

    weather_adjustment = stable_noise(
        f"{site.site_id}:{d.isoformat()}:weather",
        0.86,
        1.08,
    )

    noise = stable_noise(
        f"{site.site_id}:{d.isoformat()}:noise",
        0.97,
        1.03,
    )

    estimated_kwh = (
        site.capacity_kw
        * base_sun_hours
        * season_factor
        * area_factor
        * performance_ratio
        * weather_adjustment
        * operation_factor
        * noise
    )

    estimated_kwh = round(max(0.0, estimated_kwh), 2)

    return {
        "estimated_generation_kwh": estimated_kwh,
        "irradiance_factor_pct": round(season_factor * area_factor * 100, 2),
        "weather_adjustment_pct": round(weather_adjustment * 100, 2),
        "performance_ratio_pct": round(performance_ratio * 100, 2),
    }


def get_fact_columns(conn) -> set[str]:
    """讀取 fact_generation_estimate 目前可用欄位名稱。"""
    sql = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'mart'
          AND table_name = 'fact_generation_estimate'
        """
    )
    rows = conn.execute(sql).mappings().all()
    return {row["column_name"] for row in rows}


def resolve_site_key_column(columns: set[str]) -> str:
    """從事實表欄位中判斷可用的案場鍵欄位。"""
    candidates = ["site_sk", "site_id"]
    for c in candidates:
        if c in columns:
            return c
    raise RuntimeError(
        "fact_generation_estimate does not contain supported site key column. "
        "Expected one of: site_sk, site_id"
    )


def resolve_date_column(columns: set[str]) -> str:
    """從事實表欄位中判斷可用的日期欄位。"""
    candidates = ["generation_date", "estimate_date", "biz_date", "date_key"]
    for c in candidates:
        if c in columns:
            return c
    raise RuntimeError(
        "fact_generation_estimate does not contain supported date column. "
        "Expected one of: generation_date, estimate_date, biz_date, date_key"
    )


def resolve_generation_column(columns: set[str]) -> str:
    """從事實表欄位中判斷可用的估算發電量欄位。"""
    candidates = [
        "estimated_generation_kwh",
        "estimate_generation_kwh",
        "generation_estimate_kwh",
        "estimated_kwh",
        "generation_kwh",
    ]
    for c in candidates:
        if c in columns:
            return c
    raise RuntimeError(
        "fact_generation_estimate does not contain supported generation column. "
        "Expected one of: estimated_generation_kwh, estimate_generation_kwh, "
        "generation_estimate_kwh, estimated_kwh, generation_kwh"
    )


def load_sites(conn) -> list[SolarSite]:
    """載入目前有效的光電案場維度資料，供估算流程使用。"""
    where_clause = "WHERE is_current = TRUE"
    if ONLY_ACTIVE_SITES:
        where_clause += " AND COALESCE(site_status, 'ACTIVE') = 'ACTIVE'"

    sql = text(
        f"""
        SELECT
            site_sk,
            site_id,
            site_name,
            county_name,
            town_name,
            location_sk,
            capacity_kw,
            baseline_efficiency_pct,
            site_status
        FROM mart.dim_solar_site
        {where_clause}
        ORDER BY site_sk
        """
    )

    rows = conn.execute(sql).mappings().all()
    sites: list[SolarSite] = []

    for row in rows:
        sites.append(
            SolarSite(
                site_sk=int(row["site_sk"]),
                site_id=str(row["site_id"]),
                site_name=str(row["site_name"]),
                county_name=row.get("county_name"),
                town_name=row.get("town_name"),
                location_sk=int(row["location_sk"]) if row.get("location_sk") is not None else None,
                capacity_kw=float(row["capacity_kw"]),
                baseline_efficiency_pct=(
                    float(row["baseline_efficiency_pct"])
                    if row.get("baseline_efficiency_pct") is not None
                    else None
                ),
                site_status=row.get("site_status"),
            )
        )

    if not sites:
        raise RuntimeError("No rows found in mart.dim_solar_site.")

    return sites


def build_payload(
    site: SolarSite,
    d: date,
    columns: set[str],
    site_key_col: str,
    date_col: str,
    gen_col: str,
    run_id: int,
) -> dict[str, Any]:
    """依目標表欄位組成可寫入的發電量估算 payload。"""
    metrics = compute_estimated_generation_kwh(site, d)

    payload: dict[str, Any] = {}

    if "run_id" in columns:
        payload["run_id"] = run_id

    if site_key_col == "site_sk":
        payload["site_sk"] = site.site_sk
    elif site_key_col == "site_id":
        payload["site_id"] = site.site_id

    if "location_sk" in columns:
        payload["location_sk"] = site.location_sk

    payload[date_col] = d
    payload[gen_col] = metrics["estimated_generation_kwh"]

    if "sunshine_hours" in columns:
        payload["sunshine_hours"] = round(4.2 * month_season_factor(d.month), 2)

    if "install_area_ping" in columns:
        payload["install_area_ping"] = round(site.capacity_kw * 0.9, 2)

    if "formula_version" in columns:
        payload["formula_version"] = "v1.0-demo"

    if "loaded_at" in columns:
        payload["loaded_at"] = datetime.now()

    return payload


def upsert_generation_estimate(
    conn,
    payload: dict[str, Any],
    columns: set[str],
    site_key_col: str,
    date_col: str,
    gen_col: str,
) -> None:
    """依案場鍵與日期新增或更新發電量估算事實資料。"""
    key_params = {
        site_key_col: payload[site_key_col],
        date_col: payload[date_col],
    }

    exists_sql = text(
        f"""
        SELECT 1
        FROM mart.fact_generation_estimate
        WHERE {site_key_col} = :{site_key_col}
          AND {date_col} = :{date_col}
        LIMIT 1
        """
    )

    exists = conn.execute(exists_sql, key_params).first() is not None

    if exists:
        update_cols = [c for c in payload.keys() if c not in (site_key_col, date_col, "fact_id")]
        if not update_cols:
            return

        set_clause = ", ".join([f"{c} = :{c}" for c in update_cols])

        update_sql = text(
            f"""
            UPDATE mart.fact_generation_estimate
            SET {set_clause}
            WHERE {site_key_col} = :{site_key_col}
              AND {date_col} = :{date_col}
            """
        )
        conn.execute(update_sql, payload)
        return

    insert_cols = list(payload.keys())
    col_sql = ", ".join(insert_cols)
    val_sql = ", ".join([f":{c}" for c in insert_cols])

    insert_sql = text(
        f"""
        INSERT INTO mart.fact_generation_estimate ({col_sql})
        VALUES ({val_sql})
        """
    )
    conn.execute(insert_sql, payload)


def validate_sites(sites: list[SolarSite]) -> None:
    """檢查案場容量與基準效率是否落在合理範圍。"""
    for site in sites:
        if site.capacity_kw <= 0:
            raise ValueError(f"{site.site_id}: capacity_kw must be > 0")
        if site.baseline_efficiency_pct is not None and not (
            0 <= site.baseline_efficiency_pct <= 100
        ):
            raise ValueError(
                f"{site.site_id}: baseline_efficiency_pct must be between 0 and 100"
            )

def create_etl_run_log(conn) -> int:
    """建立 ETL 執行紀錄並回傳 run_id。"""
    now = datetime.now()

    sql = text(
        """
        INSERT INTO meta.etl_run_log
        (
            pipeline_name,
            started_at,
            finished_at,
            status,
            rows_raw,
            rows_staging,
            rows_mart,
            rows_quarantine,
            message
        )
        VALUES
        (
            :pipeline_name,
            :started_at,
            :finished_at,
            :status,
            :rows_raw,
            :rows_staging,
            :rows_mart,
            :rows_quarantine,
            :message
        )
        RETURNING run_id
        """
    )

    row = conn.execute(
        sql,
        {
            "pipeline_name": "seed_generation_estimate",
            "started_at": now,
            "finished_at": now,
            "status": "RUNNING",
            "rows_raw": 0,
            "rows_staging": 0,
            "rows_mart": 0,
            "rows_quarantine": 0,
            "message": "seed started",
        },
    ).mappings().first()

    if not row:
        raise RuntimeError("Failed to create row in meta.etl_run_log.")

    return int(row["run_id"])

def finalize_etl_run_log(
    conn,
    run_id: int,
    status: str,
    rows_mart: int,
    message: str,
) -> None:
    """更新 ETL 執行紀錄的完成狀態、寫入列數與訊息。"""
    sql = text(
        """
        UPDATE meta.etl_run_log
        SET
            finished_at = :finished_at,
            status = :status,
            rows_mart = :rows_mart,
            message = :message
        WHERE run_id = :run_id
        """
    )

    conn.execute(
        sql,
        {
            "run_id": run_id,
            "finished_at": datetime.now(),
            "status": status,
            "rows_mart": rows_mart,
            "message": message[:1000],  # 避免訊息太長
        },
    )

# def get_existing_run_id(conn) -> int:
    # sql = text(
        # """
        # SELECT run_id
        # FROM meta.etl_run_log
        # ORDER BY run_id DESC
        # LIMIT 1
        # """
    # )
    # row = conn.execute(sql).mappings().first()
    # if not row:
        # raise RuntimeError("No rows found in meta.etl_run_log, cannot resolve run_id.")
    # return int(row["run_id"])

# def build_run_id() -> int:
    # return int(datetime.now().strftime("%Y%m%d%H%M%S"))


def main() -> int:
    """執行發電量估算種子資料產生與寫入流程。"""
    engine: Engine = get_engine()

    run_id: int | None = None
    total_rows = 0

    try:
        with engine.begin() as conn:
            columns = get_fact_columns(conn)

            if not columns:
                raise RuntimeError(
                    "Table mart.fact_generation_estimate not found "
                    "or has no readable columns."
                )

            site_key_col = resolve_site_key_column(columns)
            date_col = resolve_date_column(columns)
            gen_col = resolve_generation_column(columns)

            print(f"[INFO] fact_generation_estimate columns = {sorted(columns)}")
            print(f"[INFO] site key column = {site_key_col}")
            print(f"[INFO] date column = {date_col}")
            print(f"[INFO] generation column = {gen_col}")

            sites = load_sites(conn)
            validate_sites(sites)

            run_id = create_etl_run_log(conn)
            print(f"[INFO] created run_id in meta.etl_run_log = {run_id}")

            start_date = date.today() - timedelta(days=DAYS_TO_SEED - 1)
            end_date = date.today()

            current = start_date
            while current <= end_date:
                for site in sites:
                    payload = build_payload(
                        site=site,
                        d=current,
                        columns=columns,
                        site_key_col=site_key_col,
                        date_col=date_col,
                        gen_col=gen_col,
                        run_id=run_id,
                    )

                    upsert_generation_estimate(
                        conn=conn,
                        payload=payload,
                        columns=columns,
                        site_key_col=site_key_col,
                        date_col=date_col,
                        gen_col=gen_col,
                    )
                    total_rows += 1

                current += timedelta(days=1)

            finalize_etl_run_log(
                conn=conn,
                run_id=run_id,
                status="SUCCESS",
                rows_mart=total_rows,
                message=f"seed_generation_estimate completed successfully, total upsert rows = {total_rows}",
            )

        print(f"\nSeed completed successfully. total upsert rows = {total_rows}")
        return 0

    except Exception as ex:
        error_message = f"{type(ex).__name__}: {str(ex)}"
        print(f"\n[ERROR] {error_message}")

        if run_id is not None:
            try:
                with engine.begin() as conn:
                    finalize_etl_run_log(
                        conn=conn,
                        run_id=run_id,
                        status="FAILED",
                        rows_mart=total_rows,
                        message=error_message,
                    )
            except Exception as log_ex:
                print(f"[WARN] failed to finalize etl_run_log: {log_ex}")

        raise

if __name__ == "__main__":
    sys.exit(main())