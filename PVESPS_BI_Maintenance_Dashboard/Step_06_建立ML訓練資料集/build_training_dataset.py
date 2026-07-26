"""ML 每日訓練資料集建立腳本。

從每日發電量標籤、估算發電量、日照資料與案場維度整併資料，建立一站點一日期的模型訓練寬表。
流程包含來源資料讀取、時間特徵、案場靜態特徵、天氣特徵、交互特徵、落後期特徵、品質旗標、目標表 upsert 與資料集建置紀錄寫入。
"""
import os
import sys
import traceback
from datetime import datetime
from typing import Optional

import numpy as np
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

RUN_LOG_TABLE = "meta.etl_run_log"
DATASET_LOG_TABLE = "meta.model_training_dataset_log"

ACTUAL_TABLE = "mart.fact_generation_actual_daily"
ESTIMATE_TABLE = "mart.fact_generation_estimate"
SUNSHINE_TABLE = "mart.fact_sunshine_daily"
SITE_DIM_TABLE = "mart.dim_solar_site"

TARGET_TABLE = "mart.ml_training_generation_daily"

PIPELINE_NAME = "build_training_dataset"

STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"


# ============================================================
# 資料庫輔助函式
# ============================================================
def get_engine() -> Engine:
    """建立 PostgreSQL 連線引擎。"""
    return create_engine(
        DATABASE_URL,
        future=True,
        pool_pre_ping=True,
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
    """建立 ETL 執行紀錄並回傳 run_id。"""
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
            :status,
            0,
            0,
            0,
            0,
            NULL
        )
        RETURNING run_id
    """)

    with engine.begin() as conn:
        run_id = conn.execute(sql, {
            "pipeline_name": pipeline_name,
            "status": STATUS_RUNNING
        }).scalar_one()

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
            status = :status,
            rows_raw = :rows_raw,
            rows_mart = :rows_mart,
            message = :message
        WHERE run_id = :run_id
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "status": STATUS_SUCCESS,
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
            status = :status,
            message = :message
        WHERE run_id = :run_id
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "status": STATUS_FAILED,
            "message": message[:1000]
        })


# ============================================================
# 來源資料讀取
# ============================================================
def read_base_dataset(engine: Engine) -> pd.DataFrame:
    """整併標籤、估算、日照與案場維度資料，形成特徵工程基礎資料集。"""
    sql = f"""
    SELECT
        a.site_sk,
        a.location_sk,
        a.generation_date::date AS training_date,
        a.actual_generation_kwh,
        a.actual_type,
        a.weather_adjustment_factor,
        a.efficiency_factor,
        a.maintenance_flag,

        e.estimated_generation_kwh,
        e.sunshine_hours AS estimate_sunshine_hours,
        e.install_area_ping AS estimate_install_area_ping,

        s.sunshine_hours,
        s.sunshine_rate_pct,
        s.solar_radiation_mj_m2,

        ds.capacity_kw,
        ds.baseline_efficiency_pct,
        ds.install_area_ping AS site_install_area_ping,
        ds.city_name,
        ds.county_name AS site_county,
        ds.site_status,
        ds.commission_date
    FROM {ACTUAL_TABLE} a
    LEFT JOIN {ESTIMATE_TABLE} e
           ON a.site_sk = e.site_sk
          AND a.location_sk = e.location_sk
          AND a.generation_date::date = e.estimate_date::date
    LEFT JOIN {SUNSHINE_TABLE} s
           ON a.location_sk = s.location_sk
          AND a.generation_date::date = s.obs_date::date
    LEFT JOIN {SITE_DIM_TABLE} ds
           ON a.site_sk = ds.site_sk
          AND ds.is_current = TRUE
    ORDER BY a.site_sk, a.generation_date::date
    """

    df = pd.read_sql(sql, engine)
    print(f"[INFO] base dataset rows loaded = {len(df)}")
    print(f"[INFO] base dataset columns = {list(df.columns)}")
    return df


# ============================================================
# 特徵工程
# ============================================================
def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """加入年度、月份、週別、星期、週末與季節等時間特徵。"""
    out = df.copy()
    dt = pd.to_datetime(out["training_date"])

    out["year_num"] = dt.dt.year
    out["month_num"] = dt.dt.month
    out["day_num"] = dt.dt.day
    out["day_of_year"] = dt.dt.dayofyear
    out["week_of_year"] = dt.dt.isocalendar().week.astype(int)
    out["weekday_num"] = dt.dt.weekday
    out["is_weekend"] = out["weekday_num"].isin([5, 6])

    def season_map(month: int) -> str:
        if month in [3, 4, 5]:
            return "spring"
        if month in [6, 7, 8]:
            return "summer"
        if month in [9, 10, 11]:
            return "autumn"
        return "winter"

    out["season_code"] = out["month_num"].apply(season_map)
    return out


def add_static_features(df: pd.DataFrame) -> pd.DataFrame:
    """加入案場面積、容量、面板效率、區域與案場型態等靜態特徵。"""
    out = df.copy()

    out["install_area_ping"] = out["site_install_area_ping"].combine_first(
        out["estimate_install_area_ping"]
    )

    out["panel_efficiency"] = np.where(
        out["baseline_efficiency_pct"].notna(),
        pd.to_numeric(out["baseline_efficiency_pct"], errors="coerce") / 100.0,
        np.nan
    )

    out["site_region"] = out["city_name"]
    out["site_type"] = out["site_status"]

    return out


def add_weather_features(df: pd.DataFrame) -> pd.DataFrame:
    """加入日照、降雨風險與陰天風險相關天氣特徵。"""
    out = df.copy()

    out["sunshine_hours"] = out["sunshine_hours"].combine_first(out["estimate_sunshine_hours"])

    out["pop_value"] = np.nan
    out["pop_type"] = None
    out["forecast_issue_time"] = pd.NaT

    out["rain_risk_flag"] = False
    out["cloudy_risk_flag"] = np.where(
        out["sunshine_hours"].notna() & (out["sunshine_hours"] < 3),
        True,
        False
    )

    return out


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """加入規則估算值、單位面積發電量與天氣面積交互特徵。"""
    out = df.copy()

    out["estimated_generation_rule_kwh"] = pd.to_numeric(
        out["estimated_generation_kwh"], errors="coerce"
    )

    out["generation_per_ping"] = np.where(
        pd.to_numeric(out["install_area_ping"], errors="coerce") > 0,
        pd.to_numeric(out["target_generation_kwh"], errors="coerce")
        / pd.to_numeric(out["install_area_ping"], errors="coerce"),
        np.nan
    )

    out["sunshine_x_area"] = (
        pd.to_numeric(out["sunshine_hours"], errors="coerce")
        * pd.to_numeric(out["install_area_ping"], errors="coerce")
    )

    out["radiation_x_area"] = (
        pd.to_numeric(out["solar_radiation_mj_m2"], errors="coerce")
        * pd.to_numeric(out["install_area_ping"], errors="coerce")
    )

    out["pop_x_sunshine"] = (
        pd.to_numeric(out["pop_value"], errors="coerce")
        * pd.to_numeric(out["sunshine_hours"], errors="coerce")
    )

    return out


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """依站點產生前期發電量與日照時數的落後期及移動平均特徵。"""
    out = df.copy().sort_values(["site_sk", "training_date"])

    out["lag_1_generation_kwh"] = out.groupby("site_sk")["target_generation_kwh"].shift(1)

    out["lag_3_avg_generation_kwh"] = (
        out.groupby("site_sk")["target_generation_kwh"]
        .transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    )

    out["lag_7_avg_generation_kwh"] = (
        out.groupby("site_sk")["target_generation_kwh"]
        .transform(lambda s: s.shift(1).rolling(7, min_periods=1).mean())
    )

    out["lag_14_avg_generation_kwh"] = (
        out.groupby("site_sk")["target_generation_kwh"]
        .transform(lambda s: s.shift(1).rolling(14, min_periods=1).mean())
    )

    out["lag_1_sunshine_hours"] = out.groupby("site_sk")["sunshine_hours"].shift(1)

    out["lag_3_avg_sunshine_hours"] = (
        out.groupby("site_sk")["sunshine_hours"]
        .transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    )

    return out


def add_quality_flags(df: pd.DataFrame) -> pd.DataFrame:
    """檢查訓練必要欄位缺失狀態，產生可訓練旗標與無效原因。"""
    out = df.copy()

    required_cols = [
        "target_generation_kwh",
        "sunshine_hours",
        "install_area_ping",
    ]

    out["feature_missing_cnt"] = out[required_cols].isna().sum(axis=1)

    invalid_reason = []
    valid_flags = []

    for _, row in out.iterrows():
        reasons = []

        if pd.isna(row["target_generation_kwh"]):
            reasons.append("missing_target")

        if pd.isna(row["sunshine_hours"]):
            reasons.append("missing_sunshine_hours")

        if pd.isna(row["install_area_ping"]):
            reasons.append("missing_install_area_ping")

        if len(reasons) == 0:
            valid_flags.append(True)
            invalid_reason.append(None)
        else:
            valid_flags.append(False)
            invalid_reason.append(",".join(reasons))

    out["is_valid_for_training"] = valid_flags
    out["invalid_reason"] = invalid_reason

    return out


def build_training_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """串接特徵工程步驟，建立模型訓練資料集。"""
    out = df.copy()

    out["target_generation_kwh"] = pd.to_numeric(out["actual_generation_kwh"], errors="coerce")
    out["target_available"] = out["target_generation_kwh"].notna()
    out["target_type"] = out["actual_type"].fillna("simulated")

    out = add_time_features(out)
    out = add_static_features(out)
    out = add_weather_features(out)
    out = add_derived_features(out)
    out = add_lag_features(out)
    out = add_quality_flags(out)

    return out


# ============================================================
# 輸出資料整理
# ============================================================
def prepare_output_df(df: pd.DataFrame, run_id: int) -> pd.DataFrame:
    """整理 ml_training_generation_daily 目標表需要的欄位與資料型別。"""
    out = pd.DataFrame({
        "run_id": run_id,
        "site_sk": df["site_sk"].astype("Int64"),
        "location_sk": df["location_sk"].astype("Int64"),
        "training_date": pd.to_datetime(df["training_date"]).dt.date,

        "target_generation_kwh": pd.to_numeric(df["target_generation_kwh"], errors="coerce").round(2),
        "target_available": df["target_available"].fillna(False).astype(bool),
        "target_type": df["target_type"].fillna("simulated"),

        "year_num": pd.to_numeric(df["year_num"], errors="coerce").astype("Int64"),
        "month_num": pd.to_numeric(df["month_num"], errors="coerce").astype("Int64"),
        "day_num": pd.to_numeric(df["day_num"], errors="coerce").astype("Int64"),
        "day_of_year": pd.to_numeric(df["day_of_year"], errors="coerce").astype("Int64"),
        "week_of_year": pd.to_numeric(df["week_of_year"], errors="coerce").astype("Int64"),
        "weekday_num": pd.to_numeric(df["weekday_num"], errors="coerce").astype("Int64"),
        "is_weekend": df["is_weekend"].fillna(False).astype(bool),
        "season_code": df["season_code"],

        "install_area_ping": pd.to_numeric(df["install_area_ping"], errors="coerce").round(2),
        "capacity_kw": pd.to_numeric(df["capacity_kw"], errors="coerce").round(2),
        "panel_efficiency": pd.to_numeric(df["panel_efficiency"], errors="coerce").round(4),
        "site_region": df["site_region"],
        "site_county": df["site_county"],
        "site_type": df["site_type"],

        "sunshine_hours": pd.to_numeric(df["sunshine_hours"], errors="coerce").round(2),
        "sunshine_rate_pct": pd.to_numeric(df["sunshine_rate_pct"], errors="coerce").round(2),
        "solar_radiation_mj_m2": pd.to_numeric(df["solar_radiation_mj_m2"], errors="coerce").round(2),
        "pop_value": pd.to_numeric(df["pop_value"], errors="coerce").round(2),
        "pop_type": df["pop_type"],
        "forecast_issue_time": df["forecast_issue_time"],
        "rain_risk_flag": df["rain_risk_flag"].fillna(False).astype(bool),
        "cloudy_risk_flag": df["cloudy_risk_flag"].fillna(False).astype(bool),

        "estimated_generation_rule_kwh": pd.to_numeric(df["estimated_generation_rule_kwh"], errors="coerce").round(2),
        "generation_per_ping": pd.to_numeric(df["generation_per_ping"], errors="coerce").round(4),
        "sunshine_x_area": pd.to_numeric(df["sunshine_x_area"], errors="coerce").round(4),
        "radiation_x_area": pd.to_numeric(df["radiation_x_area"], errors="coerce").round(4),
        "pop_x_sunshine": pd.to_numeric(df["pop_x_sunshine"], errors="coerce").round(4),

        "lag_1_generation_kwh": pd.to_numeric(df["lag_1_generation_kwh"], errors="coerce").round(2),
        "lag_3_avg_generation_kwh": pd.to_numeric(df["lag_3_avg_generation_kwh"], errors="coerce").round(2),
        "lag_7_avg_generation_kwh": pd.to_numeric(df["lag_7_avg_generation_kwh"], errors="coerce").round(2),
        "lag_14_avg_generation_kwh": pd.to_numeric(df["lag_14_avg_generation_kwh"], errors="coerce").round(2),
        "lag_1_sunshine_hours": pd.to_numeric(df["lag_1_sunshine_hours"], errors="coerce").round(2),
        "lag_3_avg_sunshine_hours": pd.to_numeric(df["lag_3_avg_sunshine_hours"], errors="coerce").round(2),

        "feature_missing_cnt": pd.to_numeric(df["feature_missing_cnt"], errors="coerce").fillna(0).astype(int),
        "is_valid_for_training": df["is_valid_for_training"].fillna(False).astype(bool),
        "invalid_reason": df["invalid_reason"],
    })

    out = out.drop_duplicates(subset=["site_sk", "training_date"], keep="last").copy()
    return out


# ============================================================
# 目標表寫入
# ============================================================
def upsert_target_table(engine: Engine, df: pd.DataFrame) -> int:
    """透過暫存表將訓練資料集 upsert 至目標表。"""
    if df.empty:
        print("[WARN] output dataframe is empty, nothing to write")
        return 0

    temp_table = "tmp_ml_training_generation_daily"

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
            training_date,
            target_generation_kwh,
            target_available,
            target_type,
            year_num,
            month_num,
            day_num,
            day_of_year,
            week_of_year,
            weekday_num,
            is_weekend,
            season_code,
            install_area_ping,
            capacity_kw,
            panel_efficiency,
            site_region,
            site_county,
            site_type,
            sunshine_hours,
            sunshine_rate_pct,
            solar_radiation_mj_m2,
            pop_value,
            pop_type,
            forecast_issue_time,
            rain_risk_flag,
            cloudy_risk_flag,
            estimated_generation_rule_kwh,
            generation_per_ping,
            sunshine_x_area,
            radiation_x_area,
            pop_x_sunshine,
            lag_1_generation_kwh,
            lag_3_avg_generation_kwh,
            lag_7_avg_generation_kwh,
            lag_14_avg_generation_kwh,
            lag_1_sunshine_hours,
            lag_3_avg_sunshine_hours,
            feature_missing_cnt,
            is_valid_for_training,
            invalid_reason
        )
        SELECT
            run_id,
            site_sk,
            location_sk,
            training_date,
            target_generation_kwh,
            target_available,
            target_type,
            year_num,
            month_num,
            day_num,
            day_of_year,
            week_of_year,
            weekday_num,
            is_weekend,
            season_code,
            install_area_ping,
            capacity_kw,
            panel_efficiency,
            site_region,
            site_county,
            site_type,
            sunshine_hours,
            sunshine_rate_pct,
            solar_radiation_mj_m2,
            pop_value,
            pop_type,
            forecast_issue_time,
            rain_risk_flag,
            cloudy_risk_flag,
            estimated_generation_rule_kwh,
            generation_per_ping,
            sunshine_x_area,
            radiation_x_area,
            pop_x_sunshine,
            lag_1_generation_kwh,
            lag_3_avg_generation_kwh,
            lag_7_avg_generation_kwh,
            lag_14_avg_generation_kwh,
            lag_1_sunshine_hours,
            lag_3_avg_sunshine_hours,
            feature_missing_cnt,
            is_valid_for_training,
            invalid_reason
        FROM {temp_table}
        ON CONFLICT (site_sk, training_date)
        DO UPDATE SET
            run_id = EXCLUDED.run_id,
            location_sk = EXCLUDED.location_sk,
            target_generation_kwh = EXCLUDED.target_generation_kwh,
            target_available = EXCLUDED.target_available,
            target_type = EXCLUDED.target_type,
            year_num = EXCLUDED.year_num,
            month_num = EXCLUDED.month_num,
            day_num = EXCLUDED.day_num,
            day_of_year = EXCLUDED.day_of_year,
            week_of_year = EXCLUDED.week_of_year,
            weekday_num = EXCLUDED.weekday_num,
            is_weekend = EXCLUDED.is_weekend,
            season_code = EXCLUDED.season_code,
            install_area_ping = EXCLUDED.install_area_ping,
            capacity_kw = EXCLUDED.capacity_kw,
            panel_efficiency = EXCLUDED.panel_efficiency,
            site_region = EXCLUDED.site_region,
            site_county = EXCLUDED.site_county,
            site_type = EXCLUDED.site_type,
            sunshine_hours = EXCLUDED.sunshine_hours,
            sunshine_rate_pct = EXCLUDED.sunshine_rate_pct,
            solar_radiation_mj_m2 = EXCLUDED.solar_radiation_mj_m2,
            pop_value = EXCLUDED.pop_value,
            pop_type = EXCLUDED.pop_type,
            forecast_issue_time = EXCLUDED.forecast_issue_time,
            rain_risk_flag = EXCLUDED.rain_risk_flag,
            cloudy_risk_flag = EXCLUDED.cloudy_risk_flag,
            estimated_generation_rule_kwh = EXCLUDED.estimated_generation_rule_kwh,
            generation_per_ping = EXCLUDED.generation_per_ping,
            sunshine_x_area = EXCLUDED.sunshine_x_area,
            radiation_x_area = EXCLUDED.radiation_x_area,
            pop_x_sunshine = EXCLUDED.pop_x_sunshine,
            lag_1_generation_kwh = EXCLUDED.lag_1_generation_kwh,
            lag_3_avg_generation_kwh = EXCLUDED.lag_3_avg_generation_kwh,
            lag_7_avg_generation_kwh = EXCLUDED.lag_7_avg_generation_kwh,
            lag_14_avg_generation_kwh = EXCLUDED.lag_14_avg_generation_kwh,
            lag_1_sunshine_hours = EXCLUDED.lag_1_sunshine_hours,
            lag_3_avg_sunshine_hours = EXCLUDED.lag_3_avg_sunshine_hours,
            feature_missing_cnt = EXCLUDED.feature_missing_cnt,
            is_valid_for_training = EXCLUDED.is_valid_for_training,
            invalid_reason = EXCLUDED.invalid_reason,
            loaded_at = CURRENT_TIMESTAMP
    """)

    with engine.begin() as conn:
        conn.execute(sql)
        conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))

    return len(df)


# ============================================================
# 資料集建置紀錄
# ============================================================
def insert_dataset_log(engine: Engine, run_id: int, df: pd.DataFrame) -> None:
    """寫入訓練資料集建置摘要，包含列數、日期範圍與缺失率。"""
    if not table_exists(engine, DATASET_LOG_TABLE):
        print(f"[WARN] {DATASET_LOG_TABLE} not found, skip dataset log")
        return

    total_rows = len(df)
    valid_rows = int(df["is_valid_for_training"].sum()) if "is_valid_for_training" in df.columns else 0
    invalid_rows = total_rows - valid_rows

    feature_cols = [
        "target_generation_kwh",
        "sunshine_hours",
        "install_area_ping",
        "lag_1_generation_kwh",
        "lag_3_avg_generation_kwh",
        "lag_7_avg_generation_kwh",
    ]

    missing_rate_pct = float(df[feature_cols].isna().mean().mean() * 100) if total_rows > 0 else 0.0

    date_from = pd.to_datetime(df["training_date"]).min().date() if total_rows > 0 else None
    date_to = pd.to_datetime(df["training_date"]).max().date() if total_rows > 0 else None

    sql = text(f"""
        INSERT INTO {DATASET_LOG_TABLE}
        (
            run_id,
            dataset_name,
            date_from,
            date_to,
            total_rows,
            valid_rows,
            invalid_rows,
            missing_rate_pct,
            target_type
        )
        VALUES
        (
            :run_id,
            :dataset_name,
            :date_from,
            :date_to,
            :total_rows,
            :valid_rows,
            :invalid_rows,
            :missing_rate_pct,
            :target_type
        )
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "dataset_name": TARGET_TABLE,
            "date_from": date_from,
            "date_to": date_to,
            "total_rows": total_rows,
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows,
            "missing_rate_pct": round(missing_rate_pct, 2),
            "target_type": "simulated",
        })


# ============================================================
# 主流程
# ============================================================
def main() -> None:
    """執行 ML 每日訓練資料集建立流程。"""
    engine = get_engine()
    run_id: Optional[int] = None

    try:
        print("=" * 80)
        print(f"[INFO] start pipeline: {PIPELINE_NAME}")

        run_id = get_next_run_id(engine, PIPELINE_NAME)

        base_df = read_base_dataset(engine)
        if base_df.empty:
            msg = "No rows found from base dataset."
            print(f"[WARN] {msg}")
            update_run_log_success(engine, run_id, rows_raw=0, rows_mart=0, message=msg)
            return

        training_df = build_training_dataset(base_df)
        output_df = prepare_output_df(training_df, run_id=run_id)

        print(f"[INFO] output rows prepared = {len(output_df)}")
        print("[INFO] output preview:")
        print(output_df.head(10).to_string(index=False))

        affected_rows = upsert_target_table(engine, output_df)
        insert_dataset_log(engine, run_id, output_df)

        msg = f"build_training_dataset success; affected_rows={affected_rows}"
        print(f"[INFO] {msg}")

        update_run_log_success(
            engine,
            run_id=run_id,
            rows_raw=len(base_df),
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