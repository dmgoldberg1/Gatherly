#include "controllers/api_controller.h"
#include "db/postgres_store.h"
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
        auto service = std::make_shared<gatherly::GatherlyService>(postgres_store);
        std::cout << "PostgreSQL store is enabled.\n";
#endif

        auto controller = std::make_shared<gatherly::ApiController>(*service, "static");
        drogon::app().registerHandlerViaRegex(
            "/.*",
            [controller](const drogon::HttpRequestPtr& request, std::function<void(const drogon::HttpResponsePtr&)>&& callback) {
                callback(ToDrogonResponse(controller->Handle(ToGatherlyRequest(request))));
            },
            {drogon::Get, drogon::Post, drogon::Patch, drogon::Delete, drogon::Options}
        );

        std::cout << "Gatherly Drogon server is running at http://0.0.0.0:" << port << '\n';
        drogon::app().addListener("0.0.0.0", port).run();
    } catch (const std::exception& error) {
        std::cerr << "Fatal error: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
