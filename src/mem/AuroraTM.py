from m5.params import *
from m5.SimObject import SimObject

class AuroraTM(SimObject):
    type      = 'AuroraTM'
    cxx_class = 'gem5::AuroraTM'
    cxx_header = "mem/aurora_tm.hh"

    port             = ResponsePort("Aurora TM port")
    latency          = Param.Latency("4ns",    "Clause evaluation latency")
    active_fraction  = Param.Float(0.5,        "Mean active fraction (fallback)")
    discharge_worst  = Param.Latency("2300ps", "Worst case discharge")
    discharge_best   = Param.Latency("280ps",  "Best case discharge")
    precharge_time   = Param.Latency("5ns",    "RBL precharge time")
    row_count        = Param.Int(256,          "Number of rows")
    per_request_file = Param.String("",        "Path to .npy with per-request fractions")  # ← ADD