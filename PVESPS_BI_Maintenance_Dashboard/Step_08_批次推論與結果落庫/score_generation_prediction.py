import os
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestRegressor


# ============================================================
# Config
# ============================================================
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

RUN_LOG_TABLE = "meta.etl_run_log"
SCORING_LOG_TABLE = "meta.model_scoring_run_log"
SOURCE_TABLE = "mart.ml_training_generation_daily"
TARGET_TABLE = "mart.fact_generation_prediction_daily"

PIPELINE_NAME = "score_generation_prediction"

STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_PARTIAL_SUCCESS = "PARTIAL_SUCCESS"

# 預設 scoring 類型：
# historical_backtest = 使用既有 target 回填 actual / error
# day_ahead = 預測未來資料，actual 先留空
PREDICTION_TYPE = "historical_backtest"

# 預設抓最新 N 天做 scoring
SCORING_DAYS = 14

# 預設使用 Step_07 artifacts
BASE_DIR = Path(__file__).resolve().parent
STEP07_MODEL_DIR = BASE_DIR.parent / "Step_07_訓練基準模型與樹模型" / "artifacts" / "models"

# 可手動指定模型
DEFAULT_MODEL_NAME = "random_forest"
DEFAULT_MODEL_VERSION = "v1"

# 若 metadata 找得到 best_model_name，會優先用 metadata
METADATA_PATTERN = "*_best_metadata.json"
MODEL_PATTERN = "*_best.joblib"


# ============================================================
# DB Helpers
# ============================================================
def get_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        future=True,
        pool_pre_ping=True,
    )


def table_exists(engine: Engine, full_table_name: str) -> bool:
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
# Model Helpers
# ============================================================
def get_default_feature_columns() -> Tuple[List[str], List[str]]:
    numeric_features = [
        "site_sk",
        "location_sk",
        "month_num",
        "day_num",
        "day_of_year",
        "week_of_year",
        "weekday_num",
        "install_area_ping",
        "capacity_kw",
        "panel_efficiency",
        "sunshine_hours",
        "sunshine_rate_pct",
        "solar_radiation_mj_m2",
        "pop_value",
        "estimated_generation_rule_kwh",
        "generation_per_ping",
        "sunshine_x_area",
        "radiation_x_area",
        "pop_x_sunshine",
        "lag_1_generation_kwh",
        "lag_3_avg_generation_kwh",
        "lag_7_avg_generation_kwh",
        "lag_14_avg_generation_kwh",
        "lag_1_sunshine_hours",
        "lag_3_avg_sunshine_hours",
        "feature_missing_cnt",
    ]

    categorical_features = [
        "season_code",
        "site_region",
        "site_county",
        "site_type",
        "target_type",
        "pop_type",
        "is_weekend",
        "rain_risk_flag",
        "cloudy_risk_flag",
    ]

    return numeric_features, categorical_features


def load_best_model_and_metadata() -> Tuple[object, dict, str, str]:
    if not STEP07_MODEL_DIR.exists():
        raise FileNotFoundError(f"Model directory not found: {STEP07_MODEL_DIR}")

    metadata_files = sorted(STEP07_MODEL_DIR.glob(METADATA_PATTERN), key=lambda p: p.stat().st_mtime, reverse=True)
    model_files = sorted(STEP07_MODEL_DIR.glob(MODEL_PATTERN), key=lambda p: p.stat().st_mtime, reverse=True)

    if metadata_files:
        meta_path = metadata_files[0]
        with open(meta_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        model_name = metadata.get("best_model_name", DEFAULT_MODEL_NAME)
        model_version = metadata.get("model_version", DEFAULT_MODEL_VERSION)

        candidate_model_path = STEP07_MODEL_DIR / f"{model_name}_best.joblib"
        if candidate_model_path.exists():
            model = joblib.load(candidate_model_path)
            print(f"[INFO] loaded model from metadata: {candidate_model_path}")
            print(f"[INFO] loaded model metadata: {meta_path}")
            return model, metadata, model_name, model_version

    if model_files:
        model_path = model_files[0]
        model = joblib.load(model_path)
        metadata = {}
        model_name = model_path.stem.replace("_best", "")
        model_version = DEFAULT_MODEL_VERSION
        print(f"[INFO] loaded fallback model: {model_path}")
        return model, metadata, model_name, model_version

    raise FileNotFoundError(f"No model artifact found under: {STEP07_MODEL_DIR}")


# ============================================================
# Data Load
# ============================================================
def get_latest_scoring_dates(engine: Engine, scoring_days: int) -> Tuple[datetime.date, datetime.date]:
    sql = text(f"""
        WITH latest_dates AS (
            SELECT DISTINCT training_date::date AS training_date
            FROM {SOURCE_TABLE}
            WHERE is_valid_for_training = TRUE
            ORDER BY training_date DESC
            LIMIT :scoring_days
        )
        SELECT MIN(training_date), MAX(training_date)
        FROM latest_dates
    """)

    with engine.connect() as conn:
        row = conn.execute(sql, {"scoring_days": scoring_days}).fetchone()

    if row is None or row[0] is None or row[1] is None:
        raise ValueError("Cannot determine latest scoring dates from source table.")

    return row[0], row[1]


def read_scoring_input(
    engine: Engine,
    date_from,
    date_to,
    prediction_type: str
) -> pd.DataFrame:
    base_filter = """
        WHERE is_valid_for_training = TRUE
          AND training_date::date BETWEEN :date_from AND :date_to
    """

    # historical_backtest 需要 target 來算誤差
    if prediction_type == "historical_backtest":
        base_filter += "\n  AND target_available = TRUE"

    sql = text(f"""
        SELECT
            site_sk,
            location_sk,
            training_date::date AS prediction_date,

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
        FROM {SOURCE_TABLE}
        {base_filter}
        ORDER BY prediction_date, site_sk
    """)

    df = pd.read_sql(sql, engine, params={
        "date_from": date_from,
        "date_to": date_to
    })

    print(f"[INFO] scoring rows loaded = {len(df)}")
    print(f"[INFO] scoring columns = {list(df.columns)}")
    return df


# ============================================================
# Prediction Helpers
# ============================================================
def prepare_scoring_features(df: pd.DataFrame) -> pd.DataFrame:
    X = df.copy()

    drop_cols = [
        "prediction_date",
        "target_generation_kwh",
        "is_valid_for_training",
        "invalid_reason",
    ]
    X = X.drop(columns=drop_cols, errors="ignore")
    return X


def estimate_prediction_interval(model, X: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """
    若為 sklearn Pipeline + RandomForestRegressor，使用 individual trees 估簡易區間。
    否則回傳全 NaN。
    """
    n = len(X)
    lower = np.full(n, np.nan, dtype=float)
    upper = np.full(n, np.nan, dtype=float)

    try:
        if not isinstance(model, Pipeline):
            return lower, upper

        if "preprocessor" not in model.named_steps or "model" not in model.named_steps:
            return lower, upper

        inner_model = model.named_steps["model"]
        preprocessor = model.named_steps["preprocessor"]

        if not isinstance(inner_model, RandomForestRegressor):
            return lower, upper

        X_transformed = preprocessor.transform(X)
        tree_preds = np.column_stack([tree.predict(X_transformed) for tree in inner_model.estimators_])

        lower = np.percentile(tree_preds, 10, axis=1)
        upper = np.percentile(tree_preds, 90, axis=1)
        return lower, upper

    except Exception as ex:
        print(f"[WARN] prediction interval estimation skipped: {type(ex).__name__}: {ex}")
        return lower, upper


def build_prediction_output(
    df: pd.DataFrame,
    pred: np.ndarray,
    lower_bound: np.ndarray,
    upper_bound: np.ndarray,
    run_id: int,
    model_name: str,
    model_version: str,
    prediction_type: str,
    source_table: str,
    date_from,
    date_to
) -> pd.DataFrame:
    out = df.copy()

    out["predicted_generation_kwh"] = np.round(pred, 2)
    out["lower_bound_kwh"] = np.round(lower_bound, 2)
    out["upper_bound_kwh"] = np.round(upper_bound, 2)

    if prediction_type == "historical_backtest":
        out["actual_generation_kwh"] = pd.to_numeric(out["target_generation_kwh"], errors="coerce").round(2)
        out["prediction_error_kwh"] = (out["actual_generation_kwh"] - out["predicted_generation_kwh"]).round(2)
        out["abs_error_kwh"] = np.abs(out["prediction_error_kwh"]).round(2)

        actual = pd.to_numeric(out["actual_generation_kwh"], errors="coerce")
        ape = np.where(actual != 0, np.abs((actual - out["predicted_generation_kwh"]) / actual) * 100, np.nan)
        out["abs_pct_error"] = np.round(ape, 4)
        out["is_actual_backfilled"] = True
    else:
        out["actual_generation_kwh"] = np.nan
        out["prediction_error_kwh"] = np.nan
        out["abs_error_kwh"] = np.nan
        out["abs_pct_error"] = np.nan
        out["is_actual_backfilled"] = False

    result = pd.DataFrame({
        "run_id": run_id,
        "site_sk": out["site_sk"].astype("Int64"),
        "location_sk": out["location_sk"].astype("Int64"),
        "prediction_date": pd.to_datetime(out["prediction_date"]).dt.date,

        "model_name": model_name,
        "model_version": model_version,
        "prediction_type": prediction_type,

        "predicted_generation_kwh": pd.to_numeric(out["predicted_generation_kwh"], errors="coerce").round(2),
        "actual_generation_kwh": pd.to_numeric(out["actual_generation_kwh"], errors="coerce").round(2),
        "prediction_error_kwh": pd.to_numeric(out["prediction_error_kwh"], errors="coerce").round(2),
        "abs_error_kwh": pd.to_numeric(out["abs_error_kwh"], errors="coerce").round(2),
        "abs_pct_error": pd.to_numeric(out["abs_pct_error"], errors="coerce").round(4),

        "lower_bound_kwh": pd.to_numeric(out["lower_bound_kwh"], errors="coerce").round(2),
        "upper_bound_kwh": pd.to_numeric(out["upper_bound_kwh"], errors="coerce").round(2),

        "is_latest_prediction": True,
        "is_actual_backfilled": out["is_actual_backfilled"].fillna(False).astype(bool),

        "source_table": source_table,
        "source_date_from": date_from,
        "source_date_to": date_to,
        "source_row_count": len(out),
    })

    result = result.drop_duplicates(
        subset=["site_sk", "prediction_date", "model_name", "model_version", "prediction_type"],
        keep="last"
    ).copy()

    return result


# ============================================================
# Upsert Prediction Table
# ============================================================
def upsert_prediction_table(engine: Engine, df: pd.DataFrame) -> int:
    if df.empty:
        print("[WARN] prediction output is empty, nothing to write")
        return 0

    temp_table = "tmp_fact_generation_prediction_daily"

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

    # 先把同 site/date/type 的舊資料標成非 latest
    deactivate_sql = text(f"""
        UPDATE {TARGET_TABLE} tgt
        SET
            is_latest_prediction = FALSE,
            updated_at = CURRENT_TIMESTAMP
        FROM {temp_table} src
        WHERE tgt.site_sk = src.site_sk
          AND tgt.prediction_date = src.prediction_date
          AND tgt.prediction_type = src.prediction_type
          AND (
                COALESCE(tgt.model_name, '') <> COALESCE(src.model_name, '')
             OR COALESCE(tgt.model_version, '') <> COALESCE(src.model_version, '')
          )
    """)

    upsert_sql = text(f"""
        INSERT INTO {TARGET_TABLE}
        (
            run_id,
            site_sk,
            location_sk,
            prediction_date,
            model_name,
            model_version,
            prediction_type,
            predicted_generation_kwh,
            actual_generation_kwh,
            prediction_error_kwh,
            abs_error_kwh,
            abs_pct_error,
            lower_bound_kwh,
            upper_bound_kwh,
            is_latest_prediction,
            is_actual_backfilled,
            source_table,
            source_date_from,
            source_date_to,
            source_row_count
        )
        SELECT
            run_id,
            site_sk,
            location_sk,
            prediction_date,
            model_name,
            model_version,
            prediction_type,
            predicted_generation_kwh,
            actual_generation_kwh,
            prediction_error_kwh,
            abs_error_kwh,
            abs_pct_error,
            lower_bound_kwh,
            upper_bound_kwh,
            is_latest_prediction,
            is_actual_backfilled,
            source_table,
            source_date_from,
            source_date_to,
            source_row_count
        FROM {temp_table}
        ON CONFLICT (site_sk, prediction_date, model_name, model_version, prediction_type)
        DO UPDATE SET
            run_id = EXCLUDED.run_id,
            location_sk = EXCLUDED.location_sk,
            predicted_generation_kwh = EXCLUDED.predicted_generation_kwh,
            actual_generation_kwh = EXCLUDED.actual_generation_kwh,
            prediction_error_kwh = EXCLUDED.prediction_error_kwh,
            abs_error_kwh = EXCLUDED.abs_error_kwh,
            abs_pct_error = EXCLUDED.abs_pct_error,
            lower_bound_kwh = EXCLUDED.lower_bound_kwh,
            upper_bound_kwh = EXCLUDED.upper_bound_kwh,
            is_latest_prediction = EXCLUDED.is_latest_prediction,
            is_actual_backfilled = EXCLUDED.is_actual_backfilled,
            source_table = EXCLUDED.source_table,
            source_date_from = EXCLUDED.source_date_from,
            source_date_to = EXCLUDED.source_date_to,
            source_row_count = EXCLUDED.source_row_count,
            updated_at = CURRENT_TIMESTAMP
    """)

    with engine.begin() as conn:
        conn.execute(deactivate_sql)
        conn.execute(upsert_sql)
        conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))

    return len(df)


# ============================================================
# Scoring Run Log
# ============================================================
def insert_scoring_run_log(
    engine: Engine,
    run_id: int,
    model_name: str,
    model_version: str,
    prediction_type: str,
    source_table: str,
    date_from,
    date_to,
    total_input_rows: int,
    scored_rows: int,
    output_rows: int,
    failed_rows: int,
    status: str,
    message: str
) -> None:
    if not table_exists(engine, SCORING_LOG_TABLE):
        print(f"[WARN] {SCORING_LOG_TABLE} not found, skip scoring log")
        return

    sql = text(f"""
        INSERT INTO {SCORING_LOG_TABLE}
        (
            run_id,
            pipeline_name,
            model_name,
            model_version,
            prediction_type,
            source_table,
            score_date_from,
            score_date_to,
            total_input_rows,
            scored_rows,
            output_rows,
            failed_rows,
            status,
            message
        )
        VALUES
        (
            :run_id,
            :pipeline_name,
            :model_name,
            :model_version,
            :prediction_type,
            :source_table,
            :score_date_from,
            :score_date_to,
            :total_input_rows,
            :scored_rows,
            :output_rows,
            :failed_rows,
            :status,
            :message
        )
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "pipeline_name": PIPELINE_NAME,
            "model_name": model_name,
            "model_version": model_version,
            "prediction_type": prediction_type,
            "source_table": source_table,
            "score_date_from": date_from,
            "score_date_to": date_to,
            "total_input_rows": total_input_rows,
            "scored_rows": scored_rows,
            "output_rows": output_rows,
            "failed_rows": failed_rows,
            "status": status,
            "message": message[:1000]
        })


# ============================================================
# Main
# ============================================================
def main() -> None:
    engine = get_engine()
    run_id: Optional[int] = None

    try:
        print("=" * 80)
        print(f"[INFO] start pipeline: {PIPELINE_NAME}")

        run_id = get_next_run_id(engine, PIPELINE_NAME)

        model, metadata, model_name, model_version = load_best_model_and_metadata()

        # 若 metadata 有 model_version 就沿用；否則預設 v1
        model_version = metadata.get("model_version", model_version) if metadata else model_version

        if metadata:
            numeric_features = metadata.get("numeric_features")
            categorical_features = metadata.get("categorical_features")
        else:
            numeric_features, categorical_features = get_default_feature_columns()

        if not numeric_features or not categorical_features:
            numeric_features, categorical_features = get_default_feature_columns()

        date_from, date_to = get_latest_scoring_dates(engine, SCORING_DAYS)
        print(f"[INFO] scoring window = {date_from} ~ {date_to}")

        scoring_df = read_scoring_input(
            engine=engine,
            date_from=date_from,
            date_to=date_to,
            prediction_type=PREDICTION_TYPE
        )

        if scoring_df.empty:
            msg = "No scoring input rows found."
            print(f"[WARN] {msg}")
            insert_scoring_run_log(
                engine, run_id,
                model_name=model_name,
                model_version=model_version,
                prediction_type=PREDICTION_TYPE,
                source_table=SOURCE_TABLE,
                date_from=date_from,
                date_to=date_to,
                total_input_rows=0,
                scored_rows=0,
                output_rows=0,
                failed_rows=0,
                status=STATUS_SUCCESS,
                message=msg
            )
            update_run_log_success(engine, run_id, rows_raw=0, rows_mart=0, message=msg)
            return

        X = prepare_scoring_features(scoring_df)

        # 預測
        pred = model.predict(X)
        lower_bound, upper_bound = estimate_prediction_interval(model, X)

        output_df = build_prediction_output(
            df=scoring_df,
            pred=pred,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            run_id=run_id,
            model_name=model_name,
            model_version=model_version,
            prediction_type=PREDICTION_TYPE,
            source_table=SOURCE_TABLE,
            date_from=date_from,
            date_to=date_to
        )

        print(f"[INFO] output rows prepared = {len(output_df)}")
        print("[INFO] output preview:")
        print(output_df.head(10).to_string(index=False))

        affected_rows = upsert_prediction_table(engine, output_df)

        msg = f"score_generation_prediction success; affected_rows={affected_rows}; model={model_name}; type={PREDICTION_TYPE}"
        print(f"[INFO] {msg}")

        insert_scoring_run_log(
            engine=engine,
            run_id=run_id,
            model_name=model_name,
            model_version=model_version,
            prediction_type=PREDICTION_TYPE,
            source_table=SOURCE_TABLE,
            date_from=date_from,
            date_to=date_to,
            total_input_rows=len(scoring_df),
            scored_rows=len(scoring_df),
            output_rows=affected_rows,
            failed_rows=0,
            status=STATUS_SUCCESS,
            message=msg
        )

        update_run_log_success(
            engine=engine,
            run_id=run_id,
            rows_raw=len(scoring_df),
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
            try:
                insert_scoring_run_log(
                    engine=engine,
                    run_id=run_id,
                    model_name=DEFAULT_MODEL_NAME,
                    model_version=DEFAULT_MODEL_VERSION,
                    prediction_type=PREDICTION_TYPE,
                    source_table=SOURCE_TABLE,
                    date_from=None,
                    date_to=None,
                    total_input_rows=0,
                    scored_rows=0,
                    output_rows=0,
                    failed_rows=0,
                    status=STATUS_FAILED,
                    message=err_msg
                )
            except Exception:
                pass

            update_run_log_failed(engine, run_id, err_msg)

        sys.exit(1)


if __name__ == "__main__":
    main()