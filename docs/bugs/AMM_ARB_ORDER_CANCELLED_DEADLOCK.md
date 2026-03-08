# AMM_ARB Strategy Deadlock on Order Cancellation

## Bug ID
`AMM_ARB_ORDER_CANCELLED_DEADLOCK`

## Severity
**Critical** - Strategy becomes completely unresponsive

## Affected Component
- `hummingbot/strategy/amm_arb/amm_arb.py`
- Strategy: `amm_arb` (CEX-CEX / CEX-DEX Arbitrage)

## Problem Description

When an order is **cancelled** (due to timeout, insufficient liquidity, or manual cancellation), the `amm_arb` strategy enters a deadlock state and stops all arbitrage calculations.

### Root Cause

The strategy uses `asyncio.Event` objects to track order completion:

```python
# In execute_arb_proposals():
if not self._concurrent_orders_submission:
    await arb_side.completed_event.wait()  # <-- BLOCKS HERE FOREVER
```

The strategy has handlers for:
- `OrderCompletedEvent` → calls `set_order_completed()`
- `MarketOrderFailureEvent` → calls `set_order_failed()`
- `OrderExpiredEvent` → calls `set_order_completed()`

**Missing handler:**
- `OrderCancelledEvent` → **NO HANDLER** → `completed_event` never set → **DEADLOCK**

### Symptoms

1. Arbitrage calculations stop appearing in logs
2. Only `MexcAPIUserStreamDataSource - Successfully refreshed listen key` messages every 30 minutes
3. Bot appears running but does nothing
4. No trades executed even with profitable opportunities

### Example Log Sequence

```
08:01:50 - amm_arb - Found arbitrage opportunity!
08:01:50 - amm_arb - Placing BUY order for 500.00 IRON at mexc
08:01:50 - client_order_tracker - Created MARKET BUY order HUMBOTBINUT645079fe61d7cdd9435fc
08:01:50 - client_order_tracker - The BUY order amounting to 18.59/500.00 IRON has been filled
08:02:00 - client_order_tracker - Successfully canceled order HUMBOTBINUT645079fe61d7cdd9435fc  # <-- CANCELLED!
# ===== SILENCE - NO MORE ARB CALCULATIONS =====
08:33:34 - MexcAPIUserStreamDataSource - Successfully refreshed listen key
09:03:34 - MexcAPIUserStreamDataSource - Successfully refreshed listen key
# ... only listen key refreshes every 30 minutes ...
```

## Immediate Recovery

### Option 1: Restart Container
```bash
docker restart hummingbot
```

### Option 2: Stop and Start Strategy via MQTT
```bash
curl -u admin:admin -X POST http://localhost:8000/bot-orchestration/iron-arb-v2/stop
sleep 5
curl -u admin:admin -X POST http://localhost:8000/bot-orchestration/iron-arb-v2/start
```

## Permanent Fix

### Code Changes Required

**File:** `hummingbot/strategy/amm_arb/amm_arb.py`

#### Step 1: Add Import

```python
# Change this:
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    MarketOrderFailureEvent,
    OrderExpiredEvent,
    OrderType,
    SellOrderCompletedEvent,
)

# To this:
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    OrderExpiredEvent,
    OrderType,
    SellOrderCompletedEvent,
)
```

#### Step 2: Add Handler Method

Add after `did_expire_order` method (around line 488):

```python
def did_cancel_order(self, cancelled_event: OrderCancelledEvent):
    """Handle order cancellation to prevent strategy deadlock"""
    self.log_with_clock(logging.WARNING,
                        f"Order {cancelled_event.order_id} was cancelled. Marking as failed.")
    self.set_order_failed(order_id=cancelled_event.order_id)
```

### Apply Fix to Running Container

```bash
# Copy modified file into container
docker cp /path/to/modified/amm_arb.py hummingbot:/home/hummingbot/hummingbot/strategy/amm_arb/amm_arb.py

# Restart container
docker restart hummingbot
```

### Rebuild Docker Image (Permanent)

```bash
cd /Users/alice/Dropbox/投资/量化交易/hummingbot
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

## Automated Monitoring Script

Create `/usr/local/bin/check_hummingbot_stuck.py`:

```python
#!/usr/bin/env python3
"""
Monitors hummingbot for deadlock condition and auto-restarts if stuck.
"""
import subprocess
import sys
from datetime import datetime

CONTAINER_NAME = "hummingbot"
CHECK_WINDOW_MINUTES = 5

def get_recent_logs():
    result = subprocess.run(
        ["docker", "logs", CONTAINER_NAME, "--since", f"{CHECK_WINDOW_MINUTES}m"],
        capture_output=True, text=True
    )
    return result.stdout + result.stderr

def is_stuck(logs: str) -> bool:
    """
    Stuck condition:
    - Has listen key refresh messages (bot is running)
    - No amm_arb log messages (strategy not calculating)
    """
    has_listen_key = "MexcAPIUserStreamDataSource" in logs or "GateIoAPI" in logs
    has_arb_activity = "amm_arb" in logs
    
    return has_listen_key and not has_arb_activity

def restart_container():
    print(f"[{datetime.now()}] Restarting {CONTAINER_NAME}...")
    subprocess.run(["docker", "restart", CONTAINER_NAME])

def main():
    logs = get_recent_logs()
    
    if is_stuck(logs):
        print(f"[{datetime.now()}] Deadlock detected! No arb activity in last {CHECK_WINDOW_MINUTES} minutes.")
        restart_container()
        sys.exit(1)
    else:
        print(f"[{datetime.now()}] Bot is healthy.")
        sys.exit(0)

if __name__ == "__main__":
    main()
```

### Crontab Setup

```bash
# Check every 10 minutes
*/10 * * * * /usr/bin/python3 /usr/local/bin/check_hummingbot_stuck.py >> /var/log/hummingbot_monitor.log 2>&1
```

## Related Issues

- Partial fills on low-liquidity pairs trigger cancellation
- MEXC API may cancel orders that exceed 10-second fill timeout
- Gate.io similar behavior with market orders

## Prevention Recommendations

1. **Reduce order size** for low-liquidity pairs (e.g., IRON)
2. **Enable concurrent order submission** if risk is acceptable:
   ```yaml
   concurrent_orders_submission: true
   ```
3. **Set up monitoring** with the script above
4. **Consider using limit orders** instead of market orders for better control

## Date Discovered
2025-12-03

## Status
**Workaround Available** - Restart container when stuck
**Permanent Fix** - Code change documented above (not yet applied)

