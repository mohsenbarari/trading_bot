# Offer Price Control

- The estimator-range guard is condition-independent. With a fresh atomic Snapshot, reject only sell above upper or buy below lower plus 0.5% امام/بهار, 1% نیم variants, 1.5% ربع variants, or 3% یک‌گرمی; double tolerances for 15 minutes after a real open transition.
- Missing, stale, future, malformed, low-confidence, or unsupported evidence fails open; only fresh HIGH/MEDIUM evidence with effective underlying age at most 120 seconds may reject. Packs remain unsupported because they have no independent live estimate. The guard bypasses legacy mean/0.4% rejection; production preview/selection/guard are authorized, while auto-selection stays off.
