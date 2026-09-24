# Rolling backtest in the public preview

The public backtest isolates *how* a forecasting provider is evaluated. It does not
publish or reproduce any private model, model selection policy, training data or
business-specific validation rule. The API wires only `safe_mock_last_value`, which
repeats the last available observation. The generic `rolling_backtest` function
accepts a `ForecastProvider` so local experiments can inject their own implementation
without changing the released code.

For a series of length `N` and horizon `H`, the newest cutoff is `N-H`. Older cutoffs
move backward by `step` (default `H`), retaining at most `max_folds` (1–12). A cutoff
is included only if its training prefix has at least `min_train_size` observations
(default `max(8, 2H)`). At cutoff `c`, the provider gets a fresh copy of `history[:c]`;
the evaluator alone reads `history[c:c+H]` after prediction. This prevents direct
future-row leakage through the provider call. It does **not** validate external
features, feature engineering, timestamps or preprocessing performed outside the
provider. Adjacent folds may overlap if `step < H`.

For each valid fold with actual values `y` and predictions `p`:

| Metric | Formula | Output |
| --- | --- | --- |
| MAE | `mean(abs(y-p))` | Original units |
| RMSE | `sqrt(mean((y-p)^2))` | Original units |
| WAPE | `sum(abs(y-p))/sum(abs(y))` | Ratio; `null` if denominator is at most `1e-12` |
| sMAPE | `mean(2*abs(y-p)/(abs(y)+abs(p)))` | Ratio; a 0/0 point contributes 0 |

Mean metrics are arithmetic averages over the folds where that metric exists. If a
provider raises an exception, returns a wrong length, a non-finite value, or a value
outside the demo's numeric range, the fold fails with a fixed reason code; exception
text and raw values are not returned. To keep calculations finite, the request and
provider outputs are bounded in magnitude by `1e12`. The API neither persists inputs
nor associates the result with a site. The frontend's synthetic button generates a
sequence in browser memory at click time; no dataset file ships with this repository.

This evaluation makes no accuracy claim for MPWNet, PatchTST, FTMixer, or any private
provider. Choosing among models, calibrating intervals, measuring settlement impact,
and storing cross-task model performance remain outside this public example.
