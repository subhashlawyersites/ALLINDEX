# Trading Rules & Decision-Support System Guide (`SMA(2) / SMA(10) COLOR60`)

This document outlines the operating rules, index and weekday filters, partial target ladder, strike ladder conventions, live trade monitoring workflow, and Fyers integration setup for the `live_signal_app` decision-support system in `C:\AllIndicesBacktest`.

> **Environment Isolation:** Keep `C:\NiftyCrossoverBacktest` untouched as an isolated reference baseline. All multi-index development, rulebook refinements, and live dashboard features live under `C:\AllIndicesBacktest`.

---

## 1. Core Strategy Foundation (`SMA(2) / SMA(10) COLOR60`)

* **Timeframe & Candle Confirmation:** Evaluate signals on **15-minute closed candles** (`SMA(2)` vs. `SMA(10)`). Wait for candle close confirmation rather than acting on unclosed intra-bar ticks.
* **Primary Target:** **`0.60R`** (60% of initial risk `R`, where `R = |Entry - Reference SL|`).
* **Entry Time Cutoff:** **No new entries after `14:00 IST`**.
* **Exit & Session Rules:**
  * Monitor stop-loss (`SL`), primary target (`0.60R`), and end-of-day (`EOD 15:15 IST`) square-off rules.
  * When both `SL` and target levels are breached within the same bar, treat conservatively as `EXIT_SL_AMBIGUOUS` (stop-loss priority).

---

## 2. Time-of-Day, Gap & Day-of-Week Guardrails

### Blocked Chop Time Slots
Exclude high-chop / low-edge 15m slots from new entries:
* **`10:30` IST**
* **`10:45` IST**
* **`13:15` IST**

### Gap Exclusion Rule
* Flag and skip sessions with severe gap-down pressure (such as session open gaps at or below **`-0.75%`**).

### Index & Weekday Selection (Refined Rules)
* **Performance Anchor:** **`NIFTY 50` (`Monday`–`Thursday`)** is the primary high-edge index/day anchor.
* **Hard Avoid Index-Days:**
  * Avoid **`BANKNIFTY` on Mondays**.
  * Avoid **`FINNIFTY` on Thursdays**.
* **Caution / Lighter Days:** Treat Fridays or secondary days with caution based on per-index benchmarks (`Mon–Fri Unfiltered` baseline vs. `Refined` rule set).

---

## 3. Partial Target Ladder (`50%`–`100%`) & Realtime Progress Bar

Given `direction` (`CALL` or `PUT`), `entry_price`, and `sl_price` (risk `R`):
* **Target Milestones:** Computed at **`50%` (`0.50R`)**, **`60%` (`0.60R` — primary strategy target)**, **`70%` (`0.70R`)**, **`80%` (`0.80R`)**, **`90%` (`0.90R`)**, and **`100%` (`1.00R`)**.
* **Realtime Progress Bar:** Tracks current price movement along the `SL (-1.0R)` to `+1.0R` scale so you can see progress toward the `60%` target and extended `R` milestones.

---

## 4. Option Strike Ladder (`ITM1`, `ATM`, `OTM1`–`OTM10`)

Spot price is snapped to each index's strike step to generate `ITM1`, `ATM`, and `OTM1` through `OTM10` for the active direction (`CE` for `CALL`, `PE` for `PUT`):

| Index | Strike Step | Directional `ITM1` / `OTM1..OTM10` Mapping |
| :--- | :---: | :--- |
| **NIFTY 50 (`NIFTY50`)** | `50` pts | `CALL`: `ITM1` = `ATM - 50`, `OTMn` = `ATM + n*50` · `PUT`: `ITM1` = `ATM + 50`, `OTMn` = `ATM - n*50` |
| **SENSEX (`SENSEX`)** | `100` pts | `CALL`: `ITM1` = `ATM - 100`, `OTMn` = `ATM + n*100` · `PUT`: `ITM1` = `ATM + 100`, `OTMn` = `ATM - n*100` |
| **BANKNIFTY (`BANKNIFTY`)** | `100` pts | `CALL`: `ITM1` = `ATM - 100`, `OTMn` = `ATM + n*100` · `PUT`: `ITM1` = `ATM + 100`, `OTMn` = `ATM - n*100` |
| **FINNIFTY (`FINNIFTY`)** | `50` pts | `CALL`: `ITM1` = `ATM - 50`, `OTMn` = `ATM + n*50` · `PUT`: `ITM1` = `ATM + 50`, `OTMn` = `ATM - n*50` |

* When Fyers is connected, live option quote LTPs populate where available; when offline or unquoted, calculated strikes remain available.

---

## 5. Actual Trade Monitoring, Critical Alerts & Historical Analytics

* **Active Trade Monitoring:** Start or close monitored trades per index from the index page. Milestones (`50%`, `60%`, `80%`, `100%`) and exits (`EXIT_SL`, `EXIT_TARGET`, `EXIT_SL_AMBIGUOUS`, `EXIT_EOD_1515`, `MANUAL_EXIT`) are evaluated against latest candle data.
* **Appending New Trades to History & Charts:** Closed monitored trades append to `C:\AllIndicesBacktest\reports\live_monitored_trades.csv` and merge automatically into `/api/analytics/<scope>`, updating KPI cards, cumulative points/R curves, weekday/exit/mix charts, and the paginated trade log.
* **Dynamic Filtering & Sorting:** Filtering by weekday, direction (`CALL`/`PUT`), or exit reason updates both the charts/KPIs and the trade log; sorting by date, points, R value, entry time, exit reason, or direction reorders the trade log.
* **Critical Event Alerts:** Critical events (`CONFIRMED_CALL`, `CONFIRMED_PUT`, `EXIT_SL`, `EXIT_SL_AMBIGUOUS`, `MILESTONE_*`, `GAP_EXCLUDED`, `AVOID_WEEKDAY`, `EXIT_EOD_1515`) surface in the alert log, banner, and optional audio chime.

---

## 6. Fyers API v3 Authorization & Hybrid Fallback

1. **Authorization (`/auth`):** Configure `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, and `FYERS_REDIRECT_URI` (matching your Fyers API app redirect URI exactly), then exchange a fresh `auth_code` for `FYERS_ACCESS_TOKEN` or save a direct token into `C:\AllIndicesBacktest\.env`.
2. **Connect / Reconnect:** Use the header **Connect / Reconnect** button or `/api/fyers/connect` to test and refresh connection status (`get_profile()`).
3. **Hybrid CSV Fallback:** If Fyers returns an expired/invalid token (`-16`), rate limit, or network error, `data_provider.py` falls back seamlessly to local 15m CSV files in `C:\AllIndicesBacktest\data\`.

---

## 7. Running the Application

```powershell
Set-Location "C:\AllIndicesBacktest"
.\.venv\Scripts\Activate.ps1
python live_signal_app\app.py
```

* Combined Command Center: `http://127.0.0.1:5055/`
* Individual Index Pages: `/nifty50`, `/sensex`, `/banknifty`, `/finnifty`
* Fyers Authorization Page: `/auth`