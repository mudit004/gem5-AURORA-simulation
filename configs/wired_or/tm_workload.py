# class TMWorkload:
#     def __init__(self, tgen, num_clauses=100, num_literals=1568,
#                  num_samples=100, parallel=False):

#         self.tgen = tgen
#         self.num_clauses  = num_clauses
#         self.num_literals = num_literals
#         self.num_samples  = num_samples
#         self.parallel     = parallel

#     def run(self):
#         if self.parallel:
#             # AURORA: one request = full parallel clause evaluation
#             total_accesses = self.num_clauses * self.num_samples
#         else:
#             # CMOS / Memristor: one request per literal
#             total_accesses = (self.num_clauses *
#                               self.num_literals *
#                               self.num_samples)

#         self.total_accesses = total_accesses

#         print("TM Workload:")
#         print(f"  Clauses  : {self.num_clauses}")
#         print(f"  Literals : {self.num_literals}")
#         print(f"  Samples  : {self.num_samples}")
#         print(f"  Parallel : {self.parallel}")
#         print(f"  Total accesses: {total_accesses}")

#         block_size = 64
#         period     = 5_000
#         duration   = max(total_accesses * period, period)

#         yield self.tgen.createLinear(
#             duration, 0, 512 * 1024 * 1024,
#             block_size, period, period, 100, 0)

#         yield self.tgen.createExit(0)



class TMWorkload:
    def __init__(self, tgen, num_clauses=100, num_literals=1568,
                 num_samples=100, parallel=False):

        self.tgen = tgen
        self.num_clauses  = num_clauses
        self.num_literals = num_literals
        self.num_samples  = num_samples
        self.parallel     = parallel

    def run(self):
        if self.parallel:
            # AURORA: one request = full parallel clause evaluation
            total_accesses = self.num_clauses * self.num_samples
        else:
            # CMOS / Memristor: one request per literal
            total_accesses = (self.num_clauses *
                              self.num_literals *
                              self.num_samples)

        self.total_accesses = total_accesses

        print("TM Workload:")
        print(f"  Clauses  : {self.num_clauses}")
        print(f"  Literals : {self.num_literals}")
        print(f"  Samples  : {self.num_samples}")
        print(f"  Parallel : {self.parallel}")
        print(f"  Total accesses: {total_accesses}")

        block_size = 64
        period     = 5_000
        # +1 period of slack: createLinear sends the i-th request at tick
        # i*period, so the last request lands at total_accesses*period.
        # Without the +1 that tick == duration and the request is not fired.
        duration   = max((total_accesses ) * period, period)

        # Worst-case DDR4 queue drain: ~500 in-flight requests x ~500ns each
        # = 250us = 250_000_000 ticks at 1ps/tick.
        # Memristor is slower (372k ticks avg) so we use 500M ticks to be safe.
        # AURORA drains instantly (5k ticks avg) so this barely adds runtime.
        drain_ticks = 500_000_000

        print(f"  Drain period  : {drain_ticks // 1_000_000}ms "
              f"({drain_ticks} ticks)")

        yield self.tgen.createLinear(
            duration, 0, 512 * 1024 * 1024,
            block_size, period, period, 100, 0)

        # Idle state: no new requests, lets the memory queue fully drain
        yield self.tgen.createIdle(drain_ticks)

        yield self.tgen.createExit(0)