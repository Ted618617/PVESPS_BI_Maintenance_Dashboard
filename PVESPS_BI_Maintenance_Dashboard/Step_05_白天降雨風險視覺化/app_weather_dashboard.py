"""白天降雨風險 Streamlit 視覺化儀表板。

讀取光電案場與天氣預報事實資料，提供 ETL 批次、站點與白天時段篩選。
畫面聚焦 06:00 至 18:00 的三小時降雨機率，呈現 KPI、站點風險比較、單站時序圖、預報明細與 CSV 匯出。
"""
import pandas as pd
import plotly.express as px
import streamlit as st

from db import get_engine
from queries import (
    get_available_runs,
    get_forecast_data,
    get_site_daytime_summary,
    get_sites,
)
from utils import (
    add_daytime_flag,
    add_risk_level,
    add_summary_recommendation,
    build_decision_note,
    calculate_kpis,
    ensure_datetime_columns,
    format_time_slot,
    prepare_detail_display,
)

# 儀表板顯示時間統一轉換為台北時區。
LOCAL_TZ = "Asia/Taipei"

# Streamlit 頁面基礎設定，採寬版配置以容納圖表與明細表。
st.set_page_config(
    page_title="PVESPS 白天降雨風險儀表板",
    page_icon="🌦️",
    layout="wide",
)

st.title("PVESPS｜白天降雨風險決策儀表板")
st.caption("聚焦 06:00–18:00 白天時段，觀察各太陽能站點 3 小時降雨機率，作為日照與發電判讀輔助。")


@st.cache_resource
def load_engine():
    """建立並快取資料庫連線引擎，避免頁面重跑時重複初始化。"""
    return get_engine()


@st.cache_data(ttl=300)
def load_runs():
    """載入可用 ETL 批次清單，快取五分鐘。"""
    engine = load_engine()
    return get_available_runs(engine)


@st.cache_data(ttl=300)
def load_sites():
    """載入可用光電站點清單，快取五分鐘。"""
    engine = load_engine()
    return get_sites(engine)


@st.cache_data(ttl=300)
def load_forecast_data(run_id, site_sk):
    """依 ETL 批次與站點條件載入預報明細資料。"""
    engine = load_engine()
    return get_forecast_data(
        engine=engine,
        run_id=run_id,
        site_sk=site_sk,
    )


@st.cache_data(ttl=300)
def load_daytime_summary(run_id):
    """載入指定 ETL 批次的白天站點摘要資料。"""
    engine = load_engine()
    return get_site_daytime_summary(
        engine=engine,
        run_id=run_id,
    )


# 先載入篩選器需要的基礎資料，失敗時停止後續畫面渲染。
try:
    run_df = load_runs()
    site_df = load_sites()
except Exception as ex:
    st.error(f"讀取基礎資料失敗：{ex}")
    st.stop()

if run_df.empty:
    st.warning("fact_weather_forecast 尚無可用的 run 資料。")
    st.stop()

# 將批次資料整理為 selectbox 可顯示的選項格式。
run_df = ensure_datetime_columns(run_df)
run_options = run_df.to_dict(orient="records")

site_name_map = {"全部站點": None}
if not site_df.empty:
    for _, row in site_df.iterrows():
        site_name_map[str(row["site_name"])] = row["site_sk"]


def format_run_option(row: dict) -> str:
    """格式化 ETL 批次選項，將預報發布時間轉為台北時區。"""
    ts = pd.to_datetime(row["forecast_issue_time"], errors="coerce")
    if pd.isna(ts):
        ts_text = "Unknown time"
    else:
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts = ts.tz_convert(LOCAL_TZ)
        ts_text = ts.strftime("%Y-%m-%d %H:%M:%S")
    return f"Run {row['run_id']} | {ts_text} | rows={row['row_count']}"


# 側邊欄集中放置批次、站點與顯示範圍篩選條件。
with st.sidebar:
    st.header("篩選條件")

    selected_run = st.selectbox(
        "ETL Run",
        options=run_options,
        format_func=format_run_option,
        index=0,
    )
    selected_run_id = selected_run["run_id"]

    selected_site_name = st.selectbox(
        "站點",
        options=list(site_name_map.keys()),
        index=0,
    )
    selected_site_sk = site_name_map[selected_site_name]

    daytime_only = st.checkbox("只顯示白天時段（06:00–18:00）", value=True)
    show_detail_table = st.checkbox("顯示明細表", value=True)

# 依篩選條件載入預報資料，並套用時間、白天旗標與風險等級整理。
try:
    df = load_forecast_data(selected_run_id, selected_site_sk)
except Exception as ex:
    st.error(f"讀取預報資料失敗：{ex}")
    st.stop()

df = ensure_datetime_columns(df)
df = add_daytime_flag(df)
df = add_risk_level(df)
df = format_time_slot(df)

df = df.sort_values(["site_name", "forecast_start_time"], ascending=[True, True]).reset_index(drop=True)

if daytime_only:
    df = df[df["is_daytime"]].copy()

if df.empty:
    st.warning("查無符合條件的資料。")
    st.stop()

# KPI 與決策提示使用目前篩選後的資料集計算。
kpis = calculate_kpis(df)
decision_note = build_decision_note(df)

col1, col2, col3, col4 = st.columns(4)
col1.metric("白天預報時段數", kpis["time_slot_count"])
col2.metric("最高降雨機率", f'{kpis["max_pop"]}%')
col3.metric("平均降雨機率", f'{kpis["avg_pop"]}%')
col4.metric("高風險時段數", kpis["high_risk_count"])

st.info(decision_note)

# 站點摘要資料用於全部站點模式的橫向比較圖與摘要表。
summary_df = pd.DataFrame()
try:
    summary_df = load_daytime_summary(selected_run_id)
    summary_df = add_summary_recommendation(summary_df)
except Exception as ex:
    st.error(f"讀取白天摘要資料失敗：{ex}")

is_all_sites_mode = selected_site_sk is None

# 全部站點模式顯示各站點最高降雨機率比較與摘要表。
if is_all_sites_mode:
    st.subheader("各站點白天風險比較")

    if not summary_df.empty:
        fig_summary = px.bar(
            summary_df,
            x="site_name",
            y="max_pop_value",
            color="recommendation",
            text="max_pop_value",
            hover_data={
                "county_name": True,
                "town_name": True,
                "avg_pop_value": True,
                "high_risk_count": True,
                "daytime_slot_count": True,
            },
            title="各站點白天最高降雨機率比較",
        )
        fig_summary.update_traces(texttemplate="%{text}%", textposition="outside")
        fig_summary.update_layout(
            xaxis_title="站點",
            yaxis_title="最高降雨機率 (%)",
            yaxis_range=[0, 100],
        )
        st.plotly_chart(fig_summary, use_container_width=True)

        st.subheader("各站點白天風險摘要")
        st.dataframe(
            summary_df[
                [
                    "site_name",
                    "county_name",
                    "town_name",
                    "daytime_slot_count",
                    "max_pop_value",
                    "avg_pop_value",
                    "high_risk_count",
                    "recommendation",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("目前無法顯示站點摘要資料。")

# 單一站點模式顯示各白天預報時段的降雨機率柱狀圖。
else:
    st.subheader(f"白天 3 小時降雨機率｜{selected_site_name}")

    fig_detail = px.bar(
        df,
        x="forecast_start_time",
        y="pop_value",
        color="risk_level",
        text="pop_value",
        hover_data={
            "site_name": True,
            "county_name": True,
            "town_name": True,
            "forecast_start_time": True,
            "forecast_end_time": True,
            "dispatch_recommended": True,
            "risk_level": True,
            "pop_value": True,
        },
        title=f"{selected_site_name} 白天時段降雨機率",
    )
    fig_detail.update_traces(texttemplate="%{text}%", textposition="outside")
    fig_detail.update_layout(
        xaxis_title="預報開始時間",
        yaxis_title="降雨機率 (%)",
        yaxis_range=[0, 100],
    )
    st.plotly_chart(fig_detail, use_container_width=True)

# 明細表提供目前篩選結果的欄位重命名、表格呈現與 CSV 匯出。
if show_detail_table:
    st.subheader("預報明細")
    display_df = prepare_detail_display(df)

    detail_cols = [
        "site_name",
        "county_name",
        "town_name",
        "forecast_issue_time_fmt",
        "forecast_start_time_fmt",
        "forecast_end_time_fmt",
        "pop_type",
        "pop_value",
        "dispatch_recommended",
        "risk_level",
    ]

    rename_map = {
        "site_name": "站點",
        "county_name": "縣市",
        "town_name": "鄉鎮區",
        "forecast_issue_time_fmt": "預報發布時間",
        "forecast_start_time_fmt": "開始時間",
        "forecast_end_time_fmt": "結束時間",
        "pop_type": "預報類型",
        "pop_value": "降雨機率(%)",
        "dispatch_recommended": "是否建議派工",
        "risk_level": "風險等級",
    }

    output_df = display_df[detail_cols].rename(columns=rename_map)

    st.dataframe(
        output_df,
        use_container_width=True,
        hide_index=True,
    )

    csv_data = output_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="下載目前明細 CSV",
        data=csv_data,
        file_name=f"pvesps_daytime_forecast_run_{selected_run_id}.csv",
        mime="text/csv",
    )
