from pathlib import Path
import pandas as pd

df = pd.read_csv(Path("reports/sma2_sma10_color60_1y_trades.csv"))

print("=== EXIT REASONS ACROSS ALL 787 TRADES ===")
for reason, g in df.groupby("exit_reason"):
    n = len(g)
    wins = (g["points"] > 0).sum()
    print(
        f"  {reason:18s} | trades={n:3d} | win={wins/n*100:5.1f}% | "
        f"avg_r={g['r_val'].mean():+5.2f}R | cum_r={g['r_val'].sum():+7.2f}R | pts={g['points'].sum():+8.1f}"
    )

print("\n=== WORST 6 INDEX + WEEKDAY BUCKETS (BY CUMULATIVE R) ===")
day_rows = []
for (idx, wd), g in df.groupby(["index", "weekday"]):
    n = len(g)
    wins = (g["points"] > 0).sum()
    day_rows.append((g["r_val"].sum(), idx, wd, n, wins/n*100, g["r_val"].mean(), g["points"].sum()))
for cum_r, idx, wd, n, wr, avg_r, pts in sorted(day_rows)[:6]:
    print(f"  {idx:10s} | {wd:9s} | trades={n:2d} | win={wr:5.1f}% | avg_r={avg_r:+5.2f}R | cum_r={cum_r:+6.2f}R | pts={pts:+8.1f}")

print("\n=== BEST 6 INDEX + WEEKDAY BUCKETS (BY CUMULATIVE R) ===")
for cum_r, idx, wd, n, wr, avg_r, pts in sorted(day_rows, reverse=True)[:6]:
    print(f"  {idx:10s} | {wd:9s} | trades={n:2d} | win={wr:5.1f}% | avg_r={avg_r:+5.2f}R | cum_r={cum_r:+6.2f}R | pts={pts:+8.1f}")

print("\n=== WORST 5 ENTRY TIMES ACROSS ALL INDICES (MIN 8 TRADES) ===")
t_rows = []
for t_str, g in df.groupby("entry_time"):
    n = len(g)
    if n >= 8:
        wins = (g["points"] > 0).sum()
        t_rows.append((g["r_val"].sum(), t_str, n, wins/n*100, g["r_val"].mean(), g["points"].sum()))
for cum_r, t_str, n, wr, avg_r, pts in sorted(t_rows)[:5]:
    print(f"  Entry {t_str} | trades={n:2d} | win={wr:5.1f}% | avg_r={avg_r:+5.2f}R | cum_r={cum_r:+6.2f}R | pts={pts:+8.1f}")

print("\n=== BEST 5 ENTRY TIMES ACROSS ALL INDICES (MIN 8 TRADES) ===")
for cum_r, t_str, n, wr, avg_r, pts in sorted(t_rows, reverse=True)[:5]:
    print(f"  Entry {t_str} | trades={n:2d} | win={wr:5.1f}% | avg_r={avg_r:+5.2f}R | cum_r={cum_r:+6.2f}R | pts={pts:+8.1f}")
