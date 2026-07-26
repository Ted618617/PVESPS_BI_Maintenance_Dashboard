import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# ============================================================
# Config
# ============================================================
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

PREDICTION_TABLE = "mart.fact_generation_prediction_daily"

st.set_page_config(
    page_title="PVESPS Prediction Dashboard v1.1",
    layout="wide"
)


# ============================================================
# DB
# ============================================================
@st.cache_resource
def get_engine():
    return create_engine(
        DATABASE_URL,
        future=True,
        pool_pre_ping=True
    )


@st.cache_data(ttl=300)
def load_prediction_data():
    sql = text(f"""
        SELECT
            prediction_id,
            run_id,
            site_sk,
            location_sk,
            prediction_date::date AS prediction_date,
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
            source_row_count,
            created_at,
            updated_at
        FROM {PREDICTION_TABLE}
        ORDER BY prediction_date DESC, site_sk
    """)

    engine = get_engine()
    df = pd.read_sql(sql, engine)

    if not df.empty:
        df["prediction_date"] = pd.to_datetime(df["prediction_date"])
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
        df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce")

    return df


# ============================================================
# Helpers
# ============================================================
def format_number(x, digits=2):
    if pd.isna(x):
        return "-"
    return f"{x:,.{digits}f}"


def get_default_date_range(df: pd.DataFrame):
    if df.empty:
        today = date.today()
        return today - timedelta(days=14), today

    min_dt = df["prediction_date"].min().date()
    max_dt = df["prediction_date"].max().date()
    return min_dt, max_dt


def build_filtered_df(df: pd.DataFrame):
    if df.empty:
        return df

    st.sidebar.header("Filters")

    model_options = sorted(df["model_name"].dropna().unique().tolist())
    selected_models = st.sidebar.multiselect(
        "Model Name",
        options=model_options,
        default=model_options
    )

    version_options = sorted(df["model_version"].dropna().unique().tolist())
    selected_versions = st.sidebar.multiselect(
        "Model Version",
        options=version_options,
        default=version_options
    )

    prediction_type_options = sorted(df["prediction_type"].dropna().unique().tolist())
    selected_prediction_types = st.sidebar.multiselect(
        "Prediction Type",
        options=prediction_type_options,
        default=prediction_type_options
    )

    site_options = sorted(df["site_sk"].dropna().astype(int).unique().tolist())
    selected_sites = st.sidebar.multiselect(
        "Site",
        options=site_options,
        default=site_options
    )

    latest_only = st.sidebar.checkbox("Latest Prediction Only", value=True)

    min_dt, max_dt = get_default_date_range(df)
    date_range = st.sidebar.date_input(
        "Prediction Date Range",
        value=(min_dt, max_dt),
        min_value=min_dt,
        max_value=max_dt
    )

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = min_dt, max_dt

    filtered = df.copy()

    if selected_models:
        filtered = filtered[filtered["model_name"].isin(selected_models)]

    if selected_versions:
        filtered = filtered[filtered["model_version"].isin(selected_versions)]

    if selected_prediction_types:
        filtered = filtered[filtered["prediction_type"].isin(selected_prediction_types)]

    if selected_sites:
        filtered = filtered[filtered["site_sk"].isin(selected_sites)]

    filtered = filtered[
        (filtered["prediction_date"].dt.date >= start_date) &
        (filtered["prediction_date"].dt.date <= end_date)
    ]

    if latest_only:
        filtered = filtered[filtered["is_latest_prediction"] == True]

    return filtered


def get_worst_site(df: pd.DataFrame):
    if df.empty:
        return "-"

    site_rank = (
        df.groupby("site_sk", as_index=False)
        .agg(avg_abs_pct_error=("abs_pct_error", "mean"))
        .sort_values("avg_abs_pct_error", ascending=False)
    )

    if site_rank.empty:
        return "-"

    return str(site_rank.iloc[0]["site_sk"])


# ============================================================
# Render Blocks
# ============================================================
def render_header_notes(df: pd.DataFrame):
    st.subheader("Overview")

    if df.empty:
        st.info("No prediction data found.")
        return

    latest_date = df["prediction_date"].max().date()
    model_names = ", ".join(sorted(df["model_name"].dropna().unique().tolist()))
    model_versions = ", ".join(sorted(df["model_version"].dropna().unique().tolist()))
    prediction_types = ", ".join(sorted(df["prediction_type"].dropna().unique().tolist()))

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"**Latest Date**  \n{latest_date}")
    c2.markdown(f"**Model**  \n{model_names}")
    c3.markdown(f"**Version**  \n{model_versions}")
    c4.markdown(f"**Prediction Type**  \n{prediction_types}")


def render_kpis(df: pd.DataFrame):
    total_rows = len(df)

    mean_pred = df["predicted_generation_kwh"].mean()
    mean_actual = df["actual_generation_kwh"].mean()
    mean_abs_error = df["abs_error_kwh"].mean()
    mean_abs_pct_error = df["abs_pct_error"].mean()
    worst_site = get_worst_site(df)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Rows", f"{total_rows:,}")
    c2.metric("Avg Predicted kWh", format_number(mean_pred))
    c3.metric("Avg Actual kWh", format_number(mean_actual))
    c4.metric("Avg Abs Error", format_number(mean_abs_error))
    c5.metric("Worst Site", worst_site)

    c6, c7 = st.columns(2)
    c6.metric("Avg Abs % Error", format_number(mean_abs_pct_error, 4))
    latest_df = df.sort_values("prediction_date", ascending=False)
    latest_date = latest_df["prediction_date"].max()
    latest_date_df = latest_df[latest_df["prediction_date"] == latest_date]
    latest_day_avg_error = latest_date_df["abs_error_kwh"].mean()
    c7.metric("Latest Day Avg Abs Error", format_number(latest_day_avg_error))


def render_prediction_vs_actual(df: pd.DataFrame):
    st.subheader("Prediction vs Actual Overview")

    daily_df = (
        df.groupby("prediction_date", as_index=False)
        .agg(
            total_predicted_kwh=("predicted_generation_kwh", "sum"),
            total_actual_kwh=("actual_generation_kwh", "sum"),
        )
        .sort_values("prediction_date")
    )

    chart_df = daily_df.set_index("prediction_date")[["total_predicted_kwh", "total_actual_kwh"]]
    st.line_chart(chart_df, width="stretch")

    with st.expander("Prediction vs Actual Daily Table"):
        st.dataframe(daily_df, width="stretch", hide_index=True)


def render_error_monitoring(df: pd.DataFrame):
    st.subheader("Error Monitoring")

    daily_error_df = (
        df.groupby("prediction_date", as_index=False)
        .agg(
            avg_abs_error_kwh=("abs_error_kwh", "mean"),
            avg_abs_pct_error=("abs_pct_error", "mean"),
        )
        .sort_values("prediction_date")
    )

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Average Absolute Error (kWh)**")
        chart_df_1 = daily_error_df.set_index("prediction_date")[["avg_abs_error_kwh"]]
        st.line_chart(chart_df_1, width="stretch")

    with c2:
        st.markdown("**Average Absolute Percentage Error (%)**")
        chart_df_2 = daily_error_df.set_index("prediction_date")[["avg_abs_pct_error"]]
        st.line_chart(chart_df_2, width="stretch")

    with st.expander("Daily Error Summary Table"):
        st.dataframe(daily_error_df, width="stretch", hide_index=True)


def render_top_error_sites(df: pd.DataFrame):
    st.subheader("Top Error Sites")

    rank_df = (
        df.groupby("site_sk", as_index=False)
        .agg(
            avg_abs_error_kwh=("abs_error_kwh", "mean"),
            avg_abs_pct_error=("abs_pct_error", "mean"),
            max_abs_error_kwh=("abs_error_kwh", "max"),
            rows=("prediction_id", "count"),
        )
        .sort_values(["avg_abs_pct_error", "avg_abs_error_kwh"], ascending=[False, False])
    )

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Top 5 by Avg Abs % Error**")
        top_pct = rank_df.nlargest(5, "avg_abs_pct_error")
        st.dataframe(top_pct, width="stretch", hide_index=True)

    with c2:
        st.markdown("**Top 5 by Avg Abs Error (kWh)**")
        top_kwh = rank_df.nlargest(5, "avg_abs_error_kwh")
        st.dataframe(top_kwh, width="stretch", hide_index=True)

    with st.expander("Full Site Error Ranking"):
        st.dataframe(rank_df, width="stretch", hide_index=True)


def render_site_trend(df: pd.DataFrame):
    st.subheader("Site Trend View")

    site_list = sorted(df["site_sk"].dropna().astype(int).unique().tolist())
    if not site_list:
        st.info("No site data available.")
        return

    selected_site = st.selectbox("Select Site", options=site_list, index=0)

    site_df = (
        df[df["site_sk"] == selected_site]
        .sort_values("prediction_date")
        .copy()
    )

    if site_df.empty:
        st.info("No site detail data found.")
        return

    c1, c2, c3, c4, c5 = st.columns(5)

    latest_row = site_df.sort_values("prediction_date", ascending=False).iloc[0]
    c1.metric("Latest Predicted", format_number(latest_row["predicted_generation_kwh"]))
    c2.metric("Latest Actual", format_number(latest_row["actual_generation_kwh"]))
    c3.metric("Latest Abs Error", format_number(latest_row["abs_error_kwh"]))
    c4.metric("Latest Abs % Error", format_number(latest_row["abs_pct_error"], 4))
    c5.metric("Period Avg Abs Error", format_number(site_df["abs_error_kwh"].mean()))

    trend_df = site_df[[
        "prediction_date",
        "predicted_generation_kwh",
        "actual_generation_kwh",
        "abs_error_kwh"
    ]].set_index("prediction_date")

    st.markdown("**Predicted vs Actual**")
    st.line_chart(
        trend_df[["predicted_generation_kwh", "actual_generation_kwh"]],
        width="stretch"
    )

    st.markdown("**Absolute Error Trend**")
    st.line_chart(
        trend_df[["abs_error_kwh"]],
        width="stretch"
    )

    with st.expander(f"Site {selected_site} Detail Table"):
        show_cols = [
            "site_sk",
            "prediction_date",
            "model_name",
            "model_version",
            "predicted_generation_kwh",
            "actual_generation_kwh",
            "prediction_error_kwh",
            "abs_error_kwh",
            "abs_pct_error",
            "lower_bound_kwh",
            "upper_bound_kwh",
        ]
        st.dataframe(site_df[show_cols], width="stretch", hide_index=True)


def render_prediction_table(df: pd.DataFrame):
    st.subheader("Prediction Detail")

    display_cols = [
        "site_sk",
        "prediction_date",
        "model_name",
        "model_version",
        "prediction_type",
        "predicted_generation_kwh",
        "actual_generation_kwh",
        "prediction_error_kwh",
        "abs_error_kwh",
        "abs_pct_error",
        "lower_bound_kwh",
        "upper_bound_kwh",
        "is_latest_prediction",
    ]

    show_df = df[display_cols].copy().sort_values(
        ["prediction_date", "site_sk"], ascending=[False, True]
    )

    st.dataframe(show_df, width="stretch", hide_index=True)


# ============================================================
# App
# ============================================================
def main():
    st.title("PVESPS｜Prediction Dashboard v1.1")
    st.caption("Step_09 Prediction Monitoring / Forecast Review")

    raw_df = load_prediction_data()

    if raw_df.empty:
        st.warning("No prediction records found in mart.fact_generation_prediction_daily.")
        return

    filtered_df = build_filtered_df(raw_df)

    if filtered_df.empty:
        st.warning("No rows matched the selected filters.")
        return

    render_header_notes(filtered_df)
    st.divider()

    render_kpis(filtered_df)
    st.divider()

    render_prediction_vs_actual(filtered_df)
    st.divider()

    render_error_monitoring(filtered_df)
    st.divider()

    render_top_error_sites(filtered_df)
    st.divider()

    render_site_trend(filtered_df)
    st.divider()

    render_prediction_table(filtered_df)


if __name__ == "__main__":
    main()