"""
run_quantum.py  —  three-way comparison: baseline vs quantum forecast vs full quantum.

  1. Baseline      : MomentumForecaster  + MeanVarianceOptimizer
  2. Quantum Fcst  : QuantumForecaster   + MeanVarianceOptimizer
  3. Full Quantum  : QuantumForecaster   + QaoaOptimizer

Quantum backend selection (via environment variables):
  QUANTUM_BACKEND  — "xpyq" (default) | "ibm" | "local"
  XPYQ_KEY         — xpyq Bearer token      (required when QUANTUM_BACKEND=xpyq)
  IBM_TOKEN        — IBM Quantum API token   (required when QUANTUM_BACKEND=ibm)
  IBM_DEVICE       — specific QPU name       (optional; defaults to least-busy)
  IBM_SHOTS        — QAOA shot count         (optional; default 1024)

Run:  python run_quantum.py
"""
import os

from backtest import Backtest
from data import MockDataSource
from execute import PaperExecutor
from forecast import MomentumForecaster, QuantumForecaster
from news import MockNewsSource
from optimize import MeanVarianceOptimizer, QaoaOptimizer
from risk import SampleCovRisk
from score import BacktestScorer, RiskScorer

API_KEY          = os.environ.get("XPYQ_KEY", "")
QUANTUM_BACKEND  = os.environ.get("QUANTUM_BACKEND", "xpyq")   # "xpyq"|"ibm"|"local"
IBM_TOKEN        = os.environ.get("IBM_TOKEN", "")
IBM_DEVICE       = os.environ.get("IBM_DEVICE", "") or None
IBM_SHOTS        = int(os.environ.get("IBM_SHOTS", "1024"))
XPYQ_TIMEOUT     = float(os.environ.get("XPYQ_TIMEOUT", "20"))
RISK_N_PATHS     = int(os.environ.get("QUANTINEL_N_PATHS", "10000"))
N_DAYS           = int(os.environ.get("QUANTINEL_N_DAYS", "504"))
REBALANCE_EVERY  = int(os.environ.get("QUANTINEL_REBALANCE_EVERY", "5"))


def run(forecaster, optimizer, label):
    bt = Backtest(
        source=MockDataSource(n_days=N_DAYS),
        news_source=MockNewsSource(),
        forecaster=forecaster,
        risk=SampleCovRisk(n_paths=RISK_N_PATHS),
        optimizer=optimizer,
        executor=PaperExecutor(),
        rebalance_every=REBALANCE_EVERY,
    )
    data, records, baseline = bt.run()
    card = BacktestScorer().score(records, baseline)
    risk_report = RiskScorer().score(records)

    print(f"\n{'=' * 56}")
    print(f"  {label}")
    print(f"{'=' * 56}")
    print(f"  rebalances             : {len(records)}")
    print(f"  Sharpe                 : {card.sharpe:6.2f}")
    print(f"  total return           : {card.total_return * 100:6.2f}%")
    print(f"  directional accuracy   : {card.directional_accuracy * 100:6.1f}%")
    print(f"  IC                     : {card.information_coefficient:6.2f}")
    print(f"  Sharpe vs 50/50 hold   : {card.vs_baseline_sharpe:+6.2f}")
    print(f"  final equity           : {card.equity_curve.iloc[-1]:6.3f}")
    print(f"  VaR breaches           : {risk_report.var_breaches}")
    print(f"  avg disagreement       : {risk_report.avg_disagreement:.3f}")
    print(f"{'=' * 56}")
    return card


def delta(label, a, b):
    print(f"\n  {label}")
    print(f"  Sharpe        : {b.sharpe - a.sharpe:+6.2f}")
    print(f"  total return  : {(b.total_return - a.total_return) * 100:+6.2f}%")
    print(f"  dir accuracy  : {(b.directional_accuracy - a.directional_accuracy) * 100:+6.1f}%")
    print(f"  Sharpe edge   : {b.vs_baseline_sharpe - a.vs_baseline_sharpe:+6.2f}")


if __name__ == "__main__":
    print(f"Quantum backend: {QUANTUM_BACKEND.upper()}")
    if QUANTUM_BACKEND == "ibm":
        _q_fcst = QuantumForecaster(backend="ibm", timeout=XPYQ_TIMEOUT)
        _q_opt  = QaoaOptimizer(
            backend="ibm", ibm_token=IBM_TOKEN,
            ibm_device=IBM_DEVICE, shots=IBM_SHOTS, timeout=XPYQ_TIMEOUT,
        )
        backend_label = "IBM Quantum (p=1 QAOA)"
    elif QUANTUM_BACKEND == "xpyq":
        _q_fcst = QuantumForecaster(api_key=API_KEY, timeout=XPYQ_TIMEOUT)
        _q_opt  = QaoaOptimizer(api_key=API_KEY, timeout=XPYQ_TIMEOUT)
        backend_label = "xpyq hardware"
    else:
        _q_fcst = QuantumForecaster(backend="local")
        _q_opt  = QaoaOptimizer(backend="local")
        backend_label = "local (classical fallback)"

    print("1/3  Baseline (momentum + Markowitz)...")
    c_base = run(MomentumForecaster(), MeanVarianceOptimizer(), "BASELINE — momentum + Markowitz")

    print(f"\n2/3  Quantum forecast + classical optimizer ({backend_label})...")
    c_qfcst = run(_q_fcst, MeanVarianceOptimizer(),
                  f"QUANTUM FORECAST — {backend_label} SVD + Markowitz")

    print(f"\n3/3  Full quantum: forecast + optimizer ({backend_label})...")
    c_full = run(
        QuantumForecaster(
            api_key=API_KEY if QUANTUM_BACKEND == "xpyq" else None,
            backend=QUANTUM_BACKEND,
            timeout=XPYQ_TIMEOUT,
        ) if QUANTUM_BACKEND != "ibm" else QuantumForecaster(backend="ibm", timeout=XPYQ_TIMEOUT),
        _q_opt,
        f"FULL QUANTUM — {backend_label} SVD + QUBO",
    )

    print(f"\n{'=' * 56}")
    print("  DELTAS")
    print(f"{'=' * 56}")
    delta("Quantum fcst vs baseline       :", c_base, c_qfcst)
    delta("Full quantum vs baseline       :", c_base, c_full)
    delta("Full quantum vs quantum fcst   :", c_qfcst, c_full)
    print(f"{'=' * 56}")
