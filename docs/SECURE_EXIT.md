# Secure-Exit / profit-protection layer

The audit (docs/AUDIT.md) found **no existing implementation**, so there were no previous settings or code
to preserve or compare. The layer is built from the settings in the master specification. It is an
**additional** risk layer: the strategy's technical SL, targets and daily trade limits still apply.

| Setting | Value | Basis |
|---|---|---|
| Activation level | ₹5,000 gross | spec |
| Second level | ₹10,000 gross | spec |
| Profit target | ₹10,000 gross | spec |
| Trailing increment | ₹5,000 | spec |
| Max loss per trade | ₹15,000 gross | spec |
| Max daily loss | ₹15,000 gross | spec |
| Small-capital threshold | < ₹25,000 | spec |
| Small-capital activation / trail | ₹500 / continuous ₹500 | spec |
| Measurement | gross P&L before charges, at the option BID | spec + implementation |

## Interpretation — PLEASE CONFIRM
The spec lists the levels but does not say exactly how they interact. Implemented reading:
* **Capital ≥ ₹25,000 (stepped):** once the peak gross profit reaches ₹5,000 the floor becomes
  `floor(peak / 5,000) × 5,000 − 5,000`. So a ₹5k peak gives a ₹0 floor (gross breakeven), a ₹10k peak
  (second level) gives ₹5k, and a ₹15k peak gives ₹10k.
* **Profit target ₹10,000:** default action is **exit the whole position** when gross P&L ≥ ₹10,000. Setting
  `profit_target_action = trail` keeps the position and lets the stepped floor run.
* **Capital < ₹25,000 (continuous):** once the peak ≥ ₹500, floor = peak − ₹500.
* The floor is **monotonic**: it never decreases, and it is persisted to the database after every update and restored on restart.
* Exit when gross P&L ≤ floor (once active), or when gross loss ≥ ₹15,000 per trade.
* Daily: if realised plus unrealised gross P&L ≤ −₹15,000, all positions exit and new entries are blocked for the day.
* Exits are only "done" once the broker confirms fills. Rejected or partial exit orders are retried up to 3 times;
  after that the order state is flagged UNCERTAIN, new entries are blocked and manual action is requested.
  **Warning:** a market exit can fill worse than the floor because of slippage, gaps or thin bids.

Settings are visible on the Risk page and can be changed only in Settings with explicit confirmation (audited).
