import os
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Tuple, List

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


# ============================================================
# Config
# ============================================================
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

RUN_LOG_TABLE = "meta.etl_run_log"
SOURCE_TABLE = "mart.ml_training_generation_daily"

PIPELINE_NAME = "train_baseline_model"

STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"

# Artifact output dir
BASE_DIR = Path(__file__).resolve().parent
ARTIFACT_DIR = BASE_DIR / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_DIR = ARTIFACT_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

REPORT_DIR = ARTIFACT_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

PRED_DIR = ARTIFACT_DIR / "predictions"
PRED_DIR.mkdir(parents=True, exist_ok=True)


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
# Data Load
# ============================================================
def read_training_data(engine: Engine) -> pd.DataFrame:
    sql = f"""
    SELECT
        site_sk,
        location_sk,
        training_date::date AS training_date,
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
    WHERE is_valid_for_training = TRUE
      AND target_available = TRUE
    ORDER BY training_date, site_sk
    """

    df = pd.read_sql(sql, engine)
    print(f"[INFO] training rows loaded = {len(df)}")
    print(f"[INFO] training columns = {list(df.columns)}")
    return df


# ============================================================
# Split
# ============================================================
def time_based_split(
    df: pd.DataFrame,
    train_ratio: float = 0.70,
    valid_ratio: float = 0.15,
    test_ratio: float = 0.15
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not np.isclose(train_ratio + valid_ratio + test_ratio, 1.0):
        raise ValueError("train_ratio + valid_ratio + test_ratio must equal 1.0")

    out = df.copy()
    out["training_date"] = pd.to_datetime(out["training_date"])

    unique_dates = sorted(out["training_date"].dropna().unique())
    if len(unique_dates) < 10:
        raise ValueError("Too few unique dates for stable time-based split.")

    n_dates = len(unique_dates)
    train_end_idx = max(1, int(n_dates * train_ratio))
    valid_end_idx = max(train_end_idx + 1, int(n_dates * (train_ratio + valid_ratio)))

    train_dates = unique_dates[:train_end_idx]
    valid_dates = unique_dates[train_end_idx:valid_end_idx]
    test_dates = unique_dates[valid_end_idx:]

    train_df = out[out["training_date"].isin(train_dates)].copy()
    valid_df = out[out["training_date"].isin(valid_dates)].copy()
    test_df = out[out["training_date"].isin(test_dates)].copy()

    print("[INFO] split summary:")
    print(f"       train rows = {len(train_df)}, dates = {train_df['training_date'].min()} ~ {train_df['training_date'].max()}")
    print(f"       valid rows = {len(valid_df)}, dates = {valid_df['training_date'].min()} ~ {valid_df['training_date'].max()}")
    print(f"       test  rows = {len(test_df)}, dates = {test_df['training_date'].min()} ~ {test_df['training_date'].max()}")

    if train_df.empty or valid_df.empty or test_df.empty:
        raise ValueError("One of train/valid/test split is empty. Please adjust split ratios.")

    return train_df, valid_df, test_df


# ============================================================
# Metrics
# ============================================================
def safe_mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    mask = y_true != 0
    if mask.sum() == 0:
        return np.nan

    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100.0)


def calc_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mape = safe_mape(y_true, y_pred)

    return {
        "mae": round(float(mae), 4),
        "rmse": round(float(rmse), 4),
        "mape": round(float(mape), 4) if pd.notna(mape) else np.nan
    }


# ============================================================
# Feature Selection
# ============================================================
def get_feature_columns() -> Tuple[List[str], List[str]]:
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


def build_preprocessor(
    numeric_features: List[str],
    categorical_features: List[str]
) -> ColumnTransformer:
    numeric_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )

    categorical_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_transformer, numeric_features),
            ("cat", categorical_transformer, categorical_features),
        ]
    )

    return preprocessor


# ============================================================
# Models
# ============================================================
def train_linear_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    numeric_features: List[str],
    categorical_features: List[str]
) -> Pipeline:
    preprocessor = build_preprocessor(numeric_features, categorical_features)

    model = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("model", LinearRegression())
        ]
    )

    model.fit(X_train, y_train)
    return model


def train_random_forest_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    numeric_features: List[str],
    categorical_features: List[str]
) -> Pipeline:
    preprocessor = build_preprocessor(numeric_features, categorical_features)

    model = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("model", RandomForestRegressor(
                n_estimators=300,
                max_depth=8,
                min_samples_split=8,
                min_samples_leaf=4,
                random_state=42,
                n_jobs=-1
            ))
        ]
    )

    model.fit(X_train, y_train)
    return model


def predict_naive_lag1(df: pd.DataFrame) -> np.ndarray:
    pred = pd.to_numeric(df["lag_1_generation_kwh"], errors="coerce").copy()

    # 璝 lag_1 アノ lag_3 干ぃ︽ノ estimated_generation_rule_kwh 干
    pred = pred.fillna(pd.to_numeric(df["lag_3_avg_generation_kwh"], errors="coerce"))
    pred = pred.fillna(pd.to_numeric(df["estimated_generation_rule_kwh"], errors="coerce"))

    # 程临钡干俱砰い计
    if pred.isna().any():
        pred = pred.fillna(pred.median())

    return pred.to_numpy(dtype=float)


# ============================================================
# Training / Evaluation
# ============================================================
def prepare_xy(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    y = pd.to_numeric(df["target_generation_kwh"], errors="coerce")
    X = df.copy()

    drop_cols = [
        "training_date",
        "target_generation_kwh",
        "is_valid_for_training",
        "invalid_reason",
    ]
    X = X.drop(columns=drop_cols, errors="ignore")

    return X, y


def evaluate_model(
    model_name: str,
    y_valid: np.ndarray,
    valid_pred: np.ndarray,
    y_test: np.ndarray,
    test_pred: np.ndarray
) -> Dict[str, float]:
    valid_metrics = calc_metrics(y_valid, valid_pred)
    test_metrics = calc_metrics(y_test, test_pred)

    return {
        "model_name": model_name,
        "valid_mae": valid_metrics["mae"],
        "valid_rmse": valid_metrics["rmse"],
        "valid_mape": valid_metrics["mape"],
        "test_mae": test_metrics["mae"],
        "test_rmse": test_metrics["rmse"],
        "test_mape": test_metrics["mape"],
    }


def save_predictions(
    model_name: str,
    df: pd.DataFrame,
    pred: np.ndarray,
    split_name: str
) -> Path:
    out = df[["site_sk", "location_sk", "training_date", "target_generation_kwh"]].copy()
    out["prediction"] = pred
    out["model_name"] = model_name
    out["split_name"] = split_name
    out["abs_error"] = np.abs(out["target_generation_kwh"] - out["prediction"])

    path = PRED_DIR / f"{model_name}_{split_name}_predictions.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_metrics_report(metrics_rows: List[Dict[str, float]]) -> Path:
    df = pd.DataFrame(metrics_rows).sort_values(["valid_mae", "test_mae"], ascending=[True, True])
    path = REPORT_DIR / "model_metrics_summary.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_best_model(model, model_name: str, metadata: Dict) -> Tuple[Path, Path]:
    model_path = MODEL_DIR / f"{model_name}_best.joblib"
    meta_path = MODEL_DIR / f"{model_name}_best_metadata.json"

    joblib.dump(model, model_path)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)

    return model_path, meta_path


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

        df = read_training_data(engine)
        if df.empty:
            msg = "No valid training rows found."
            print(f"[WARN] {msg}")
            update_run_log_success(engine, run_id, rows_raw=0, rows_mart=0, message=msg)
            return

        df["training_date"] = pd.to_datetime(df["training_date"])
        train_df, valid_df, test_df = time_based_split(df)

        numeric_features, categorical_features = get_feature_columns()

        X_train, y_train = prepare_xy(train_df)
        X_valid, y_valid = prepare_xy(valid_df)
        X_test, y_test = prepare_xy(test_df)

        metrics_rows = []

        # 1. Naive baseline
        naive_valid_pred = predict_naive_lag1(valid_df)
        naive_test_pred = predict_naive_lag1(test_df)

        metrics_rows.append(
            evaluate_model(
                model_name="naive_lag1",
                y_valid=y_valid.to_numpy(dtype=float),
                valid_pred=naive_valid_pred,
                y_test=y_test.to_numpy(dtype=float),
                test_pred=naive_test_pred
            )
        )

        save_predictions("naive_lag1", valid_df, naive_valid_pred, "valid")
        save_predictions("naive_lag1", test_df, naive_test_pred, "test")

        # 2. Linear Regression
        lr_model = train_linear_model(
            X_train, y_train,
            numeric_features=numeric_features,
            categorical_features=categorical_features
        )

        lr_valid_pred = lr_model.predict(X_valid)
        lr_test_pred = lr_model.predict(X_test)

        metrics_rows.append(
            evaluate_model(
                model_name="linear_regression",
                y_valid=y_valid.to_numpy(dtype=float),
                valid_pred=lr_valid_pred,
                y_test=y_test.to_numpy(dtype=float),
                test_pred=lr_test_pred
            )
        )

        save_predictions("linear_regression", valid_df, lr_valid_pred, "valid")
        save_predictions("linear_regression", test_df, lr_test_pred, "test")

        # 3. Random Forest
        rf_model = train_random_forest_model(
            X_train, y_train,
            numeric_features=numeric_features,
            categorical_features=categorical_features
        )

        rf_valid_pred = rf_model.predict(X_valid)
        rf_test_pred = rf_model.predict(X_test)

        metrics_rows.append(
            evaluate_model(
                model_name="random_forest",
                y_valid=y_valid.to_numpy(dtype=float),
                valid_pred=rf_valid_pred,
                y_test=y_test.to_numpy(dtype=float),
                test_pred=rf_test_pred
            )
        )

        save_predictions("random_forest", valid_df, rf_valid_pred, "valid")
        save_predictions("random_forest", test_df, rf_test_pred, "test")

        # Metrics summary
        metrics_path = save_metrics_report(metrics_rows)
        metrics_df = pd.read_csv(metrics_path)

        print("[INFO] model metrics summary:")
        print(metrics_df.to_string(index=False))

        # Pick best model by valid_mae
        best_row = metrics_df.sort_values(["valid_mae", "test_mae"], ascending=[True, True]).iloc[0]
        best_model_name = str(best_row["model_name"])

        if best_model_name == "linear_regression":
            best_model = lr_model
        elif best_model_name == "random_forest":
            best_model = rf_model
        else:
            best_model = None  # naive ぃ sklearn model

        if best_model is not None:
            metadata = {
                "run_id": run_id,
                "pipeline_name": PIPELINE_NAME,
                "source_table": SOURCE_TABLE,
                "best_model_name": best_model_name,
                "train_rows": len(train_df),
                "valid_rows": len(valid_df),
                "test_rows": len(test_df),
                "train_date_from": str(train_df["training_date"].min().date()),
                "train_date_to": str(train_df["training_date"].max().date()),
                "valid_date_from": str(valid_df["training_date"].min().date()),
                "valid_date_to": str(valid_df["training_date"].max().date()),
                "test_date_from": str(test_df["training_date"].min().date()),
                "test_date_to": str(test_df["training_date"].max().date()),
                "metrics": metrics_df.to_dict(orient="records"),
                "numeric_features": numeric_features,
                "categorical_features": categorical_features,
                "created_at": datetime.now().isoformat()
            }

            model_path, meta_path = save_best_model(best_model, best_model_name, metadata)
            print(f"[INFO] best model saved: {model_path}")
            print(f"[INFO] model metadata saved: {meta_path}")
        else:
            print("[INFO] best model is naive_lag1, skip joblib save")

        msg = f"train_baseline_model success; rows={len(df)}; best_model={best_model_name}"
        print(f"[INFO] {msg}")

        update_run_log_success(
            engine,
            run_id=run_id,
            rows_raw=len(df),
            rows_mart=len(metrics_rows),
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