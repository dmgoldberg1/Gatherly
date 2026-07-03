#include "controllers/api_controller.h"

#include <fstream>
#include <sstream>

namespace gatherly {

namespace {

Json ToArray(const std::vector<User>& values) {
    Json::Array array;
    for (const auto& value : values) {
        array.push_back(ToJson(value));
    }
    return array;
}

Json ToArray(const std::vector<Event>& values) {
    Json::Array array;
    for (const auto& value : values) {
        array.push_back(ToJson(value));
    }
    return array;
}

Json ToArray(const std::vector<Participation>& values) {
    Json::Array array;
    for (const auto& value : values) {
        array.push_back(ToJson(value));
    }
    return array;
}

Json ToArray(const std::vector<Notification>& values) {
    Json::Array array;
    for (const auto& value : values) {
        array.push_back(ToJson(value));
    }
    return array;
}

Json PageJson(const Json& items, PageRequest page) {
    const int64_t count = items.IsArray() ? static_cast<int64_t>(items.AsArray().size()) : 0;
    return Json::Object{{"items", items}, {"count", count}, {"limit", page.limit}, {"offset", page.offset}};
}

Json ParticipationWithEventJson(const Participation& participation, const Event& event) {
    return Json::Object{{"participation", ToJson(participation)}, {"event", ToJson(event)}};
}

std::string ContentTypeForPath(const std::string& path) {
    if (path.ends_with(".css")) {
        return "text/css";
    }
    if (path.ends_with(".js")) {
        return "application/javascript";
    }
    if (path.ends_with(".html")) {
        return "text/html";
    }
    return "text/plain";
}

int64_t ParseId(const std::string& value) {
    return std::stoll(value);
}

std::string StripBearer(std::string value) {
    const std::string prefix = "Bearer ";
    if (value.starts_with(prefix)) {
        return value.substr(prefix.size());
    }
    return value;
}

} // namespace

ApiController::ApiController(GatherlyService& service, std::string static_dir)
    : service_(service),
      static_dir_(std::move(static_dir)) {}

HttpResponse ApiController::Handle(const HttpRequest& request) {
    if (request.method == "OPTIONS") {
        return TextResponse(200, "", "text/plain");
    }
    try {
        if (request.path.starts_with("/api/")) {
            return HandleApi(request);
        }
        return HandleStatic(request);
    } catch (const ServiceError& error) {
        return JsonResponse(error.StatusCode(), ErrorJson(error.what()));
    } catch (const std::exception& error) {
        return JsonResponse(400, ErrorJson(error.what()));
    }
}

HttpResponse ApiController::HandleApi(const HttpRequest& request) {
    const std::vector<std::string> parts = SplitPath(request.path);
    if (parts.size() < 3 || parts[0] != "api" || parts[1] != "v1") {
        return JsonResponse(404, ErrorJson("endpoint not found."));
    }

    if (request.method == "POST" && request.path == "/api/v1/auth/register") {
        const Json body = ReadBody(request);
        RegisterUserCommand command;
        command.username = body.At("username").AsString();
        command.email = body.At("email").AsString();
        command.password = body.At("password").AsString();
        command.bio = body.At("bio").AsString();
        command.avatar_url = body.At("avatar_url").AsString();
        return JsonResponse(201, ToJson(service_.RegisterUser(command)));
    }

    if (request.method == "POST" && request.path == "/api/v1/auth/login") {
        const Json body = ReadBody(request);
        const LoginResult result = service_.Login(
            body.At("username").AsString(body.At("email").AsString()),
            body.At("password").AsString()
        );
        return JsonResponse(200, Json::Object{{"user", ToJson(result.user)}, {"token", result.token}});
    }

    if (request.method == "GET" && request.path == "/api/v1/me") {
        return JsonResponse(200, ToJson(service_.GetUser(RequireAuth(request))));
    }

    if (request.method == "GET" && request.path == "/api/v1/me/participations") {
        const int64_t user_id = RequireAuth(request);
        const PageRequest page = PageFromRequest(request);
        Json::Array items;
        for (const auto& participation : service_.ListMyParticipations(user_id, page)) {
            items.push_back(ParticipationWithEventJson(participation, service_.GetEvent(user_id, participation.event_id)));
        }
        return JsonResponse(200, PageJson(items, page));
    }

    if (request.method == "GET" && request.path == "/api/v1/users") {
        const PageRequest page = PageFromRequest(request);
        return JsonResponse(200, PageJson(ToArray(service_.ListUsers(page)), page));
    }

    if (parts.size() == 4 && parts[2] == "users" && request.method == "GET") {
        return JsonResponse(200, ToJson(service_.GetUser(ParseId(parts[3]))));
    }

    if (parts.size() == 5 && parts[2] == "users" && parts[4] == "follow" && request.method == "POST") {
        service_.FollowUser(RequireAuth(request), ParseId(parts[3]));
        return JsonResponse(200, OkJson("Подписка создана."));
    }

    if (parts.size() == 5 && parts[2] == "users" && parts[4] == "follow" && request.method == "DELETE") {
        service_.UnfollowUser(RequireAuth(request), ParseId(parts[3]));
        return JsonResponse(200, OkJson("Подписка удалена."));
    }

    if (parts.size() == 5 && parts[2] == "users" && parts[4] == "followers" && request.method == "GET") {
        return JsonResponse(200, ToArray(service_.ListFollowers(ParseId(parts[3]))));
    }

    if (parts.size() == 5 && parts[2] == "users" && parts[4] == "following" && request.method == "GET") {
        return JsonResponse(200, ToArray(service_.ListFollowing(ParseId(parts[3]))));
    }

    if (request.method == "POST" && request.path == "/api/v1/events") {
        return JsonResponse(201, ToJson(service_.CreateEvent(RequireAuth(request), EventCommandFromJson(ReadBody(request)))));
    }

    if (request.method == "GET" && request.path == "/api/v1/events") {
        const PageRequest page = PageFromRequest(request);
        return JsonResponse(200, PageJson(ToArray(service_.ListPublicEvents(page)), page));
    }

    if (request.method == "GET" && request.path == "/api/v1/feed") {
        const PageRequest page = PageFromRequest(request);
        return JsonResponse(200, PageJson(ToArray(service_.ListFeed(RequireAuth(request), page)), page));
    }

    if (parts.size() == 4 && parts[2] == "events" && request.method == "GET") {
        return JsonResponse(200, ToJson(service_.GetEvent(RequireAuth(request), ParseId(parts[3]))));
    }

    if (parts.size() == 4 && parts[2] == "events" && request.method == "PATCH") {
        const int64_t user_id = RequireAuth(request);
        const int64_t event_id = ParseId(parts[3]);
        const Event current = service_.GetEvent(user_id, event_id);
        return JsonResponse(200, ToJson(service_.UpdateEvent(user_id, event_id, EventCommandFromJson(ReadBody(request), current))));
    }

    if (parts.size() == 5 && parts[2] == "events" && parts[4] == "publish" && request.method == "POST") {
        const int64_t user_id = RequireAuth(request);
        const int64_t event_id = ParseId(parts[3]);
        if (!request.body.empty()) {
            const Json body = ReadBody(request);
            if (body.Contains("notify_user_ids")) {
                std::vector<int64_t> notify_user_ids;
                for (const auto& item : body.At("notify_user_ids").AsArray()) {
                    notify_user_ids.push_back(item.AsInt());
                }
                return JsonResponse(200, ToJson(service_.PublishEventForUsers(user_id, event_id, notify_user_ids)));
            }
        }
        return JsonResponse(200, ToJson(service_.PublishEvent(user_id, event_id)));
    }

    if (parts.size() == 5 && parts[2] == "events" && parts[4] == "cancel" && request.method == "POST") {
        return JsonResponse(200, ToJson(service_.CancelEvent(RequireAuth(request), ParseId(parts[3]))));
    }

    if (parts.size() == 5 && parts[2] == "events" && parts[4] == "participants" && request.method == "POST") {
        return JsonResponse(201, ToJson(service_.JoinEvent(RequireAuth(request), ParseId(parts[3]))));
    }

    if (parts.size() == 6 && parts[2] == "events" && parts[4] == "participants" && parts[5] == "me" && request.method == "PATCH") {
        const Json body = ReadBody(request);
        return JsonResponse(200, ToJson(service_.SetMyParticipationStatus(
            RequireAuth(request),
            ParseId(parts[3]),
            ParticipationStatusFromString(body.At("status").AsString("GOING"))
        )));
    }

    if (parts.size() == 6 && parts[2] == "events" && parts[4] == "participants" && parts[5] == "me" && request.method == "DELETE") {
        service_.LeaveEvent(RequireAuth(request), ParseId(parts[3]));
        return JsonResponse(200, OkJson("Пользователь вышел из события."));
    }

    if (parts.size() == 5 && parts[2] == "events" && parts[4] == "participants" && request.method == "GET") {
        return JsonResponse(200, ToArray(service_.ListParticipants(RequireAuth(request), ParseId(parts[3]))));
    }

    if (request.method == "GET" && request.path == "/api/v1/notifications") {
        const PageRequest page = PageFromRequest(request);
        return JsonResponse(200, PageJson(ToArray(service_.ListNotifications(RequireAuth(request), page)), page));
    }

    if (parts.size() == 5 && parts[2] == "notifications" && parts[4] == "read" && request.method == "PATCH") {
        return JsonResponse(200, ToJson(service_.MarkNotificationRead(RequireAuth(request), ParseId(parts[3]))));
    }

    return JsonResponse(404, ErrorJson("endpoint not found."));
}

HttpResponse ApiController::HandleStatic(const HttpRequest& request) const {
    std::string path = request.path == "/" ? "/index.html" : request.path;
    if (path.find("..") != std::string::npos) {
        return TextResponse(403, "forbidden", "text/plain");
    }
    const std::string filename = static_dir_ + path;
    std::ifstream file(filename, std::ios::binary);
    if (!file.is_open()) {
        return TextResponse(404, "not found", "text/plain");
    }
    std::ostringstream output;
    output << file.rdbuf();
    return TextResponse(200, output.str(), ContentTypeForPath(filename));
}

HttpResponse ApiController::JsonResponse(int status, const Json& json) const {
    HttpResponse response;
    response.status = status;
    response.content_type = "application/json";
    response.body = json.Dump();
    return response;
}

HttpResponse ApiController::TextResponse(int status, const std::string& text, const std::string& content_type) const {
    HttpResponse response;
    response.status = status;
    response.content_type = content_type;
    response.body = text;
    return response;
}

Json ApiController::ReadBody(const HttpRequest& request) const {
    return Json::Parse(request.body);
}

int64_t ApiController::RequireAuth(const HttpRequest& request) const {
    auto it = request.headers.find("Authorization");
    if (it == request.headers.end()) {
        throw ServiceError(401, "Authorization header is required.");
    }
    const std::optional<int64_t> user_id = service_.Authenticate(StripBearer(it->second));
    if (!user_id.has_value()) {
        throw ServiceError(401, "invalid token.");
    }
    return user_id.value();
}

PageRequest ApiController::PageFromRequest(const HttpRequest& request) const {
    PageRequest page;
    const std::string limit = QueryParam(request, "limit");
    const std::string offset = QueryParam(request, "offset");
    if (!limit.empty()) {
        page.limit = std::stoll(limit);
    }
    if (!offset.empty()) {
        page.offset = std::stoll(offset);
    }
    if (page.limit <= 0 || page.limit > 100) {
        throw ServiceError(400, "limit must be in range 1..100.");
    }
    if (page.offset < 0) {
        throw ServiceError(400, "offset cannot be negative.");
    }
    return page;
}

std::string ApiController::QueryParam(const HttpRequest& request, const std::string& name, const std::string& fallback) const {
    std::stringstream input(request.query);
    std::string part;
    while (std::getline(input, part, '&')) {
        const size_t sep = part.find('=');
        const std::string key = sep == std::string::npos ? part : part.substr(0, sep);
        if (key == name) {
            return sep == std::string::npos ? "" : part.substr(sep + 1);
        }
    }
    return fallback;
}

std::vector<std::string> ApiController::SplitPath(const std::string& path) const {
    std::vector<std::string> result;
    std::stringstream input(path);
    std::string part;
    while (std::getline(input, part, '/')) {
        if (!part.empty()) {
            result.push_back(part);
        }
    }
    return result;
}

EventCommand ApiController::EventCommandFromJson(const Json& json, const std::optional<Event>& fallback) const {
    EventCommand command;
    if (fallback.has_value()) {
        const Event& event = fallback.value();
        command.title = event.title;
        command.description = event.description;
        command.location = event.location;
        command.starts_at = event.starts_at;
        command.ends_at = event.ends_at;
        command.photo_url = event.photo_url;
        command.category = event.category;
        command.visibility = event.visibility;
        command.participant_limit = event.participant_limit;
    }

    if (json.Contains("title")) {
        command.title = json.At("title").AsString();
    }
    if (json.Contains("description")) {
        command.description = json.At("description").AsString();
    }
    if (json.Contains("location")) {
        command.location = json.At("location").AsString();
    }
    if (json.Contains("starts_at")) {
        command.starts_at = json.At("starts_at").AsString();
    }
    if (json.Contains("ends_at")) {
        command.ends_at = json.At("ends_at").AsString();
    }
    if (json.Contains("photo_url")) {
        command.photo_url = json.At("photo_url").AsString();
    }
    if (json.Contains("category")) {
        command.category = json.At("category").AsString();
    }
    if (json.Contains("visibility")) {
        command.visibility = EventVisibilityFromString(json.At("visibility").AsString("PUBLIC"));
    }
    if (json.Contains("participant_limit")) {
        command.participant_limit = json.At("participant_limit").AsInt();
    }
    return command;
}

Json ToJson(const User& user) {
    return Json::Object{
        {"id", user.id},
        {"username", user.username},
        {"email", user.email},
        {"bio", user.bio},
        {"avatar_url", user.avatar_url},
        {"created_at", user.created_at}
    };
}

Json ToJson(const Event& event) {
    return Json::Object{
        {"id", event.id},
        {"organizer_id", event.organizer_id},
        {"title", event.title},
        {"description", event.description},
        {"location", event.location},
        {"starts_at", event.starts_at},
        {"ends_at", event.ends_at},
        {"photo_url", event.photo_url},
        {"category", event.category},
        {"visibility", ToString(event.visibility)},
        {"participant_limit", event.participant_limit},
        {"status", ToString(event.status)},
        {"created_at", event.created_at},
        {"updated_at", event.updated_at}
    };
}

Json ToJson(const Participation& participation) {
    return Json::Object{
        {"id", participation.id},
        {"event_id", participation.event_id},
        {"user_id", participation.user_id},
        {"status", ToString(participation.status)},
        {"created_at", participation.created_at},
        {"updated_at", participation.updated_at}
    };
}

Json ToJson(const Notification& notification) {
    return Json::Object{
        {"id", notification.id},
        {"user_id", notification.user_id},
        {"type", notification.type},
        {"title", notification.title},
        {"text", notification.text},
        {"status", ToString(notification.status)},
        {"created_at", notification.created_at},
        {"read_at", notification.read_at.value_or("")}
    };
}

} // namespace gatherly
