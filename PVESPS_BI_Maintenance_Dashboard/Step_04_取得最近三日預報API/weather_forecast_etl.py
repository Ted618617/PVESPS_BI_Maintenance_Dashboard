"""中央氣象署三日天氣預報 ETL 腳本。

依資料庫中的天氣地點維度逐筆呼叫中央氣象署開放資料 API，擷取鄉鎮層級三小時降雨機率。
流程涵蓋 API 參數組裝、回應樣本保存、JSON 結構解析、既有預報刪除、事實表寫入與 ETL 執行紀錄更新。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# =========================================================
# 參數設定
# =========================================================

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"
OUTPUT_DIR = Path(__file__).resolve().parent
SAMPLE_RESPONSE_PATH = OUTPUT_DIR / "sample_response.json"

load_dotenv(ENV_PATH)

DATABASE_URL = os.getenv("DATABASE_URL")
CWA_API_KEY = os.getenv("CWA_API_KEY")

# 03/19 版本沿用的 API 端點策略。
CWA_BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093"

REQUEST_TIMEOUT = 30
VERIFY_SSL = False

# 依縣市對應 dataset id，沿用 03/19 版本策略。
COUNTY_DATASET_ID_MAP: dict[str, str] = {
    "宜蘭縣": "F-D0047-001",
    "桃園市": "F-D0047-005",
    "新竹縣": "F-D0047-009",
    "苗栗縣": "F-D0047-013",
    "彰化縣": "F-D0047-017",
    "南投縣": "F-D0047-021",
    "雲林縣": "F-D0047-025",
    "嘉義縣": "F-D0047-029",
    "屏東縣": "F-D0047-033",
    "臺東縣": "F-D0047-037",
    "花蓮縣": "F-D0047-041",
    "澎湖縣": "F-D0047-045",
    "基隆市": "F-D0047-049",
    "新竹市": "F-D0047-053",
    "嘉義市": "F-D0047-057",
    "臺北市": "F-D0047-061",
    "高雄市": "F-D0047-065",
    "新北市": "F-D0047-069",
    "臺中市": "F-D0047-073",
    "臺南市": "F-D0047-077",
    "連江縣": "F-D0047-081",
    "金門縣": "F-D0047-085",
}

# 目標天氣元素名稱，需與 API 回應中的實際欄位一致。
TARGET_ELEMENT_NAMES = ["3小時降雨機率"]


# =========================================================
# 資料模型
# =========================================================

@dataclass(frozen=True)
class WeatherLocation:
    """天氣地點維度資料，提供 API 查詢與事實表寫入所需鍵值。"""

    location_sk: int
    county_name: str
    town_name: str


# =========================================================
# 資料庫輔助函式
# =========================================================

def get_engine() -> Engine:
    """建立 PostgreSQL 連線引擎，缺少 DATABASE_URL 時中止流程。"""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable not set.")
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        future=True,
    )


def get_fact_weather_forecast_columns(engine: Engine) -> list[str]:
    """讀取 mart.fact_weather_forecast 欄位清單，供啟動時檢查。"""
    sql = """
    SELECT column_name
    FROM information_schema.columns
    WHERE table_schema = 'mart'
      AND table_name = 'fact_weather_forecast'
    ORDER BY ordinal_position;
    """
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        return [row[0] for row in result.fetchall()]


def get_identity_info(engine: Engine) -> bool:
    """確認 fact_id 是否為 identity 欄位。"""
    sql = """
    SELECT
        CASE
            WHEN is_identity = 'YES' THEN true
            ELSE false
        END AS fact_id_is_identity
    FROM information_schema.columns
    WHERE table_schema = 'mart'
      AND table_name = 'fact_weather_forecast'
      AND column_name = 'fact_id';
    """
    with engine.connect() as conn:
        result = conn.execute(text(sql)).scalar()
        return bool(result)


def load_locations(engine: Engine) -> list[WeatherLocation]:
    """載入所有天氣地點維度資料，作為 API 查詢範圍。"""
    sql = """
    SELECT
        location_sk,
        county_name,
        town_name
    FROM mart.dim_weather_location
    ORDER BY location_sk;
    """
    df = pd.read_sql(sql, engine)

    if df.empty:
        raise RuntimeError("No rows found in mart.dim_weather_location.")

    result: list[WeatherLocation] = []
    for _, row in df.iterrows():
        result.append(
            WeatherLocation(
                location_sk=int(row["location_sk"]),
                county_name=str(row["county_name"]).strip(),
                town_name=str(row["town_name"]).strip(),
            )
        )
    return result


# =========================================================
# ETL 執行紀錄
# =========================================================

def create_etl_run_log(
    engine: Engine,
    pipeline_name: str = "Step_04_weather_forecast_etl_v0_3_1",
) -> int:
    """建立 ETL 執行紀錄並回傳 run_id。"""
    sql = text(
        """
        INSERT INTO meta.etl_run_log (
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
        VALUES (
            :pipeline_name,
            NOW(),
            NOW(),
            :status,
            0,
            0,
            0,
            0,
            :message
        )
        RETURNING run_id;
        """
    )

    with engine.begin() as conn:
        run_id = conn.execute(
            sql,
            {
                "pipeline_name": pipeline_name,
                "status": "RUNNING",
                "message": "weather forecast ETL started",
            },
        ).scalar_one()

    return int(run_id)


def update_etl_run_log(
    engine: Engine,
    run_id: int,
    status: str,
    rows_raw: int = 0,
    rows_staging: int = 0,
    rows_mart: int = 0,
    rows_quarantine: int = 0,
    message: str | None = None,
) -> None:
    """更新 ETL 執行紀錄的完成狀態與資料列統計。"""
    sql = text(
        """
        UPDATE meta.etl_run_log
        SET
            finished_at = NOW(),
            status = :status,
            rows_raw = :rows_raw,
            rows_staging = :rows_staging,
            rows_mart = :rows_mart,
            rows_quarantine = :rows_quarantine,
            message = :message
        WHERE run_id = :run_id;
        """
    )

    with engine.begin() as conn:
        conn.execute(
            sql,
            {
                "run_id": run_id,
                "status": status,
                "rows_raw": rows_raw,
                "rows_staging": rows_staging,
                "rows_mart": rows_mart,
                "rows_quarantine": rows_quarantine,
                "message": (message or "")[:1000],
            },
        )


# =========================================================
# 中央氣象署 API
# =========================================================

def get_dataset_id_by_county(county_name: str) -> str:
    """依縣市名稱取得中央氣象署資料集代碼。"""
    dataset_id = COUNTY_DATASET_ID_MAP.get(county_name)
    if not dataset_id:
        raise KeyError(f"Unsupported county_name for dataset mapping: {county_name}")
    return dataset_id


def dump_sample_response(payload: dict[str, Any], output_path: Path) -> None:
    """保存首次 API 回應樣本，方便後續檢視欄位結構。"""
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def call_cwa_forecast_api(
    api_key: str,
    county_name: str,
    town_name: str,
) -> dict[str, Any]:
    """呼叫中央氣象署鄉鎮天氣預報 API 並回傳 JSON。"""
    dataset_id = get_dataset_id_by_county(county_name)

    params = {
        "Authorization": api_key,
        "format": "JSON",
        "locationId": dataset_id,
        "locationName": town_name,
        "elementName": ",".join(TARGET_ELEMENT_NAMES),
    }

    response = requests.get(
        CWA_BASE_URL,
        params=params,
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_SSL,
    )
    response.raise_for_status()

    payload = response.json()

    if not SAMPLE_RESPONSE_PATH.exists():
        dump_sample_response(payload, SAMPLE_RESPONSE_PATH)

    return payload


# =========================================================
# 回應解析輔助函式
# =========================================================

def parse_dt(value: str | None) -> datetime | None:
    """將 ISO 格式時間字串轉為 datetime，解析失敗時回傳 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def get_locations_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """取得 API 回應中的 locations 群組，兼容大小寫差異。"""
    records = payload.get("records", {})
    return (
        records.get("locations")
        or records.get("Locations")
        or []
    )


def get_location_list(group: dict[str, Any]) -> list[dict[str, Any]]:
    """取得單一群組中的地點清單，兼容大小寫差異。"""
    return (
        group.get("location")
        or group.get("Location")
        or []
    )


def get_weather_elements(location_record: dict[str, Any]) -> list[dict[str, Any]]:
    """取得地點資料中的天氣元素清單。"""
    return (
        location_record.get("weatherElement")
        or location_record.get("WeatherElement")
        or []
    )


def get_time_list(weather_element: dict[str, Any]) -> list[dict[str, Any]]:
    """取得天氣元素中的時間區間清單。"""
    return (
        weather_element.get("time")
        or weather_element.get("Time")
        or []
    )


def find_target_location_record(payload: dict[str, Any], town_name: str) -> dict[str, Any] | None:
    """從 API 回應中找出指定鄉鎮的地點資料區塊。"""
    target = str(town_name).strip()
    candidates: list[str] = []

    for group in get_locations_groups(payload):
        for loc in get_location_list(group):
            loc_name = str(
                loc.get("locationName")
                or loc.get("LocationName")
                or ""
            ).strip()

            if loc_name:
                candidates.append(loc_name)

            if loc_name == target:
                return loc

    print(f"[WARN] town block not found for town_name={target}. Available candidates={candidates}")
    return None


def extract_issue_time(payload: dict[str, Any]) -> datetime:
    """取得預報發布時間；回應缺少明確欄位時以目前時間作為 fallback。"""
    return datetime.now()


def parse_probability_value(time_item: dict[str, Any]) -> float | None:
    """解析單一時間區間的降雨機率數值。"""
    element_values = (
        time_item.get("ElementValue")
        or time_item.get("elementValue")
        or []
    )
    if not isinstance(element_values, list) or not element_values:
        return None

    first_value = element_values[0]

    # 對齊 sample_response_0319 的實際欄位。
    raw_value = (
        first_value.get("ProbabilityOfPrecipitation")
        or first_value.get("value")
        or first_value.get("Value")
    )

    if raw_value is None:
        return None

    s = str(raw_value).strip()
    if s == "":
        return None

    try:
        return float(s)
    except ValueError:
        return None


def parse_forecast_rows(
    payload: dict[str, Any],
    location_sk: int,
    county_name: str,
    town_name: str,
    run_id: int,
    batch_issue_time: datetime,
) -> list[dict[str, Any]]:
    """將 API 回應轉換為 fact_weather_forecast 可寫入資料列。"""
    rows: list[dict[str, Any]] = []

    location_record = find_target_location_record(payload, town_name)
    if not location_record:
        print(f"[WARN] town block not found: county={county_name}, town={town_name}")
        return rows

    weather_elements = get_weather_elements(location_record)
    all_element_names = [
        str(we.get("elementName") or we.get("ElementName") or "").strip()
        for we in weather_elements
    ]
    print(
        f"[DEBUG] county={county_name}, town={town_name}, "
        f"weather_elements={all_element_names}"
    )

    issue_time = extract_issue_time(payload)
    loaded_at = datetime.now()

    for we in weather_elements:
        element_name = str(
            we.get("elementName") or we.get("ElementName") or ""
        ).strip()

        if element_name not in TARGET_ELEMENT_NAMES and "降雨機率" not in element_name:
            continue

        time_list = get_time_list(we)
        print(
            f"[DEBUG] county={county_name}, town={town_name}, "
            f"pop_type={element_name}, time_count={len(time_list)}"
        )

        for time_item in time_list:
            start_time = (
                time_item.get("startTime")
                or time_item.get("StartTime")
            )
            end_time = (
                time_item.get("endTime")
                or time_item.get("EndTime")
            )
            pop_value = parse_probability_value(time_item)

            if pop_value is None:
                continue

            forecast_start_time = parse_dt(start_time)
            forecast_end_time = parse_dt(end_time)

            if not forecast_start_time or not forecast_end_time:
                continue

            rows.append(
                {
                    "run_id": run_id,
                    "location_sk": location_sk,
                    "forecast_issue_time": issue_time,
                    "forecast_start_time": forecast_start_time,
                    "forecast_end_time": forecast_end_time,
                    "pop_type": element_name,
                    "pop_value": pop_value,
                    "dispatch_recommended": bool(pop_value >= 30),
                    "loaded_at": loaded_at,
                }
            )

    print(
        f"[DEBUG] location_sk={location_sk}, "
        f"county={county_name}, town={town_name}, parsed_rows={len(rows)}"
    )

    return rows


# =========================================================
# 寫入前刪除既有資料
# =========================================================

def delete_existing_forecast_by_location(engine: Engine, location_sk: int) -> int:
    """刪除指定 location_sk 的既有預報資料，並回傳刪除筆數。"""
    sql = text(
        """
        DELETE FROM mart.fact_weather_forecast
        WHERE location_sk = :location_sk;
        """
    )

    with engine.begin() as conn:
        result = conn.execute(sql, {"location_sk": location_sk})
        return int(result.rowcount or 0)

# =========================================================
# 預報資料寫入
# =========================================================

def insert_forecast_rows(engine: Engine, rows: list[dict[str, Any]]) -> int:
    """批次寫入預報事實資料，無資料時直接回傳 0。"""
    if not rows:
        return 0

    df = pd.DataFrame(rows)

    insert_cols = [
        "run_id",
        "location_sk",
        "forecast_issue_time",
        "forecast_start_time",
        "forecast_end_time",
        "pop_type",
        "pop_value",
        "dispatch_recommended",
        "loaded_at",
    ]

    sql = text(
        """
        INSERT INTO mart.fact_weather_forecast (
            run_id,
            location_sk,
            forecast_issue_time,
            forecast_start_time,
            forecast_end_time,
            pop_type,
            pop_value,
            dispatch_recommended,
            loaded_at
        )
        VALUES (
            :run_id,
            :location_sk,
            :forecast_issue_time,
            :forecast_start_time,
            :forecast_end_time,
            :pop_type,
            :pop_value,
            :dispatch_recommended,
            :loaded_at
        );
        """
    )

    records = df[insert_cols].to_dict(orient="records")

    with engine.begin() as conn:
        conn.execute(sql, records)

    return len(records)


# =========================================================
# 主流程
# =========================================================

def main() -> None:
    """執行天氣預報 API 擷取、解析、寫入與紀錄更新流程。"""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not found in .env")
    if not CWA_API_KEY:
        raise RuntimeError("CWA_API_KEY not found in .env")

    engine = get_engine()

    fact_columns = get_fact_weather_forecast_columns(engine)
    fact_id_is_identity = get_identity_info(engine)
   
    print(f"[INFO] fact_weather_forecast columns = {fact_columns}")
    print(f"[INFO] fact_id_is_identity = {fact_id_is_identity}")

    run_id = create_etl_run_log(engine)
    print(f"[INFO] created run_id in meta.etl_run_log = {run_id}")

    locations = load_locations(engine)
    print(f"[INFO] loaded locations = {len(locations)}")
    
    batch_issue_time = datetime.now()
    print(f"[INFO] batch_issue_time = {batch_issue_time}")

    total_rows_raw = 0
    total_rows_staging = 0
    total_rows_mart = 0
    total_rows_quarantine = 0
    failed_locations: list[str] = []

    try:
        for idx, loc in enumerate(locations, start=1):
            print(
                f"[INFO] calling CWA API ({idx}/{len(locations)}) "
                f"county={loc.county_name}, town={loc.town_name}"
            )

            try:
                payload = call_cwa_forecast_api(
                    api_key=CWA_API_KEY,
                    county_name=loc.county_name,
                    town_name=loc.town_name,
                )

                rows = parse_forecast_rows(
                    payload=payload,
                    location_sk=loc.location_sk,
                    county_name=loc.county_name,
                    town_name=loc.town_name,
                    run_id=run_id,
                    batch_issue_time=batch_issue_time,
                )

                # 先刪除同一地點既有預報，再寫入本次解析結果。                
                deleted = delete_existing_forecast_by_location(
                    engine=engine,
                    location_sk=loc.location_sk,
                )

                print(
                    f"[INFO] location_sk={loc.location_sk} "
                    f"county={loc.county_name} town={loc.town_name} "
                    f"deleted old rows = {deleted}"
                )

                inserted = insert_forecast_rows(engine, rows)
                
                total_rows_raw += len(rows)
                total_rows_staging += len(rows)
                total_rows_mart += inserted

                print(
                    f"[INFO] location_sk={loc.location_sk} "
                    f"county={loc.county_name} town={loc.town_name} "
                    f"inserted rows = {inserted}"
                )

            except Exception as ex:
                failed_msg = (
                    f"location_sk={loc.location_sk}, "
                    f"county={loc.county_name}, town={loc.town_name}, error={ex}"
                )
                failed_locations.append(failed_msg)
                total_rows_quarantine += 1
                print(f"[ERROR] {failed_msg}")
                continue

        final_status = "SUCCESS" if not failed_locations else "FAILED"
        final_message = (
            f"total_rows_raw={total_rows_raw}; "
            f"total_rows_staging={total_rows_staging}; "
            f"total_rows_mart={total_rows_mart}; "
            f"failed_locations={len(failed_locations)}"
        )

        update_etl_run_log(
            engine=engine,
            run_id=run_id,
            status=final_status,
            rows_raw=total_rows_raw,
            rows_staging=total_rows_staging,
            rows_mart=total_rows_mart,
            rows_quarantine=total_rows_quarantine,
            message=final_message,
        )

        print("=" * 80)
        print(f"[INFO] total rows_raw = {total_rows_raw}")
        print(f"[INFO] total rows_staging = {total_rows_staging}")
        print(f"[INFO] total rows_mart = {total_rows_mart}")
        print(f"[INFO] total rows_quarantine = {total_rows_quarantine}")
        print(f"[INFO] failed locations = {len(failed_locations)}")

        if failed_locations:
            for msg in failed_locations:
                print(f"[INFO] failed detail -> {msg}")

    except Exception as ex:
        update_etl_run_log(
            engine=engine,
            run_id=run_id,
            status="FAILED",
            rows_raw=total_rows_raw,
            rows_staging=total_rows_staging,
            rows_mart=total_rows_mart,
            rows_quarantine=total_rows_quarantine + 1,
            message=str(ex),
        )
        raise


if __name__ == "__main__":
    main()