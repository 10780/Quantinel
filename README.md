# Quantinel: normal vs quantum trading pipeline

Quantinel is a modular multi-asset backtesting pipeline that compares two trading
systems on the same market data:

- **Normal pipeline**: recent-return momentum forecast + classical Markowitz optimizer.
- **Quantum pipeline**: quantum factor forecast + quantum/QUBO-style optimizer.

The quantum pipeline supports three interchangeable compute backends:

| Backend | Key required | What runs remotely |
|---------|-------------|--------------------|
| `xpyq` (default) | `XPYQ_KEY` | SVD (forecast), `linalg.eigh` (optimizer), `linalg.eig` (ChaosEngine / CrystalBall) |
| `ibm` | `IBM_TOKEN` | p=1 QAOA circuit (optimizer only; forecast runs classical numpy SVD) |
| `local` | none | all computation runs locally as classical fallback |

Both simulations run through the same data, news, risk, execution, and scoring
layers. The final `MasterAgent` receives both result summaries, Exa market
intelligence, and a decision trace, then explains which branch won in simple
terms.

The pipeline supports multiple asset classes in a single universe. The current
supported classes are **equities** (e.g. NVDA, GOOG), **commodities**
(Gold, Silver, Platinum, Palladium, Oil), and **real estate**
(Housing/VNQ, Homebuilders/ITB, Mortgages/REM, Commercial RE/IYR, Residential/REZ).
Every downstream layer — Forecast, Risk, Optimizer, Executor, Scorer — is
ticker-agnostic and works unchanged on any mixed universe.

## Current workflow

```text
Equity data (MockDataSource / YFinanceDataSource)
+ Commodity data (MockCommodityDataSource / CommodityDataSource)
→ merged by CombinedDataSource into one MarketData universe
        |
        | run in parallel
        |
   -----------------------------
   |                           |
NORMAL PIPELINE           QUANTUM PIPELINE
   |                           |
MomentumForecaster        QuantumForecaster
recent return signal      SVD factor signal
   |                       (xpyq hardware  OR classical numpy)
   |                           |
SampleCovRisk             SampleCovRisk
same risk engine          same risk engine
   |                           |
MeanVarianceOptimizer     QaoaOptimizer
classic Markowitz         xpyq eigh  OR  IBM QAOA  OR  local
   |                           |
PaperExecutor             PaperExecutor
same execution            same execution
   |                           |
BacktestScorer            BacktestScorer
same scoring              same scoring
   \                           /
    -------- comparison summary
                 |
       Exa headlines + sentiment
                 |
        OpenRouter MasterAgent
                 |
       plain-English final decision
```

## What "88 rebalances" means

The default mock dataset has about two years of business-day prices:

```text
504 trading days
- 60 days lookback history
- 5 days forward-return scoring window
rebalance every 5 trading days
= 88 rebalance decisions
```

So `88` means the strategy made 88 weekly trading decisions. In the full quantum
branch with xpyq, that can mean up to:

```text
88 xpyq forecast jobs
+ 88 xpyq optimizer jobs
= up to 176 remote xpyq jobs
```

With the IBM backend each rebalance submits one QAOA circuit job to the IBM QPU
(plus polling overhead). The forecast still runs locally, so the job count is
halved compared to xpyq.

For quick debugging, use a larger rebalance interval to reduce the number of
remote jobs.

## Quantum backend selection

The quantum backend controls which remote compute service (if any) powers the
quantum branch. It can be changed from the **web UI**, the **CLI**, or
**programmatically** in Python.

### Web UI

Open the app in your browser (default `http://localhost:5000`). In the
**Run Pipeline** panel, find the **Quantum backend** dropdown and choose:

| Option | Label shown | Requires |
|--------|-------------|----------|
| `local` | Local (classical) | nothing |
| `xpyq` | XpyQ | `XPYQ_KEY` set server-side |
| `ibm` | IBM Quantum | `IBM_TOKEN` set server-side |

Options whose credentials are not configured on the server are automatically
greyed out by the UI. The selected value is sent with every `/api/run` request.

### CLI / environment variables

Set `QUANTUM_BACKEND` before running any script:

```bash
# XpyQ (default)
set -a; source .env; set +a; .venv/bin/python run_master.py

# IBM Quantum
set -a; source .env; set +a; \
QUANTUM_BACKEND=ibm \
.venv/bin/python run_master.py

# Local classical fallback (no credentials needed)
QUANTUM_BACKEND=local .venv/bin/python run_master.py
```

### Programmatic

```python
# XpyQ
from forecast import QuantumForecaster
from optimize import QaoaOptimizer
qf = QuantumForecaster(api_key="<XPYQ_KEY>")
qo = QaoaOptimizer(api_key="<XPYQ_KEY>")

# IBM Quantum
qf = QuantumForecaster(backend="ibm")                    # SVD runs classically
qo = QaoaOptimizer(backend="ibm", ibm_token="<TOKEN>",   # QAOA runs on QPU
                   ibm_device="ibm_kyoto", shots=1024)

# Local classical
qf = QuantumForecaster(backend="local")
qo = QaoaOptimizer(backend="local")
```

### IBM Quantum — how it works

`QaoaOptimizer` encodes the portfolio QUBO as a **p=1 QAOA circuit**:

1. Map `x'Qx` to an Ising Hamiltonian: `x_i = (1 − Z_i) / 2`.
2. Apply cost layer (RZZ + RZ gates) and a uniform RX mixer.
3. Transpile the circuit for the target QPU via Qiskit Runtime.
4. Submit to `SamplerV2`, poll until done, decode the lowest-energy bitstring
   as the portfolio selection.

All Qiskit logic lives in `quantum_backends.py`, which is shared between
`optimize.py` and `server.py`.

`QuantumForecaster` with `backend="ibm"` runs the same factor-signal mathematics
as the xpyq path but entirely in numpy — IBM QPUs cannot execute arbitrary SVD.

ChaosEngine and CrystalBall automatically use their classical numpy fallbacks
when the IBM backend is selected (they only use xpyq).

## Main commands

Create a local `.env` file with your keys:

```bash
XPYQ_KEY=...
EXA_KEY=...
OPENROUTER_KEY=...
IBM_TOKEN=...        # only needed when QUANTUM_BACKEND=ibm
```

`.env` is ignored by git.

Run the full comparison (xpyq backend by default):

```bash
set -a; source .env; set +a; .venv/bin/python run_master.py
```

Run with IBM Quantum as the backend:

```bash
set -a; source .env; set +a; \
QUANTUM_BACKEND=ibm \
.venv/bin/python run_master.py
```

Run a faster comparison while debugging xpyq:

```bash
set -a; source .env; set +a; \
QUANTINEL_REBALANCE_EVERY=20 \
XPYQ_TIMEOUT=10 \
.venv/bin/python run_master.py
```

Run the normal baseline only:

```bash
.venv/bin/python run_baseline.py
```

Run the three-way quantum comparison:

```bash
set -a; source .env; set +a; .venv/bin/python run_quantum.py
```

## Environment knobs

| Variable | Default | Purpose |
|----------|---------|----------|
| `QUANTUM_BACKEND` | `xpyq` | Quantum compute backend: `xpyq`, `ibm`, or `local`. |
| `XPYQ_KEY` | empty | xpyq bearer token. Required when `QUANTUM_BACKEND=xpyq`. |
| `IBM_TOKEN` | empty | IBM Quantum API token. Required when `QUANTUM_BACKEND=ibm`. |
| `IBM_DEVICE` | empty | Specific IBM QPU name (e.g. `ibm_kyoto`). Defaults to least-busy. |
| `IBM_SHOTS` | `1024` | QAOA circuit shot count for IBM backend. |
| `EXA_KEY` | empty | Exa API key for market intelligence. |
| `OPENROUTER_KEY` | empty | OpenRouter key for final agent reasoning. |
| `XPYQ_TIMEOUT` | `20` | Seconds to wait per xpyq job before fallback. |
| `QUANTINEL_REBALANCE_EVERY` | `5` | Trading-day step between decisions. Higher means fewer remote jobs. |
| `QUANTINEL_N_DAYS` | `504` | Number of mock business days to generate. |
| `QUANTINEL_N_PATHS` | `10000` | Monte Carlo paths per risk estimate. |

## Layer map

| Layer | File | Normal branch | Quantum branch | Output |
|-------|------|---------------|----------------|--------|
| Data | `data.py` | `MockDataSource`, `MockCommodityDataSource`, `CombinedDataSource` | `YFinanceDataSource`, `CommodityDataSource` | `MarketData` |
| News | `news.py` | `MockNewsSource` during backtest | same | `NewsFeed` |
| Forecast | `forecast.py` | `MomentumForecaster` | `QuantumForecaster` | `Forecast` |
| Chaos Engine | `chaos.py` | `ChaosEngine` (optional) | same | `ChaosSignal` |
| Crystal Ball | `forecast.py` | `CrystalBall` (optional) | same | `CrystalBallPrediction` |
| Risk | `risk.py` | `SampleCovRisk` | same | `RiskModel` |
| Optimize | `optimize.py` | `MeanVarianceOptimizer` | `QaoaOptimizer` | `TargetPortfolio` |
| Execute | `execute.py` | `PaperExecutor` | same | `ExecutionResult` |
| Score | `score.py` | `BacktestScorer`, `RiskScorer` | same | `Scorecard`, `RiskReport` |
| Intelligence | `intelligence.py` | Exa after both backtests | same | `MarketIntelligence` |
| Final agent | `master_agent.py` | compares both summaries | compares both summaries | `ComparisonReport` |

The layer boundaries are defined in `contracts.py`. Each branch must return the
same contract objects, which is what makes the comparison fair.

## Crystal Ball

`CrystalBall` (Layer 2.6, in `forecast.py`) is a scenario forecaster that fuses
two signals produced earlier in the pipeline:

- A short-horizon **`Forecast`** from any `Forecaster` (momentum or quantum SVD).
- A **`ChaosSignal`** from `ChaosEngine`, carrying crash probability and per-ticker
  weight multipliers.

It uses the same xpyq quantum path as both `ChaosEngine` and `QuantumForecaster`:
it submits the returns covariance matrix to xpyq `linalg.eig` and uses the
returned eigenvalues as factor variances for the projection.

The default forecast horizon is **1 year (252 trading days)**. A 2-year view
(504 trading days) is also available per-call:

```python
cb = CrystalBall(forecaster, chaos_engine)           # default 1-year
pred_1y = cb.predict(data, news, as_of)              # 1-year
pred_2y = cb.predict(data, news, as_of,
                     horizon_days=CrystalBall.TWO_YEAR_DAYS)  # 2-year
```

### How Crystal Ball works

1. **Short-horizon forecast** — calls the underlying `Forecaster` for a 5-day expected
   return per ticker.
2. **ChaosEngine** — evaluates current tail risk and produces a `ChaosSignal`.
3. **Eigendecomposition** — builds the returns covariance matrix and submits it to
   xpyq `linalg.eig`. The eigenvalues are sorted descending; the leading eigenvalue
   × 252 is the dominant factor variance (a measure of market-wide co-movement).
4. **Annual vol per ticker** — derived from the factor model:
   `vol_i = sqrt(sum_k  loading_ik^2 * eigenvalue_k * 252)`.
5. **Compound to horizon** — `base = (1 + daily_exp)^horizon_days − 1` per ticker.
6. **Scenarios** — three return paths per ticker:

| Scenario | Formula |
|---|---|
| `bull` | `base + 1.5 × annual_vol` |
| `base` | compounded expected return |
| `bear` | `base − 1.5 × annual_vol` |
| `crash_adjusted` | `base × ChaosSignal.ticker_adjustments[ticker]` |

7. **IFTF Futures Thinking enrichment** — three analytical layers are computed from
   the same price-return data (no new external inputs) and embedded in the reasoning:

| IFTF Principle | Method | What it produces |
|---|---|---|
| **P2 — Focus on signals** | `_detect_signals` | Per-ticker anomalous deviations: volatility surges (5d/60d vol ratio > 1.8×), momentum breaks, counter-trend bounces, drawdown warnings |
| **P3 — Look back to see forward** | `_backcast_regimes` | Locates historical windows with a similar vol regime (±25 %) and reports median forward return and fraction positive across all analogues |
| **P4 — Uncover patterns** | `_two_curves_classify` | Classifies each ticker on the IFTF Two Curves framework: `first_curve_ascending`, `first_curve_peak`, `first_curve_declining`, `second_curve_emerging`, `transition`, or `indeterminate` |

8. **Reasoning string** — a structured plain-English narrative with four labelled
   sections: signals (P2), backcasting (P3), Two Curves (P4), and scenario
   projections.

Falls back to `numpy.linalg.eigh` (classical, symmetric, numerically stable)
when `XPYQ_KEY` is not set or the API is unreachable.

`CrystalBallPrediction` is intended for reporting and external consumers; it is
not wired into the backtest loop.

## Chaos Engine

`ChaosEngine` sits between the Forecast and Risk layers (Layer 2.5). It is an
optional wildcard detector that estimates the probability of an adverse tail event
— a crash, liquidity crisis, or sector collapse — by combining two independent
signal sources:

1. **Market features** derived from `MarketData`: volatility regime, 5-day momentum,
   60-day drawdown, and a vol-spike ratio.
2. **News sentiment** aggregated from the `NewsFeed`: negative headlines boost
   the crash-probability estimate; positive headlines dampen it.

### How the quantum classification works

The engine labels historical windows as crash (1) or normal (0) based on whether
the portfolio's cumulative return over the next 5 days fell below −4 %. It then:

1. Normalises the feature matrix and computes the covariance matrix of crash-labelled
   samples.
2. Submits that covariance matrix to xpyq's `linalg.eig` endpoint — the same
   hardware used by `QaoaOptimizer`.
3. Uses the returned eigenvectors as principal crash directions.
4. Projects the current feature vector and both cluster centroids (crash vs normal)
   into eigen-space and returns `dist_normal / (dist_crash + dist_normal)` as
   P(crash). A point geometrically close to the crash centroid yields a high
   probability.
5. Adjusts that probability up or down using a news-sentiment multiplier.

Falls back to a classical centroid-distance calculation when `XPYQ_KEY` is not set
or the API is unreachable.

### How the signal is used

| `crash_probability` | `event_label` | Effect on forecast | Effect on portfolio |
|---|---|---|---|
| ≥ 0.65 | `market_crash` | direction flipped to −1, returns scaled by −p | weights multiplied by −0.80 (strong short) |
| 0.40 – 0.65 | `elevated_risk` | returns dampened by (1 − p) | weights multiplied by 0.40 (halved) |
| < 0.40 | `normal` | unchanged | unchanged |

### Fallback and traceability

`ChaosEngine` is designed to degrade gracefully:

- If xpyq is unavailable, the classical centroid-distance fallback runs locally.
- If there is insufficient history (< 25 labelled samples) or only one class in the
  training set, a sentiment-only estimate is returned with `confidence=0.0`.

## Normal branch

The normal branch is the fully local baseline.

1. `MomentumForecaster` looks at recent returns.
2. It predicts expected return over the next horizon.
3. `SampleCovRisk` estimates covariance, VaR, CVaR, and model disagreement.
4. `MeanVarianceOptimizer` uses classical Markowitz sizing.
5. `PaperExecutor` converts target weights into simulated holdings.
6. `BacktestScorer` calculates return, Sharpe, directional accuracy, IC, and
   edge versus 50/50 buy-and-hold.

This branch is fast, deterministic enough for comparisons, and is the benchmark
the quantum branch must beat.

## Quantum branch

The quantum branch keeps the same outer pipeline but swaps the forecast and
optimizer. The specific computation depends on the selected backend.

### Quantum forecast

`QuantumForecaster` builds a recent returns matrix:

```text
          NVDA     GOOG
day 1    0.012   0.004
day 2   -0.006  -0.002
...
```

**xpyq backend** — submits Python to xpyq hardware:

```python
R = from_numpy(...)
U_mat, S_mat, Vt_mat = linalg.svd(R)
U_arr, S_arr, Vt_arr = U_mat.numpy()
```

**ibm or local backend** — runs the identical mathematics in numpy:

```python
U, S, Vt = np.linalg.svd(R, full_matrices=False)
```

In both cases, SVD decomposes returns into hidden market factors. The dominant
factor is used to estimate whether each ticker should move up or down.

### Quantum optimizer

`QaoaOptimizer` receives forecasted returns and the risk covariance matrix. It
builds a QUBO-style matrix:

```python
Q = risk_aversion * Sigma - diag(mu)
```

Plain English:

```text
reward higher expected returns
penalize risky combinations
```

**xpyq backend** — submits the eigendecomposition to xpyq hardware:

```python
Q = from_numpy(...)
eigvals_mat, eigvecs_mat = linalg.eigh(Q)
```

The ground-state eigenvector's sign pattern becomes the long/short weights.

**ibm backend** — encodes the QUBO as a p=1 QAOA circuit and runs it on an
IBM QPU. The lowest-energy measured bitstring is decoded as the portfolio
selection (binary `{0,1}` mapped to signed `{-1,+1}` weights).

## Fallback and traceability

The quantum branch is designed to finish even if xpyq fails or queues too long.

- `QuantumForecaster` falls back to `MomentumForecaster`.
- `QaoaOptimizer` falls back to `DiscreteQuboOptimizer`.

The comparison report prints engine diagnostics for the quantum branch:

```text
engine trace:
  forecaster calls=88 xpyq_completed=... fallbacks=... statuses={...}
  optimizer  calls=88 xpyq_completed=... fallbacks=... statuses={...}
```

The final report also prints a `DECISION TRACE`, including:

- return gap between quantum and normal
- Sharpe gap
- directional accuracy gap
- risk breach difference
- final weight comparison
- xpyq completion/fallback counts

This makes the final agent's recommendation auditable instead of just a black-box
LLM answer.

## Final agent

`MasterAgent.compare(...)` receives:

- normal simulation summary
- quantum simulation summary
- metric deltas
- decision trace
- Exa headlines and sentiment

It returns:

- `winner`: `normal`, `quantum`, or `tie`
- `recommendation`: what to run next
- `rationale`: simple explanation for a non-technical teammate
- `decision_trace`: evidence items the decision used

The final agent is intentionally not allowed to hide the score comparison. The
numbers are computed in code first, then the agent explains them.

## Important files

| File | Purpose |
|------|----------|
| `run_master.py` | Main normal-vs-quantum comparison runner. |
| `run_baseline.py` | Normal-only baseline run. |
| `run_quantum.py` | Three-way baseline/quantum/full-quantum comparison. |
| `quantum_backends.py` | Shared IBM Quantum QAOA primitives (circuit build, submit, poll, decode). |
| `contracts.py` | Shared dataclasses and Protocol interfaces, including `AssetClass` enum. |
| `chaos.py` | Chaos Engine — tail-risk detector using xpyq eigendecomposition and news sentiment. |
| `forecast.py` | Momentum, xpyq SVD, and numpy SVD forecast logic; CrystalBall. |
| `optimize.py` | Markowitz, discrete QUBO, xpyq eigh, and IBM QAOA optimizer logic. |
| `risk.py` | Covariance + multi-agent VaR/CVaR risk simulation. |
| `score.py` | Performance and risk scoring. |
| `intelligence.py` | Exa search, sentiment, and theme extraction. |
| `master_agent.py` | Final report and comparison reasoning. |

## Asset classes

The pipeline uses `AssetClass` (defined in `contracts.py`) to tag every ticker
with its broad category. Supported values:

| `AssetClass` | Tickers | Data source | Mock source |
|---|---|---|---|
| `EQUITY` | NVDA, GOOG, … | `YFinanceDataSource` | `MockDataSource` |
| `COMMODITY` | GOLD, SILVER, PLATINUM, PALLADIUM, OIL | `CommodityDataSource` | `MockCommodityDataSource` |
| `REAL_ESTATE` | HOUSING, HOMEBUILDERS, MORTGAGES, COMMERCIAL_RE, RESIDENTIAL | `HousingDataSource` | `MockHousingDataSource` |

`MarketData` exposes two helpers for asset-class-aware logic:

```python
data.asset_class("GOLD")                          # AssetClass.COMMODITY
data.asset_class("HOUSING")                       # AssetClass.REAL_ESTATE
data.tickers_by_class(AssetClass.REAL_ESTATE)     # ["HOUSING", "HOMEBUILDERS", ...]
```

To run a combined equity + commodity + housing universe:

```python
from data import (CombinedDataSource, MockDataSource,
                  MockCommodityDataSource, MockHousingDataSource)
source = CombinedDataSource(
    MockDataSource(),
    MockCommodityDataSource(),
    MockHousingDataSource(),
)
```

`CombinedDataSource` accepts any number of source arguments and aligns them
on the intersection of business days. For real data replace mock sources with
`YFinanceDataSource`, `CommodityDataSource`, or `HousingDataSource`.

### Commodity mock parameters

| Ticker | Annual drift | Annual vol | Start price | yfinance symbol |
|--------|-------------|------------|-------------|-----------------|
| GOLD | 7% | 15% | $2 000 | GC=F |
| SILVER | 5% | 25% | $25 | SI=F |
| PLATINUM | 3% | 22% | $950 | PL=F |
| PALLADIUM | 4% | 35% | $1 000 | PA=F |
| OIL | 5% | 30% | $75 | CL=F |

Precious metals (GOLD, SILVER, PLATINUM, PALLADIUM) share a correlation of
≈ 0.55–0.60 with each other. OIL has a weaker correlation of ≈ 0.15–0.20
with the precious-metals group.

### Housing / Real Estate mock parameters

| Ticker | Annual drift | Annual vol | Start price | yfinance ETF |
|--------|-------------|------------|-------------|--------------|
| HOUSING | 8% | 18% | $90 | VNQ |
| HOMEBUILDERS | 12% | 28% | $85 | ITB |
| MORTGAGES | 6% | 22% | $25 | REM |
| COMMERCIAL_RE | 7% | 20% | $95 | IYR |
| RESIDENTIAL | 8% | 19% | $80 | REZ |

Broad REIT ETFs (HOUSING, COMMERCIAL_RE, RESIDENTIAL) are highly correlated
with each other (≈ 0.72–0.80). HOMEBUILDERS and MORTGAGES are more
idiosyncratic but still linked to the group (≈ 0.40–0.65).
Correlation with commodities and equities is lower (≈ 0.10–0.35 and 0.30–0.50
respectively).

## Current interpretation

Use the normal branch as the benchmark. Use the quantum branch to test whether
quantum-backed factor extraction and QUBO-style optimization can beat that benchmark.

With **xpyq**, both the forecast (SVD) and the optimizer (eigendecomposition) run on
xpyq hardware. If the branch has many fallbacks, the result is not a clean quantum
advantage test — check the engine trace for completion counts before comparing scores.

With **IBM Quantum**, the QAOA optimizer runs on a real QPU. The forecast uses
classical numpy SVD, which produces the same signals. IBM backend jobs can take
longer due to QPU queue times; the engine trace will show `ibm_done` instead of
`xpyq_completed` for optimizer calls.
