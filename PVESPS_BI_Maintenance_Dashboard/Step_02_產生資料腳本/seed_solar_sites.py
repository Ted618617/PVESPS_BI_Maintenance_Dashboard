"""光電案場示範資料寫入腳本。

負責建立天氣地點維度與光電案場維度的示範資料，支援重複執行。
資料寫入採先查詢後新增或更新，避免同一案場代碼重複產生資料。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from db import get_engine


@dataclass(frozen=True)
class WeatherLocationSeed:
    """天氣地點種子資料，對應 mart.dim_weather_location 的業務鍵與座標。"""

    station_id: Optional[str]
    station_name: Optional[str]
    county_name: str
    town_name: Optional[str]
    latitude: float
    longitude: float


@dataclass(frozen=True)
class SolarSiteSeed:
    """光電案場種子資料，包含案場主檔欄位與對應天氣地點。"""

    site_id: str
    site_name: str
    city_name: str
    county_name: str
    town_name: str
    capacity_kw: float
    install_area_ping: float
    baseline_efficiency_pct: float
    commission_date: str
    site_status: str
    weather_location: WeatherLocationSeed


# 預設示範案場清單，涵蓋北中南不同地理區域與裝置容量情境。
SEED_SITES: list[SolarSiteSeed] = [
    SolarSiteSeed(
        site_id="TPE_SITE_01",
        site_name="Taipei Rooftop Solar Site",
        city_name="台北市",
        county_name="臺北市",
        town_name="內湖區",
        capacity_kw=320.0,
        install_area_ping=145.0,
        baseline_efficiency_pct=18.8,
        commission_date="2024-03-15",
        site_status="ACTIVE",
        weather_location=WeatherLocationSeed(
            station_id="TPE_DEMO_01",
            station_name="Taipei Demo Weather Point",
            county_name="臺北市",
            town_name="內湖區",
            latitude=25.0836,
            longitude=121.5947,
        ),
    ),
    SolarSiteSeed(
        site_id="MIA_SITE_01",
        site_name="Miaoli Ground Solar Site",
        city_name="苗栗縣",
        county_name="苗栗縣",
        town_name="竹南鎮",
        capacity_kw=520.0,
        install_area_ping=230.0,
        baseline_efficiency_pct=19.4,
        commission_date="2023-11-20",
        site_status="ACTIVE",
        weather_location=WeatherLocationSeed(
            station_id="MIA_DEMO_01",
            station_name="Miaoli Demo Weather Point",
            county_name="苗栗縣",
            town_name="竹南鎮",
            latitude=24.6869,
            longitude=120.8786,
        ),
    ),
    SolarSiteSeed(
        site_id="TXG_SITE_01",
        site_name="Taichung Industrial Solar Site",
        city_name="台中市",
        county_name="臺中市",
        town_name="龍井區",
        capacity_kw=880.0,
        install_area_ping=410.0,
        baseline_efficiency_pct=20.1,
        commission_date="2023-08-10",
        site_status="ACTIVE",
        weather_location=WeatherLocationSeed(
            station_id="467490",
            station_name="Taichung Weather Station",
            county_name="臺中市",
            town_name="西區",
            latitude=24.1457,
            longitude=120.6841,
        ),
    ),
    SolarSiteSeed(
        site_id="KHH_SITE_01",
        site_name="Kaohsiung Harbor Solar Site",
        city_name="高雄市",
        county_name="高雄市",
        town_name="小港區",
        capacity_kw=1250.0,
        install_area_ping=560.0,
        baseline_efficiency_pct=20.8,
        commission_date="2022-12-05",
        site_status="ACTIVE",
        weather_location=WeatherLocationSeed(
            station_id="KHH_DEMO_01",
            station_name="Kaohsiung Demo Weather Point",
            county_name="高雄市",
            town_name="小港區",
            latitude=22.5650,
            longitude=120.3539,
        ),
    ),
]


def ensure_weather_location(conn, location: WeatherLocationSeed) -> int:
    """確認天氣地點維度是否存在；缺少時新增並回傳 location_sk。

    業務鍵採 station_id、county_name、town_name 組合，空值以空字串比對。
    """
    select_sql = text(
        """
        SELECT location_sk
        FROM mart.dim_weather_location
        WHERE COALESCE(station_id, '') = COALESCE(:station_id, '')
          AND county_name = :county_name
          AND COALESCE(town_name, '') = COALESCE(:town_name, '')
        LIMIT 1
        """
    )

    row = conn.execute(
        select_sql,
        {
            "station_id": location.station_id,
            "county_name": location.county_name,
            "town_name": location.town_name,
        },
    ).mappings().first()

    if row:
        return int(row["location_sk"])

    insert_sql = text(
        """
        INSERT INTO mart.dim_weather_location (
            station_id,
            station_name,
            county_name,
            town_name,
            latitude,
            longitude,
            is_current
        )
        VALUES (
            :station_id,
            :station_name,
            :county_name,
            :town_name,
            :latitude,
            :longitude,
            TRUE
        )
        RETURNING location_sk
        """
    )

    inserted = conn.execute(
        insert_sql,
        {
            "station_id": location.station_id,
            "station_name": location.station_name,
            "county_name": location.county_name,
            "town_name": location.town_name,
            "latitude": location.latitude,
            "longitude": location.longitude,
        },
    ).mappings().first()

    if not inserted:
        raise RuntimeError("Failed to insert mart.dim_weather_location")

    return int(inserted["location_sk"])


def upsert_solar_site(conn, site: SolarSiteSeed, location_sk: int) -> None:
    """依 site_id 新增或更新光電案場維度，維持示範資料可重複執行。"""
    exists_sql = text(
        """
        SELECT site_sk
        FROM mart.dim_solar_site
        WHERE site_id = :site_id
        LIMIT 1
        """
    )

    existing = conn.execute(exists_sql, {"site_id": site.site_id}).mappings().first()

    if existing:
        update_sql = text(
            """
            UPDATE mart.dim_solar_site
            SET
                site_name = :site_name,
                city_name = :city_name,
                county_name = :county_name,
                town_name = :town_name,
                location_sk = :location_sk,
                capacity_kw = :capacity_kw,
                install_area_ping = :install_area_ping,
                baseline_efficiency_pct = :baseline_efficiency_pct,
                commission_date = :commission_date,
                site_status = :site_status,
                is_current = TRUE
            WHERE site_id = :site_id
            """
        )

        conn.execute(
            update_sql,
            {
                "site_id": site.site_id,
                "site_name": site.site_name,
                "city_name": site.city_name,
                "county_name": site.county_name,
                "town_name": site.town_name,
                "location_sk": location_sk,
                "capacity_kw": site.capacity_kw,
                "install_area_ping": site.install_area_ping,
                "baseline_efficiency_pct": site.baseline_efficiency_pct,
                "commission_date": site.commission_date,
                "site_status": site.site_status,
            },
        )
        print(f"[UPDATED] {site.site_id} - {site.site_name}")
        return

    insert_sql = text(
        """
        INSERT INTO mart.dim_solar_site (
            site_id,
            site_name,
            city_name,
            county_name,
            town_name,
            location_sk,
            capacity_kw,
            install_area_ping,
            baseline_efficiency_pct,
            commission_date,
            site_status,
            is_current
        )
        VALUES (
            :site_id,
            :site_name,
            :city_name,
            :county_name,
            :town_name,
            :location_sk,
            :capacity_kw,
            :install_area_ping,
            :baseline_efficiency_pct,
            :commission_date,
            :site_status,
            TRUE
        )
        """
    )

    conn.execute(
        insert_sql,
        {
            "site_id": site.site_id,
            "site_name": site.site_name,
            "city_name": site.city_name,
            "county_name": site.county_name,
            "town_name": site.town_name,
            "location_sk": location_sk,
            "capacity_kw": site.capacity_kw,
            "install_area_ping": site.install_area_ping,
            "baseline_efficiency_pct": site.baseline_efficiency_pct,
            "commission_date": site.commission_date,
            "site_status": site.site_status,
        },
    )
    print(f"[INSERTED] {site.site_id} - {site.site_name}")


def validate_seed_data() -> None:
    """檢查種子資料的容量、設置面積與基準效率範圍。"""
    for site in SEED_SITES:
        if site.capacity_kw <= 0:
            raise ValueError(f"{site.site_id}: capacity_kw must be > 0")
        if site.install_area_ping <= 0:
            raise ValueError(f"{site.site_id}: install_area_ping must be > 0")
        if not (0 <= site.baseline_efficiency_pct <= 100):
            raise ValueError(f"{site.site_id}: baseline_efficiency_pct must be between 0 and 100")


def main() -> int:
    """執行種子資料檢核與資料庫寫入流程。"""
    validate_seed_data()
    engine: Engine = get_engine()

    with engine.begin() as conn:
        for site in SEED_SITES:
            location_sk = ensure_weather_location(conn, site.weather_location)
            upsert_solar_site(conn, site, location_sk)

    print("\nSeed completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())