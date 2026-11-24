from pathlib import Path
import pandas as pd
from test_rule_filters import INDICES, BAD_TIMES_DEFAULT, simulate_variant

REPORT_DIR = Path("reports")
DATA_DIR = Path("data")

base_csv = REPORT_DIR / "sma2_sma10_color60_1y_trades.csv"
if not base_csv.exists():
    raise FileNotFoundError(f"Missing {base_csv}. Run run_sma2_sma10_backtest.py first.")

bdf = pd.read_csv(base_csv)

print("=== TOP 12 WINNING SESSION DATES (BASELINE ALL INDICES) ===")
by_date = (
    bdf.groupby(["session_date", "weekday"], as_index=False)
    .agg(
        trades=("r_val", "count"),
        wins=("points", lambda s: int((s > 0).sum())),
        cum_r=("r_val", "sum"),
        cum_pts=("points", "sum"),
        indices=("index", lambda s: ",".join(sorted(set(s)))),
    )
    .sort_values("cum_r", ascending=False)
)
for _, r in by_date.head(12).iterrows():
    print(
        f"  {r['session_date']} ({r['weekday'][:3]}) | trades={r['trades']:2d} | "
        f"wins={r['wins']:2d} | cum_r={r['cum_r']:+5.2f}R | pts={r['cum_pts']:+8.1f} | [{r['indices']}]"
    )

print("\n=== TOP 12 WORST LOSING SESSION DATES (BASELINE ALL INDICES) ===")
for _, r in by_date.tail(12).iloc[::-1].iterrows():
    print(
        f"  {r['session_date']} ({r['weekday'][:3]}) | trades={r['trades']:2d} | "
        f"wins={r['wins']:2d} | cum_r={r['cum_r']:+5.2f}R | pts={r['cum_pts']:+8.1f} | [{r['indices']}]"
    )

by_date.to_csv(REPORT_DIR / "session_dates_ranked_1y.csv", index=False)

# Also evaluate refined variant across all 4 indices
ref_frames = []
for idx_name, fname in INDICES.items():
    p = DATA_DIR / fname
    if p.exists():
        ref_frames.append(
            simulate_variant(
                pd.read_csv(p),
                idx_name,
                target_mult=0.60,
                latest_entry="14:00",
                blocked_times=BAD_TIMES_DEFAULT,
                allowed_weekdays=None,
                drop_bad_index_days=True,
            )
        )

if ref_frames:
    rdf = pd.concat(ref_frames, ignore_index=True)
    rdf.to_csv(REPORT_DIR / "refined_trades_1y.csv", index=False)
    print("\n=== REFINED VARIANT WEEKDAY SUMMARY (ALL 4 INDICES COMBINED) ===")
    wd_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for wd in wd_order:
        sub = rdf[rdf["weekday"] == wd]
        if sub.empty:
            continue
        n = len(sub)
        wr = (sub["points"] > 0).mean() * 100
        print(
            f"  {wd:9s} | trades={n:3d} | win={wr:5.1f}% | "
            f"avg_r={sub['r_val'].mean():+5.2f}R | cum_r={sub['r_val'].sum():+6.2f}R | pts={sub['points'].sum():+8.1f}"
        )
    print(f"\nSaved session ranking -> {REPORT_DIR / 'session_dates_ranked_1y.csv'}")
    print(f"Saved refined trades   -> {REPORT_DIR / 'refined_trades_1y.csv'}")
