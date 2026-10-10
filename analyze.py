"""Event-study stats for insider buy-clusters versus SPY.

Abnormal return at horizon N is the ticker's cumulative return from the
event-date close to N trading days later, minus SPY's cumulative return
over those same two dates. If the event date is not a session, day 0 is
the first trading day on or after it.

Writes results/summary.csv and one histogram per horizon. Also repeats the
stats on clusters with total_dollars >= 500000, and on ex_mega: every
cluster except those with row_count >= 50.
"""

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent
CLUSTERS = ROOT / "data" / "clusters.csv"
PRICES = ROOT / "data" / "prices"
RESULTS = ROOT / "results"
HORIZONS = (30, 60, 90)
ROBUST_MIN_DOLLARS = 500000
MEGA_MIN_ROWS = 50
MIN_PRE_EVENT_SESSIONS = 60


def load_prices(ticker):
    path = PRICES / f"{ticker}.csv"
    if not path.is_file():
        return None
    frame = pd.read_csv(path)
    if "date" not in frame.columns or "adj_close" not in frame.columns:
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["adj_close"] = pd.to_numeric(frame["adj_close"], errors="coerce")
    frame = frame.dropna(subset=["date", "adj_close"])
    frame = frame[frame["adj_close"] > 0]
    if frame.empty:
        return None
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    series = frame.set_index("date")["adj_close"]
    series.index = pd.to_datetime(series.index).normalize()
    return series.sort_index()


def sessions_before_day0(series, event_date):
    """Trading days strictly before day 0, or None when the series has no day-0 session."""
    if series is None or series.empty:
        return None
    event_ts = pd.Timestamp(event_date).normalize()
    start_pos = int(series.index.searchsorted(event_ts))
    if start_pos >= len(series.index):
        return None
    return start_pos


def forward_return(series, event_date, horizon):
    if series is None or series.empty:
        return None
    event_ts = pd.Timestamp(event_date).normalize()
    start_pos = int(series.index.searchsorted(event_ts))
    end_pos = start_pos + horizon
    if start_pos >= len(series.index) or end_pos >= len(series.index):
        return None
    start_day = series.index[start_pos]
    end_day = series.index[end_pos]
    price0 = float(series.iloc[start_pos])
    price1 = float(series.iloc[end_pos])
    if not math.isfinite(price0) or not math.isfinite(price1) or price0 <= 0 or price1 <= 0:
        return None
    return (price1 / price0) - 1.0, start_day, end_day


def spy_return(spy, start_day, end_day):
    if spy is None or start_day not in spy.index or end_day not in spy.index:
        return None
    price0 = float(spy.loc[start_day])
    price1 = float(spy.loc[end_day])
    if not math.isfinite(price0) or not math.isfinite(price1) or price0 <= 0 or price1 <= 0:
        return None
    return (price1 / price0) - 1.0


def collect(clusters, price_cache, spy, horizon):
    records = []
    skipped = 0
    excluded_insufficient_history = 0
    for row in clusters.itertuples(index=False):
        ticker = str(row.ticker).strip().upper()
        series = price_cache.get(ticker)
        prior = sessions_before_day0(series, row.event_date)
        if prior is not None and prior < MIN_PRE_EVENT_SESSIONS:
            excluded_insufficient_history += 1
            continue
        stock = forward_return(series, row.event_date, horizon)
        if stock is None:
            skipped += 1
            continue
        market = spy_return(spy, stock[1], stock[2])
        if market is None:
            skipped += 1
            continue
        event_date = pd.Timestamp(row.event_date).strftime("%Y-%m-%d")
        records.append((ticker, event_date, stock[0] - market))
    return records, skipped, excluded_insufficient_history


def print_tails(records, horizon):
    ordered = sorted(records, key=lambda item: item[2])
    print(f"+{horizon} trading days, 3 most negative:")
    for ticker, event_date, abnormal in ordered[:3]:
        print(f"  {ticker}  {event_date}  {abnormal:.6f}")
    print(f"+{horizon} trading days, 3 most positive:")
    for ticker, event_date, abnormal in reversed(ordered[-3:]):
        print(f"  {ticker}  {event_date}  {abnormal:.6f}")


def summarize(values, skipped, excluded_insufficient_history):
    n = len(values)
    if n == 0:
        return {
            "n": 0,
            "skipped": skipped,
            "excluded_insufficient_history": excluded_insufficient_history,
            "mean_abnormal_return": None,
            "median_abnormal_return": None,
            "hit_rate_pct": None,
            "std_error": None,
            "t_stat": None,
        }
    series = pd.Series(values, dtype="float64")
    mean = float(series.mean())
    median = float(series.median())
    hit = 100.0 * float((series > 0).sum()) / n
    std_error = None
    t_stat = None
    if n >= 2:
        std_error = float(series.std(ddof=1)) / math.sqrt(n)
        if std_error > 0:
            t_stat = mean / std_error
    return {
        "n": n,
        "skipped": skipped,
        "excluded_insufficient_history": excluded_insufficient_history,
        "mean_abnormal_return": mean,
        "median_abnormal_return": median,
        "hit_rate_pct": hit,
        "std_error": std_error,
        "t_stat": t_stat,
    }


def fmt(value, digits):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    return f"{value:.{digits}f}"


def save_hist(values, horizon, title_suffix, path):
    figure, axis = plt.subplots(figsize=(8, 4.5))
    bins = min(40, max(8, int(math.sqrt(len(values)))))
    axis.hist(values, bins=bins, color="#1f4e79", edgecolor="white")
    axis.axvline(0.0, color="#222222", linewidth=1)
    mean = float(pd.Series(values, dtype="float64").mean())
    axis.axvline(mean, color="#d4760a", linewidth=1.5, linestyle="--", label=f"mean {mean:.2%}")
    axis.set_title(f"Buy-cluster abnormal return, +{horizon} trading days{title_suffix}")
    axis.set_xlabel("Abnormal return (stock minus SPY)")
    axis.set_ylabel("Number of clusters")
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def subset_frame(clusters, name):
    if name == "dollars_gte_500000":
        return clusters[clusters["total_dollars"] >= ROBUST_MIN_DOLLARS].copy()
    if name == "ex_mega":
        return clusters[~(clusters["row_count"] >= MEGA_MIN_ROWS)].copy()
    return clusters


def main():
    if not CLUSTERS.is_file():
        raise SystemExit(f"missing {CLUSTERS}")
    clusters = pd.read_csv(CLUSTERS)
    required = {"ticker", "event_date", "total_dollars", "row_count"}
    missing = required - set(clusters.columns)
    if missing:
        raise SystemExit("clusters.csv missing columns: " + ", ".join(sorted(missing)))
    clusters["ticker"] = clusters["ticker"].astype("string").fillna("").str.strip().str.upper()
    clusters["event_date"] = pd.to_datetime(clusters["event_date"], errors="coerce")
    clusters["total_dollars"] = pd.to_numeric(clusters["total_dollars"], errors="coerce")
    clusters["row_count"] = pd.to_numeric(clusters["row_count"], errors="coerce")
    clusters = clusters.dropna(subset=["ticker", "event_date", "total_dollars"])
    clusters = clusters[clusters["ticker"] != ""]

    spy = load_prices("SPY")
    if spy is None:
        raise SystemExit("missing data/prices/SPY.csv; run fetch_prices.py first")

    price_cache = {}
    for ticker in sorted(set(clusters["ticker"].tolist())):
        price_cache[ticker] = load_prices(ticker)

    subsets = ("all", "dollars_gte_500000", "ex_mega")
    rows = []
    chart_jobs = []
    for subset_name in subsets:
        sample = subset_frame(clusters, subset_name)
        for horizon in HORIZONS:
            records, skipped, excluded_insufficient_history = collect(sample, price_cache, spy, horizon)
            values = [record[2] for record in records]
            stats = summarize(values, skipped, excluded_insufficient_history)
            stats["subset"] = subset_name
            stats["horizon_trading_days"] = horizon
            rows.append(stats)
            if subset_name == "all":
                print_tails(records, horizon)
            if values and subset_name == "all":
                chart_jobs.append((values, horizon, "", RESULTS / f"hist_{horizon}.png"))
            elif values and subset_name == "dollars_gte_500000":
                chart_jobs.append(
                    (values, horizon, " (dollars >= 500000)", RESULTS / f"hist_{horizon}_ge500k.png")
                )

    summary = pd.DataFrame(rows)
    column_order = [
        "subset",
        "horizon_trading_days",
        "n",
        "skipped",
        "excluded_insufficient_history",
        "mean_abnormal_return",
        "median_abnormal_return",
        "hit_rate_pct",
        "std_error",
        "t_stat",
    ]
    summary = summary[column_order]
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / "summary.csv"
    formatted = summary.copy()
    for column, digits in (
        ("mean_abnormal_return", 6),
        ("median_abnormal_return", 6),
        ("hit_rate_pct", 4),
        ("std_error", 6),
        ("t_stat", 4),
    ):
        formatted[column] = summary[column].map(lambda value, d=digits: fmt(value, d))
    formatted.to_csv(out_path, index=False)

    for values, horizon, suffix, path in chart_jobs:
        save_hist(values, horizon, suffix, path)
        print(f"wrote {path.name}")

    print(formatted.to_string(index=False))
    print(f"wrote {out_path}")
    print(f"clusters in file: {len(clusters)}")


if __name__ == "__main__":
    main()
