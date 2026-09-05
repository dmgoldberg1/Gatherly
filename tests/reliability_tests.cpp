#include "test_support.h"
#include "utils/json.h"
#include "utils/time.h"
#include "observability/metrics.h"
#include <future>

namespace gatherly::test {
TEST(JsonTest, PreservesUnicodeAndLargeIntegerIds) {
    const auto value = Json::Parse(R"({"text":"\u041f\u0440\u0438\u0432\u0435\u0442 \ud83d\udc4b","id":9007199254740993})");
    EXPECT_EQ(value.At("text").AsString(), "Привет 👋");
    EXPECT_EQ(value.At("id").AsInt(), 9007199254740993LL);
    EXPECT_EQ(Json::Parse(value.Dump()).At("id").AsInt(), 9007199254740993LL);
    EXPECT_THROW(Json::Parse(R"({"id":1.5})").At("id").AsInt(), std::invalid_argument);
    EXPECT_THROW(Json::Parse(R"({"text":"\x"})"), std::invalid_argument);
}
TEST(TimeTest, ComparesInstantsAndRejectsInvalidCalendarDates) {
    EXPECT_EQ(NormalizeTimestamp("2026-09-05T20:30:00.000+03:00"), "2026-09-05T17:30:00Z");
    EXPECT_EQ(NormalizeTimestamp("2026-09-05 17:30:00+00"), "2026-09-05T17:30:00Z");
    EXPECT_LT(TimestampMicros("2026-09-05T19:00:00+03:00"), TimestampMicros("2026-09-05T17:00:00Z"));
    EXPECT_THROW(TimestampMicros("2026-02-30T17:00:00Z"), std::invalid_argument);
    EXPECT_THROW(TimestampMicros("2026-09-05T17:00:00"), std::invalid_argument);
}
class FailingOutboxStore : public FakePostgresStore {
public:
    bool fail = false;
    void UpsertOutboxRecord(const NotificationOutboxRecord& record) override {
        if (fail) throw std::runtime_error("injected write failure");
        FakePostgresStore::UpsertOutboxRecord(record);
    }
};
TEST(TransactionTest, FailedOutboxInsertRollsBackPublication) {
    auto store = std::make_shared<FailingOutboxStore>();
    GatherlyService service(store);
    auto owner = service.RegisterUser(UserCommand("owner"));
    auto follower = service.RegisterUser(UserCommand("follower"));
    service.FollowUser(follower.id, owner.id);
    auto event = service.CreateEvent(owner.id, BoardGamesCommand());
    store->fail = true;
    EXPECT_THROW(service.PublishEvent(owner.id, event.id), std::runtime_error);
    EXPECT_EQ(service.GetEvent(owner.id, event.id).status, EventStatus::DRAFT);
    EXPECT_TRUE(service.ListOutbox().empty());
    store->fail = false;
}
TEST(TransactionTest, ConcurrentJoinsRespectCapacity) {
    auto store = std::make_shared<FakePostgresStore>();
    GatherlyService service(store);
    auto owner = service.RegisterUser(UserCommand("owner"));
    auto first = service.RegisterUser(UserCommand("first"));
    auto second = service.RegisterUser(UserCommand("second"));
    auto event = service.CreateEvent(owner.id, BoardGamesCommand(1));
    service.PublishEvent(owner.id, event.id);
    auto a = std::async(std::launch::async, [&] { return service.JoinEvent(first.id, event.id); });
    auto b = std::async(std::launch::async, [&] { return service.JoinEvent(second.id, event.id); });
    const auto pa = a.get(), pb = b.get();
    EXPECT_NE(pa.status, pb.status);
    EXPECT_EQ(store->CountGoingParticipants(event.id), 1);
}
TEST(EventTest, OwnedListIncludesDraftsAndCancelledEventsOnlyForOwner) {
    auto store = std::make_shared<FakePostgresStore>();
    GatherlyService service(store);
    auto owner = service.RegisterUser(UserCommand("owner"));
    auto other = service.RegisterUser(UserCommand("other"));
    auto draft = service.CreateEvent(owner.id, BoardGamesCommand());
    auto cancelled = service.CreateEvent(owner.id, BoardGamesCommand());
    service.CancelEvent(owner.id, cancelled.id);
    EXPECT_EQ(service.ListOwnedEvents(owner.id, {}).size(), 2);
    EXPECT_TRUE(service.ListOwnedEvents(other.id, {}).empty());
}
TEST(MetricsTest, DoesNotExposeRawPathsOrIdentifiers) {
    auto& metrics = Metrics::Instance();
    metrics.ObserveHttp("GET", "/api/v1/events/987654321", 200, .02);
    metrics.ObserveHttp("GET", "/some-secret-unknown-url", 404, .01);
    const auto output = metrics.Render();
    EXPECT_NE(output.find("/api/v1/events/:id"), std::string::npos);
    EXPECT_EQ(output.find("987654321"), std::string::npos);
    EXPECT_EQ(output.find("some-secret"), std::string::npos);
}
}
