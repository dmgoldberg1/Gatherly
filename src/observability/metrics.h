#pragma once
#include <array>
#include <atomic>
#include <map>
#include <mutex>
#include <sstream>
#include <string>

namespace gatherly {

// A fixed set of counters and cumulative histogram buckets; no user identifiers.
class Metrics {
public:
    static Metrics& Instance() { static Metrics instance; return instance; }
    void ObserveHttp(const std::string& method, const std::string& path, int status, double seconds);
    std::string Render();
    std::atomic<int64_t> in_flight{0};
    std::atomic<uint64_t> delivery_success{0}, delivery_failure{0}, processing_errors{0};
    std::atomic<uint64_t> processing_count{0};
    std::atomic<double> processing_seconds{0};
private:
    struct Histogram {
        uint64_t count = 0;
        double sum = 0;
        std::array<uint64_t, 9> buckets{};
    };
    std::mutex mutex_;
    std::map<std::string, Histogram> http_;
};
}
