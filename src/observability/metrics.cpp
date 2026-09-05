#include "observability/metrics.h"
#include <algorithm>
#include <set>
#include <vector>

namespace gatherly {
namespace {
constexpr std::array<double, 9> kBuckets{.005,.01,.025,.05,.1,.25,.5,1,5};
std::string Route(const std::string& path) {
    std::istringstream input(path);
    std::string part, normalized;
    while (std::getline(input, part, '/')) {
        if (part.empty()) continue;
        if (std::all_of(part.begin(), part.end(), [](char c) { return c >= '0' && c <= '9'; })) part = ":id";
        normalized += "/" + part;
    }
    static const std::set<std::string> routes{
        "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/me", "/api/v1/me/participations", "/api/v1/me/events",
        "/api/v1/users", "/api/v1/users/:id", "/api/v1/users/:id/follow", "/api/v1/users/:id/followers", "/api/v1/users/:id/following",
        "/api/v1/events", "/api/v1/events/:id", "/api/v1/events/:id/publish", "/api/v1/events/:id/cancel",
        "/api/v1/events/:id/participants/me", "/api/v1/events/:id/participants",
        "/api/v1/feed", "/api/v1/notifications", "/api/v1/notifications/:id/read"};
    if (routes.contains(normalized)) return normalized;
    if (path == "/" || path == "/app.js" || path == "/styles.css") return "static";
    return "unmatched";
}
}
void Metrics::ObserveHttp(const std::string& method, const std::string& path, int status, double seconds) {
    const auto labels = "method=\"" + method + "\",route=\"" + Route(path) + "\",status=\"" + std::to_string(status) + "\"";
    std::lock_guard lock(mutex_);
    auto& histogram = http_[labels];
    ++histogram.count; histogram.sum += seconds;
    for (size_t i = 0; i < kBuckets.size(); ++i) if (seconds <= kBuckets[i]) ++histogram.buckets[i];
}
std::string Metrics::Render() {
    std::ostringstream output;
    output << "# TYPE http_requests_total counter\n# TYPE http_request_duration_seconds histogram\n";
    {
        std::lock_guard lock(mutex_);
        for (const auto& [labels, h] : http_) {
            output << "http_requests_total{" << labels << "} " << h.count << '\n';
            output << "http_request_duration_seconds_count{" << labels << "} " << h.count << '\n';
            output << "http_request_duration_seconds_sum{" << labels << "} " << h.sum << '\n';
            for (size_t i = 0; i < kBuckets.size(); ++i) {
                output << "http_request_duration_seconds_bucket{" << labels << ",le=\"" << kBuckets[i] << "\"} " << h.buckets[i] << '\n';
            }
            output << "http_request_duration_seconds_bucket{" << labels << ",le=\"+Inf\"} " << h.count << '\n';
        }
    }
    output << "# TYPE http_requests_in_flight gauge\nhttp_requests_in_flight " << in_flight << '\n';
    output << "# TYPE notification_delivery_total counter\nnotification_delivery_total " << delivery_success << '\n';
    output << "# TYPE notification_delivery_failures_total counter\nnotification_delivery_failures_total " << delivery_failure << '\n';
    output << "# TYPE notification_processing_errors_total counter\nnotification_processing_errors_total " << processing_errors << '\n';
    output << "# TYPE notification_processing_duration_seconds summary\nnotification_processing_duration_seconds_count " << processing_count << '\n';
    output << "notification_processing_duration_seconds_sum " << processing_seconds.load() << '\n';
    return output.str();
}
}
