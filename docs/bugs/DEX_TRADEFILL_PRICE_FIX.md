# Hummingbot DEX TradeFill Price=0 修复指南

## 问题

DEX (uniswap/clmm) 的 AMM_SWAP 交易在 TradeFill 中 `price=0`，导致 `history` 命令收益计算错误（虚高）。

## 数据流

```
amm_arb 策略 place_arb_order(arb_side.order_price)
  → gateway_swap.place_order(price)
  → gateway_swap._create_order(price)
  → quantize_order_price(price)  # 可能返回 0
  → start_tracking_order(price=price)
  → [交易确认] process_transaction_confirmation_update(tracked_order)
  → TradeUpdate(fill_price=tracked_order.price)  # 若 price=0 则错误
  → OrderFilledEvent(price=fill_price)
  → markets_recorder._did_fill_order(evt)
  → TradeFill(price=evt.price)  # 写入 DB
```

## 修改位置

### 方案 A：在 process_transaction_confirmation_update 中补全价格（推荐）

**文件**: `hummingbot/hummingbot/connector/gateway/gateway_base.py`  
**方法**: `process_transaction_confirmation_update` (约 L605-615)

当 `tracked_order.price == 0` 时，用 `get_quote_price` 获取当前报价作为 fill_price 的近似值。

需将 `process_transaction_confirmation_update` 改为 async，并在调用处 await。

### 方案 B：在 _create_order 中确保 price 非 0

**文件**: `hummingbot/hummingbot/connector/gateway/gateway_swap.py`  
**方法**: `_create_order` (约 L128-136)

在 `quantize_order_price` 之后、`start_tracking_order` 之前：

```python
# L128-136 附近
amount = self.quantize_order_amount(trading_pair, amount)
price = self.quantize_order_price(trading_pair, price)
# 若 price 为 0，用 get_quote_price 获取
if price is None or price <= 0:
    price = await self.get_quote_price(trading_pair, trade_type == TradeType.BUY, amount)
    price = self.quantize_order_price(trading_pair, price) if price else price
base, quote = trading_pair.split("-")
self.start_tracking_order(...)
```

### 方案 C：在 markets_recorder 写入时修正

**文件**: `hummingbot/hummingbot/connector/markets_recorder.py`  
**方法**: `_did_fill_order` (约 L442-454)

当 `evt.price == 0` 且 `evt.order_type == "AMM_SWAP"` 时，用 `market.get_quote_price()` 获取价格再写入 TradeFill。需处理 async 调用。

## 已实现：方案 B

**文件**: `hummingbot/hummingbot/connector/gateway/gateway_swap.py`  
**方法**: `_create_order` (L128-145)

当 `quantize_order_price` 返回 0 时，调用 `get_quote_price` 获取当前报价并写入 `start_tracking_order`，确保 TradeFill 中 DEX 价格非 0。

## 验证

修改后重新运行 AMM arb，检查：

```bash
sqlite3 hummingbot/data/conf_amm_arb_BNKR.sqlite "SELECT market, trade_type, price, amount FROM TradeFill WHERE market='uniswap/clmm' LIMIT 5;"
```

DEX 的 `price` 应不再为 0。
