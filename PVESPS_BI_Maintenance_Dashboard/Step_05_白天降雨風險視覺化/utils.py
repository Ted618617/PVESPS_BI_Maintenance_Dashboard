import pandas as pd


LOCAL_TZ = "Asia/Taipei"


def ensure_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    將時間欄位轉成 datetime
    """
    df = df.copy()

    datetime_cols = [
        "forecast_issue_time",
        "forecast_start_time",
        "forecast_end_time",
        "loaded_at",
    ]

    for col in datetime_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    return df


def add_daytime_flag(df: pd.DataFrame) -> pd.DataFrame:
    """
    增加 is_daytime 欄位
    規則：06:00 <= forecast_start_time < 18:00
    """
    df = df.copy()

    if "forecast_start_time" not in df.columns:
        df["is_daytime"] = False
        return df

    df["forecast_start_time"] = pd.to_datetime(df["forecast_start_time"], errors="coerce")
    df["forecast_hour"] = df["forecast_start_time"].dt.hour
    df["is_daytime"] = df["forecast_hour"].between(6, 17, inclusive="both")

    return df


def classify_risk(pop_value) -> str:
    """
    依降雨機率分類風險等級
    """
    try:
        pop = float(pop_value)
    except (TypeError, ValueError):
        return "未知"

    if pop >= 60:
        return "高風險"
    if pop >= 30:
        return "中風險"
    return "低風險"


def add_risk_level(df: pd.DataFrame) -> pd.DataFrame:
    """
    增加 risk_level 欄位
    """
    df = df.copy()

    if "pop_value" not in df.columns:
        df["risk_level"] = "未知"
        return df

    df["risk_level"] = df["pop_value"].apply(classify_risk)
    return df


def format_time_slot(df: pd.DataFrame) -> pd.DataFrame:
    """
    增加圖表顯示用的時段文字
    """
    df = df.copy()

    if "forecast_start_time" in df.columns and "forecast_end_time" in df.columns:
        start_dt = pd.to_datetime(df["forecast_start_time"], errors="coerce")
        end_dt = pd.to_datetime(df["forecast_end_time"], errors="coerce")

        df["time_slot_label"] = (
            start_dt.dt.strftime("%m-%d %H:%M")
            + " ~ "
            + end_dt.dt.strftime("%H:%M")
        )
    else:
        df["time_slot_label"] = ""

    return df


def build_decision_note(df: pd.DataFrame) -> str:
    """
    依目前篩選資料產生一句話決策建議
    """
    if df.empty:
        return "目前查無符合條件的資料。"

    max_pop = pd.to_numeric(df["pop_value"], errors="coerce").max()
    high_risk_count = (df["risk_level"] == "高風險").sum()
    avg_pop = pd.to_numeric(df["pop_value"], errors="coerce").mean()

    if pd.isna(max_pop):
        return "目前資料不足，無法判斷白天降雨風險。"

    if high_risk_count >= 2:
        return "白天多個時段降雨風險偏高，建議留意巡檢安排與發電預估下修。"

    if max_pop >= 60:
        return "白天局部時段可能降雨，建議持續關注日照變化。"

    if avg_pop >= 30:
        return "白天整體有中度降雨機會，建議保守觀察發電表現。"

    return "白天降雨風險可控，可維持正常觀測。"


def calculate_kpis(df: pd.DataFrame) -> dict:
    """
    計算 KPI 指標
    """
    if df.empty:
        return {
            "time_slot_count": 0,
            "max_pop": 0,
            "avg_pop": 0.0,
            "high_risk_count": 0,
        }

    pop_series = pd.to_numeric(df["pop_value"], errors="coerce")

    return {
        "time_slot_count": int(len(df)),
        "max_pop": int(pop_series.max()) if pop_series.notna().any() else 0,
        "avg_pop": round(float(pop_series.mean()), 1) if pop_series.notna().any() else 0.0,
        "high_risk_count": int((df["risk_level"] == "高風險").sum()),
    }


def add_summary_recommendation(df: pd.DataFrame) -> pd.DataFrame:
    """
    給 summary dataframe 補 recommendation 欄位
    預期欄位：max_pop_value / high_risk_count
    """
    df = df.copy()

    def _recommend(row):
        max_pop = row.get("max_pop_value", 0)
        high_risk_count = row.get("high_risk_count", 0)

        try:
            max_pop = float(max_pop)
        except (TypeError, ValueError):
            max_pop = 0

        try:
            high_risk_count = int(high_risk_count)
        except (TypeError, ValueError):
            high_risk_count = 0

        if high_risk_count >= 2:
            return "注意巡檢與下修"
        if max_pop >= 60:
            return "局部時段需注意"
        if max_pop >= 30:
            return "中度關注"
        return "正常觀測"

    df["recommendation"] = df.apply(_recommend, axis=1)
    return df


def format_datetime_series(series: pd.Series, fmt: str, assume_utc: bool = False) -> pd.Series:
    """
    將 datetime series 格式化成字串
    若 assume_utc=True，會先把 naive timestamp 視為 UTC，再轉為台北時間。
    """
    dt = pd.to_datetime(series, errors="coerce")

    if assume_utc:
        try:
            if getattr(dt.dt, "tz", None) is None:
                dt = dt.dt.tz_localize("UTC")
            dt = dt.dt.tz_convert(LOCAL_TZ)
        except Exception:
            pass

    return dt.dt.strftime(fmt)


def prepare_detail_display(df: pd.DataFrame) -> pd.DataFrame:
    """
    產出明細表顯示用 dataframe
    """
    display_df = df.copy()

    if "forecast_issue_time" in display_df.columns:
        display_df["forecast_issue_time_fmt"] = format_datetime_series(
            display_df["forecast_issue_time"], "%Y-%m-%d %H:%M", assume_utc=True
        )

    if "forecast_start_time" in display_df.columns:
        display_df["forecast_start_time_fmt"] = format_datetime_series(
            display_df["forecast_start_time"], "%m-%d %H:%M"
        )

    if "forecast_end_time" in display_df.columns:
        display_df["forecast_end_time_fmt"] = format_datetime_series(
            display_df["forecast_end_time"], "%H:%M"
        )

    return display_df
