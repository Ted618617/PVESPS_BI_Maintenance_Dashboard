import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


def get_available_runs(engine: Engine) -> pd.DataFrame:
    """
    取得可用的 ETL run 清單（新到舊）
    以 run_id 作為主篩選鍵，避免 forecast_issue_time 因秒數/時區差異造成批次切碎。
    """
    sql = """
    SELECT
        f.run_id,
        MIN(f.forecast_issue_time) AS forecast_issue_time,
        COUNT(*) AS row_count
    FROM mart.fact_weather_forecast f
    GROUP BY f.run_id
    ORDER BY f.run_id DESC;
    """
    return pd.read_sql(sql, engine)



def get_sites(engine: Engine) -> pd.DataFrame:
    """
    取得站點清單
    預設假設 mart.dim_solar_site 具有 site_sk / site_name / location_sk
    """
    sql = """
    SELECT
        site_sk,
        site_name,
        location_sk
    FROM mart.dim_solar_site
    ORDER BY site_name;
    """
    return pd.read_sql(sql, engine)



def get_forecast_data(
    engine: Engine,
    run_id,
    site_sk=None,
) -> pd.DataFrame:
    """
    取得指定 run_id 下的天氣預報資料
    若 site_sk 為 None，則回傳全部站點
    """
    sql = text(
        """
        SELECT
            s.site_sk,
            s.site_name,
            l.location_sk,
            l.county_name,
            l.town_name,
            f.fact_id,
            f.run_id,
            f.forecast_issue_time,
            f.forecast_start_time,
            f.forecast_end_time,
            f.pop_type,
            f.pop_value,
            f.dispatch_recommended,
            f.loaded_at
        FROM mart.fact_weather_forecast f
        JOIN mart.dim_weather_location l
            ON f.location_sk = l.location_sk
        JOIN mart.dim_solar_site s
            ON s.location_sk = l.location_sk
        WHERE 1 = 1
          AND f.pop_type LIKE '%降雨機率%'
          AND f.run_id = :run_id
          AND (:site_sk IS NULL OR s.site_sk = :site_sk)
        ORDER BY s.site_name, f.forecast_start_time;
        """
    )

    df = pd.read_sql(
        sql,
        engine,
        params={
            "run_id": run_id,
            "site_sk": site_sk,
        },
    )

    return df



def get_site_daytime_summary(
    engine: Engine,
    run_id,
) -> pd.DataFrame:
    """
    取得各站點白天時段（06:00~18:00）的摘要
    可用於後續多站點比較表
    """
    sql = text(
        """
        SELECT
            s.site_sk,
            s.site_name,
            l.county_name,
            l.town_name,
            COUNT(*) AS daytime_slot_count,
            MAX(f.pop_value) AS max_pop_value,
            ROUND(AVG(f.pop_value)::numeric, 1) AS avg_pop_value,
            SUM(
                CASE
                    WHEN f.pop_value >= 60 THEN 1
                    ELSE 0
                END
            ) AS high_risk_count
        FROM mart.fact_weather_forecast f
        JOIN mart.dim_weather_location l
            ON f.location_sk = l.location_sk
        JOIN mart.dim_solar_site s
            ON s.location_sk = l.location_sk
        WHERE 1 = 1
          AND f.pop_type LIKE '%降雨機率%'
          AND f.run_id = :run_id
          AND EXTRACT(HOUR FROM f.forecast_start_time) >= 6
          AND EXTRACT(HOUR FROM f.forecast_start_time) < 18
        GROUP BY
            s.site_sk,
            s.site_name,
            l.county_name,
            l.town_name
        ORDER BY s.site_name;
        """
    )

    return pd.read_sql(
        sql,
        engine,
        params={"run_id": run_id},
    )
