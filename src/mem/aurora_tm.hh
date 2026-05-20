#ifndef __MEM_AURORA_TM_HH__
#define __MEM_AURORA_TM_HH__

#include <vector>
#include <string>

#include "base/statistics.hh"
#include "mem/packet.hh"
#include "mem/port.hh"
#include "params/AuroraTM.hh"
#include "sim/sim_object.hh"

namespace gem5
{

class AuroraTM : public SimObject
{
  private:

    class CPUPort : public ResponsePort
    {
      private:
        AuroraTM *owner;

      public:
        CPUPort(const std::string& name, AuroraTM *owner)
            : ResponsePort(name), owner(owner) {}

      protected:
        bool recvTimingReq(PacketPtr pkt) override;
        void recvRespRetry() override {}

        AddrRangeList getAddrRanges() const override
        {
            return AddrRangeList();
        }

        Tick recvAtomic(PacketPtr pkt) override
        {
            pkt->makeResponse();
            return owner->latency;
        }

        void recvFunctional(PacketPtr pkt) override
        {
            pkt->makeResponse();
        }
    };

    CPUPort port;

  public:

    // Parameters
    const Tick   latency;
    const double activeFraction;
    const Tick   dischargeWorst;
    const Tick   dischargeBest;
    const Tick   prechargeTime;
    const int    rowCount;

    // Per-request fraction support
    std::vector<float> perRequestFractions;
    mutable size_t     fractionIndex;

    // Stats
    struct AuroraStats : public statistics::Group
    {
        AuroraStats(AuroraTM *parent);
        statistics::Scalar  totalReads;
        statistics::Scalar  totalReadLatency;
        statistics::Formula avgReadLatency;
    } stats;

    // Constructor and port
    AuroraTM(const AuroraTMParams &p);
    Port& getPort(const std::string &if_name,
                  PortID idx = InvalidPortID) override;

    // Methods — NO AuroraTM:: prefix inside the class declaration
    void  loadPerRequestFractions(const std::string &path);
    double sampleActiveFraction();
    Tick  computeDischargeLatency(double f, int rows);
    Tick  computePrechargeLatency(double f, int rows);
    Tick  addPVTVariation(Tick base_latency);
};

} // namespace gem5

#endif // __MEM_AURORA_TM_HH__