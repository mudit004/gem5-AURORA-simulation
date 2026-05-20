#include "mem/aurora_tm.hh"

#include <fstream>
#include <random>

#include "base/trace.hh"
#include "sim/eventq.hh"

namespace gem5
{

// ── Stats ─────────────────────────────────────────────────────────────────

AuroraTM::AuroraStats::AuroraStats(AuroraTM *parent)
    : statistics::Group(parent),
      ADD_STAT(totalReads,       "Total read requests completed"),
      ADD_STAT(totalReadLatency, "Total read latency (Ticks)"),
      ADD_STAT(avgReadLatency,   "Average read latency (Ticks/read)")
{
    avgReadLatency = totalReadLatency / totalReads;
}

// ── Constructor ───────────────────────────────────────────────────────────

AuroraTM::AuroraTM(const AuroraTMParams &p)
    : SimObject(p),
      port(name() + ".port", this),
      latency(p.latency),
      activeFraction(p.active_fraction),
      dischargeWorst(p.discharge_worst),
      dischargeBest(p.discharge_best),
      prechargeTime(p.precharge_time),
      rowCount(p.row_count),
      fractionIndex(0),
      stats(this)
{
    if (!p.per_request_file.empty()) {
        loadPerRequestFractions(p.per_request_file);
    }
}

// ── Port ──────────────────────────────────────────────────────────────────

Port&
AuroraTM::getPort(const std::string &if_name, PortID idx)
{
    if (if_name == "port")
        return port;
    return SimObject::getPort(if_name, idx);
}

// ── Load per-request fractions from .npy ─────────────────────────────────

void
AuroraTM::loadPerRequestFractions(const std::string &path)
{
    std::ifstream f(path, std::ios::binary);
    if (!f.is_open()) {
        warn("AuroraTM: could not open %s — using mean fraction\n",
             path.c_str());
        return;
    }

    // .npy format:
    //   bytes 0-5  : magic "\x93NUMPY"
    //   byte  6    : major version
    //   byte  7    : minor version
    //   bytes 8-9  : header_len (uint16, little-endian) for version 1.x
    //   bytes 10.. : ASCII header dict of length header_len
    //   then       : raw float32 data

    // Read and verify magic
    char magic[6];
    f.read(magic, 6);
    if (f.gcount() != 6 ||
        magic[0] != '\x93' || magic[1] != 'N' || magic[2] != 'U' ||
        magic[3] != 'M'    || magic[4] != 'P' || magic[5] != 'Y') {
        warn("AuroraTM: %s is not a valid .npy file\n", path.c_str());
        return;
    }

    // Read version
    uint8_t major, minor;
    f.read(reinterpret_cast<char*>(&major), 1);
    f.read(reinterpret_cast<char*>(&minor), 1);

    // Read header length
    uint32_t header_len = 0;
    if (major == 1) {
        // Version 1.x: header_len is uint16 little-endian
        uint16_t hlen;
        f.read(reinterpret_cast<char*>(&hlen), 2);
        header_len = hlen;
    } else if (major == 2) {
        // Version 2.x: header_len is uint32 little-endian
        f.read(reinterpret_cast<char*>(&header_len), 4);
    } else {
        warn("AuroraTM: unsupported .npy version %d.%d\n", major, minor);
        return;
    }

    // Read the ASCII header dict — we need it to detect dtype
    std::string header_dict(header_len, '\0');
    f.read(&header_dict[0], header_len);
    if ((size_t)f.gcount() != header_len) {
        warn("AuroraTM: could not read header dict from %s\n", path.c_str());
        return;
    }

    // Detect dtype: look for 'f4' (float32) or 'f8' (float64) in the dict.
    // Numpy writes e.g. {'descr': '<f4', ...} or {'descr': '<f8', ...}
    bool is_float64 = (header_dict.find("f8") != std::string::npos);
    bool is_float32 = (header_dict.find("f4") != std::string::npos);

    if (!is_float32 && !is_float64) {
        warn("AuroraTM: unrecognised dtype in header of %s — "
             "expected float32 (f4) or float64 (f8). Header: %.80s\n",
             path.c_str(), header_dict.c_str());
        return;
    }

    if (is_float64) {
        warn("AuroraTM: %s contains float64 data — reading as float64 "
             "and casting to float32. "
             "Re-save with arr.astype(np.float32) to avoid this.\n",
             path.c_str());
        double val64;
        while (f.read(reinterpret_cast<char*>(&val64), sizeof(double))) {
            perRequestFractions.push_back(static_cast<float>(val64));
        }
    } else {
        // float32 — nominal path
        float val32;
        while (f.read(reinterpret_cast<char*>(&val32), sizeof(float))) {
            perRequestFractions.push_back(val32);
        }
    }

    // Debug: print first few values so we can verify loading in the sim log
    // inform("AuroraTM: loaded %lu per-request fractions from %s "
    //        "(dtype=%s)\n",
    //        (unsigned long)perRequestFractions.size(), path.c_str(),
    //        is_float64 ? "float64->float32" : "float32");

    if (!perRequestFractions.empty()) {
        size_t preview = std::min(perRequestFractions.size(), (size_t)5);
        std::string dbg = "AuroraTM: first values: ";
        for (size_t i = 0; i < preview; ++i) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%.4f ", perRequestFractions[i]);
            dbg += buf;
        }
        // inform("%s\n", dbg.c_str());
    } else {
        warn("AuroraTM: perRequestFractions is EMPTY after reading %s — "
             "check file integrity\n", path.c_str());
    }
}

// ── Active fraction sampling ──────────────────────────────────────────────

double
AuroraTM::sampleActiveFraction()
{
    // If per-request fractions are loaded use them in order,
    // wrapping around if we run out.
    if (!perRequestFractions.empty()) {
        double f = perRequestFractions[fractionIndex % perRequestFractions.size()];
        fractionIndex++;
        return f;
    }

    // Fallback: Gaussian around mean matching paper Monte Carlo (Fig 5, σ=9%)
    static std::default_random_engine rng(42);
    static std::normal_distribution<double> dist(0.0, 0.09);

    double f = activeFraction + dist(rng);
    if (f < 0.0) f = 0.0;
    if (f > 1.0) f = 1.0;
    return f;
}

// ── Discharge latency lookup (paper Fig 4a, post-layout 1.2V 27°C) ───────

Tick
AuroraTM::computeDischargeLatency(double f, int rows)
{
    if (rows <= 32) {
        if (f <= 0.0)  return 600;
        if (f <= 0.25) return 420;
        if (f <= 0.50) return 370;
        if (f <= 0.75) return 330;
        return 280;
    } else if (rows <= 256) {
        if (f <= 0.0)  return 2300;
        if (f <= 0.25) return 380;
        if (f <= 0.50) return 320;
        if (f <= 0.75) return 295;
        return 280;
    } else {
        // 512 rows: paper says fails at 100MHz
        return 10000;
    }
}

// ── Precharge latency (paper Fig 4b) ─────────────────────────────────────

Tick
AuroraTM::computePrechargeLatency(double f, int rows)
{
    if (rows >= 512 && f >= 0.9)
        return 8000;   // fails at 512 rows all-active
    return 5000;       // constant 5ns for 32 and 256 rows
}

// ── PVT variation (paper Fig 5, 256×32 at 1.2V: σ=9%) ───────────────────

Tick
AuroraTM::addPVTVariation(Tick base_latency)
{
    static std::default_random_engine rng(42);
    static std::normal_distribution<double> dist(0.0, 0.09);

    double variation = 1.0 + dist(rng);
    if (variation < 0.0) variation = 0.0;
    return static_cast<Tick>(base_latency * variation);
}

// ── Request handler ───────────────────────────────────────────────────────

bool
AuroraTM::CPUPort::recvTimingReq(PacketPtr pkt)
{
    pkt->makeResponse();

    double f       = owner->sampleActiveFraction();
    Tick discharge = owner->computeDischargeLatency(f, owner->rowCount);
    Tick precharge = owner->computePrechargeLatency(f, owner->rowCount);
    Tick total     = std::max(precharge, discharge);
    total          = owner->addPVTVariation(total);

    ++owner->stats.totalReads;
    owner->stats.totalReadLatency += total;

    owner->schedule(
        new EventFunctionWrapper(
            [this, pkt]() { sendTimingResp(pkt); },
            owner->name() + ".auroraResp", true),
        curTick() + total);

    return true;
}

} // namespace gem5