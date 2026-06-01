"""
server.py — Flask API server for the Quantinel multi-agent portfolio frontend.

Endpoints:
  GET  /                        → serves frontend/index.html
  GET  /api/health              → returns key-presence flags + optional xpyq credits
  POST /api/run                 → full multi-agent walk-forward pipeline
  POST /api/qubo/local          → solve a single QUBO locally (brute force / greedy)
  POST /api/quantum/simulate    → stub (Aer simulator not wired in requirements)
  POST /api/quantum/ibm/devices → list IBM QPU devices (requires IBM_TOKEN)
  POST /api/quantum/ibm/submit  → submit QAOA job to IBM QPU (requires IBM_TOKEN)
  POST /api/quantum/ibm/status  → poll IBM QPU job
  POST /api/xpyq/submit         → submit QUBO to xpyq (requires XPYQ_KEY)
  POST /api/xpyq/status         → poll xpyq run
  POST /api/xpyq/cancel         → cancel xpyq run

Run:
  pip install flask
  python server.py
"""
from __future__ import annotations

import itertools
import json
import os
import time
import traceback
from math import comb
from typing import Any

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

import quantum_backends as _qb
from data import MockDataSource, YFinanceDataSource

app = Flask(__name__, static_folder="frontend")
app.config["JSON_SORT_KEYS"] = False

# ── Environment variables ─────────────────────────────────────────────────────
XPYQ_KEY       = os.environ.get("XPYQ_KEY", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
IBM_TOKEN      = os.environ.get("IBM_TOKEN", "")
HOST           = os.environ.get("HOST", "127.0.0.1")
PORT           = int(os.environ.get("PORT", "5000"))

_XPYQ_BASE = "https://xpyq-lib-production.up.railway.app"

# ── Default synthetic parameters per known ticker ─────────────────────────────
# (annual_drift, annual_vol, start_price)
_KNOWN_PARAMS: dict[str, tuple[float, float, float]] = {
    "NVDA": (0.35, 0.45, 480.0),
    "GOOG": (0.15, 0.28, 140.0),
    "AAPL": (0.20, 0.30, 175.0),
    "MSFT": (0.22, 0.28, 380.0),
    "AMZN": (0.18, 0.32, 185.0),
    "META": (0.28, 0.38, 480.0),
    "TSLA": (0.12, 0.65, 245.0),
    "AMD":  (0.30, 0.50, 160.0),
    "NFLX": (0.20, 0.38, 490.0),
    "INTC": (-0.05, 0.35, 35.0),
    "AVGO": (0.25, 0.30, 185.0),
    "ADBE": (0.15, 0.33, 490.0),
    "CRM":  (0.18, 0.35, 290.0),
    "ORCL": (0.20, 0.28, 135.0),
    "QCOM": (0.15, 0.35, 180.0),
    "CSCO": (0.10, 0.25, 55.0),
    "TSM":  (0.18, 0.32, 165.0),   # Taiwan Semiconductor
    "HDRN": (0.05, 0.80,   2.5),   # Hedron (high-vol small cap)
    "JOBY": (0.08, 0.75,   6.0),   # Joby Aviation (eVTOL)
    # Commodities (synthetic price proxies)
    "GOLD":      (0.07, 0.15, 2000.0),
    "SILVER":    (0.05, 0.25,   25.0),
    "PLATINUM":  (0.03, 0.22,  950.0),
    "PALLADIUM": (0.04, 0.35, 1000.0),
    "OIL":       (0.05, 0.30,   75.0),
    "URANIUM":   (0.12, 0.40,   28.0),
    "LITHIUM":   (0.08, 0.45,   55.0),
    "NEODYMIUM": (0.10, 0.50,   18.0),
    # Agricultural futures
    "CORN":      (0.03, 0.25,  450.0),
    "WHEAT":     (0.02, 0.28,  550.0),
    "RICE":      (0.02, 0.20,   15.0),
    "SOYBEANS":  (0.04, 0.23, 1200.0),
    "SUGAR":     (0.03, 0.32,   20.0),
}


def _ticker_params(tickers: list[str], seed: int) -> dict[str, tuple[float, float, float]]:
    """Return per-ticker synthetic generation parameters, using known values or random fallbacks."""
    rng = np.random.default_rng(seed + 999)
    result = {}
    for t in tickers:
        if t in _KNOWN_PARAMS:
            result[t] = _KNOWN_PARAMS[t]
        else:
            drift = float(rng.uniform(-0.05, 0.35))
            vol   = float(rng.uniform(0.20, 0.60))
            price = float(rng.uniform(20.0, 500.0))
            result[t] = (drift, vol, price)
    return result


# =============================================================================
# Static file serving
# =============================================================================

@app.route("/")
def index():
    return send_from_directory("frontend", "index.html")


@app.route("/<path:path>")
def static_files(path: str):
    return send_from_directory("frontend", path)


# =============================================================================
# /api/health
# =============================================================================

@app.route("/api/health")
def health():
    xpyq_credits = None
    if XPYQ_KEY:
        try:
            import requests as _r
            resp = _r.get(
                f"{_XPYQ_BASE}/api/v1/credits",
                headers={"Authorization": f"Bearer {XPYQ_KEY}"},
                timeout=4,
            )
            if resp.ok:
                xpyq_credits = resp.json().get("credits")
        except Exception:
            pass
    return jsonify({
        "xpyq_key_present":       bool(XPYQ_KEY),
        "xpyq_credits":           xpyq_credits,
        "ibm_token_present":      bool(IBM_TOKEN),
        "anthropic_key_present":  bool(ANTHROPIC_KEY),
        "openrouter_key_present": bool(OPENROUTER_KEY),
    })


# =============================================================================
# QUBO helpers
# =============================================================================

def _build_qubo(
    mu: np.ndarray,
    sigma: np.ndarray,
    K: int,
    lam: float,
    penalty: float,
) -> np.ndarray:
    """
    Build cardinality-K QUBO matrix Q such that minimising x'Qx (x ∈ {0,1}^N)
    is equivalent to:

        maximise  μ·x − λ·x'Σx
        subject to  Σ_i x_i = K

    After expanding P·(Σ x_i − K)² the matrix is:
        Q_ii = −μ_i + λ·Σ_ii + P·(1 − 2K)
        Q_ij = λ·Σ_ij + P          (i ≠ j, symmetric)
    """
    n = len(mu)
    Q = lam * sigma.copy()
    Q += penalty * np.ones((n, n))                        # off-diagonal penalty interaction
    np.fill_diagonal(Q, np.diag(Q) - mu + penalty * (1 - 2 * K))
    return Q


def _qubo_energy(Q: np.ndarray, x: np.ndarray) -> float:
    return float(x @ Q @ x)


def _solve_qubo_brute(Q: np.ndarray, K: int, tickers: list[str]) -> dict:
    """Exact brute-force QUBO solver — O(C(N, K))."""
    n = len(tickers)
    best_e  = np.inf
    best_x  = None
    run2_e  = np.inf
    run2_x  = None

    for combo in itertools.combinations(range(n), K):
        x = np.zeros(n)
        x[list(combo)] = 1.0
        e = _qubo_energy(Q, x)
        if e < best_e:
            run2_e, run2_x = best_e, best_x
            best_e,  best_x = e, x.copy()
        elif e < run2_e:
            run2_e, run2_x = e, x.copy()

    selected  = [tickers[i] for i in range(n) if best_x is not None and best_x[i] > 0.5]
    runner_up = (
        [tickers[i] for i in range(n) if run2_x is not None and run2_x[i] > 0.5]
        if run2_x is not None else []
    )
    return {
        "selected":    selected,
        "energy":      best_e,
        "runner_up":   runner_up,
        "energy_gap":  round(run2_e - best_e, 6) if run2_x is not None else None,
        "backend":     "local brute force",
        "n_outcomes":  comb(n, K),
        "shots":       None,
        "penalty":     float(penalty_from_q(Q, K, n)),
        "top_counts":  None,
    }


def penalty_from_q(Q: np.ndarray, K: int, n: int) -> float:
    """Recover the approximate penalty value from a QUBO diagonal."""
    return float(np.diag(Q).max() - np.diag(Q).min()) / max(1, abs(1 - 2 * K))


def _solve_qubo_greedy(Q: np.ndarray, K: int, tickers: list[str]) -> dict:
    """Greedy cardinality-K QUBO solver — O(N·K), used when C(N,K) is large."""
    n = len(tickers)
    selected: list[int] = []
    remaining = list(range(n))

    for _ in range(K):
        best_i, best_e = None, np.inf
        for i in remaining:
            x = np.zeros(n)
            x[selected + [i]] = 1.0
            e = _qubo_energy(Q, x)
            if e < best_e:
                best_e, best_i = e, i
        if best_i is None:
            break
        selected.append(best_i)
        remaining.remove(best_i)

    x_final = np.zeros(n)
    x_final[selected] = 1.0
    return {
        "selected":    [tickers[i] for i in selected],
        "energy":      float(_qubo_energy(Q, x_final)),
        "runner_up":   [],
        "energy_gap":  None,
        "backend":     "greedy heuristic (N too large for brute force)",
        "n_outcomes":  n,
        "shots":       None,
        "penalty":     None,
        "top_counts":  None,
    }


def _solve_qubo(Q: np.ndarray, K: int, tickers: list[str], brute_limit: int = 5000) -> dict:
    """Dispatch to brute-force or greedy depending on problem size."""
    if comb(len(tickers), K) <= brute_limit:
        return _solve_qubo_brute(Q, K, tickers)
    return _solve_qubo_greedy(Q, K, tickers)


# =============================================================================
# Feature computation
# =============================================================================

# =============================================================================
# Quantum backend solvers — xpyq (synchronous) + IBM QAOA
# =============================================================================

def _build_qubo_from_params(params: dict) -> tuple[np.ndarray, int, list[str]]:
    """Reconstruct (Q, K, tickers) from pipeline request params."""
    tickers   = _parse_tickers(params.get("tickers", "NVDA,GOOG,AAPL"))[:16]
    K         = max(1, min(int(params.get("K", 3)), len(tickers)))
    lam       = float(params.get("risk_aversion", 4.0))
    n_days    = int(params.get("days", 320))
    seed      = int(params.get("seed", 7))
    lookback  = max(20, int(params.get("lookback", 120)))
    tk_params = _ticker_params(tickers, seed)
    source    = MockDataSource(n_days=n_days, seed=seed, params=tk_params)
    data      = source.load()
    rets      = data.close_prices().pct_change().dropna().tail(lookback)
    mu        = rets.mean().values * 252
    sigma     = rets.cov().values * 252
    penalty_s = str(params.get("penalty", "auto")).strip()
    penalty   = (
        float(2.0 * lam * np.abs(sigma).max())
        if penalty_s.lower() == "auto"
        else float(penalty_s)
    )
    return _build_qubo(mu, sigma, K, lam, penalty), K, tickers


def _solve_qubo_xpyq(
    Q: np.ndarray, K: int, tickers: list[str], timeout: float = 120.0
) -> dict:
    """Submit QUBO to xpyq and block until result (or timeout). Falls back to local."""
    if not XPYQ_KEY:
        result = _solve_qubo(Q, K, tickers)
        result["backend"] = "local brute force (no XPYQ_KEY)"
        return result

    try:
        import requests as _r

        code    = _build_qubo_code(tickers, Q.tolist(), K)
        resp    = _r.post(
            f"{_XPYQ_BASE}/api/v1/compute/runs",
            headers=_xpyq_headers(),
            json={"code": code, "name": "quantinel_pipeline"},
            timeout=10,
        )
        resp.raise_for_status()
        run_data = resp.json()
        run_id   = run_data.get("run_id") or run_data.get("id")
        if not run_id:
            raise ValueError("No run_id in xpyq response")

        deadline = time.time() + timeout
        r: dict  = {}
        while time.time() < deadline:
            r = _r.get(
                f"{_XPYQ_BASE}/api/v1/compute/runs/{run_id}",
                headers=_xpyq_headers(),
                timeout=10,
            ).json()
            if r.get("status") in ("completed", "failed", "timed_out", "cancelled"):
                break
            time.sleep(0.5)

        if r.get("status") == "completed":
            stdout = r.get("stdout", "")
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    parsed = json.loads(line)
                    return {
                        "selected":   parsed.get("selected", []),
                        "energy":     parsed.get("energy"),
                        "runner_up":  parsed.get("runner_up", []),
                        "energy_gap": parsed.get("energy_gap"),
                        "backend":    "xpyq hardware",
                        "n_outcomes": comb(len(tickers), K),
                        "shots":      None,
                        "penalty":    float(penalty_from_q(Q, K, len(tickers))),
                        "top_counts": None,
                    }
    except Exception:
        traceback.print_exc()

    result = _solve_qubo(Q, K, tickers)
    result["backend"] = "local brute force (xpyq fallback)"
    return result


def _submit_qaoa_ibm(
    Q: np.ndarray, K: int, tickers: list[str],
    token: str, device: str | None = None, shots: int = 1024,
) -> dict:
    """Submit a p=1 QAOA circuit to IBM QPU. Returns {job_id, backend}."""
    sub = _qb.submit_to_ibm(Q, token=token, device=device, shots=shots)
    return {"job_id": sub["job_id"], "backend": sub["backend_name"]}


def _poll_qaoa_ibm(
    job_id: str, Q: np.ndarray, K: int, tickers: list[str], token: str, shots: int
) -> dict:
    """Poll an IBM QPU QAOA job; return status + parsed QUBO result when finished."""
    n = len(tickers)
    poll = _qb.poll_ibm_job(job_id, token=token)
    status = poll["status"]
    backend_label = f"IBM QPU — {poll['backend_name']} (p=1 QAOA, {shots} shots)"

    if status not in {"DONE", "COMPLETED"}:
        return {"status": status, "result": None, "backend": poll["backend_name"],
                "n_outcomes": comb(n, K), "top_counts": None}

    decoded = _qb.decode_counts(poll.get("counts") or {}, Q, K=K)
    best_x, best_e = decoded["best_x"], decoded["best_e"]
    run2_x, run2_e = decoded["run2_x"], decoded["run2_e"]

    if best_x is None:
        return {"status": status, "result": None, "backend": backend_label,
                "n_outcomes": comb(n, K), "top_counts": decoded["top_counts"]}

    selected  = [tickers[i] for i in range(n) if best_x[i] > 0.5]
    runner_up = [tickers[i] for i in range(n) if run2_x is not None and run2_x[i] > 0.5]
    return {
        "status": status,
        "result": {
            "selected":   selected,
            "energy":     best_e,
            "runner_up":  runner_up,
            "energy_gap": round(run2_e - best_e, 6) if run2_x is not None else None,
        },
        "backend":    backend_label,
        "n_outcomes": comb(n, K),
        "top_counts": decoded["top_counts"],
    }


def _solve_qubo_ibm(
    Q: np.ndarray, K: int, tickers: list[str],
    token: str, device: str | None = None, shots: int = 1024,
) -> dict:
    """Submit QUBO to IBM QPU (p=1 QAOA) and block until result. Falls back to local."""
    try:
        result = _qb.run_qaoa_ibm(
            Q=Q, token=token, device=device, shots=shots, K=K,
            poll_interval=5.0, timeout=600.0,
        )
        best_x = result["best_x"]
        if best_x is None:
            raise ValueError("No K-constrained bitstring found in IBM counts")
        n = len(tickers)
        selected  = [tickers[i] for i in range(n) if best_x[i] > 0.5]
        run2_x    = result.get("run2_x")
        run2_e    = result.get("run2_e", float("inf"))
        runner_up = [tickers[i] for i in range(n) if run2_x is not None and run2_x[i] > 0.5]
        return {
            "selected":   selected,
            "energy":     result["best_e"],
            "runner_up":  runner_up,
            "energy_gap": round(run2_e - result["best_e"], 6) if run2_x is not None else None,
            "backend":    result["backend"],
            "n_outcomes": comb(n, K),
            "shots":      shots,
            "penalty":    None,
            "top_counts": result["top_counts"],
        }
    except Exception as exc:
        traceback.print_exc()
        fallback = _solve_qubo(Q, K, tickers)
        fallback["backend"] = f"local brute force (IBM failed: {exc})"
        return fallback


def _compute_features(closes: pd.DataFrame, as_of, lookback: int) -> dict[str, dict]:
    """Compute ML features per ticker for the window ending at as_of."""
    window = closes.loc[:as_of].tail(lookback)
    rets   = window.pct_change().dropna()
    if len(rets) < 5:
        return {}

    eq_rets = rets.mean(axis=1)
    features: dict[str, dict] = {}

    for t in closes.columns:
        r = rets[t]
        mom20 = float(r.tail(20).sum())
        vol20 = float(r.tail(20).std() * np.sqrt(252)) if len(r.tail(20)) > 1 else 0.0

        # Beta to equal-weight index
        cov_mat = np.cov(r.values, eq_rets.values)
        beta = float(cov_mat[0, 1] / cov_mat[1, 1]) if cov_mat[1, 1] > 0 else 1.0

        # Max drawdown over 60 days
        tail60 = r.tail(60)
        cum    = (1 + tail60).cumprod()
        peak   = cum.cummax()
        dd     = float(((cum - peak) / (peak + 1e-9)).min())

        pred_sharpe = mom20 / (vol20 + 1e-9) * np.sqrt(252)

        features[t] = {
            "momentum_20":          round(mom20, 4),
            "vol_20":               round(vol20, 4),
            "beta_to_equal_weight": round(beta, 3),
            "max_drawdown_60":      round(dd, 4),
            "pred_sharpe":          round(pred_sharpe, 3),
        }
    return features


# =============================================================================
# /api/run — multi-agent walk-forward pipeline
# =============================================================================

@app.route("/api/run", methods=["POST"])
def api_run():
    body = request.get_json(force=True) or {}
    try:
        return jsonify(_run_pipeline(body))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


def _parse_tickers(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(t).strip().upper() for t in raw if str(t).strip()]
    return [t.strip().upper() for t in str(raw).split(",") if t.strip()]


def _run_pipeline(params: dict) -> dict:
    # ── Parse request ─────────────────────────────────────────────────────────
    tickers    = _parse_tickers(params.get("tickers", "NVDA,GOOG,AAPL,MSFT,AMZN,META,TSLA,AMD,NFLX,INTC"))[:16]
    K          = max(1, min(int(params.get("K", 3)), len(tickers)))
    lam        = float(params.get("risk_aversion", 4.0))
    n_days     = int(params.get("days", 320))
    seed       = int(params.get("seed", 7))
    reb_every  = max(1, int(params.get("rebalance_every", 10)))
    lookback   = max(20, int(params.get("lookback", 120)))
    holding    = max(1, int(params.get("holding", 5)))
    src_type        = params.get("data_source", "synthetic")
    penalty_s       = str(params.get("penalty", "auto")).strip()
    quantum_backend = str(params.get("quantum_backend", "local")).strip().lower()

    # ── Load market data ──────────────────────────────────────────────────────
    if src_type == "live":
        start  = params.get("start", "2023-01-01")
        end    = params.get("end", "2024-12-31")
        source = YFinanceDataSource(tickers=tickers, start=start, end=end)
    else:
        tk_params = _ticker_params(tickers, seed)
        source    = MockDataSource(n_days=n_days, seed=seed, params=tk_params)

    data     = source.load()
    tickers  = data.tickers
    closes   = data.close_prices()
    dates    = closes.index
    n_assets = len(tickers)
    rets_df  = closes.pct_change().dropna()

    # ── Determine walk-forward step indices ───────────────────────────────────
    step_indices = list(range(lookback, len(dates) - holding, reb_every))
    if not step_indices:
        raise ValueError(
            f"Not enough data for walk-forward: need at least {lookback + holding} days, "
            f"got {len(dates)}. Reduce lookback or increase days."
        )

    # ── Build QUBO for the final (most recent) window ─────────────────────────
    last_i     = step_indices[-1]
    last_rets  = rets_df.iloc[max(0, last_i - lookback): last_i]
    mu_last    = last_rets.mean().values * 252
    sigma_last = last_rets.cov().values * 252

    if penalty_s.lower() == "auto":
        penalty = float(2.0 * lam * np.abs(sigma_last).max())
    else:
        try:
            penalty = float(penalty_s)
        except ValueError:
            penalty = float(2.0 * lam * np.abs(sigma_last).max())

    Q_final = _build_qubo(mu_last, sigma_last, K, lam, penalty)

    # ── QuantumAgent: solve the final QUBO (backend chosen by the user) ───────
    if quantum_backend == "xpyq":
        qubo_result = _solve_qubo_xpyq(
            Q_final, K, tickers,
            timeout=float(params.get("poll_timeout", 120.0)),
        )
    elif quantum_backend == "ibm":
        qubo_result = _solve_qubo_ibm(
            Q_final, K, tickers,
            token=IBM_TOKEN,
            device=params.get("ibm_backend") or None,
            shots=int(params.get("shots", 1024)),
        )
    else:
        qubo_result = _solve_qubo(Q_final, K, tickers)
    quantum_final  = qubo_result["selected"]

    # ── MLAgent: rank by predicted Sharpe (final window) ─────────────────────
    feat_final = _compute_features(closes, dates[last_i], lookback)
    ml_scores  = {t: feat_final.get(t, {}).get("pred_sharpe", 0.0) for t in tickers}
    ml_final   = sorted(ml_scores, key=ml_scores.get, reverse=True)[:K]  # type: ignore[arg-type]

    # ── MetaAgent: deterministic Sharpe rule ──────────────────────────────────
    def _exp_return(selection: list[str], mu: np.ndarray) -> float:
        idxs = [tickers.index(t) for t in selection if t in tickers]
        return float(mu[idxs].mean()) if idxs else 0.0

    q_exp = _exp_return(quantum_final, mu_last)
    m_exp = _exp_return(ml_final, mu_last)
    meta_method_final = "quantum" if q_exp >= m_exp else "ml"
    meta_final        = quantum_final if meta_method_final == "quantum" else ml_final

    # ── Walk-forward history ──────────────────────────────────────────────────
    history_rows:        list[dict] = []
    quantum_returns_all: list[float] = []
    ml_returns_all:      list[float] = []
    meta_returns_all:    list[float] = []
    quantum_wins = ml_wins = 0

    for step_i in step_indices:
        t_now = dates[step_i]
        t_fwd = dates[min(step_i + holding, len(dates) - 1)]

        window_rets = rets_df.iloc[max(0, step_i - lookback): step_i]
        if len(window_rets) < 10:
            continue

        mu_w    = window_rets.mean().values * 252
        sigma_w = window_rets.cov().values * 252
        pen_w   = float(2.0 * lam * np.abs(sigma_w).max())
        Q_w     = _build_qubo(mu_w, sigma_w, K, lam, pen_w)

        # QuantumAgent (local solve)
        q_res = _solve_qubo(Q_w, K, tickers)
        q_sel = q_res["selected"]

        # MLAgent (momentum ranking)
        mom = {t: float(window_rets[t].tail(20).sum()) for t in tickers}
        ml_sel = sorted(mom, key=mom.get, reverse=True)[:K]  # type: ignore[arg-type]

        # Forward return for each selection
        def _fwd_ret(selection: list[str]) -> float:
            rets_step = []
            for ticker in selection:
                try:
                    p0 = float(closes[ticker].loc[t_now])
                    p1 = float(closes[ticker].loc[t_fwd])
                    rets_step.append((p1 - p0) / (p0 + 1e-9))
                except Exception:
                    rets_step.append(0.0)
            return float(np.mean(rets_step)) if rets_step else 0.0

        q_ret  = _fwd_ret(q_sel)
        ml_ret = _fwd_ret(ml_sel)

        # MetaAgent decision this step
        q_mu_w = _exp_return(q_sel, mu_w)
        m_mu_w = _exp_return(ml_sel, mu_w)
        step_meta = "quantum" if q_mu_w >= m_mu_w else "ml"
        meta_sel  = q_sel if step_meta == "quantum" else ml_sel
        meta_ret  = q_ret if step_meta == "quantum" else ml_ret

        quantum_returns_all.append(q_ret)
        ml_returns_all.append(ml_ret)
        meta_returns_all.append(meta_ret)

        if step_meta == "quantum":
            quantum_wins += 1
        else:
            ml_wins += 1

        history_rows.append({
            "date":        str(t_now.date()),
            "quantum":     q_sel,
            "ml":          ml_sel,
            "meta_method": step_meta,
            "fell_back":   False,
            "sharpe_meta": 0.0,  # filled below
        })

    # ── Compute rolling 10-step Sharpe for each history row ───────────────────
    _WIN = 10
    for idx_h, row in enumerate(history_rows):
        start_w  = max(0, idx_h - _WIN + 1)
        window_m = meta_returns_all[start_w: idx_h + 1]
        arr_w    = np.array(window_m)
        if len(arr_w) > 1 and arr_w.std() > 0:
            sh = float(arr_w.mean() / arr_w.std() * np.sqrt(252 / holding))
        elif meta_returns_all:
            sh = float(meta_returns_all[idx_h]) * np.sqrt(252 / holding)
        else:
            sh = 0.0
        history_rows[idx_h]["sharpe_meta"] = round(sh, 3)

    # ── Realized performance summary ──────────────────────────────────────────
    meta_arr    = np.array(meta_returns_all) if meta_returns_all else np.array([0.0])
    meta_total  = float(np.prod(1 + meta_arr) - 1)
    meta_vol    = float(meta_arr.std() * np.sqrt(252 / holding)) if len(meta_arr) > 1 else 0.0
    meta_sharpe = (
        float(meta_arr.mean() / (meta_arr.std() + 1e-9) * np.sqrt(252 / holding))
        if len(meta_arr) > 1 else 0.0
    )

    total_wins     = quantum_wins + ml_wins
    quantum_share  = round(quantum_wins / total_wins * 100) if total_wins else 50
    ml_share       = 100 - quantum_share

    # ── Build agent rationales ────────────────────────────────────────────────
    def _quantum_rationale(selected: list[str]) -> tuple[dict, float]:
        per = {}
        for t in tickers:
            i   = tickers.index(t)
            exp = float(mu_last[i])
            vol = float(np.sqrt(sigma_last[i, i]))
            tag = "Selected" if t in selected else "Rejected"
            per[t] = f"{tag} — exp. return {exp*100:.1f}%/yr, vol {vol*100:.1f}%/yr"

        rejected = [
            (t, per[t].replace("Rejected — ", ""))
            for t in tickers if t not in selected
        ][:5]

        key_sigs = {
            "QUBO energy":      round(qubo_result["energy"], 4),
            "penalty P":        round(penalty, 4),
            "λ (risk aversion)": round(lam, 2),
            "K (cardinality)":  K,
        }
        avg_conf = float(np.mean([
            min(1.0, abs(float(mu_last[tickers.index(t)])) /
                (float(np.sqrt(sigma_last[tickers.index(t), tickers.index(t)])) + 1e-9))
            for t in selected
        ])) if selected else 0.5

        caveats = []
        backend_str = qubo_result.get("backend", "")
        if backend_str.startswith("greedy"):
            caveats.append("Universe too large for exact QUBO solve — greedy heuristic used")
        elif "fallback" in backend_str.lower():
            caveats.append(f"Fell back to local solve — {backend_str}")
        elif quantum_backend == "local":
            caveats.append("QUBO solved by local brute force (choose xpyq or IBM for hardware)")

        return {
            "summary":     f"QUBO selected {K} assets minimising risk-adjusted cost at energy {qubo_result['energy']:.3f}",
            "per_ticker":  per,
            "rejected":    rejected,
            "key_signals": key_sigs,
            "caveats":     caveats,
        }, min(1.0, max(0.0, avg_conf))

    def _ml_rationale(selected: list[str]) -> tuple[dict, float]:
        per = {}
        for t in tickers:
            f  = feat_final.get(t, {})
            sh = f.get("pred_sharpe", 0.0)
            mo = f.get("momentum_20", 0.0)
            tag = "Selected" if t in selected else "Ranked out"
            per[t] = f"{tag} — pred. Sharpe {sh:.2f}, 20d momentum {mo*100:.1f}%"

        rejected = [
            (t, per[t].replace("Ranked out — ", ""))
            for t in tickers if t not in selected
        ][:5]

        scores   = {t: feat_final.get(t, {}).get("pred_sharpe", 0.0) for t in tickers}
        avg_conf = float(np.mean([
            min(1.0, max(0.0, abs(scores.get(t, 0.0))))
            for t in selected
        ])) if selected else 0.5

        return {
            "summary":     f"Momentum/Sharpe ranking selected {K} highest-conviction assets",
            "per_ticker":  per,
            "rejected":    rejected,
            "key_signals": {t: round(scores.get(t, 0.0), 3) for t in tickers},
            "caveats":     [],
        }, min(1.0, max(0.0, avg_conf))

    q_rationale, q_conf = _quantum_rationale(quantum_final)
    ml_rationale, ml_conf = _ml_rationale(ml_final)

    # ── Universe snapshot (final rebalance) ───────────────────────────────────
    _ac = getattr(data, 'asset_classes', None) or {}
    universe = [
        {
            "ticker":     t,
            "exp_return": round(float(mu_last[tickers.index(t)]), 4),
            "vol":        round(float(np.sqrt(sigma_last[tickers.index(t), tickers.index(t)])), 4),
            "in_quantum": t in quantum_final,
            "in_ml":      t in ml_final,
            "in_meta":    t in meta_final,
            "asset_class": _ac[t].value if t in _ac else "equity",
        }
        for t in tickers
    ]
    n_eq  = sum(1 for t in tickers if (t not in _ac or _ac[t].value == "equity"))
    n_cmd = sum(1 for t in tickers if t in _ac and _ac[t].value == "commodity")
    n_re  = sum(1 for t in tickers if t in _ac and _ac[t].value == "real_estate")

    # ── MetaAgent reasoning ───────────────────────────────────────────────────
    meta_reasoning = (
        f"Comparing QuantumAgent's selection {quantum_final} "
        f"(exp. return {q_exp*100:.1f}%/yr) vs MLAgent's {ml_final} "
        f"(exp. return {m_exp*100:.1f}%/yr). "
        f"{'QuantumAgent' if meta_method_final == 'quantum' else 'MLAgent'} "
        f"wins with higher expected return. Over {len(history_rows)} rebalances, "
        f"QuantumAgent was chosen {quantum_wins}×, MLAgent {ml_wins}×."
    )
    # ── Crystal Ball: scenario forecast ──────────────────────────────────────────
    cb_pred = None
    if params.get("use_crystal_ball", True):
        try:
            from forecast import CrystalBall, MomentumForecaster
            from chaos import ChaosEngine
            from contracts import NewsFeed
            _as_of = dates[-1]
            _news  = NewsFeed(as_of=pd.Timestamp(_as_of), articles=[])
            _xpyq_for_cb = XPYQ_KEY if quantum_backend == "xpyq" else ""
            _cb    = CrystalBall(MomentumForecaster(), ChaosEngine(api_key=_xpyq_for_cb), api_key=_xpyq_for_cb)
            _pred  = _cb.predict(data, _news, _as_of)
            cb_pred = {
                "base_returns":           {t: round(v, 4) for t, v in _pred.base_returns.items()},
                "bull_returns":           {t: round(v, 4) for t, v in _pred.bull_returns.items()},
                "bear_returns":           {t: round(v, 4) for t, v in _pred.bear_returns.items()},
                "crash_adjusted_returns": {t: round(v, 4) for t, v in _pred.crash_adjusted_returns.items()},
                "annual_volatility":      {t: round(v, 4) for t, v in _pred.annual_volatility.items()},
                "crash_probability":      round(_pred.crash_probability, 4),
                "dominant_factor_var":    round(_pred.dominant_factor_var, 4),
                "confidence":             {t: round(v, 4) for t, v in _pred.confidence.items()},
                "horizon_days":           _pred.horizon_days,
                "reasoning":              _pred.reasoning,
            }
        except Exception:
            traceback.print_exc()
    # ── Assemble and return ───────────────────────────────────────────────────
    return {
        "data": {
            "source":        src_type,
            "rows":          len(dates),
            "n_assets":      n_assets,
            "n_equities":    n_eq,
            "n_commodities": n_cmd,
            "n_real_estate": n_re,
            "rebalances":    len(history_rows),
            "as_of":         str(dates[-1].date()),
        },
        "proposals": {
            "quantum": {
                "selected":   quantum_final,
                "confidence": round(q_conf, 3),
                "backend":    qubo_result.get("backend", "local brute force"),
                "penalty":    round(penalty, 4),
                "rationale":  q_rationale,
            },
            "ml": {
                "selected":   ml_final,
                "confidence": round(ml_conf, 3),
                "backend":    "momentum / pred. Sharpe",
                "penalty":    None,
                "rationale":  ml_rationale,
                "features":   feat_final,
            },
        },
        "qubo": Q_final.tolist(),
        "meta": {
            "selected":  meta_final,
            "method":    meta_method_final,
            "backend":   "deterministic Sharpe rule",
            "fell_back": False,
            "reasoning": meta_reasoning,
            "blend_weights": None,
            "inputs_seen": {
                "proposals": {
                    "quantum": {"selected": quantum_final, "confidence": round(q_conf, 3)},
                    "ml":      {"selected": ml_final,      "confidence": round(ml_conf, 3)},
                },
                "context": {
                    "vol_regime":              "elevated" if float(sigma_last.diagonal().mean()) > 0.15 else "low",
                    "recent_universe_return":  round(float(mu_last.mean()), 4),
                },
            },
        },
        "realized": {
            "meta": {
                "return": round(meta_total, 4),
                "vol":    round(meta_vol, 4),
                "sharpe": round(meta_sharpe, 3),
            }
        },
        "history": history_rows,
        "summary": {
            "shares":           {"quantum": quantum_share, "ml": ml_share},
            "cumulative_sharpe": round(meta_sharpe, 3),
        },
        "universe":     universe,
        "crystal_ball": cb_pred,
    }


# =============================================================================
# /api/qubo/local — standalone QUBO solver
# =============================================================================

@app.route("/api/qubo/local", methods=["POST"])
def api_qubo_local():
    body = request.get_json(force=True) or {}
    try:
        return jsonify(_qubo_from_params(body))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


def _qubo_from_params(params: dict) -> dict:
    tickers  = _parse_tickers(params.get("tickers", "NVDA,GOOG,AAPL"))[:16]
    K        = max(1, min(int(params.get("K", 3)), len(tickers)))
    lam      = float(params.get("risk_aversion", 4.0))
    n_days   = int(params.get("days", 320))
    seed     = int(params.get("seed", 7))
    lookback = max(20, int(params.get("lookback", 120)))

    tk_params = _ticker_params(tickers, seed)
    source    = MockDataSource(n_days=n_days, seed=seed, params=tk_params)
    data      = source.load()
    rets      = data.close_prices().pct_change().dropna().tail(lookback)

    mu    = rets.mean().values * 252
    sigma = rets.cov().values * 252

    penalty_s = str(params.get("penalty", "auto")).strip()
    penalty   = (
        float(2.0 * lam * np.abs(sigma).max())
        if penalty_s.lower() == "auto"
        else float(penalty_s)
    )

    Q   = _build_qubo(mu, sigma, K, lam, penalty)
    res = _solve_qubo(Q, K, tickers)
    return res


# =============================================================================
# /api/quantum/simulate — Aer simulator stub
# =============================================================================

@app.route("/api/quantum/simulate", methods=["POST"])
def api_quantum_simulate():
    """
    Qiskit/Aer is not in the project requirements, so we fall back to the local
    brute-force solver and label it as 'simulator (local fallback)'.
    """
    body = request.get_json(force=True) or {}
    try:
        res = _qubo_from_params(body)
        res["backend"] = "Aer simulator (local fallback — Qiskit not installed)"
        return jsonify(res)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# =============================================================================
# /api/quantum/ibm/* — IBM QPU stubs
# =============================================================================

@app.route("/api/quantum/ibm/devices", methods=["POST"])
def api_ibm_devices():
    if not IBM_TOKEN:
        return jsonify({"error": "IBM_TOKEN not configured on the server."}), 400
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService  # type: ignore
        service = QiskitRuntimeService(channel="ibm_quantum", token=IBM_TOKEN)
        backends = service.backends(operational=True, simulator=False)
        devices = [
            {"name": b.name, "num_qubits": b.num_qubits, "pending_jobs": b.status().pending_jobs}
            for b in backends
        ]
        return jsonify({"devices": devices})
    except Exception as exc:
        return jsonify({"error": str(exc), "devices": []}), 500


@app.route("/api/quantum/ibm/submit", methods=["POST"])
def api_ibm_submit():
    if not IBM_TOKEN:
        return jsonify({"error": "IBM_TOKEN not configured on the server."}), 400
    body = request.get_json(force=True) or {}
    try:
        Q, K, tickers = _build_qubo_from_params(body)
        shots  = int(body.get("shots", 1024))
        device = body.get("ibm_backend") or None
        result = _submit_qaoa_ibm(Q, K, tickers, token=IBM_TOKEN, device=device, shots=shots)
        return jsonify({**result, "K": K, "tickers": tickers, "n_outcomes": comb(len(tickers), K)})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/quantum/ibm/status", methods=["POST"])
def api_ibm_status():
    if not IBM_TOKEN:
        return jsonify({"error": "IBM_TOKEN not configured on the server."}), 400
    body   = request.get_json(force=True) or {}
    job_id = body.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    try:
        Q, K, tickers = _build_qubo_from_params(body)
        shots  = int(body.get("shots", 1024))
        result = _poll_qaoa_ibm(job_id, Q, K, tickers, token=IBM_TOKEN, shots=shots)
        return jsonify(result)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# =============================================================================
# /api/xpyq/* — xpyq compute queue
# =============================================================================

def _xpyq_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {XPYQ_KEY}",
        "Content-Type":  "application/json",
    }


def _build_qubo_code(tickers: list[str], Q: list[list[float]], K: int) -> str:
    """Generate Python code that xpyq will execute to solve the QUBO."""
    return (
        "import numpy as np, itertools, json\n"
        f"Q = np.array({Q!r})\n"
        f"tickers = {tickers!r}\n"
        f"K = {K}\n"
        "n = len(tickers)\n"
        "best_e, best_x, run2_e, run2_x = np.inf, None, np.inf, None\n"
        "for combo in itertools.combinations(range(n), K):\n"
        "    x = np.zeros(n); x[list(combo)] = 1.0\n"
        "    e = float(x @ Q @ x)\n"
        "    if e < best_e: run2_e, run2_x, best_e, best_x = best_e, best_x, e, x.copy()\n"
        "    elif e < run2_e: run2_e, run2_x = e, x.copy()\n"
        "selected = [tickers[i] for i in range(n) if best_x is not None and best_x[i] > 0.5]\n"
        "runner_up = [tickers[i] for i in range(n) if run2_x is not None and run2_x[i] > 0.5]\n"
        "print(json.dumps({'selected': selected, 'energy': best_e,"
        " 'runner_up': runner_up, 'energy_gap': run2_e - best_e}))\n"
    )


@app.route("/api/xpyq/submit", methods=["POST"])
def api_xpyq_submit():
    if not XPYQ_KEY:
        return jsonify({"error": "XPYQ_KEY not configured on the server."}), 400

    body    = request.get_json(force=True) or {}
    tickers = _parse_tickers(body.get("tickers", "NVDA,GOOG,AAPL"))[:16]
    K       = max(1, min(int(body.get("K", 3)), len(tickers)))

    try:
        result_dict = _qubo_from_params(body)
        tk_params   = _ticker_params(tickers, int(body.get("seed", 7)))
        source      = MockDataSource(n_days=int(body.get("days", 320)), seed=int(body.get("seed", 7)), params=tk_params)
        data        = source.load()
        closes      = data.close_prices()
        rets        = closes.pct_change().dropna().tail(max(20, int(body.get("lookback", 120))))
        mu          = rets.mean().values * 252
        sigma       = rets.cov().values * 252
        lam         = float(body.get("risk_aversion", 4.0))
        penalty_s   = str(body.get("penalty", "auto")).strip()
        penalty     = (
            float(2.0 * lam * np.abs(sigma).max())
            if penalty_s.lower() == "auto"
            else float(penalty_s)
        )
        Q = _build_qubo(mu, sigma, K, lam, penalty)

        import requests as _r
        code = _build_qubo_code(tickers, Q.tolist(), K)
        resp = _r.post(
            f"{_XPYQ_BASE}/api/v1/compute/runs",
            headers=_xpyq_headers(),
            json={"code": code, "name": "quantinel_qubo"},
            timeout=10,
        )
        resp.raise_for_status()
        run_data  = resp.json()
        run_id    = run_data.get("run_id") or run_data.get("id")
        queue_pos = run_data.get("queue_position")
        return jsonify({
            "run_id":         run_id,
            "status":         run_data.get("status", "queued"),
            "queue_position": queue_pos,
            "N":              len(tickers),
            "K":              K,
            "tickers":        tickers,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/xpyq/status", methods=["POST"])
def api_xpyq_status():
    if not XPYQ_KEY:
        return jsonify({"error": "XPYQ_KEY not configured."}), 400

    body   = request.get_json(force=True) or {}
    run_id = body.get("run_id")
    if not run_id:
        return jsonify({"error": "run_id required"}), 400

    try:
        import requests as _r
        resp = _r.get(
            f"{_XPYQ_BASE}/api/v1/compute/runs/{run_id}",
            headers=_xpyq_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data   = resp.json()
        status = data.get("status")
        result = None

        if status == "completed":
            stdout = data.get("stdout", "")
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed  = json.loads(line)
                        tickers = body.get("tickers", [])
                        if isinstance(tickers, str):
                            tickers = [t.strip().upper() for t in tickers.split(",") if t.strip()]
                        result = {
                            "selected":   parsed.get("selected", []),
                            "energy":     parsed.get("energy"),
                            "runner_up":  parsed.get("runner_up", []),
                            "energy_gap": parsed.get("energy_gap"),
                        }
                    except Exception:
                        pass
                    break

        return jsonify({
            "status":         status,
            "queue_position": data.get("queue_position"),
            "result":         result,
            "credits_charged": data.get("credits_charged"),
            "duration_ms":    data.get("duration_ms"),
            "boards_used":    data.get("boards_used"),
            "stdout":         data.get("stdout", "")[:500],
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/xpyq/cancel", methods=["POST"])
def api_xpyq_cancel():
    if not XPYQ_KEY:
        return jsonify({"error": "XPYQ_KEY not configured."}), 400

    body   = request.get_json(force=True) or {}
    run_id = body.get("run_id")
    if not run_id:
        return jsonify({"error": "run_id required"}), 400

    try:
        import requests as _r
        resp = _r.post(
            f"{_XPYQ_BASE}/api/v1/compute/runs/{run_id}/cancel",
            headers=_xpyq_headers(),
            timeout=10,
        )
        return jsonify(resp.json())
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    print(f"Quantinel server starting on http://{HOST}:{PORT}")
    print(f"  xpyq key    : {'SET' if XPYQ_KEY else 'not set'}")
    print(f"  Anthropic   : {'SET' if ANTHROPIC_KEY else 'not set'}")
    print(f"  OpenRouter  : {'SET' if OPENROUTER_KEY else 'not set'}")
    print(f"  IBM token   : {'SET' if IBM_TOKEN else 'not set'}")
    app.run(host=HOST, port=PORT, debug=False)
