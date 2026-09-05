#include "controllers/api_controller.h"
#include "db/postgres_store.h"
#include "http/worker_pool.h"
#include "observability/metrics.h"
#include <chrono>
#include "services/gatherly_service.h"

#include <drogon/drogon.h>

#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <string>

namespace {

std::string MethodToString(drogon::HttpMethod method) {
    switch (method) {
        case drogon::Get:
            return "GET";
        case drogon::Post:
            return "POST";
        case drogon::Put:
            return "PUT";
        case drogon::Delete:
            return "DELETE";
        case drogon::Patch:
            return "PATCH";
        case drogon::Options:
            return "OPTIONS";
        default:
            return "GET";
    }
}

gatherly::HttpRequest ToGatherlyRequest(const drogon::HttpRequestPtr& request) {
    gatherly::HttpRequest result;
    result.method = MethodToString(request->method());
    result.path = request->path();
    result.query = request->query();
    result.body = std::string(request->getBody());
    result.headers["Authorization"] = request->getHeader("Authorization");
    result.headers["Content-Type"] = request->getHeader("Content-Type");
    return result;
}

drogon::HttpResponsePtr ToDrogonResponse(const gatherly::HttpResponse& response) {
    auto result = drogon::HttpResponse::newHttpResponse();
    result->setStatusCode(static_cast<drogon::HttpStatusCode>(response.status));
    result->setContentTypeString(response.content_type);
    result->setBody(response.body);
    for (const auto& [key, value] : response.headers) {
        result->addHeader(key, value);
    }
    result->addHeader("Access-Control-Allow-Origin", "*");
    result->addHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
    result->addHeader("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS");
    return result;
}

} // namespace

int main() {
    try {
        const char* port_env = std::getenv("GATHERLY_PORT");
        const int port = port_env == nullptr ? 8080 : std::atoi(port_env);

#ifndef GATHERLY_HAS_POSTGRES_STORE
        throw std::runtime_error("Gatherly runtime requires PostgreSQL support.");
#else
        const char* database_url = std::getenv("GATHERLY_DATABASE_URL");
        if (database_url == nullptr || std::string(database_url).empty()) {
            throw std::runtime_error("GATHERLY_DATABASE_URL is required.");
        }
        auto postgres_store = gatherly::CreatePostgresStore(database_url);
        const char* auth_secret_env = std::getenv("GATHERLY_AUTH_SECRET");
        std::string auth_secret;
        if (auth_secret_env == nullptr || std::string(auth_secret_env).empty()) {
            auth_secret = gatherly::GenerateAuthSecret();
            std::cout << "Using a temporary auth secret; sessions will expire on restart.\n";
        } else {
            auth_secret = auth_secret_env;
        }
        auto service = std::make_shared<gatherly::GatherlyService>(postgres_store, std::move(auth_secret));
        std::cout << "PostgreSQL store is enabled.\n";
#endif

        auto controller = std::make_shared<gatherly::ApiController>(*service, "static");
        auto workers = std::make_shared<gatherly::WorkerPool>(4);
        auto probes = std::make_shared<gatherly::WorkerPool>(2);
        const std::string database_connection = database_url;
        drogon::app().registerHandlerViaRegex(
            "/.*",
            [controller, service, workers, probes, database_connection](const drogon::HttpRequestPtr& request, std::function<void(const drogon::HttpResponsePtr&)>&& callback) {
                const auto path = request->path();
                if (path == "/health" && request->method() == drogon::Get) {
                    callback(ToDrogonResponse({200, "application/json", "{\"status\":\"ok\"}", {}}));
                    return;
                }
                const bool probe = (path == "/ready" || path == "/metrics") && request->method() == drogon::Get;
                auto& metrics = gatherly::Metrics::Instance();
                if (!probe) ++metrics.in_flight;
                auto started = std::chrono::steady_clock::now();
                // Queued requests own the service until they finish, including shutdown.
                auto task = [controller, service, request, callback, database_connection, probe, started] {
                    gatherly::HttpResponse response;
                    if (probe) {
                        if (request->path() == "/ready") {
                            const bool ready = gatherly::IsPostgresReady(database_connection);
                            response = {ready ? 200 : 503, "application/json", ready ? "{\"status\":\"ready\"}" : "{\"status\":\"unavailable\"}", {}};
                        } else {
                            response = {200, "text/plain; version=0.0.4", gatherly::Metrics::Instance().Render() + gatherly::PostgresMetrics(database_connection), {}};
                        }
                    } else {
                        response = controller->Handle(ToGatherlyRequest(request));
                        auto& metrics = gatherly::Metrics::Instance();
                        --metrics.in_flight;
                        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
                        metrics.ObserveHttp(MethodToString(request->method()), request->path(), response.status, elapsed);
                        LOG_INFO << "HTTP method=" << MethodToString(request->method()) << " status=" << response.status << " duration_seconds=" << elapsed;
                    }
                    callback(ToDrogonResponse(response));
                };
                if (!(probe ? probes : workers)->Submit(std::move(task))) {
                    if (!probe) {
                        --metrics.in_flight;
                        metrics.ObserveHttp(MethodToString(request->method()), path, 503, 0);
                    }
                    callback(ToDrogonResponse({503, "application/json", "{\"error\":\"server busy\"}", {}}));
                }
            },
            {drogon::Get, drogon::Post, drogon::Patch, drogon::Delete, drogon::Options}
        );
        drogon::app().setClientMaxBodySize(64 * 1024).setThreadNum(2);

        std::cout << "Gatherly Drogon server is running at http://0.0.0.0:" << port << '\n';
        drogon::app().addListener("0.0.0.0", port).run();
    } catch (const std::exception& error) {
        std::cerr << "Fatal error: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
