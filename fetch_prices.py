"""Download adjusted daily closes for cluster tickers and SPY.

Reads data/clusters.csv. Writes one CSV per ticker under data/prices/.
That directory is gitignored: Yahoo Finance series are not redistributable
and must not be copied into the public repository.

The download spans every event date, plus a calendar buffer of 21 days
before the earliest event and 170 days after the latest. That covers 10
trading days before each event and 100 trading days after it, including
holidays. analyze.py then measures returns on the trading calendar.
"""

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
CLUSTERS = ROOT / "data" / "clusters.csv"
PRICES = ROOT / "data" / "prices"
BATCH_SIZE = 20
PAD_BEFORE_DAYS = 21
PAD_AFTER_DAYS = 170


def load_clusters(path):
    frame = pd.read_csv(path, dtype={"ticker": "string"})
    if "ticker" not in frame.columns or "event_date" not in frame.columns:
        raise SystemExit("clusters.csv must have ticker and event_date columns")
    frame["ticker"] = frame["ticker"].fillna("").str.strip().str.upper()
    frame["event_date"] = pd.to_datetime(frame["event_date"], errors="coerce")
    frame = frame[(frame["ticker"] != "") & frame["event_date"].notna()]
    return frame


def adj_close_series(frame, ticker):
    if frame is None or getattr(frame, "empty", True):
        return None
    table = frame
    if isinstance(frame.columns, pd.MultiIndex):
        level0 = set(frame.columns.get_level_values(0))
        level1 = set(frame.columns.get_level_values(1))
        if ticker in level0:
            table = frame[ticker]
        elif ticker in level1:
            pieces = {}
            for field in frame.columns.get_level_values(0).unique():
                column = frame[field]
                if ticker in column.columns:
                    pieces[field] = column[ticker]
            table = pd.DataFrame(pieces)
        else:
            return None
    if isinstance(table, pd.Series):
        series = table
    else:
        column = None
        for name in ("Adj Close", "Close"):
            if name in table.columns:
                column = name
                break
        if column is None:
            return None
        series = table[column]
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty:
        return None
    # utc=True accepts both naive session dates and tz-aware yfinance indexes.
    series.index = pd.to_datetime(series.index, utc=True).tz_localize(None).normalize()
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series


def download_frame(tickers, start, end):
    try:
        return yf.download(
            tickers,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=False,
            actions=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:
        print(f"batch download failed ({exc})", file=sys.stderr)
        return None


def write_series(ticker, series):
    out = pd.DataFrame(
        {
            "date": series.index.strftime("%Y-%m-%d"),
            "adj_close": series.to_numpy(),
        }
    )
    path = PRICES / f"{ticker}.csv"
    out.to_csv(path, index=False)
    return path


def fetch_one(ticker, start, end):
    frame = download_frame([ticker], start, end)
    return adj_close_series(frame, ticker)


def main():
    if not CLUSTERS.is_file():
        raise SystemExit(f"missing {CLUSTERS}")
    clusters = load_clusters(CLUSTERS)
    if clusters.empty:
        print("no cluster rows in data/clusters.csv; nothing to download")
        return

    start = (clusters["event_date"].min() - pd.Timedelta(days=PAD_BEFORE_DAYS)).normalize()
    end = (clusters["event_date"].max() + pd.Timedelta(days=PAD_AFTER_DAYS + 1)).normalize()
    tickers = sorted(set(clusters["ticker"].tolist()))
    requested = tickers + (["SPY"] if "SPY" not in tickers else [])
    print(
        f"events={len(clusters)} tickers={len(tickers)} "
        f"window={start.date()} .. {end.date()} (end exclusive)"
    )

    PRICES.mkdir(parents=True, exist_ok=True)
    saved = []
    skipped = []

    for offset in range(0, len(requested), BATCH_SIZE):
        batch = requested[offset : offset + BATCH_SIZE]
        print(f"batch {offset // BATCH_SIZE + 1}: {', '.join(batch)}")
        frame = download_frame(batch if len(batch) > 1 else batch[0], start, end)
        for ticker in batch:
            series = adj_close_series(frame, ticker)
            if series is None:
                series = fetch_one(ticker, start, end)
            if series is None:
                print(f"skip {ticker}: no adjusted closes (missing or delisted)")
                skipped.append(ticker)
                continue
            write_series(ticker, series)
            saved.append(ticker)
            print(f"saved {ticker}: {len(series)} rows")

    print(f"tickers requested: {len(requested)} (including SPY)")
    print(f"saved: {len(saved)}")
    print(f"skipped: {len(skipped)}")
    if skipped:
        print("skipped tickers: " + ", ".join(skipped))
    if "SPY" in skipped:
        raise SystemExit("SPY download failed; abnormal returns cannot be computed")


if __name__ == "__main__":
    main()
