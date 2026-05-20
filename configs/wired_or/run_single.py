import argparse
import os
import m5
from m5.objects import *

from tm_workload import TMWorkload


parser = argparse.ArgumentParser()
parser.add_argument("--arch", type=str, default="CMOS")
parser.add_argument("--active-fraction", type=float, default=None,
                    help="Active fraction for AURORA")
parser.add_argument("--summary",         type=str,   default=None,   # ← ADD THIS
                    help="Path to tm_summary.json from extract_ta_patterns.py")


args = parser.parse_args()
ARCH = args.arch.upper()

print(f"\nRunning architecture: {ARCH}")


# ---------------- System ----------------

system = System()
system.clk_domain = SrcClockDomain(
    clock="1GHz",
    voltage_domain=VoltageDomain()
)

system.mem_mode = "timing"
system.mem_ranges = [AddrRange("512MB")]


# ---------------- Bus ----------------

system.membus = SystemXBar()
system.system_port = system.membus.cpu_side_ports


# ---------------- Memory ----------------

system.mem_ctrl = MemCtrl()
system.mem_ctrl.dram = DDR4_2400_8x8()
system.mem_ctrl.dram.range = system.mem_ranges[0]
system.mem_ctrl.port = system.membus.mem_side_ports


# ---------- Architecture Configuration ----------

if ARCH == "CMOS":
    system.mem_ctrl.inference_mode = False

elif ARCH == "MEMRISTOR":
    system.mem_ctrl.inference_mode  = True
    system.mem_ctrl.row_count       = 256
    system.mem_ctrl.active_fraction = 0.2
    system.mem_ctrl.row_cap_penalty = "200ns"
    system.mem_ctrl.discharge_const = "500ns"

elif ARCH == "AURORA":

    import json

    # Priority: --summary arg > default tm_summary.json next to run_single.py
    if args.summary and os.path.exists(args.summary):
        summary_file = args.summary
    else:
        summary_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "tm_summary.json"
        )

    mean_fraction    = 0.5
    per_request_file = ""

    if os.path.exists(summary_file):
        with open(summary_file) as f:
            tm_data = json.load(f)

        mean_fraction = tm_data["mean_fraction"]
        print(f"  Summary file    : {summary_file}")
        print(f"  Dataset         : {tm_data.get('dataset', 'unknown')}")
        print(f"  TM accuracy     : {tm_data['accuracy']:.1f}%")
        print(f"  Clauses         : {tm_data.get('num_clauses', '?')}")
        print(f"  Literals        : {tm_data.get('num_literals', '?')}")
        print(f"  Mean fraction   : {mean_fraction:.4f}")

        # Check if per-request .npy file exists
        candidate = tm_data.get("per_request_file", "")

        # FIX: if candidate is a relative path, resolve it relative
        # to the directory where tm_summary.json lives, not cwd
        if candidate and not os.path.isabs(candidate):
            candidate = os.path.join(
                os.path.dirname(os.path.abspath(summary_file)),
                os.path.basename(candidate)
            )

        # print(f"  Candidate   : {candidate}")
        if candidate and os.path.exists(candidate):
            per_request_file = candidate
            print(f"  Per-request     : {candidate}")
            print(f"  Total fractions : {tm_data.get('per_request_total', '?')}")
            print(f"  Mode            : PER-REQUEST (physically accurate)")
        else:
            print(f"  Per-request     : not found — using mean fraction")
            print(f"  Mode            : MEAN + Gaussian (run with --per-request to improve)")

    else:
        print(f"  WARNING: no tm_summary.json found at {summary_file}")
        print(f"  Using default active_fraction = 0.5")
        print(f"  Run: python3 extract_ta_patterns.py --output tm_summary.json")

    # Override with explicit argument if provided
    if args.active_fraction is not None:
        mean_fraction = args.active_fraction
        per_request_file = ""   # explicit arg overrides file too
        print(f"  active_fraction overridden by --active-fraction {mean_fraction:.4f}")

    # Create banks — port wiring happens after tgen is created below
    NUM_BANKS = 1
    system.aurora_banks = [
        AuroraTM(
            latency          = "4ns",
            row_count        = 256,
            active_fraction  = mean_fraction,
            discharge_worst  = "2300ps",
            discharge_best   = "280ps",
            precharge_time   = "5ns",
            per_request_file = per_request_file,
        )
        for i in range(NUM_BANKS)
    ]

else:
    raise ValueError(f"Unknown architecture: {ARCH!r}")

# ---------------- Traffic Generator ----------------

system.tgen = PyTrafficGen()   # ← tgen created first

# FIX 3: wire AFTER tgen exists, use aurora_banks[0] not aurora
if ARCH == "AURORA":
    system.tgen.port = system.aurora_banks[0].port
else:
    system.monitor = CommMonitor()
    system.tgen.port = system.monitor.cpu_side_port
    system.monitor.mem_side_port = system.membus.cpu_side_ports


# ---------------- TM Workload ----------------

# FIX 4: remove hardcoded overrides — use tm_workload.py defaults
tm = TMWorkload(
    system.tgen,
    parallel=(ARCH == "AURORA")
)


# ---------------- Run ----------------

root = Root(full_system=False, system=system)
m5.instantiate()

system.tgen.start(tm.run())

print("Starting simulation...")
exit_event = m5.simulate()

print("Finished @ tick", m5.curTick(), "because", exit_event.getCause())

m5.stats.dump()


# ---------------- Results ----------------

stats_file = os.path.join(m5.options.outdir, "stats.txt")


def find_stat(stats_file, substring):
    """Return (full_key, value) for the first line whose key contains substring."""
    with open(stats_file, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and substring in parts[0]:
                try:
                    return parts[0], float(parts[1])
                except ValueError:
                    continue
    return None, None


def read_stat(stat_name):
    with open(stats_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith(stat_name):
                return float(line.split()[1])
    raise ValueError(f"Missing stat: {stat_name}")


if ARCH == "AURORA":
    try:
        # ── Discover the real stat key ───────────────────────────────────────
        # gem5 may name the bank "aurora_banks0" or "aurora_banks[0]" depending
        # on whether it was assigned as a SimObjectVector or a plain list.
        # We scan for any key containing "avgReadLatency" under aurora_banks.
        key_avg, avg_read_lat   = find_stat(stats_file, "avgReadLatency")
        key_tot, total_read_lat = find_stat(stats_file, "totalReadLatency")
        key_cnt, total_reads    = find_stat(stats_file, "totalReads")

        if avg_read_lat is None:
            # Nothing matched — dump all aurora lines to help debugging
            print("\nNo avgReadLatency stat found. Aurora-related stats in stats.txt:")
            with open(stats_file, "r", encoding="utf-8") as sf:
                for line in sf:
                    if "aurora" in line.lower():
                        print(" ", line.rstrip())
        else:
            print(f"\nAURORA avg read latency   : {avg_read_lat:.2f} ticks  [{key_avg}]")
            print(f"AURORA total read latency : {total_read_lat:.2f} ticks  [{key_tot}]")
            print(f"AURORA completed reads    : {int(total_reads)}  [{key_cnt}]")
    except Exception as exc:
        print("Could not parse aurora stats:", exc)
else:
    try:
        avg_read_lat   = read_stat("system.mem_ctrl.requestorReadAvgLat::tgen")
        total_read_lat = read_stat("system.mem_ctrl.requestorReadTotalLat::tgen")
        total_reads    = read_stat("system.mem_ctrl.requestorReadAccesses::tgen")

        print(f"\n{ARCH} avg read latency   : {avg_read_lat:.2f} ticks")
        print(f"{ARCH} total read latency : {total_read_lat:.2f} ticks")
        print(f"{ARCH} completed reads    : {int(total_reads)}")
    except Exception as exc:
        print("Could not parse memory latency stats from stats.txt:", exc)