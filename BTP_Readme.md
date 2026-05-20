# AURORA Wired-OR Tsetlin Machine Simulation using gem5

**B.Tech Project — IIT Roorkee (2025–26)**  
**Authors:** Mudit Mangal (22114057) · Dhruv Agrawal (22114030)  
**Supervisors:** Dr. Sudip Roy (IIT Roorkee) · Prof. Rishad Shafik (Newcastle University)

---

## Overview

This project implements a cycle-accurate gem5 simulation framework that compares three memory architectures under a real Tsetlin Machine (TM) inference workload on MNIST:

| Architecture | gem5 Object | Requests/run | Avg Latency |
|---|---|---|---|
| CMOS DDR4 | `MemCtrl + DDR4_2400_8x8` | 15,680,000 | 235.7 ns |
| Memristor IMC | Modified `MemCtrl` | 15,680,000 | 372.5 ns |
| AURORA Wired-OR | Custom `AuroraTM` | 10,000 | 4.99 ns |

**Headline result:** AURORA achieves ~73,977× effective throughput improvement over CMOS DDR4 — from a 47.2× latency reduction and a 1,568× request-count reduction working simultaneously.

---

## Project Structure

```
project-root/
│
├── gem5/                          # gem5 v25.1 source tree
│   └── src/
│       └── mem/
│           ├── aurora_tm.cc       # AuroraTM SimObject — C++ implementation
│           ├── aurora_tm.hh       # AuroraTM SimObject — header
│           ├── AuroraTM.py        # AuroraTM — Python parameter wrapper
│           ├── mem_ctrl.cc        # Modified MemCtrl (Memristor IMC penalty)
│           ├── mem_ctrl.hh        # Modified MemCtrl — header
│           └── MemCtrl.py         # Modified MemCtrl — Python wrapper
│
├── configs/
│   └── wired_or/
│       ├── run_single.py          # Top-level simulation script
│       └── tm_workload.py         # TMWorkload traffic generator class
│
├── tm_training/
│   ├── train_tm.py                # TM training script (PyTsetlin)
│   ├── tm_summary.json            # Trained TM metadata (auto-generated)
│   └── tm_summary_per_request.npy # Per-clause active fractions (auto-generated)
│
├── Diagrams/
│   ├── gem5.drawio.pdf            # gem5 simulation flow diagram
│   └── iitr.png                   # IIT Roorkee logo
│
├── BTP_Report.tex                 # Final LaTeX report
└── README.md                      # This file
```

---

## File Descriptions

### `src/mem/aurora_tm.cc` / `.hh` / `AuroraTM.py`
Custom gem5 SimObject implementing the AURORA wired-OR discharge physics.

**Key methods:**
- `loadPerRequestFractions()` — parses `.npy` binary file in C++, supports `float32` and `float64`
- `sampleActiveFraction()` — reads next fraction from vector (or samples Gaussian fallback)
- `computeDischargeLatency(f, rows)` — piecewise lookup from SPICE data
- `computePrechargeLatency(f, rows)` — returns 5,000 ps constant for ≤256 rows
- `addPVTVariation(t)` — applies multiplicative Gaussian noise (σ = 9%)

**Configurable parameters (in `AuroraTM.py`):**

| Parameter | Default | Description |
|---|---|---|
| `row_count` | 256 | Number of rows in the crossbar |
| `active_fraction` | 0.5 | Mean active fraction (Gaussian fallback) |
| `precharge_time` | `"5ns"` | Fixed precharge latency |
| `discharge_worst` | `"2300ps"` | Worst-case discharge (f≈0) |
| `discharge_best` | `"280ps"` | Best-case discharge (f=1.0) |
| `per_request_file` | `""` | Path to `.npy` fractions file |

---

### `src/mem/mem_ctrl.cc` / `.hh` / `MemCtrl.py`
Standard gem5 MemCtrl extended with a Memristor IMC discharge penalty.

**Five added parameters (in `MemCtrl.py`):**

| Parameter | Default | Description |
|---|---|---|
| `inference_mode` | `False` | Enable wired-OR penalty |
| `row_count` | 32 | Crossbar row count |
| `active_fraction` | 0.5 | Fraction of active discharge rows |
| `row_cap_penalty` | `"0.1ns"` | Fixed bitline capacitance cost |
| `discharge_const` | `"5ns"` | Discharge drive constant |

**Penalty formula applied per request:**
```
Δt = row_cap_penalty + discharge_const / (row_count × active_fraction)
```

---

### `configs/wired_or/run_single.py`
Top-level script that instantiates the gem5 system and runs the simulation.

**Usage:**
```bash
# CMOS baseline
./build/X86/gem5.opt configs/wired_or/run_single.py --arch CMOS

# Memristor IMC
./build/X86/gem5.opt configs/wired_or/run_single.py --arch MEMRISTOR

# AURORA wired-OR
./build/X86/gem5.opt configs/wired_or/run_single.py \
    --arch AURORA \
    --summary tm_training/tm_summary.json
```

**Command-line arguments:**

| Argument | Description |
|---|---|
| `--arch` | Architecture to simulate: `CMOS`, `MEMRISTOR`, or `AURORA` |
| `--summary` | Path to `tm_summary.json` (used by AURORA for `.npy` path and mean fraction) |
| `--active-fraction` | Override mean active fraction for AURORA (bypasses JSON) |

---

### `configs/wired_or/tm_workload.py`
`TMWorkload` class that wraps `PyTrafficGen` and configures the three-phase request sequence.

**Constructor parameters:**

| Parameter | Default | Effect |
|---|---|---|
| `num_clauses` | 100 | Clauses per class |
| `num_literals` | 1568 | Literals per clause |
| `num_samples` | 100 | Test samples to simulate |
| `parallel` | `False` | Set `True` for AURORA (clause-level requests) |

**Three phases:**
1. `createLinear()` — fires all requests at 5 ns intervals
2. `createIdle(500_000_000)` — 500 ms drain period
3. `createExit(0)` — terminates simulation

**Request count logic:**
```python
# CMOS / Memristor
total = num_clauses * num_literals * num_samples   # 15,680,000

# AURORA
total = num_clauses * num_samples                  # 10,000
```

---

## TM Training Script

### `tm_training/train_tm.py`

Trains a Tsetlin Machine on MNIST and extracts per-clause active fractions for use in the gem5 AURORA simulation.

**Dependencies:**
```bash
pip install tmu numpy
```

**What the script does:**

1. **Loads MNIST** — 60,000 training / 10,000 test samples, binarised at threshold 127
2. **Builds literal array** — each 784-pixel image → 1,568 Boolean literals (feature + complement)
3. **Trains TM** — using PyTsetlin (`tmu`) with configurable hyperparameters
4. **Evaluates on test set** — records test accuracy
5. **Extracts active fractions** — for each test sample × clause × class, computes:
   ```
   f_j(x) = |{k : a_jk = 1  AND  l_k(x) = 0}| / N_rows
   ```
6. **Saves outputs** — writes `tm_summary.json` and `tm_summary_per_request.npy`

**Hyperparameters (edit at top of script):**

| Parameter | Value used | Description |
|---|---|---|
| `NUM_CLAUSES` | 100 | Clauses per class |
| `T` | 400 | Threshold — controls feedback sensitivity |
| `s` | 3.9 | Specificity — controls literal inclusion |
| `EPOCHS` | 100 | Training epochs |
| `THRESHOLD` | 127 | Pixel binarisation threshold |
| `NUM_ROWS` | 256 | AURORA array row count (for fraction normalisation) |

**Run:**
```bash
cd tm_training
python train_tm.py
```

**Outputs:**

`tm_summary.json` — metadata file read by `run_single.py`:
```json
{
  "dataset": "MNIST_784",
  "num_clauses": 100,
  "num_literals": 1568,
  "threshold_T": 400,
  "specificity_s": 3.9,
  "epochs": 100,
  "test_accuracy": 69.0,
  "mean_active_fraction": 0.1372,
  "per_request_file": "tm_training/tm_summary_per_request.npy"
}
```

`tm_summary_per_request.npy` — float32 NumPy array of shape `(100000,)`:
- 100 clauses × 10 classes × 100 samples = 100,000 values
- Each value is the active fraction `f ∈ [0, 1]` for that clause/sample pair
- Loaded by `AuroraTM::loadPerRequestFractions()` at simulation startup

---

## Building and Running

### 1. Build gem5
```bash
cd gem5
scons build/X86/gem5.opt -j$(nproc)
```

### 2. Train the TM
```bash
cd tm_training
python train_tm.py
```

### 3. Run simulations
```bash
# From project root
./gem5/build/X86/gem5.opt configs/wired_or/run_single.py --arch CMOS
./gem5/build/X86/gem5.opt configs/wired_or/run_single.py --arch MEMRISTOR
./gem5/build/X86/gem5.opt configs/wired_or/run_single.py \
    --arch AURORA --summary tm_training/tm_summary.json
```

### 4. Read results
Stats are written to `m5out/stats.txt`. The script prints a summary:
```
CMOS      avg latency : 235737 ticks  (235.7 ns)   completed: 15019881 / 15680000
MEMRISTOR avg latency : 372536 ticks  (372.5 ns)   completed: 12946378 / 15680000
AURORA    avg latency :   4993 ticks  (  4.99 ns)  completed:      9999 / 10000
```

---

## Key Results

| Metric | CMOS DDR4 | Memristor IMC | AURORA |
|---|---|---|---|
| Avg latency | 235.7 ns | 372.5 ns | **4.99 ns** |
| Total requests | 15,680,000 | 15,680,000 | **10,000** |
| Completion rate | 95.8% | 82.6% | **99.99%** |
| Simulated time | 78.9 s | 78.9 s | **550 ms** |
| Throughput vs CMOS | 1× | 0.63× | **~73,977×** |

---

## Dependencies

| Tool | Version | Purpose |
|---|---|---|
| gem5 | v25.1 | Cycle-accurate simulation |
| Python | ≥ 3.9 | Config scripts and training |
| tmu (PyTsetlin) | latest | TM training |
| numpy | ≥ 1.21 | Active fraction extraction and storage |
| SCons | ≥ 4.0 | gem5 build system |

---

## Notes

- The Memristor arm is intentionally slower than CMOS — this is not a bug. The 200 ns bitline capacitance penalty dominates, demonstrating that in-memory advantage requires dedicated low-capacitance array design.
- AURORA's 550 ms simulated time includes a 500 ms idle drain period. Actual active simulation time is ~50 µs.
- The `+1` in traffic generator duration (`(accesses+1) × period`) is a deliberate off-by-one fix to prevent the last request from being dropped.
- PVT noise is seeded for reproducibility. The observed simulation mean of 4,993 ps is within 0.14% of the theoretical 5,000 ps.