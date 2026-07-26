-- =========================================================
-- PVESPS - schema_postgres_v1.sql
-- PostgreSQL 16+
-- 用途說明：
--   光電能源與案場營運績效的 MVP 資料平台結構。
--   涵蓋天氣預報、逐日日照資料、案場主檔、案場績效與發電量估算。
--   資料流由 raw 原始層、stg 標準化層至 mart 分析層逐步整理。
-- =========================================================

BEGIN;

-- =========================================================
-- 0) 資料結構命名空間
-- =========================================================
-- meta：保存 ETL 狀態、執行紀錄、資料品質結果與隔離資料。
CREATE SCHEMA IF NOT EXISTS meta;
-- raw：保存來源原始載荷，支援追溯與重新處理。
CREATE SCHEMA IF NOT EXISTS raw;
-- stg：保存清理後的標準化資料，集中套用型別與品質限制。
CREATE SCHEMA IF NOT EXISTS stg;
-- mart：保存維度、事實與檢視，供分析報表與應用查詢使用。
CREATE SCHEMA IF NOT EXISTS mart;

-- =========================================================
-- 1) META 管理層
-- =========================================================

-- 1.1 ETL 水位與執行狀態，記錄各管線最後處理位置。
CREATE TABLE IF NOT EXISTS meta.etl_state (
    pipeline_name       VARCHAR(100) PRIMARY KEY,
    last_watermark      VARCHAR(255),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 1.2 ETL 執行紀錄，保存批次開始、結束、狀態與列數統計。
CREATE TABLE IF NOT EXISTS meta.etl_run_log (
    run_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pipeline_name       VARCHAR(100) NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    status              VARCHAR(20) NOT NULL
                            CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    rows_raw            INT NOT NULL DEFAULT 0,
    rows_staging        INT NOT NULL DEFAULT 0,
    rows_mart           INT NOT NULL DEFAULT 0,
    rows_quarantine     INT NOT NULL DEFAULT 0,
    message             TEXT
);

CREATE INDEX IF NOT EXISTS idx_etl_run_log_pipeline_started
    ON meta.etl_run_log (pipeline_name, started_at DESC);

-- 1.3 資料品質檢核摘要，彙整每次執行的規則失敗數。
CREATE TABLE IF NOT EXISTS meta.dq_results (
    dq_id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL
                            REFERENCES meta.etl_run_log(run_id)
                            ON DELETE CASCADE,
    pipeline_name       VARCHAR(100) NOT NULL,
    rule_code           VARCHAR(50) NOT NULL,
    table_name          VARCHAR(100) NOT NULL,
    failed_count        INT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dq_results_run_id
    ON meta.dq_results (run_id);

CREATE INDEX IF NOT EXISTS idx_dq_results_pipeline
    ON meta.dq_results (pipeline_name, created_at DESC);

-- 1.4 天氣預報隔離區，存放未通過檢核的預報資料與失敗原因。
CREATE TABLE IF NOT EXISTS meta.quarantine_weather_forecast (
    quarantine_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL
                            REFERENCES meta.etl_run_log(run_id)
                            ON DELETE CASCADE,
    dataset_id          VARCHAR(50),
    location_name       VARCHAR(100),
    county_name         VARCHAR(50),
    town_name           VARCHAR(50),
    forecast_issue_time TIMESTAMPTZ,
    forecast_start_time TIMESTAMPTZ,
    forecast_end_time   TIMESTAMPTZ,
    pop_type            VARCHAR(20),
    pop_value           NUMERIC(5,2),
    failed_rule         VARCHAR(100) NOT NULL,
    failed_reason       TEXT,
    raw_payload         JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quarantine_weather_run
    ON meta.quarantine_weather_forecast (run_id);

CREATE INDEX IF NOT EXISTS idx_quarantine_weather_created
    ON meta.quarantine_weather_forecast (created_at DESC);

-- 1.5 逐日日照隔離區，存放未通過檢核的觀測資料與原始內容。
CREATE TABLE IF NOT EXISTS meta.quarantine_sunshine_daily (
    quarantine_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                  BIGINT NOT NULL
                                REFERENCES meta.etl_run_log(run_id)
                                ON DELETE CASCADE,
    source_file_name        VARCHAR(255),
    station_id              VARCHAR(30),
    station_name            VARCHAR(100),
    county_name             VARCHAR(50),
    town_name               VARCHAR(50),
    obs_date                DATE,
    sunshine_hours          NUMERIC(6,2),
    sunshine_rate_pct       NUMERIC(6,2),
    solar_radiation_mj_m2   NUMERIC(10,2),
    failed_rule             VARCHAR(100) NOT NULL,
    failed_reason           TEXT,
    raw_payload             JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quarantine_sunshine_run
    ON meta.quarantine_sunshine_daily (run_id);

CREATE INDEX IF NOT EXISTS idx_quarantine_sunshine_created
    ON meta.quarantine_sunshine_daily (created_at DESC);

-- =========================================================
-- 2) RAW 原始資料層
-- =========================================================

-- 2.1 天氣預報原始載荷，保留來源 JSON 與匯入批次資訊。
CREATE TABLE IF NOT EXISTS raw.weather_forecast (
    raw_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL
                            REFERENCES meta.etl_run_log(run_id)
                            ON DELETE CASCADE,
    dataset_id          VARCHAR(50) NOT NULL,
    location_name       VARCHAR(100),
    county_name         VARCHAR(50),
    town_name           VARCHAR(50),
    forecast_issue_time TIMESTAMPTZ,
    payload_json        JSONB NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_weather_run
    ON raw.weather_forecast (run_id);

CREATE INDEX IF NOT EXISTS idx_raw_weather_issue_time
    ON raw.weather_forecast (forecast_issue_time DESC);

-- 2.2 逐日日照原始載荷，保留來源檔名、觀測日期與 JSON 內容。
CREATE TABLE IF NOT EXISTS raw.sunshine_daily (
    raw_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                  BIGINT NOT NULL
                                REFERENCES meta.etl_run_log(run_id)
                                ON DELETE CASCADE,
    source_file_name        VARCHAR(255) NOT NULL,
    station_id              VARCHAR(30),
    station_name            VARCHAR(100),
    county_name             VARCHAR(50),
    town_name               VARCHAR(50),
    obs_date                DATE,
    payload_json            JSONB NOT NULL,
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_sunshine_run
    ON raw.sunshine_daily (run_id);

CREATE INDEX IF NOT EXISTS idx_raw_sunshine_obs_date
    ON raw.sunshine_daily (obs_date DESC);

-- 2.3 案場回報原始載荷，保留案場每日回報資料與匯入時間。
CREATE TABLE IF NOT EXISTS raw.site_reports (
    raw_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL
                            REFERENCES meta.etl_run_log(run_id)
                            ON DELETE CASCADE,
    site_id             VARCHAR(50) NOT NULL,
    report_date         DATE NOT NULL,
    payload_json        JSONB NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_site_reports_run
    ON raw.site_reports (run_id);

CREATE INDEX IF NOT EXISTS idx_raw_site_reports_site_date
    ON raw.site_reports (site_id, report_date DESC);

-- =========================================================
-- 3) STAGING 標準化資料層
-- =========================================================

-- 3.1 標準化天氣預報，統一地區、時間區間與降雨機率欄位。
CREATE TABLE IF NOT EXISTS stg.weather_forecast (
    stg_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL
                            REFERENCES meta.etl_run_log(run_id)
                            ON DELETE CASCADE,
    dataset_id          VARCHAR(50) NOT NULL,
    location_name       VARCHAR(100) NOT NULL,
    county_name         VARCHAR(50) NOT NULL,
    town_name           VARCHAR(50),
    forecast_issue_time TIMESTAMPTZ NOT NULL,
    forecast_start_time TIMESTAMPTZ NOT NULL,
    forecast_end_time   TIMESTAMPTZ NOT NULL,
    pop_type            VARCHAR(20) NOT NULL,
    pop_value           NUMERIC(5,2) NOT NULL,
    staged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_stg_weather_pop_range
        CHECK (pop_value BETWEEN 0 AND 100),
    CONSTRAINT ck_stg_weather_time_range
        CHECK (forecast_end_time > forecast_start_time)
);

CREATE INDEX IF NOT EXISTS idx_stg_weather_issue_time
    ON stg.weather_forecast (forecast_issue_time DESC);

CREATE INDEX IF NOT EXISTS idx_stg_weather_location_time
    ON stg.weather_forecast (county_name, town_name, forecast_start_time);

-- 3.2 標準化逐日日照，整理測站、日照時數、日照率與太陽輻射量。
CREATE TABLE IF NOT EXISTS stg.sunshine_daily (
    stg_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                  BIGINT NOT NULL
                                REFERENCES meta.etl_run_log(run_id)
                                ON DELETE CASCADE,
    source_file_name        VARCHAR(255) NOT NULL,
    station_id              VARCHAR(30) NOT NULL,
    station_name            VARCHAR(100),
    county_name             VARCHAR(50) NOT NULL,
    town_name               VARCHAR(50),
    obs_date                DATE NOT NULL,
    sunshine_hours          NUMERIC(6,2),
    sunshine_rate_pct       NUMERIC(6,2),
    solar_radiation_mj_m2   NUMERIC(10,2),
    staged_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_stg_sunshine_hours_range
        CHECK (sunshine_hours IS NULL OR (sunshine_hours >= 0 AND sunshine_hours <= 24)),
    CONSTRAINT ck_stg_sunshine_rate_range
        CHECK (sunshine_rate_pct IS NULL OR (sunshine_rate_pct >= 0 AND sunshine_rate_pct <= 110)),
    CONSTRAINT ck_stg_solar_radiation_range
        CHECK (solar_radiation_mj_m2 IS NULL OR solar_radiation_mj_m2 >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_stg_sunshine_station_date
    ON stg.sunshine_daily (station_id, obs_date);

CREATE INDEX IF NOT EXISTS idx_stg_sunshine_county_date
    ON stg.sunshine_daily (county_name, obs_date DESC);

-- =========================================================
-- 4) MART 維度資料
-- =========================================================

-- 4.1 天氣地點維度，建立測站與縣市鄉鎮的分析鍵值。
CREATE TABLE IF NOT EXISTS mart.dim_weather_location (
    location_sk         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id          VARCHAR(30),
    station_name        VARCHAR(100),
    county_name         VARCHAR(50) NOT NULL,
    town_name           VARCHAR(50),
    latitude            NUMERIC(10,6),
    longitude           NUMERIC(10,6),
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_weather_location_business
    ON mart.dim_weather_location (
        COALESCE(station_id, ''),
        county_name,
        COALESCE(town_name, '')
    );

CREATE INDEX IF NOT EXISTS idx_dim_weather_location_county_town
    ON mart.dim_weather_location (county_name, town_name);

-- 4.2 光電案場維度，保存案場容量、面積、基準效率與有效期間。
CREATE TABLE IF NOT EXISTS mart.dim_solar_site (
    site_sk                 INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id                 VARCHAR(50) NOT NULL UNIQUE,
    site_name               VARCHAR(100) NOT NULL,
    city_name               VARCHAR(50),
    county_name             VARCHAR(50) NOT NULL,
    town_name               VARCHAR(50),
    location_sk             INT
                                REFERENCES mart.dim_weather_location(location_sk),
    capacity_kw             NUMERIC(12,2) NOT NULL,
    install_area_ping       NUMERIC(12,2) NOT NULL,
    baseline_efficiency_pct NUMERIC(6,2) NOT NULL,
    commission_date         DATE,
    site_status             VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                                CHECK (site_status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE')),
    effective_from          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_to            TIMESTAMPTZ,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_dim_solar_capacity_kw
        CHECK (capacity_kw > 0),
    CONSTRAINT ck_dim_solar_install_area
        CHECK (install_area_ping > 0),
    CONSTRAINT ck_dim_solar_baseline_eff
        CHECK (baseline_efficiency_pct BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS idx_dim_solar_site_location
    ON mart.dim_solar_site (location_sk);

CREATE INDEX IF NOT EXISTS idx_dim_solar_site_county_town
    ON mart.dim_solar_site (county_name, town_name);

-- =========================================================
-- 5) MART 事實資料
-- =========================================================

-- 5.1 天氣預報事實，提供地點與預報時間區間的降雨機率分析。
CREATE TABLE IF NOT EXISTS mart.fact_weather_forecast (
    fact_id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                  BIGINT NOT NULL
                                REFERENCES meta.etl_run_log(run_id)
                                ON DELETE CASCADE,
    location_sk             INT NOT NULL
                                REFERENCES mart.dim_weather_location(location_sk),
    forecast_issue_time     TIMESTAMPTZ NOT NULL,
    forecast_start_time     TIMESTAMPTZ NOT NULL,
    forecast_end_time       TIMESTAMPTZ NOT NULL,
    pop_type                VARCHAR(20) NOT NULL,
    pop_value               NUMERIC(5,2) NOT NULL,
    dispatch_recommended    BOOLEAN,
    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_fact_weather_pop_range
        CHECK (pop_value BETWEEN 0 AND 100),
    CONSTRAINT ck_fact_weather_time_range
        CHECK (forecast_end_time > forecast_start_time)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_weather_business
    ON mart.fact_weather_forecast (
        location_sk,
        forecast_issue_time,
        forecast_start_time,
        forecast_end_time,
        pop_type
    );

CREATE INDEX IF NOT EXISTS idx_fact_weather_location_start
    ON mart.fact_weather_forecast (location_sk, forecast_start_time DESC);

-- 5.2 逐日日照事實，提供地點與日期層級的日照與輻射量分析。
CREATE TABLE IF NOT EXISTS mart.fact_sunshine_daily (
    fact_id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                  BIGINT NOT NULL
                                REFERENCES meta.etl_run_log(run_id)
                                ON DELETE CASCADE,
    location_sk             INT NOT NULL
                                REFERENCES mart.dim_weather_location(location_sk),
    obs_date                DATE NOT NULL,
    sunshine_hours          NUMERIC(6,2),
    sunshine_rate_pct       NUMERIC(6,2),
    solar_radiation_mj_m2   NUMERIC(10,2),
    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_fact_sunshine_hours_range
        CHECK (sunshine_hours IS NULL OR (sunshine_hours >= 0 AND sunshine_hours <= 24)),
    CONSTRAINT ck_fact_sunshine_rate_range
        CHECK (sunshine_rate_pct IS NULL OR (sunshine_rate_pct >= 0 AND sunshine_rate_pct <= 110)),
    CONSTRAINT ck_fact_sunshine_radiation_range
        CHECK (solar_radiation_mj_m2 IS NULL OR solar_radiation_mj_m2 >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_sunshine_business
    ON mart.fact_sunshine_daily (location_sk, obs_date);

CREATE INDEX IF NOT EXISTS idx_fact_sunshine_date
    ON mart.fact_sunshine_daily (obs_date DESC);

-- 5.3 案場績效事實，記錄每日發電量、效率、可用率與維護判斷。
CREATE TABLE IF NOT EXISTS mart.fact_site_performance (
    fact_id                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                      BIGINT NOT NULL
                                    REFERENCES meta.etl_run_log(run_id)
                                    ON DELETE CASCADE,
    site_sk                     INT NOT NULL
                                    REFERENCES mart.dim_solar_site(site_sk),
    location_sk                 INT
                                    REFERENCES mart.dim_weather_location(location_sk),
    report_date                 DATE NOT NULL,
    daily_generation_kwh        NUMERIC(12,2) NOT NULL,
    conversion_efficiency_pct   NUMERIC(6,2),
    availability_pct            NUMERIC(6,2),
    cleanliness_score           NUMERIC(6,2),
    maintenance_flag            BOOLEAN NOT NULL DEFAULT FALSE,
    maintenance_reason          VARCHAR(255),
    loaded_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_fact_site_perf_generation
        CHECK (daily_generation_kwh >= 0),
    CONSTRAINT ck_fact_site_perf_eff
        CHECK (conversion_efficiency_pct IS NULL OR (conversion_efficiency_pct >= 0 AND conversion_efficiency_pct <= 100)),
    CONSTRAINT ck_fact_site_perf_availability
        CHECK (availability_pct IS NULL OR (availability_pct >= 0 AND availability_pct <= 100)),
    CONSTRAINT ck_fact_site_perf_cleanliness
        CHECK (cleanliness_score IS NULL OR (cleanliness_score >= 0 AND cleanliness_score <= 100))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_site_performance_business
    ON mart.fact_site_performance (site_sk, report_date);

CREATE INDEX IF NOT EXISTS idx_fact_site_perf_date
    ON mart.fact_site_performance (report_date DESC);

CREATE INDEX IF NOT EXISTS idx_fact_site_perf_maint_flag
    ON mart.fact_site_performance (maintenance_flag, report_date DESC);

-- 5.4 發電量估算事實，依公式版本保存每日估算發電量。
CREATE TABLE IF NOT EXISTS mart.fact_generation_estimate (
    fact_id                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                      BIGINT NOT NULL
                                    REFERENCES meta.etl_run_log(run_id)
                                    ON DELETE CASCADE,
    site_sk                     INT NOT NULL
                                    REFERENCES mart.dim_solar_site(site_sk),
    location_sk                 INT
                                    REFERENCES mart.dim_weather_location(location_sk),
    estimate_date               DATE NOT NULL,
    sunshine_hours              NUMERIC(6,2),
    install_area_ping           NUMERIC(12,2) NOT NULL,
    formula_version             VARCHAR(50) NOT NULL,
    estimated_generation_kwh    NUMERIC(12,2) NOT NULL,
    loaded_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_fact_generation_install_area
        CHECK (install_area_ping > 0),
    CONSTRAINT ck_fact_generation_estimated_kwh
        CHECK (estimated_generation_kwh >= 0),
    CONSTRAINT ck_fact_generation_sunshine
        CHECK (sunshine_hours IS NULL OR (sunshine_hours >= 0 AND sunshine_hours <= 24))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_generation_estimate_business
    ON mart.fact_generation_estimate (site_sk, estimate_date, formula_version);

CREATE INDEX IF NOT EXISTS idx_fact_generation_estimate_date
    ON mart.fact_generation_estimate (estimate_date DESC);

-- =========================================================
-- 6) 示範查詢檢視
-- =========================================================

-- 6.1 各地點最新預報檢視，取每個地點最新發布時間的預報資料。
CREATE OR REPLACE VIEW mart.v_latest_weather_forecast AS
SELECT f.*
FROM mart.fact_weather_forecast f
JOIN (
    SELECT location_sk, MAX(forecast_issue_time) AS latest_issue_time
    FROM mart.fact_weather_forecast
    GROUP BY location_sk
) x
    ON f.location_sk = x.location_sk
   AND f.forecast_issue_time = x.latest_issue_time;

-- 6.2 維護候選案場檢視，列出被標記為需要維護的案場績效資料。
CREATE OR REPLACE VIEW mart.v_site_maintenance_candidates AS
SELECT
    p.site_sk,
    s.site_id,
    s.site_name,
    p.location_sk,
    p.report_date,
    p.daily_generation_kwh,
    p.conversion_efficiency_pct,
    p.availability_pct,
    p.cleanliness_score,
    p.maintenance_flag,
    p.maintenance_reason
FROM mart.fact_site_performance p
JOIN mart.dim_solar_site s
    ON p.site_sk = s.site_sk
WHERE p.maintenance_flag = TRUE;

COMMIT;