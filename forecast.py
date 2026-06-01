"""
LAYER 2 · FORECAST   (owner: GT (10780))

BASELINE (no ML, no quantum): MomentumForecaster.
QUANTUM SWAP: QuantumForecaster — submits Python to the xpyq compute API.
  xpyq runs SVD on its purpose-built hardware; we read back U/S/Vt and
  extract factor-momentum signals per ticker. Same interface — drop it in.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd

from contracts import AssetClass, CrystalBallPrediction, Forecast, MarketData, NewsFeed

_XPYQ_BASE = "https://xpyq-lib-production.up.railway.app"


class MomentumForecaster:
    """
    Expected return = average daily return over `lookback`, scaled to the horizon.
    Dumb on purpose — it is the bar everything else must beat.
    Implements Forecaster: predict(data, as_of, horizon_days) -> Forecast.
    """

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def predict(self, data: MarketData, as_of, horizon_days: int) -> Forecast:
        rets = data.returns().loc[:as_of].tail(self.lookback)
        exp_daily = rets.mean()

        expected = {t: float(exp_daily[t] * horizon_days) for t in data.tickers}
        direction = {t: int(1 if expected[t] >= 0 else -1) for t in data.tickers}
        confidence = {}
        for t in data.tickers:
            s = float(rets[t].std())
            confidence[t] = float(min(1.0, abs(exp_daily[t]) / s)) if s > 0 else 0.0

        return Forecast(
            as_of=pd.Timestamp(as_of),
            horizon_days=horizon_days,
            expected_returns=expected,
            direction=direction,
            confidence=confidence,
        )


class QuantumForecaster:
    """
    Factor-momentum forecaster backed by the xpyq compute API.

    How it works:
      1. Build a returns matrix R (lookback x n_tickers) from recent history.
      2. POST Python code to xpyq that calls linalg.svd(R) on hardware.
         xpyq returns U (time factors), S (singular values), Vt (ticker loadings).
      3. Compute factor scores F = U * S  — the time series of each market factor.
      4. Factor momentum = F[-1, 0] - F[-horizon_days, 0]  (dominant factor trend).
      5. Each ticker's direction = sign(factor_momentum * Vt[0, ticker_index]).
      6. Falls back to MomentumForecaster if the API is unreachable or fails.

    Args:
        api_key:    xpyq Bearer token.
        lookback:   rows of return history fed into SVD (default 40).
        poll_secs:  polling interval while waiting for xpyq result (default 0.4s).
        timeout:    max seconds to wait per run before falling back (default 20s).
    """

    def __init__(
        self,
        api_key: str | None = None,
        lookback: int = 40,
        poll_secs: float = 0.4,
        timeout: float = 20.0,
        backend: str = "xpyq",    # "xpyq" | "ibm" | "local"
    ):
        self.backend = backend
        self.api_key = os.environ.get("XPYQ_KEY", "") if api_key is None else api_key
        self.lookback = lookback
        self.poll_secs = poll_secs
        self.timeout = timeout
        self._fallback = MomentumForecaster()
        # _disabled only governs the xpyq path; IBM/local backends check self.backend
        self._disabled = (backend != "xpyq") or (not bool(self.api_key))
        self._stats = {
            "calls": 0,
            "xpyq_completed": 0,
            "fallbacks": 0,
            "status_counts": {},
        }

    # ------------------------------------------------------------------
    # xpyq helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _run_code(self, code: str, name: str = "forecast") -> dict:
        """Submit code to xpyq and block until terminal status."""
        import requests

        if self._disabled:
            return {"status": "disabled", "stdout": ""}

        h = self._headers()
        run = requests.post(
            f"{_XPYQ_BASE}/api/v1/compute/runs",
            headers=h,
            json={"code": code, "name": name},
            timeout=10,
        ).json()
        run_id = run.get("run_id") or run.get("id")
        if not run_id:
            self._disabled = True
            return {"status": "failed", "stdout": ""}

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            r = requests.get(
                f"{_XPYQ_BASE}/api/v1/compute/runs/{run_id}",
                headers=h,
                timeout=10,
            ).json()
            if r["status"] in ("completed", "failed", "timed_out", "cancelled"):
                return r
            time.sleep(self.poll_secs)

        return {"status": "timed_out", "stdout": ""}

    @staticmethod
    def _parse_json_stdout(stdout: str) -> dict:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        raise ValueError("xpyq stdout did not contain a JSON object")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def _numpy_svd_predict(
        self,
        rets,
        tickers: list,
        horizon_days: int,
        as_of,
    ) -> Forecast:
        """
        Classical numpy SVD factor-signal forecast.

        Used when backend='ibm': IBM QPU handles portfolio optimisation via
        QAOA; the SVD-based factor forecast runs classically with the same
        mathematics as the xpyq path.
        """
        R = rets[tickers].values.astype(float)
        U, S, Vt = np.linalg.svd(R, full_matrices=False)  # raises on failure
        factor_scores_col0 = (U * S)[:, 0]
        Vt_row0 = Vt[0]
        ticker_vols = np.array([float(rets[t].std()) for t in tickers])

        factor_vol = float(factor_scores_col0.std()) + 1e-8
        momentum = float(factor_scores_col0[-1] - factor_scores_col0[-horizon_days])

        expected: dict[str, float] = {}
        direction: dict[str, int] = {}
        confidence: dict[str, float] = {}
        for i, ticker in enumerate(tickers):
            loading = float(Vt_row0[i])
            signal = momentum * loading
            scale = float(ticker_vols[i] * np.sqrt(horizon_days))
            direction[ticker] = 1 if signal >= 0 else -1
            confidence[ticker] = float(min(1.0, abs(momentum) / factor_vol))
            expected[ticker] = float(signal * scale)

        self._stats["xpyq_completed"] += 1
        return Forecast(
            as_of=pd.Timestamp(as_of),
            horizon_days=horizon_days,
            expected_returns=expected,
            direction=direction,
            confidence=confidence,
        )

    def predict(self, data: MarketData, as_of, horizon_days: int) -> Forecast:
        self._stats["calls"] += 1

        rets = data.returns().loc[:as_of].tail(self.lookback)
        if len(rets) < horizon_days + 2:
            self._stats["fallbacks"] += 1
            return self._fallback.predict(data, as_of, horizon_days)

        tickers = data.tickers

        if self.backend == "ibm":
            # IBM QPU is used for optimisation (QaoaOptimizer); the factor
            # forecast runs via classical numpy SVD with identical mathematics.
            try:
                return self._numpy_svd_predict(rets, tickers, horizon_days, as_of)
            except Exception:
                self._stats["fallbacks"] += 1
                return self._fallback.predict(data, as_of, horizon_days)

        if self._disabled:
            self._stats["fallbacks"] += 1
            return self._fallback.predict(data, as_of, horizon_days)

        R_list = rets[tickers].values.astype(float).tolist()

        # Code that runs on xpyq hardware
        code = f"""
import numpy as _np, json
R = from_numpy(_np.array({R_list}, dtype=_np.float32))
U_mat, S_mat, Vt_mat = linalg.svd(R)
U_arr, S_arr, Vt_arr = U_mat.numpy()
factor_scores = U_arr * S_arr          # (lookback x n_factors)
ticker_vols = _np.array({[float(rets[t].std()) for t in tickers]})
print(json.dumps({{
    "factor_scores_col0": factor_scores[:, 0].tolist(),
    "Vt_row0": Vt_arr[0].tolist(),
    "ticker_vols": ticker_vols.tolist(),
}}))
"""

        try:
            result = self._run_code(code)
            status = result.get("status", "unknown")
            self._stats["status_counts"][status] = (
                self._stats["status_counts"].get(status, 0) + 1
            )
            if result["status"] != "completed" or not result.get("stdout", "").strip():
                if result["status"] in ("failed", "timed_out", "cancelled"):
                    self._disabled = True
                self._stats["fallbacks"] += 1
                return self._fallback.predict(data, as_of, horizon_days)

            out = self._parse_json_stdout(result["stdout"])
            self._stats["xpyq_completed"] += 1
        except Exception:
            self._disabled = True
            self._stats["fallbacks"] += 1
            return self._fallback.predict(data, as_of, horizon_days)

        factor_scores_col0 = np.array(out["factor_scores_col0"])
        Vt_row0 = np.array(out["Vt_row0"])
        ticker_vols = np.array(out["ticker_vols"])

        factor_vol = float(factor_scores_col0.std()) + 1e-8
        momentum = float(factor_scores_col0[-1] - factor_scores_col0[-horizon_days])

        expected: dict[str, float] = {}
        direction: dict[str, int] = {}
        confidence: dict[str, float] = {}

        for i, ticker in enumerate(tickers):
            loading = float(Vt_row0[i])
            signal = momentum * loading
            scale = float(ticker_vols[i] * np.sqrt(horizon_days))

            direction[ticker] = 1 if signal >= 0 else -1
            confidence[ticker] = float(min(1.0, abs(momentum) / factor_vol))
            expected[ticker] = float(signal * scale)

        return Forecast(
            as_of=pd.Timestamp(as_of),
            horizon_days=horizon_days,
            expected_returns=expected,
            direction=direction,
            confidence=confidence,
        )

    def diagnostics(self) -> dict:
        return {
            "calls": self._stats["calls"],
            "xpyq_completed": self._stats["xpyq_completed"],
            "fallbacks": self._stats["fallbacks"],
            "status_counts": dict(self._stats["status_counts"]),
            "disabled": self._disabled,
        }


class CrystalBall:
    """
    1-year scenario forecaster that fuses the Chaos Engine's tail-risk signal
    with a short-horizon Forecast and xpyq eigendecomposition of the returns
    covariance matrix — the same quantum path used by ChaosEngine.

    Produces a ``CrystalBallPrediction`` with three scenarios per ticker:
      - bull  : base + 1.5 × annual_vol  (optimistic)
      - base  : short-horizon expected return compounded over ``horizon_days``
      - bear  : base − 1.5 × annual_vol  (pessimistic)
    plus a crash-adjusted return that applies the ChaosEngine's per-ticker
    weight multipliers to the base estimate.

    The per-ticker annual volatility is derived from the leading eigenvalues of
    the returns covariance matrix submitted to xpyq ``linalg.eig``.  The
    dominant factor variance (leading eigenvalue × 252) measures how strongly
    a single market-wide risk factor drives co-movement.

    Args:
        forecaster:    Any Forecaster (``MomentumForecaster`` or ``QuantumForecaster``).
        chaos_engine:  An initialised ``ChaosEngine`` instance.
        lookback:      Trading days of return history for the covariance matrix (default 60).
        short_horizon: Days for the underlying Forecast before compounding (default 5).
        horizon_days:  Prediction target in trading days (default 252 ≈ 1 year).
        api_key:       xpyq Bearer token (falls back to XPYQ_KEY env var).
        poll_secs:     Polling interval while waiting for xpyq (default 0.4 s).
        timeout:       Max seconds per xpyq job before classical fallback (default 20 s).
    """

    ONE_YEAR_DAYS: int = 252
    TWO_YEAR_DAYS: int = 504

    def __init__(
        self,
        forecaster,
        chaos_engine,
        lookback: int = 60,
        short_horizon: int = 5,
        horizon_days: int = 252,
        api_key: str | None = None,
        poll_secs: float = 0.4,
        timeout: float = 20.0,
    ) -> None:
        self.forecaster = forecaster
        self.chaos_engine = chaos_engine
        self.lookback = lookback
        self.short_horizon = short_horizon
        self.horizon_days = horizon_days
        self.api_key = os.environ.get("XPYQ_KEY", "") if api_key is None else api_key
        self.poll_secs = poll_secs
        self.timeout = timeout
        self._disabled = not bool(self.api_key)

    # ------------------------------------------------------------------
    # xpyq helpers (same pattern as QuantumForecaster and ChaosEngine)
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _run_code(self, code: str, name: str = "crystal_ball") -> dict:
        """Submit code to xpyq and block until a terminal status is reached."""
        import requests

        if self._disabled:
            return {"status": "disabled", "stdout": ""}

        h = self._headers()
        run = requests.post(
            f"{_XPYQ_BASE}/api/v1/compute/runs",
            headers=h,
            json={"code": code, "name": name},
            timeout=10,
        ).json()
        run_id = run.get("run_id") or run.get("id")
        if not run_id:
            self._disabled = True
            return {"status": "failed", "stdout": ""}

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            r = requests.get(
                f"{_XPYQ_BASE}/api/v1/compute/runs/{run_id}",
                headers=h,
                timeout=10,
            ).json()
            if r["status"] in ("completed", "failed", "timed_out", "cancelled"):
                return r
            time.sleep(self.poll_secs)
        return {"status": "timed_out", "stdout": ""}

    @staticmethod
    def _parse_json_stdout(stdout: str) -> dict:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        raise ValueError("xpyq stdout did not contain a JSON object")

    # ------------------------------------------------------------------
    # Factor volatility extraction via xpyq eigendecomposition
    # ------------------------------------------------------------------

    def _factor_vols(
        self,
        cov_list: list[list[float]],
        n_tickers: int,
    ) -> tuple[list[float], float]:
        """
        Submit the returns covariance matrix to xpyq ``linalg.eig``.

        Returns
        -------
        annual_vol_per_ticker : list[float]
            Per-ticker annualised vol derived from the factor model:
            ``vol_i = sqrt(sum_k  loading_ik^2 * eigenvalue_k * 252)``.
        dominant_factor_var : float
            Leading eigenvalue × 252 — the annualised variance of the strongest
            market-wide factor.

        Falls back to ``_classical_factor_vols`` if xpyq is unavailable.
        """
        code = f"""
import numpy as _np, json

cov = from_numpy(_np.array({cov_list}, dtype=_np.float32))
eigvals_mat, eigvecs_mat = linalg.eig(cov)
eigvals_arr, eigvecs_arr = eigvals_mat.numpy()

# Sort descending: factor 0 is the dominant market factor
idx = _np.argsort(eigvals_arr)[::-1]
eigvals_sorted = _np.maximum(eigvals_arr[idx], 0.0)
eigvecs_sorted = eigvecs_arr[:, idx]

# Per-ticker annual variance via factor model
factor_var_annual = eigvals_sorted * 252.0
annual_var = (eigvecs_sorted ** 2) @ factor_var_annual
annual_vol = _np.sqrt(annual_var).tolist()
dominant = float(eigvals_sorted[0] * 252.0)

print(json.dumps({{
    "annual_vol": annual_vol,
    "dominant_factor_var": dominant,
}}))
"""
        try:
            result = self._run_code(code, name="crystal_eig")
            if result["status"] == "completed" and result.get("stdout", "").strip():
                out = self._parse_json_stdout(result["stdout"])
                return out["annual_vol"], float(out["dominant_factor_var"])
            if result["status"] in ("failed", "timed_out", "cancelled"):
                self._disabled = True
        except Exception:
            self._disabled = True

        return self._classical_factor_vols(cov_list)

    @staticmethod
    def _classical_factor_vols(cov_list: list[list[float]]) -> tuple[list[float], float]:
        """Classical fallback using numpy.linalg.eigh (symmetric, numerically stable)."""
        cov = np.array(cov_list, dtype=float)
        eigvals, eigvecs = np.linalg.eigh(cov)
        idx = np.argsort(eigvals)[::-1]
        eigvals = np.maximum(eigvals[idx], 0.0)
        eigvecs = eigvecs[:, idx]
        factor_var_annual = eigvals * 252.0
        annual_var = (eigvecs ** 2) @ factor_var_annual
        annual_vol = np.sqrt(annual_var).tolist()
        dominant = float(eigvals[0] * 252.0)
        return annual_vol, dominant

    # ------------------------------------------------------------------
    # Futures Thinking Principle 2: Focus on signals
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_signals(
        rets: pd.DataFrame,
        tickers: list[str],
        asset_classes: dict[str, AssetClass] | None = None,
    ) -> dict[str, list[str]]:
        """
        IFTF Principle 2 — Focus on signals.

        Scan each ticker for anomalous deviations from its own baseline.
        Thresholds are calibrated per asset class:

        - EQUITY      : vol surge 1.8×, drawdown −8 %, momentum bands ±1 %/±2 %
        - COMMODITY   : vol surge 2.2×, drawdown −12 %, momentum bands ±2 %/±4 %
          (commodities carry naturally higher baseline vol, so standard equity
           thresholds would produce constant false positives)
        - REAL_ESTATE : vol surge 1.7×, drawdown −7 %, momentum bands ±1 %/±2 %
          (REIT ETFs can be more rate-sensitive; lower drawdown threshold)
        """
        if asset_classes is None:
            asset_classes = {}

        # (vol_surge_ratio, drawdown_floor, mom_short_neg, mom_long_pos,
        #  mom_short_pos, mom_long_neg)
        _THR: dict[str, tuple] = {
            "equity":      (1.8, -0.08, -0.01,  0.02,  0.01, -0.02),
            "commodity":   (2.2, -0.12, -0.02,  0.04,  0.02, -0.04),
            "real_estate": (1.7, -0.07, -0.01,  0.02,  0.01, -0.02),
        }

        signals: dict[str, list[str]] = {t: [] for t in tickers}
        for t in tickers:
            r = rets[t].dropna()
            if len(r) < 20:
                continue

            cls = asset_classes.get(t, AssetClass.EQUITY).value
            vol_thr, dd_thr, ms_neg, ml_pos, ms_pos, ml_neg = _THR.get(
                cls, _THR["equity"]
            )

            vol_5  = float(r.tail(5).std())
            vol_60 = float(r.tail(min(60, len(r))).std())
            if vol_60 > 0 and vol_5 / vol_60 > vol_thr:
                signals[t].append(
                    f"volatility surge (5d/60d vol ratio {vol_5 / vol_60:.1f}×,"
                    f" threshold {vol_thr:.1f}× for {cls})"
                )

            mom_10 = float(r.tail(10).sum())
            mom_60 = float(r.tail(min(60, len(r))).sum())
            if mom_10 < ms_neg and mom_60 > ml_pos:
                signals[t].append(
                    "momentum break (established uptrend losing short-term traction)"
                )
            elif mom_10 > ms_pos and mom_60 < ml_neg:
                signals[t].append(
                    "counter-trend bounce (short-term recovery within a longer downtrend)"
                )

            prices  = (1 + r).cumprod()
            peak_60 = float(prices.tail(min(60, len(prices))).max())
            current = float(prices.iloc[-1])
            dd_pct  = current / peak_60 - 1.0
            if peak_60 > 0 and dd_pct < dd_thr:
                signals[t].append(
                    f"drawdown warning ({dd_pct:+.1%} from 60d peak,"
                    f" threshold {dd_thr:.0%} for {cls})"
                )

        return signals

    # ------------------------------------------------------------------
    # Futures Thinking Principle 3: Look back to see forward (backcasting)
    # ------------------------------------------------------------------

    @staticmethod
    def _backcast_regimes(
        rets: pd.DataFrame,
        tickers: list[str],
        asset_classes: dict[str, AssetClass] | None = None,
    ) -> dict:
        """
        IFTF Principle 3 — Look back to see forward.

        Locates historical windows whose volatility regime resembles the
        current one and reports what typically followed.

        When ``asset_classes`` is provided the backcast is computed
        separately for each asset class present in the universe (equities,
        commodities, real estate).  This avoids conflating very different
        vol regimes in a mixed portfolio.  Without class information a
        single equal-weight portfolio backcast is returned under the key
        ``"portfolio"``.

        Returns ``dict[class_label, backcast_dict]`` where each inner dict
        contains: ``analog_count``, ``median_fwd_return``, ``pct_positive``,
        ``regime_label``.
        """

        def _backcast_series(port: pd.Series) -> dict:
            if len(port) < 40:
                return {
                    "analog_count": 0,
                    "median_fwd_return": None,
                    "pct_positive": None,
                    "regime_label": "insufficient history for backcasting",
                }
            window = 10
            current_vol = float(port.tail(window).std())
            analog_fwd: list[float] = []
            for i in range(len(port) - window * 2):
                hist_vol = float(port.iloc[i : i + window].std())
                if current_vol > 0 and abs(hist_vol - current_vol) / current_vol < 0.25:
                    fwd = port.iloc[i + window : i + window * 2]
                    analog_fwd.append(float((1 + fwd).prod() - 1))
            if not analog_fwd:
                return {
                    "analog_count": 0,
                    "median_fwd_return": None,
                    "pct_positive": None,
                    "regime_label": "no analogous historical regimes found",
                }
            arr = np.array(analog_fwd)
            overall_vol = float(port.std())
            return {
                "analog_count": len(arr),
                "median_fwd_return": float(np.median(arr)),
                "pct_positive": float((arr > 0).mean()),
                "regime_label": (
                    "elevated stress"
                    if current_vol > overall_vol * 1.5
                    else "typical volatility"
                ),
            }

        if not asset_classes:
            port = rets[tickers].mean(axis=1).dropna()
            return {"portfolio": _backcast_series(port)}

        # Group tickers by asset class and backcast each group independently
        groups: dict[str, list[str]] = {}
        for t in tickers:
            cls = asset_classes.get(t, AssetClass.EQUITY).value
            groups.setdefault(cls, []).append(t)

        result: dict[str, dict] = {}
        for cls_label, cls_tickers in groups.items():
            available = [t for t in cls_tickers if t in rets.columns]
            if not available:
                continue
            port = rets[available].mean(axis=1).dropna()
            result[cls_label] = _backcast_series(port)

        return result

    # ------------------------------------------------------------------
    # Futures Thinking Principle 4: Uncover patterns (Two Curves)
    # ------------------------------------------------------------------

    @staticmethod
    def _two_curves_classify(
        rets: pd.DataFrame,
        tickers: list[str],
        asset_classes: dict[str, AssetClass] | None = None,
    ) -> dict[str, str]:
        """
        IFTF Principle 4 — Uncover patterns: Two Curves framework.

        Classification per ticker using per-asset-class momentum thresholds:

        - EQUITY      : long ±3 % / ±1 %, short ±1 %
        - COMMODITY   : long ±5 % / ±2 %, short ±2 %
          (wider bands to avoid classifying ordinary commodity noise as a
           curve transition)
        - REAL_ESTATE : long ±3 % / ±1 %, short ±1 %  (similar to equity)
        """
        if asset_classes is None:
            asset_classes = {}

        # (long_strong_pos, long_weak_pos, short_weak_pos)  — negatives are mirrors
        _THR: dict[str, tuple] = {
            "equity":      (0.03, 0.01, 0.01),
            "commodity":   (0.05, 0.02, 0.02),
            "real_estate": (0.03, 0.01, 0.01),
        }

        result: dict[str, str] = {}
        for t in tickers:
            r = rets[t].dropna()
            if len(r) < 20:
                result[t] = "indeterminate"
                continue

            cls = asset_classes.get(t, AssetClass.EQUITY).value
            l_strong, l_weak, s_weak = _THR.get(cls, _THR["equity"])

            mom_long  = float(r.tail(min(60, len(r))).sum())
            mom_short = float(r.tail(10).sum())

            if mom_long > l_strong and mom_short < -s_weak:
                result[t] = "first_curve_peak"
            elif mom_long < -l_strong and mom_short > s_weak:
                result[t] = "second_curve_emerging"
            elif mom_long > l_weak and mom_short > s_weak:
                result[t] = "first_curve_ascending"
            elif mom_long < -l_weak and mom_short < -s_weak:
                result[t] = "first_curve_declining"
            else:
                result[t] = "transition"

        return result

    # ------------------------------------------------------------------
    # Reasoning string
    # ------------------------------------------------------------------

    _CURVE_LABELS: dict[str, str] = {
        "first_curve_ascending": "First Curve ↑  (established uptrend)",
        "first_curve_peak":      "First Curve ⚠  (peak / exhaustion — watch for inflection)",
        "first_curve_declining": "First Curve ↓  (established downtrend)",
        "second_curve_emerging": "Second Curve ↗ (nascent trend emerging from prior decline)",
        "transition":            "Transition     (between curves — signals mixed)",
        "indeterminate":         "Indeterminate  (insufficient history)",
    }

    def _build_reasoning(
        self,
        as_of,
        tickers: list[str],
        base_returns: dict[str, float],
        bull_returns: dict[str, float],
        bear_returns: dict[str, float],
        crash_adjusted_returns: dict[str, float],
        annual_vol: dict[str, float],
        chaos_signal,
        dominant_factor_var: float,
        signals: dict[str, list[str]],
        backcast: dict,
        two_curves: dict[str, str],
        horizon: int | None = None,
        asset_classes: dict[str, AssetClass] | None = None,
    ) -> str:
        horizon = horizon if horizon is not None else self.horizon_days
        horizon_label = (
            "2-YEAR" if horizon >= self.TWO_YEAR_DAYS
            else "1-YEAR"
        )
        level = (
            "HIGH RISK"    if chaos_signal.crash_probability >= 0.65
            else "CAUTION" if chaos_signal.crash_probability >= 0.40
            else "NORMAL"
        )

        # Build asset-class groupings for display
        _CLASS_HEADER = {
            "equity":      "EQUITIES",
            "commodity":   "COMMODITIES",
            "real_estate": "REAL ESTATE",
            "portfolio":   "PORTFOLIO",
        }
        if asset_classes:
            groups: dict[str, list[str]] = {}
            for t in tickers:
                cls = asset_classes.get(t, AssetClass.EQUITY).value
                groups.setdefault(cls, []).append(t)
        else:
            groups = {"equity": list(tickers)}

        multi_class = len(groups) > 1

        lines = [
            f"CrystalBall [{pd.Timestamp(as_of).date()}] — {horizon_label} OUTLOOK"
            f" ({horizon} trading days)",
            "",
            "── PRINCIPLE 2: FORWARD-LOOKING SIGNALS ─────────────────────────────",
            "  (Signals are anomalous deviations that may indicate future"
            " discontinuities,",
            "   not predictions. Future data does not exist; only signals do.)",
        ]
        any_signal = False
        for cls_label, cls_tickers in groups.items():
            if multi_class:
                lines.append(f"  [{_CLASS_HEADER.get(cls_label, cls_label)}]")
            for t in cls_tickers:
                sigs = signals.get(t, [])
                if sigs:
                    any_signal = True
                    lines.append(f"    {t:8s}: " + " | ".join(sigs))
                elif multi_class:
                    lines.append(f"    {t:8s}: no anomalous signals")
        if not any_signal:
            lines.append(
                "  No anomalous signals detected — market in baseline continuity."
            )

        lines += [
            "",
            "── PRINCIPLE 3: LOOK BACK TO SEE FORWARD (BACKCASTING) ──────────────",
            "  (Historical analogues reveal recurrent patterns, not repetitions.)",
        ]
        for cls_label, bc in backcast.items():
            hdr = _CLASS_HEADER.get(cls_label, cls_label)
            if len(backcast) > 1:
                lines.append(f"  [{hdr}]")
            if bc.get("analog_count", 0) > 0:
                lines += [
                    f"    Analogous historical regimes : {bc['analog_count']}",
                    f"    Median forward return        : {bc['median_fwd_return']:+.2%}"
                    f"  (next 10 days across all analogues)",
                    f"    Historically positive        : {bc['pct_positive']:.0%} of analogues",
                    f"    Current regime pattern       : {bc['regime_label']}",
                ]
            else:
                lines.append(
                    f"    {bc.get('regime_label', 'Backcasting unavailable.')}"
                )

        lines += [
            "",
            "── PRINCIPLE 4: TWO CURVES PATTERN FRAMEWORK ────────────────────────",
            "  (First curve = established trend; Second curve = nascent emergence.",
            "   The inflection between them is where futures thinking adds most value.)",
            f"  Dominant market factor variance (annualised): {dominant_factor_var:.4f}",
        ]
        for cls_label, cls_tickers in groups.items():
            if multi_class:
                lines.append(f"  [{_CLASS_HEADER.get(cls_label, cls_label)}]")
            for t in cls_tickers:
                curve = two_curves.get(t, "indeterminate")
                lines.append(
                    f"    {t:8s}: {self._CURVE_LABELS.get(curve, curve)}"
                )

        lines += [
            "",
            "── SCENARIO PROJECTIONS ──────────────────────────────────────────────",
            f"  Risk regime      : {level}",
            f"  Crash probability: {chaos_signal.crash_probability:.3f}",
        ]
        for cls_label, cls_tickers in groups.items():
            if multi_class:
                lines.append(f"  [{_CLASS_HEADER.get(cls_label, cls_label)}]")
            for t in cls_tickers:
                lines.append(
                    f"    {t:8s}  base: {base_returns[t]:+.1%}  "
                    f"bull: {bull_returns[t]:+.1%}  "
                    f"bear: {bear_returns[t]:+.1%}  "
                    f"vol: {annual_vol[t]:.1%}  "
                    f"crash-adj: {crash_adjusted_returns[t]:+.1%}"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def predict(
        self,
        data: MarketData,
        news: NewsFeed,
        as_of,
        horizon_days: int | None = None,
    ) -> CrystalBallPrediction:
        """
        Produce a ``CrystalBallPrediction`` for the given horizon.

        Parameters
        ----------
        data          : point-in-time ``MarketData`` (no look-ahead).
        news          : ``NewsFeed`` passed through to the ``ChaosEngine``.
        as_of         : the 'current' timestamp.
        horizon_days  : trading-day horizon for this call.  Defaults to the
                        instance ``horizon_days`` (252 ≈ 1 year).  Pass
                        ``CrystalBall.TWO_YEAR_DAYS`` (504) for a 2-year view.
        """
        horizon = horizon_days if horizon_days is not None else self.horizon_days
        tickers = data.tickers
        rets = data.returns().loc[:as_of].tail(self.lookback)

        # 1. Short-horizon forecast
        short_forecast = self.forecaster.predict(data, as_of, self.short_horizon)

        # 2. Tail-risk signal from ChaosEngine
        chaos_signal = self.chaos_engine.evaluate(data, news, as_of)

        # 3. Covariance matrix → xpyq for factor volatilities
        cov_list = rets[tickers].cov().values.tolist()
        annual_vol_list, dominant_factor_var = self._factor_vols(cov_list, len(tickers))
        annual_vol = {t: float(annual_vol_list[i]) for i, t in enumerate(tickers)}

        # 4. Compound short-horizon expected return to 1-year
        base_returns: dict[str, float] = {}
        bull_returns: dict[str, float] = {}
        bear_returns: dict[str, float] = {}
        crash_adjusted_returns: dict[str, float] = {}

        for t in tickers:
            daily_exp = short_forecast.expected_returns.get(t, 0.0) / self.short_horizon
            base = float((1.0 + daily_exp) ** horizon - 1.0)
            vol  = annual_vol[t]
            base_returns[t]           = base
            bull_returns[t]           = base + 1.5 * vol
            bear_returns[t]           = base - 1.5 * vol
            crash_adjusted_returns[t] = base * chaos_signal.ticker_adjustments.get(t, 1.0)

        # 5. Confidence inherited from short-horizon forecast
        confidence = {t: short_forecast.confidence.get(t, 0.0) for t in tickers}

        # 6. Futures Thinking enrichment (Principles 2, 3, 4)
        asset_cls = data.asset_classes if data.asset_classes else None
        signals    = self._detect_signals(rets, tickers, asset_cls)
        backcast   = self._backcast_regimes(rets, tickers, asset_cls)
        two_curves = self._two_curves_classify(rets, tickers, asset_cls)

        reasoning = self._build_reasoning(
            as_of, tickers, base_returns, bull_returns, bear_returns,
            crash_adjusted_returns, annual_vol, chaos_signal, dominant_factor_var,
            signals, backcast, two_curves,
            horizon=horizon,
            asset_classes=asset_cls,
        )

        return CrystalBallPrediction(
            as_of=pd.Timestamp(as_of),
            horizon_days=horizon,
            base_returns=base_returns,
            bull_returns=bull_returns,
            bear_returns=bear_returns,
            crash_adjusted_returns=crash_adjusted_returns,
            annual_volatility=annual_vol,
            crash_probability=chaos_signal.crash_probability,
            dominant_factor_var=dominant_factor_var,
            confidence=confidence,
            reasoning=reasoning,
        )
