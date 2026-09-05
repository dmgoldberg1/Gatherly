#include "test_support.h"

#include "controllers/api_controller.h"

using namespace gatherly;
using namespace gatherly::test;

TEST(ApiTest, RegistrationLoginAndOwnProfilePreserveBearerContract) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    ApiController controller(service, "static");
    HttpRequest request;
    request.method = "POST";
    request.path = "/api/v1/auth/register";
    request.body = R"({"username":"alice","email":"alice@gatherly.local","password":"password"})";

    const auto registration = controller.Handle(request);
    ASSERT_EQ(registration.status, 201);
    EXPECT_EQ(registration.content_type, "application/json");
    const auto user = Json::Parse(registration.body);
    EXPECT_EQ(user.At("username").AsString(), "alice");
    EXPECT_FALSE(user.Contains("password"));
    EXPECT_FALSE(user.Contains("password_hash"));

    request.path = "/api/v1/auth/login";
    request.body = R"({"email":"alice@gatherly.local","password":"password"})";
    const auto response = controller.Handle(request);
    ASSERT_EQ(response.status, 200);
    const auto login = Json::Parse(response.body);
    const auto token = login.At("token").AsString();
    ASSERT_FALSE(token.empty());
    EXPECT_EQ(login.At("user").At("id").AsInt(), user.At("id").AsInt());

    request.method = "GET";
    request.path = "/api/v1/me";
    request.body.clear();
    EXPECT_EQ(controller.Handle(request).status, 401);
    request.headers["Authorization"] = "Bearer " + token;
    const auto profile = controller.Handle(request);
    ASSERT_EQ(profile.status, 200);
    EXPECT_EQ(Json::Parse(profile.body).At("id").AsInt(), user.At("id").AsInt());
}

TEST(ApiTest, PatchChangesOnlyProvidedEventFields) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    ApiController controller(service, "static");
    const User alice = service.RegisterUser(UserCommand("alice"));
    const auto command = BoardGamesCommand(3);
    const Event event = service.CreateEvent(alice.id, command);
    HttpRequest request;
    request.method = "PATCH";
    request.path = "/api/v1/events/" + std::to_string(event.id);
    request.headers["Authorization"] = "Bearer " + service.Login("alice", "password").token;
    request.body = R"({"title":"Новое название"})";

    const auto response = controller.Handle(request);
    ASSERT_EQ(response.status, 200);
    EXPECT_EQ(Json::Parse(response.body).At("title").AsString(), "Новое название");
    const Event updated = service.GetEvent(alice.id, event.id);
    EXPECT_EQ(updated.title, "Новое название");
    EXPECT_EQ(updated.description, event.description);
    EXPECT_EQ(updated.location, event.location);
    EXPECT_EQ(updated.starts_at, event.starts_at);
    EXPECT_EQ(updated.ends_at, event.ends_at);
    EXPECT_EQ(updated.participant_limit, 3);
    EXPECT_EQ(updated.visibility, event.visibility);
    EXPECT_EQ(updated.status, EventStatus::DRAFT);
}

TEST(ApiTest, PublicFeedPaginationAndInvalidBounds) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    ApiController controller(service, "static");
    const User alice = service.RegisterUser(UserCommand("alice"));
    std::vector<int64_t> event_ids;
    for (int i = 0; i < 3; ++i) {
        event_ids.push_back(service.PublishEvent(alice.id, service.CreateEvent(alice.id, BoardGamesCommand()).id).id);
    }
    HttpRequest request;
    request.method = "GET";
    request.path = "/api/v1/events";
    request.query = "limit=1&offset=1";

    const auto response = controller.Handle(request);
    ASSERT_EQ(response.status, 200);
    const auto page = Json::Parse(response.body);
    EXPECT_EQ(page.At("count").AsInt(), 1);
    EXPECT_EQ(page.At("limit").AsInt(), 1);
    EXPECT_EQ(page.At("offset").AsInt(), 1);
    ASSERT_TRUE(page.At("items").IsArray());
    ASSERT_EQ(page.At("items").AsArray().size(), 1);
    EXPECT_EQ(page.At("items").AsArray().front().At("id").AsInt(), event_ids[1]);

    for (const auto* query : {"limit=0", "limit=101", "offset=-1"}) {
        SCOPED_TRACE(query);
        request.query = query;
        EXPECT_EQ(controller.Handle(request).status, 400);
    }
}

TEST(ApiTest, InvalidJsonAndUnknownEndpointReturnErrors) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    ApiController controller(service, "static");
    HttpRequest request;
    request.method = "POST";
    request.path = "/api/v1/auth/register";
    request.body = "{";
    const auto invalid_json = controller.Handle(request);
    EXPECT_EQ(invalid_json.status, 400);
    EXPECT_TRUE(Json::Parse(invalid_json.body).Contains("error"));
    EXPECT_TRUE(service.ListUsers().empty());

    request.method = "GET";
    request.path = "/api/v1/not-an-endpoint";
    request.body.clear();
    const auto unknown = controller.Handle(request);
    EXPECT_EQ(unknown.status, 404);
    EXPECT_TRUE(Json::Parse(unknown.body).Contains("error"));
}
