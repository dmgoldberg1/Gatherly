#pragma once

#include "http/http_types.h"
#include "utils/json.h"
#include "services/gatherly_service.h"

#include <string>
#include <vector>

namespace gatherly {

class ApiController {
public:
    explicit ApiController(GatherlyService& service, std::string static_dir);
    HttpResponse Handle(const HttpRequest& request);

protected:
    HttpResponse HandleApi(const HttpRequest& request);
    HttpResponse HandleStatic(const HttpRequest& request) const;
    HttpResponse JsonResponse(int status, const Json& json) const;
    HttpResponse TextResponse(int status, const std::string& text, const std::string& content_type) const;

    Json ReadBody(const HttpRequest& request) const;
    int64_t RequireAuth(const HttpRequest& request) const;
    PageRequest PageFromRequest(const HttpRequest& request) const;
    std::string QueryParam(const HttpRequest& request, const std::string& name, const std::string& fallback = "") const;
    std::vector<std::string> SplitPath(const std::string& path) const;
    EventCommand EventCommandFromJson(const Json& json, const std::optional<Event>& fallback = std::nullopt) const;

    GatherlyService& service_;
    std::string static_dir_;
};

Json ToJson(const User& user);
Json ToJson(const Event& event);
Json ToJson(const Participation& participation);
Json ToJson(const Notification& notification);

} // namespace gatherly
