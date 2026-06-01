"""
quantum_backends.py — Shared IBM Quantum QAOA primitives.

Used by optimize.py (QaoaOptimizer) and server.py. Isolating all
Qiskit-specific code here means a single import guard covers every caller.

IBM Quantum — QUBO-to-circuit mapping
--------------------------------------
QUBO:    minimise x'Qx   (x ∈ {0,1}^n)
Ising:   x_i = (1 − Z_i) / 2
         H_C = Σ_{i<j} J_ij Z_i Z_j + Σ_i h_i Z_i
         J_ij = Q[i,j] / 2,   h_i = −Σ_j Q[i,j] / 2

p=1 QAOA circuit:  |+>^n  →  cost(γ)  →  mixer(β)  →  measure
"""
from __future__ import annotations

import time

import numpy as np


# ---------------------------------------------------------------------------
# Circuit construction
# ---------------------------------------------------------------------------

def build_qaoa_circuit(
    Q: np.ndarray,
    gamma_val: float,
    beta_val: float,
    backend,
):
    """
    Build and transpile a p=1 QAOA circuit for QUBO matrix *Q* onto *backend*.

    Returns an ISA QuantumCircuit ready for Qiskit SamplerV2.

    Args:
        Q:          Symmetric QUBO matrix (n×n).
        gamma_val:  Cost-layer rotation angle (radians).
        beta_val:   Mixer-layer rotation angle (radians).
        backend:    IBM QPU backend object from QiskitRuntimeService.

    Raises:
        ImportError  if qiskit is not installed.
    """
    from qiskit import QuantumCircuit
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

    n = Q.shape[0]
    # Ising linear terms: h_i = -Σ_j Q[i,j] / 2
    h = -0.5 * Q.sum(axis=1)

    qc = QuantumCircuit(n)
    qc.h(range(n))  # uniform superposition

    # Cost layer — ZZ couplings (RZZ angle = γ·Q[i,j])
    for i in range(n):
        for j in range(i + 1, n):
            angle = float(gamma_val * Q[i, j])
            if abs(angle) > 1e-10:
                qc.rzz(angle, i, j)

    # Cost layer — Z single-qubit terms (RZ angle = 2γ·h_i)
    for i in range(n):
        angle = float(2.0 * gamma_val * h[i])
        if abs(angle) > 1e-10:
            qc.rz(angle, i)

    # Mixer layer — uniform X rotation (RX angle = 2β)
    for i in range(n):
        qc.rx(2.0 * float(beta_val), i)

    qc.measure_all()
    pm = generate_preset_pass_manager(optimization_level=1, backend=backend)
    return pm.run(qc)


# ---------------------------------------------------------------------------
# IBM Quantum submission
# ---------------------------------------------------------------------------

def submit_to_ibm(
    Q: np.ndarray,
    token: str,
    device: str | None = None,
    shots: int = 1024,
) -> dict:
    """
    Submit a p=1 QAOA circuit for QUBO *Q* to an IBM QPU.

    Args:
        Q:       Symmetric QUBO matrix (n×n).
        token:   IBM Quantum API token.
        device:  Specific QPU name, or None for least-busy.
        shots:   Number of circuit shots.

    Returns:
        {"job_id": str, "backend_name": str}

    Raises:
        RuntimeError  if qiskit-ibm-runtime is not installed.
        RuntimeError  if no suitable IBM backend is available.
    """
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService
        from qiskit_ibm_runtime import SamplerV2 as Sampler
    except ImportError as exc:
        raise RuntimeError(
            "qiskit and qiskit-ibm-runtime must be installed for IBM backend. "
            "Run: pip install qiskit qiskit-ibm-runtime"
        ) from exc

    n = Q.shape[0]
    service = QiskitRuntimeService(channel="ibm_quantum", token=token)
    ibm_backend = (
        service.backend(device) if device
        else service.least_busy(operational=True, simulator=False, min_num_qubits=n)
    )
    isa_qc = build_qaoa_circuit(
        Q,
        gamma_val=float(np.pi / 4),
        beta_val=float(np.pi / 8),
        backend=ibm_backend,
    )
    sampler = Sampler(ibm_backend)
    job = sampler.run([isa_qc], shots=shots)
    return {"job_id": job.job_id(), "backend_name": ibm_backend.name}


# ---------------------------------------------------------------------------
# IBM Quantum polling
# ---------------------------------------------------------------------------

def poll_ibm_job(job_id: str, token: str) -> dict:
    """
    Check the status of an IBM QPU job.

    Returns:
        {
            "status":       str,          — "QUEUED"|"RUNNING"|"DONE"|"ERROR"|...
            "counts":       dict | None,  — bitstring→count when DONE/COMPLETED
            "backend_name": str,
        }
    """
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService
    except ImportError:
        return {"status": "ERROR", "counts": None, "backend_name": "unknown"}

    service = QiskitRuntimeService(channel="ibm_quantum", token=token)
    job = service.job(job_id)
    backend_name = getattr(getattr(job, "backend", lambda: None)(), "name", "ibm")
    raw = job.status()
    status = str(raw.name if hasattr(raw, "name") else raw).upper()

    if status not in {"DONE", "COMPLETED"}:
        return {"status": status, "counts": None, "backend_name": backend_name}

    ibm_result = job.result()
    counts = ibm_result[0].data.meas.get_counts()
    return {"status": "DONE", "counts": counts, "backend_name": backend_name}


# ---------------------------------------------------------------------------
# Result decoding
# ---------------------------------------------------------------------------

def decode_counts(
    counts: dict[str, int],
    Q: np.ndarray,
    K: int | None = None,
) -> dict:
    """
    Find the lowest-energy bitstrings from QAOA measurement counts.

    Args:
        counts:  Bitstring→count mapping from IBM Sampler.
        Q:       QUBO matrix (n×n, symmetric).
        K:       Cardinality filter — only accept bitstrings with exactly K ones.
                 Pass None for unconstrained selection.

    Returns:
        {
            "best_x":     np.ndarray | None,   — binary {0,1}^n
            "best_e":     float,               — QUBO energy x'Qx
            "run2_x":     np.ndarray | None,   — second-best solution
            "run2_e":     float,
            "top_counts": list[dict],          — top 20 by frequency
        }
    """
    n = Q.shape[0]
    best_x, best_e = None, float("inf")
    run2_x, run2_e = None, float("inf")
    top_counts = [
        {"bits": b, "n": c}
        for b, c in sorted(counts.items(), key=lambda x: -x[1])[:20]
    ]

    for bits in counts:
        x = np.array([int(b) for b in reversed(bits)], dtype=float)
        if x.shape[0] != n:
            continue
        if K is not None and int(x.sum()) != K:
            continue
        e = float(x @ Q @ x)
        if e < best_e:
            run2_e, run2_x = best_e, best_x
            best_e, best_x = e, x.copy()
        elif e < run2_e:
            run2_e, run2_x = e, x.copy()

    return {
        "best_x":     best_x,
        "best_e":     best_e,
        "run2_x":     run2_x,
        "run2_e":     run2_e,
        "top_counts": top_counts,
    }


# ---------------------------------------------------------------------------
# Blocking end-to-end solve
# ---------------------------------------------------------------------------

def run_qaoa_ibm(
    Q: np.ndarray,
    token: str,
    device: str | None = None,
    shots: int = 1024,
    K: int | None = None,
    poll_interval: float = 5.0,
    timeout: float = 600.0,
) -> dict:
    """
    Submit QUBO to IBM QPU via p=1 QAOA and block until a result arrives.

    Args:
        Q:             QUBO matrix (n×n, symmetric).
        token:         IBM Quantum API token.
        device:        Specific QPU name, or None for least-busy.
        shots:         Number of circuit shots.
        K:             Cardinality filter (None = unconstrained).
        poll_interval: Seconds between status polls.
        timeout:       Max wait time in seconds.

    Returns:
        {
            "best_x":     np.ndarray | None,
            "best_e":     float,
            "run2_x":     np.ndarray | None,
            "run2_e":     float,
            "backend":    str,              — human-readable label
            "counts":     dict,
            "top_counts": list[dict],
        }

    Raises:
        RuntimeError   if the IBM job ends in ERROR / CANCELLED / FAILED.
        TimeoutError   if *timeout* elapses before the job completes.
    """
    sub = submit_to_ibm(Q, token=token, device=device, shots=shots)
    backend_label = f"IBM QPU — {sub['backend_name']} (p=1 QAOA, {shots} shots)"
    deadline = time.time() + timeout

    while time.time() < deadline:
        poll = poll_ibm_job(sub["job_id"], token=token)
        status = poll["status"]

        if status in {"DONE", "COMPLETED"}:
            decoded = decode_counts(poll.get("counts") or {}, Q, K=K)
            return {
                "best_x":     decoded["best_x"],
                "best_e":     decoded["best_e"],
                "run2_x":     decoded["run2_x"],
                "run2_e":     decoded["run2_e"],
                "backend":    backend_label,
                "counts":     poll.get("counts") or {},
                "top_counts": decoded["top_counts"],
            }

        if status in {"ERROR", "CANCELLED", "FAILED"}:
            raise RuntimeError(
                f"IBM job {sub['job_id']} ended with status {status}"
            )

        time.sleep(poll_interval)

    raise TimeoutError(
        f"IBM job {sub['job_id']} did not complete within {timeout}s"
    )
