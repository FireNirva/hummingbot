# LP Trend Rebalancer

Extends `LPRebalancer` with EMA-based trend detection for the **P3.5 strategy** from LP Reverse Research.

## P3.5 Strategy → Config Mapping

| P3.5 Parameter | Config Field | Default |
|---|---|---|
| EMA 24h trend filter | `ema_period=24`, `candles_interval="1h"` | 24 |
| Uptrend range ~5000 ticks | `uptrend_width_pct` | 40% |
| Sideways range ~2000 ticks | `sideways_width_pct` | 18% |
| Max duration uptrend | `uptrend_max_duration_hours` | 120h |
| Max duration sideways | `sideways_max_duration_hours` | 72h |
| Drawdown stop | `max_drawdown_pct` | 8% |
| Idle in downtrend | `idle_in_downtrend` | True |
| Regime threshold | `ema_uptrend_threshold_pct` / `ema_downtrend_threshold_pct` | 0.5% |

## Architecture

```
LpTrendRebalancer (this controller)
  inherits LPRebalancer
    │
    ├── update_processed_data()     ← adds EMA + regime detection
    ├── determine_executor_actions() ← adds idle/duration/drawdown checks
    ├── _check_force_close()        ← NEW: drawdown + duration + regime change
    ├── _get_regime_width_pct()     ← NEW: dynamic width by regime
    └── to_format_status()          ← adds regime display
        │
        └── LPExecutor (from hummingbot)
              ├── open position with calculated bounds
              ├── monitor IN_RANGE / OUT_OF_RANGE
              └── close on stop signal
```

## Regime Detection Logic

```
price > EMA * (1 + threshold)  →  UPTREND   →  wide range, 120h max
|price - EMA| < threshold     →  SIDEWAYS  →  tight range, 72h max
price < EMA * (1 - threshold)  →  DOWNTREND →  IDLE (no position)
```

## Example Config (UniV3 WETH/USDC 0.05% on Base)

```json
{
  "id": "lp_trend_weth_usdc",
  "controller_name": "lp_trend_rebalancer",
  "controller_type": "generic",

  "connector_name": "uniswap_ethereum_base",
  "trading_pair": "WETH-USDC",
  "pool_address": "0xd0b53d9277642d899df5c87a3966a349a798f224",

  "total_amount_quote": "500",
  "side": 0,

  "candles_connector": "binance",
  "candles_trading_pair": "ETH-USDT",
  "candles_interval": "1h",

  "ema_period": 24,
  "ema_uptrend_threshold_pct": "0.5",
  "ema_downtrend_threshold_pct": "0.5",

  "uptrend_width_pct": "40",
  "sideways_width_pct": "18",

  "uptrend_max_duration_hours": "120",
  "sideways_max_duration_hours": "72",
  "max_drawdown_pct": "8",
  "idle_in_downtrend": true,

  "rebalance_seconds": 300,
  "rebalance_threshold_pct": "1.0",
  "position_offset_pct": "0.01"
}
```

## Deployment

1. Ensure Gateway is running with Base chain configured and wallet added
2. Add the pool to Gateway: `POST /pools` with the pool address
3. Create controller config via hummingbot-api: `POST /controllers/configs/lp_trend_weth_usdc`
4. Deploy bot: `POST /bot-orchestration/deploy-v2-controllers`

## Force-Close Conditions

The controller force-closes a position when ANY of these triggers:

1. **Drawdown stop**: unrealized PnL < `-max_drawdown_pct`
2. **Max duration**: position held longer than regime-specific limit
3. **Regime → downtrend**: market turns bearish while holding

After force-close, the controller waits for the next uptrend/sideways signal before re-entering.
