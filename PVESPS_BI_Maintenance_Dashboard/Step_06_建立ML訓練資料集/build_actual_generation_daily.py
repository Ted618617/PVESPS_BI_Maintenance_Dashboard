"""每日實際發電量標籤資料建立腳本。

從發電量估算事實表讀取基礎資料，結合日照資料與案場維度欄位，產生可供監督式學習使用的每日發電量標籤。
標籤資料目前以規則與隨機擾動模擬，流程包含來源資料讀取、天氣與效率係數推導、維護旗標模擬、輸出整理、目標表 upsert 與 ETL 紀錄更新。
"""
import os
import sys
import random
import traceback
from datetime import datetime
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# ============================================================
# 參數設定
# ============================================================
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

TARGET_TABLE = "mart.fact_generation_actual_daily"
ESTIMATE_TABLE = "mart.fact_generation_estimate"
SUNSHINE_TABLE = "mart.fact_sunshine_daily"
SITE_DIM_TABLE = "mart.dim_solar_site"
RUN_LOG_TABLE = "meta.etl_run_log"

PIPELINE_NAME = "build_actual_generation_daily"
# 固定隨機種子，確保模擬結果可重現。
RANDOM_SEED = 42
random.seed(RANDOM_SEED)


# ============================================================
# 資料庫輔助函式
# ============================================================
def get_engine() -> Engine:
    """建立 PostgreSQL 連線引擎。"""
    return create_engine(
        DATABASE_URL,
        future=True,
        pool_pre_ping=True
    )


def table_exists(engine: Engine, full_table_name: str) -> bool:
    """檢查指定 schema.table 是否存在。"""
    schema_name, table_name = full_table_name.split(".", 1)

    sql = text("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = :schema_name
              AND table_name = :table_name
        )
    """)

    with engine.connect() as conn:
        return bool(conn.execute(sql, {
            "schema_name": schema_name,
            "table_name": table_name
        }).scalar())


def get_next_run_id(engine: Engine, pipeline_name: str) -> int:
    """建立 ETL 執行紀錄並回傳 run_id。

    對齊 meta.etl_run_log 結構：
    - pipeline_name
    - started_at
    - finished_at
    - status
    - rows_raw
    - rows_staging
    - rows_mart
    - rows_quarantine
    - message
    """
    if not table_exists(engine, RUN_LOG_TABLE):
        fallback_run_id = int(datetime.now().timestamp())
        print(f"[WARN] {RUN_LOG_TABLE} not found, fallback run_id={fallback_run_id}")
        return fallback_run_id

    sql = text(f"""
        INSERT INTO {RUN_LOG_TABLE}
        (
            pipeline_name,
            started_at,
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
            CURRENT_TIMESTAMP,
            'RUNNING',
            0,
            0,
            0,
            0,
            NULL
        )
        RETURNING run_id
    """)

    with engine.begin() as conn:
        run_id = conn.execute(sql, {"pipeline_name": pipeline_name}).scalar_one()

    print(f"[INFO] created run_id in {RUN_LOG_TABLE} = {run_id}")
    return int(run_id)


def update_run_log_success(
    engine: Engine,
    run_id: int,
    rows_raw: int = 0,
    rows_mart: int = 0,
    message: str = ""
) -> None:
    """將 ETL 執行紀錄更新為成功狀態。"""
    if not table_exists(engine, RUN_LOG_TABLE):
        return

    sql = text(f"""
        UPDATE {RUN_LOG_TABLE}
        SET
            finished_at = CURRENT_TIMESTAMP,
            status = 'SUCCESS',
            rows_raw = :rows_raw,
            rows_mart = :rows_mart,
            message = :message
        WHERE run_id = :run_id
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "rows_raw": rows_raw,
            "rows_mart": rows_mart,
            "message": message[:1000]
        })


def update_run_log_failed(engine: Engine, run_id: int, message: str = "") -> None:
    """將 ETL 執行紀錄更新為失敗狀態。"""
    if not table_exists(engine, RUN_LOG_TABLE):
        return

    sql = text(f"""
        UPDATE {RUN_LOG_TABLE}
        SET
            finished_at = CURRENT_TIMESTAMP,
            status = 'FAILED',
            message = :message
        WHERE run_id = :run_id
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "message": message[:1000]
        })


# ============================================================
# 來源資料讀取
# ============================================================
def read_source_data(engine: Engine) -> pd.DataFrame:
    """讀取估算發電量、日照與案場維度資料。

    依實際 schema：
    - 主表：fact_generation_estimate
    - 補充：fact_sunshine_daily 以 location_sk + date join
    - 補充：dim_solar_site 取 capacity_kw / baseline_efficiency_pct
    """
    sunshine_exists = table_exists(engine, SUNSHINE_TABLE)
    site_dim_exists = table_exists(engine, SITE_DIM_TABLE)

    if sunshine_exists and site_dim_exists:
        sql = f"""
        SELECT
            e.site_sk,
            e.location_sk,
            e.estimate_date::date AS generation_date,
            e.estimated_generation_kwh,
            e.sunshine_hours AS estimate_sunshine_hours,
            e.install_area_ping AS estimate_install_area_ping,

            s.sunshine_hours AS sunshine_hours,
            s.sunshine_rate_pct,
            s.solar_radiation_mj_m2,

            ds.capacity_kw,
            ds.baseline_efficiency_pct,
            ds.install_area_ping AS site_install_area_ping
        FROM {ESTIMATE_TABLE} e
        LEFT JOIN {SUNSHINE_TABLE} s
               ON e.location_sk = s.location_sk
              AND e.estimate_date::date = s.obs_date::date
        LEFT JOIN {SITE_DIM_TABLE} ds
               ON e.site_sk = ds.site_sk
              AND ds.is_current = TRUE
        ORDER BY e.site_sk, e.estimate_date::date
        """
    elif sunshine_exists:
        sql = f"""
        SELECT
            e.site_sk,
            e.location_sk,
            e.estimate_date::date AS generation_date,
            e.estimated_generation_kwh,
            e.sunshine_hours AS estimate_sunshine_hours,
            e.install_area_ping AS estimate_install_area_ping,

            s.sunshine_hours AS sunshine_hours,
            s.sunshine_rate_pct,
            s.solar_radiation_mj_m2,

            NULL::numeric AS capacity_kw,
            NULL::numeric AS baseline_efficiency_pct,
            NULL::numeric AS site_install_area_ping
        FROM {ESTIMATE_TABLE} e
        LEFT JOIN {SUNSHINE_TABLE} s
               ON e.location_sk = s.location_sk
              AND e.estimate_date::date = s.obs_date::date
        ORDER BY e.site_sk, e.estimate_date::date
        """
    else:
        print(f"[WARN] {SUNSHINE_TABLE} not found.")
        sql = f"""
        SELECT
            e.site_sk,
            e.location_sk,
            e.estimate_date::date AS generation_date,
            e.estimated_generation_kwh,
            e.sunshine_hours AS estimate_sunshine_hours,
            e.install_area_ping AS estimate_install_area_ping,

            e.sunshine_hours AS sunshine_hours,
            NULL::numeric AS sunshine_rate_pct,
            NULL::numeric AS solar_radiation_mj_m2,

            NULL::numeric AS capacity_kw,
            NULL::numeric AS baseline_efficiency_pct,
            NULL::numeric AS site_install_area_ping
        FROM {ESTIMATE_TABLE} e
        ORDER BY e.site_sk, e.estimate_date::date
        """

    df = pd.read_sql(sql, engine)

    print(f"[INFO] source rows loaded = {len(df)}")
    print(f"[INFO] source columns = {list(df.columns)}")
    return df


# ============================================================
# 模擬邏輯
# ============================================================
def clamp(value: float, low: float, high: float) -> float:
    """將數值限制在指定上下界範圍內。"""
    return max(low, min(high, value))


def pick_install_area_ping(row: pd.Series) -> float:
    """優先使用案場維度面積，缺值時改用估算事實表面積。"""
    site_area = row.get("site_install_area_ping")
    est_area = row.get("estimate_install_area_ping")

    if pd.notna(site_area):
        return float(site_area)
    if pd.notna(est_area):
        return float(est_area)
    return 0.0


def derive_weather_adjustment_factor(row: pd.Series) -> float:
    """依日照時數、日照率與太陽輻射量推導天氣調整係數。"""
    factor = 1.0

    sunshine_hours = row.get("sunshine_hours")
    sunshine_rate_pct = row.get("sunshine_rate_pct")
    solar_radiation = row.get("solar_radiation_mj_m2")

    if pd.notna(sunshine_hours):
        if sunshine_hours < 2:
            factor -= 0.18
        elif sunshine_hours < 4:
            factor -= 0.10
        elif sunshine_hours > 7:
            factor += 0.04

    if pd.notna(sunshine_rate_pct):
        if sunshine_rate_pct < 25:
            factor -= 0.08
        elif sunshine_rate_pct < 45:
            factor -= 0.04
        elif sunshine_rate_pct > 70:
            factor += 0.03

    if pd.notna(solar_radiation):
        if solar_radiation < 8:
            factor -= 0.08
        elif solar_radiation > 18:
            factor += 0.03

    return round(clamp(factor, 0.75, 1.08), 4)


def derive_efficiency_factor(row: pd.Series) -> float:
    """依基準效率與微幅隨機擾動推導設備效率係數。"""
    baseline_pct = row.get("baseline_efficiency_pct")

    if pd.notna(baseline_pct):
        base = float(baseline_pct) / 100.0
    else:
        base = 0.965

    jitter = random.uniform(-0.025, 0.025)
    factor = base + jitter
    return round(clamp(factor, 0.80, 1.02), 4)


def derive_maintenance_flag(row: pd.Series) -> bool:
    """以低機率產生維護旗標，後續可改接案場績效事實表。"""
    return random.random() < 0.03


def derive_noise_multiplier() -> float:
    """產生發電量模擬使用的常態分布噪音乘數。"""
    return round(clamp(random.normalvariate(1.0, 0.04), 0.88, 1.12), 4)


def simulate_actual_generation(df: pd.DataFrame) -> pd.DataFrame:
    """依估算發電量、天氣係數、效率係數、維護旗標與噪音產生模擬實際發電量。"""
    result = df.copy()

    result["estimated_generation_kwh"] = pd.to_numeric(
        result["estimated_generation_kwh"], errors="coerce"
    ).fillna(0)

    result["install_area_ping_final"] = result.apply(pick_install_area_ping, axis=1)

    result["weather_adjustment_factor"] = result.apply(
        derive_weather_adjustment_factor, axis=1
    )
    result["efficiency_factor"] = result.apply(
        derive_efficiency_factor, axis=1
    )
    result["maintenance_flag"] = result.apply(
        derive_maintenance_flag, axis=1
    )

    noise_values = []
    actual_values = []
    source_notes = []

    for _, row in result.iterrows():
        noise_multiplier = derive_noise_multiplier()
        maintenance_penalty = 0.85 if row["maintenance_flag"] else 1.0

        actual = (
            float(row["estimated_generation_kwh"])
            * float(row["weather_adjustment_factor"])
            * float(row["efficiency_factor"])
            * float(noise_multiplier)
            * float(maintenance_penalty)
        )

        actual = max(actual, 0.0)

        noise_values.append(noise_multiplier)
        actual_values.append(round(actual, 2))
        source_notes.append("simulated_from_fact_generation_estimate_v1")

    result["noise_multiplier"] = noise_values
    result["actual_generation_kwh"] = actual_values
    result["actual_type"] = "simulated"
    result["source_note"] = source_notes

    return result


# ============================================================
# 輸出資料整理
# ============================================================
def prepare_output_df(df: pd.DataFrame, run_id: int) -> pd.DataFrame:
    """整理 fact_generation_actual_daily 目標表需要的欄位與資料型別。"""
    out = pd.DataFrame({
        "run_id": run_id,
        "site_sk": df["site_sk"].astype("Int64"),
        "location_sk": df["location_sk"].astype("Int64"),
        "generation_date": pd.to_datetime(df["generation_date"]).dt.date,
        "actual_generation_kwh": pd.to_numeric(df["actual_generation_kwh"], errors="coerce").round(2),
        "actual_type": df["actual_type"].fillna("simulated"),
        "weather_adjustment_factor": pd.to_numeric(df["weather_adjustment_factor"], errors="coerce").round(4),
        "efficiency_factor": pd.to_numeric(df["efficiency_factor"], errors="coerce").round(4),
        "maintenance_flag": df["maintenance_flag"].fillna(False).astype(bool),
        "source_note": df["source_note"].fillna("simulated_from_fact_generation_estimate_v1"),
    })

    out = out.dropna(subset=["site_sk", "generation_date", "actual_generation_kwh"]).copy()
    out["actual_generation_kwh"] = out["actual_generation_kwh"].clip(lower=0)

    out = out.sort_values(["site_sk", "generation_date"]).drop_duplicates(
        subset=["site_sk", "generation_date", "actual_type"],
        keep="last"
    )

    return out


# ============================================================
# 目標表寫入
# ============================================================
def upsert_target_table(engine: Engine, df: pd.DataFrame) -> int:
    """透過暫存表將每日實際發電量資料 upsert 至目標表。"""
    if df.empty:
        print("[WARN] output dataframe is empty, nothing to write")
        return 0

    temp_table = "tmp_fact_generation_actual_daily"

    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))

    df.to_sql(
        name=temp_table,
        con=engine,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=1000
    )

    sql = text(f"""
        INSERT INTO {TARGET_TABLE}
        (
            run_id,
            site_sk,
            location_sk,
            generation_date,
            actual_generation_kwh,
            actual_type,
            weather_adjustment_factor,
            efficiency_factor,
            maintenance_flag,
            source_note
        )
        SELECT
            run_id,
            site_sk,
            location_sk,
            generation_date,
            actual_generation_kwh,
            actual_type,
            weather_adjustment_factor,
            efficiency_factor,
            maintenance_flag,
            source_note
        FROM {temp_table}
        ON CONFLICT (site_sk, generation_date, actual_type)
        DO UPDATE SET
            run_id = EXCLUDED.run_id,
            location_sk = EXCLUDED.location_sk,
            actual_generation_kwh = EXCLUDED.actual_generation_kwh,
            weather_adjustment_factor = EXCLUDED.weather_adjustment_factor,
            efficiency_factor = EXCLUDED.efficiency_factor,
            maintenance_flag = EXCLUDED.maintenance_flag,
            source_note = EXCLUDED.source_note,
            loaded_at = CURRENT_TIMESTAMP
    """)

    with engine.begin() as conn:
        conn.execute(sql)
        conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))

    return len(df)


# ============================================================
# 主流程
# ============================================================
def main() -> None:
    """執行每日實際發電量標籤資料建立流程。"""
    engine = get_engine()
    run_id: Optional[int] = None

    try:
        print("=" * 80)
        print(f"[INFO] start pipeline: {PIPELINE_NAME}")

        run_id = get_next_run_id(engine, PIPELINE_NAME)

        source_df = read_source_data(engine)
        if source_df.empty:
            msg = "No source rows found from fact_generation_estimate."
            print(f"[WARN] {msg}")
            update_run_log_success(engine, run_id, rows_raw=0, rows_mart=0, message=msg)
            return

        simulated_df = simulate_actual_generation(source_df)
        output_df = prepare_output_df(simulated_df, run_id=run_id)

        print(f"[INFO] output rows prepared = {len(output_df)}")
        print("[INFO] output preview:")
        print(output_df.head(10).to_string(index=False))

        affected_rows = upsert_target_table(engine, output_df)

        msg = f"build_actual_generation_daily success; affected_rows={affected_rows}"
        print(f"[INFO] {msg}")

        update_run_log_success(
            engine,
            run_id=run_id,
            rows_raw=len(source_df),
            rows_mart=affected_rows,
            message=msg
        )

        print("=" * 80)
        print("[INFO] pipeline finished successfully")

    except Exception as ex:
        err_msg = f"{type(ex).__name__}: {ex}"
        print("[ERROR] pipeline failed")
        print(err_msg)
        print(traceback.format_exc())

        if run_id is not None:
            update_run_log_failed(engine, run_id, err_msg)

        sys.exit(1)


if __name__ == "__main__":
    main()