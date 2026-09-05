#include "test_support.h"

using namespace gatherly;
using namespace gatherly::test;

TEST(FollowingTest, FollowAndUnfollow) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));

    service.FollowUser(bob.id, alice.id);
    EXPECT_EQ(service.ListFollowers(alice.id).size(), 1);
    EXPECT_EQ(service.ListFollowing(bob.id).size(), 1);
    ExpectThrowsStatus(409, [&] { service.FollowUser(bob.id, alice.id); });
    ExpectThrowsStatus(400, [&] { service.FollowUser(alice.id, alice.id); });

    service.UnfollowUser(bob.id, alice.id);
    EXPECT_TRUE(service.ListFollowers(alice.id).empty());
}

TEST(EventTest, PublishCreatesOutboxAndNotification) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    service.FollowUser(bob.id, alice.id);

    const Event draft = service.CreateEvent(alice.id, BoardGamesCommand());
    EXPECT_EQ(draft.status, EventStatus::DRAFT);
    const Event published = service.PublishEvent(alice.id, draft.id);
    EXPECT_EQ(published.status, EventStatus::PUBLISHED);
    EXPECT_EQ(service.ListPublicEvents().size(), 1);
    EXPECT_EQ(service.ListNotifications(bob.id).size(), 1);
    EXPECT_EQ(service.ListOutbox().front().status, OutboxStatus::PROCESSED);
}


TEST(EventTest, PublishCanNotifySelectedFollowersOnly) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const User carol = service.RegisterUser(UserCommand("carol"));
    const User dave = service.RegisterUser(UserCommand("dave"));
    service.FollowUser(bob.id, alice.id);
    service.FollowUser(carol.id, alice.id);

    const Event draft = service.CreateEvent(alice.id, BoardGamesCommand());
    const Event published = service.PublishEventForUsers(alice.id, draft.id, {bob.id});

    EXPECT_EQ(published.status, EventStatus::PUBLISHED);
    EXPECT_EQ(service.ListNotifications(bob.id).size(), 1);
    EXPECT_TRUE(service.ListNotifications(carol.id).empty());

    const Event second_draft = service.CreateEvent(alice.id, BoardGamesCommand());
    ExpectThrowsStatus(403, [&] { service.PublishEventForUsers(alice.id, second_draft.id, {dave.id}); });
}

TEST(ParticipationTest, LimitAndWaitlistPromotion) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const User carol = service.RegisterUser(UserCommand("carol"));

    const Event event = service.PublishEvent(alice.id, service.CreateEvent(alice.id, BoardGamesCommand(1)).id);
    const Participation bob_participation = service.JoinEvent(bob.id, event.id);
    const Participation carol_participation = service.JoinEvent(carol.id, event.id);
    EXPECT_EQ(bob_participation.status, ParticipationStatus::GOING);
    EXPECT_EQ(carol_participation.status, ParticipationStatus::WAITLISTED);

    service.LeaveEvent(bob.id, event.id);
    const auto participants = service.ListParticipants(alice.id, event.id);
    ASSERT_EQ(participants.size(), 1);
    EXPECT_EQ(participants.front().user_id, carol.id);
    EXPECT_EQ(participants.front().status, ParticipationStatus::GOING);
}


TEST(ParticipationTest, UserCanSeeOwnParticipationHistory) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));

    const Event event = service.PublishEvent(alice.id, service.CreateEvent(alice.id, BoardGamesCommand()).id);
    service.JoinEvent(bob.id, event.id);

    const auto history = service.ListMyParticipations(bob.id, PageRequest{});
    ASSERT_EQ(history.size(), 1);
    EXPECT_EQ(history.front().event_id, event.id);
    EXPECT_EQ(history.front().user_id, bob.id);
    EXPECT_EQ(history.front().status, ParticipationStatus::GOING);
}

TEST(NotificationTest, FailureAndRecovery) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    service.FollowUser(bob.id, alice.id);
    service.SetNotificationMode(NotificationMode::FAILING);

    const Event event = service.CreateEvent(alice.id, BoardGamesCommand());
    service.PublishEvent(alice.id, event.id);
    EXPECT_TRUE(service.ListNotifications(bob.id).empty());
    EXPECT_EQ(service.ListOutbox().front().status, OutboxStatus::FAILED);

    service.SetNotificationMode(NotificationMode::RECOVERY);
    const int64_t delivered = service.ProcessOutbox();
    EXPECT_EQ(delivered, 1);
    EXPECT_EQ(service.ListNotifications(bob.id).size(), 1);
    EXPECT_EQ(service.ListOutbox().front().status, OutboxStatus::PROCESSED);
}

TEST(NotificationTest, EventUpdateNotifiesParticipants) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));

    EventCommand command = BoardGamesCommand();
    const Event event = service.PublishEvent(alice.id, service.CreateEvent(alice.id, command).id);
    service.JoinEvent(bob.id, event.id);
    command.starts_at = "2026-07-04T19:00:00Z";
    service.UpdateEvent(alice.id, event.id, command);
    EXPECT_EQ(service.ListNotifications(bob.id).size(), 1);
}

TEST(AuthTest, LoginReturnsVerifiableBearerToken) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const LoginResult login = service.Login("alice", "password");

    ASSERT_FALSE(login.token.empty());
    ASSERT_TRUE(service.Authenticate(login.token).has_value());
    EXPECT_EQ(service.Authenticate(login.token).value(), alice.id);
}

TEST(AuthTest, RegistrationValidatesRequiredFieldsAndUniqueness) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    auto command = UserCommand("alice");
    command.password.clear();
    ExpectThrowsStatus(400, [&] { service.RegisterUser(command); });
    EXPECT_TRUE(service.ListUsers().empty());

    service.RegisterUser(UserCommand("alice"));
    ExpectThrowsStatus(409, [&] { service.RegisterUser(UserCommand("alice")); });
    command = UserCommand("bob");
    command.email = "alice@gatherly.local";
    ExpectThrowsStatus(409, [&] { service.RegisterUser(command); });
    EXPECT_EQ(service.ListUsers().size(), 1);
}

TEST(AuthTest, EmailLoginAndInvalidCredentials) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const auto login = service.Login(alice.email, "password");
    EXPECT_EQ(service.Authenticate(login.token), alice.id);

    ExpectThrowsStatus(401, [&] { service.Login("alice", "wrong-password"); });
    ExpectThrowsStatus(401, [&] { service.Login("unknown", "password"); });
    EXPECT_FALSE(service.Authenticate("").has_value());
    EXPECT_FALSE(service.Authenticate("invalid-token").has_value());
    EXPECT_FALSE(service.Authenticate(login.token + "invalid").has_value());
}

TEST(EventTest, OnlyOrganizerCanManageEventAndListParticipants) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const auto command = BoardGamesCommand();
    const Event draft = service.CreateEvent(alice.id, command);

    ExpectThrowsStatus(403, [&] { service.UpdateEvent(bob.id, draft.id, command); });
    ExpectThrowsStatus(403, [&] { service.PublishEvent(bob.id, draft.id); });
    ExpectThrowsStatus(403, [&] { service.CancelEvent(bob.id, draft.id); });
    ExpectThrowsStatus(403, [&] { service.ListParticipants(bob.id, draft.id); });
    EXPECT_EQ(service.GetEvent(alice.id, draft.id).status, EventStatus::DRAFT);
    EXPECT_TRUE(service.ListOutbox().empty());
}

TEST(EventTest, VisibilitySeparatesPublicFeedAndFollowerFeed) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const User carol = service.RegisterUser(UserCommand("carol"));
    service.FollowUser(bob.id, alice.id);

    for (auto visibility : {EventVisibility::PUBLIC, EventVisibility::FOLLOWERS_ONLY, EventVisibility::PRIVATE}) {
        SCOPED_TRACE(ToString(visibility));
        auto command = BoardGamesCommand();
        command.visibility = visibility;
        const Event draft = service.CreateEvent(alice.id, command);
        EXPECT_EQ(service.GetEvent(alice.id, draft.id).id, draft.id);
        ExpectThrowsStatus(403, [&] { service.GetEvent(bob.id, draft.id); });
        const Event event = service.PublishEvent(alice.id, draft.id);

        if (visibility == EventVisibility::PRIVATE) {
            ExpectThrowsStatus(403, [&] { service.GetEvent(bob.id, event.id); });
            ExpectThrowsStatus(403, [&] { service.JoinEvent(bob.id, event.id); });
        } else {
            EXPECT_EQ(service.GetEvent(bob.id, event.id).id, event.id);
        }
        if (visibility == EventVisibility::PUBLIC) {
            EXPECT_EQ(service.GetEvent(carol.id, event.id).id, event.id);
        } else {
            ExpectThrowsStatus(403, [&] { service.GetEvent(carol.id, event.id); });
        }
    }

    const auto public_events = service.ListPublicEvents();
    ASSERT_EQ(public_events.size(), 1);
    EXPECT_EQ(public_events.front().visibility, EventVisibility::PUBLIC);
    const auto feed = service.ListFeed(bob.id);
    ASSERT_EQ(feed.size(), 2);
    EXPECT_EQ(feed[0].visibility, EventVisibility::PUBLIC);
    EXPECT_EQ(feed[1].visibility, EventVisibility::FOLLOWERS_ONLY);
    EXPECT_TRUE(service.ListFeed(carol.id).empty());
}

TEST(EventTest, CancellationNotifiesParticipantsAndRemovesEventFromFeeds) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const User carol = service.RegisterUser(UserCommand("carol"));
    const Event event = service.PublishEvent(alice.id, service.CreateEvent(alice.id, BoardGamesCommand()).id);
    service.FollowUser(bob.id, alice.id);
    service.JoinEvent(bob.id, event.id);

    EXPECT_EQ(service.CancelEvent(alice.id, event.id).status, EventStatus::CANCELLED);
    EXPECT_TRUE(service.ListPublicEvents().empty());
    EXPECT_TRUE(service.ListFeed(bob.id).empty());
    EXPECT_EQ(service.GetEvent(bob.id, event.id).status, EventStatus::CANCELLED);
    const auto notifications = service.ListNotifications(bob.id);
    ASSERT_EQ(notifications.size(), 1);
    EXPECT_EQ(notifications.front().type, "EVENT_CANCELLED");
    ExpectThrowsStatus(409, [&] { service.JoinEvent(carol.id, event.id); });
    ExpectThrowsStatus(409, [&] { service.PublishEvent(alice.id, event.id); });
    ExpectThrowsStatus(409, [&] { service.UpdateEvent(alice.id, event.id, BoardGamesCommand()); });
    ExpectThrowsStatus(409, [&] { service.CancelEvent(alice.id, event.id); });
}

TEST(EventTest, InvalidSelectedRecipientLeavesDraftAndOutboxUnchanged) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const User carol = service.RegisterUser(UserCommand("carol"));
    service.FollowUser(bob.id, alice.id);
    const Event draft = service.CreateEvent(alice.id, BoardGamesCommand());

    ExpectThrowsStatus(403, [&] { service.PublishEventForUsers(alice.id, draft.id, {bob.id, carol.id}); });
    EXPECT_EQ(service.GetEvent(alice.id, draft.id).status, EventStatus::DRAFT);
    EXPECT_TRUE(service.ListOutbox().empty());
    EXPECT_TRUE(service.ListNotifications(bob.id).empty());
}

TEST(ParticipationTest, JoinRejectsDraftsAndDuplicateParticipation) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const Event draft = service.CreateEvent(alice.id, BoardGamesCommand());
    ExpectThrowsStatus(409, [&] { service.JoinEvent(bob.id, draft.id); });

    service.PublishEvent(alice.id, draft.id);
    service.JoinEvent(bob.id, draft.id);
    ExpectThrowsStatus(409, [&] { service.JoinEvent(bob.id, draft.id); });
    EXPECT_EQ(service.ListParticipants(alice.id, draft.id).size(), 1);
}

TEST(ParticipationTest, PromotionFollowsWaitlistOrder) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const User carol = service.RegisterUser(UserCommand("carol"));
    const User dave = service.RegisterUser(UserCommand("dave"));
    const Event event = service.PublishEvent(alice.id, service.CreateEvent(alice.id, BoardGamesCommand(1)).id);
    service.JoinEvent(bob.id, event.id);
    service.JoinEvent(carol.id, event.id);
    service.JoinEvent(dave.id, event.id);

    service.LeaveEvent(bob.id, event.id);
    auto participants = service.ListParticipants(alice.id, event.id);
    ASSERT_EQ(participants.size(), 2);
    EXPECT_EQ(participants[0].user_id, carol.id);
    EXPECT_EQ(participants[0].status, ParticipationStatus::GOING);
    EXPECT_EQ(participants[1].user_id, dave.id);
    EXPECT_EQ(participants[1].status, ParticipationStatus::WAITLISTED);

    service.LeaveEvent(carol.id, event.id);
    participants = service.ListParticipants(alice.id, event.id);
    ASSERT_EQ(participants.size(), 1);
    EXPECT_EQ(participants[0].user_id, dave.id);
    EXPECT_EQ(participants[0].status, ParticipationStatus::GOING);
}

TEST(ParticipationTest, StatusChangeCannotExceedCapacity) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    const User carol = service.RegisterUser(UserCommand("carol"));
    const Event event = service.PublishEvent(alice.id, service.CreateEvent(alice.id, BoardGamesCommand(1)).id);
    service.JoinEvent(bob.id, event.id);
    service.JoinEvent(carol.id, event.id);

    EXPECT_EQ(service.SetMyParticipationStatus(bob.id, event.id, ParticipationStatus::GOING).status,
              ParticipationStatus::GOING);
    EXPECT_EQ(service.SetMyParticipationStatus(carol.id, event.id, ParticipationStatus::MAYBE).status,
              ParticipationStatus::MAYBE);
    EXPECT_EQ(service.SetMyParticipationStatus(carol.id, event.id, ParticipationStatus::GOING).status,
              ParticipationStatus::WAITLISTED);
}

TEST(NotificationTest, ReadStatusBelongsToRecipient) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    service.FollowUser(bob.id, alice.id);
    service.PublishEvent(alice.id, service.CreateEvent(alice.id, BoardGamesCommand()).id);
    const auto notifications = service.ListNotifications(bob.id);
    ASSERT_EQ(notifications.size(), 1);
    const auto id = notifications.front().id;
    EXPECT_EQ(notifications.front().status, NotificationStatus::NEW);
    EXPECT_TRUE(service.ListNotifications(alice.id).empty());

    ExpectThrowsStatus(404, [&] { service.MarkNotificationRead(alice.id, id); });
    EXPECT_EQ(service.ListNotifications(bob.id).front().status, NotificationStatus::NEW);
    const auto read = service.MarkNotificationRead(bob.id, id);
    EXPECT_EQ(read.status, NotificationStatus::READ);
    EXPECT_TRUE(read.read_at.has_value());
    EXPECT_EQ(service.ListNotifications(bob.id).front().status, NotificationStatus::READ);
}

TEST(NotificationTest, CompletedOutboxIsNotDeliveredAgainOnSequentialRetry) {
    GatherlyService service(std::make_shared<FakePostgresStore>());
    const User alice = service.RegisterUser(UserCommand("alice"));
    const User bob = service.RegisterUser(UserCommand("bob"));
    service.FollowUser(bob.id, alice.id);
    service.PublishEventForUsers(alice.id, service.CreateEvent(alice.id, BoardGamesCommand()).id, {bob.id, bob.id});

    EXPECT_EQ(service.ProcessOutbox(), 0);
    EXPECT_EQ(service.ListNotifications(bob.id).size(), 1);
    const auto outbox = service.ListOutbox();
    ASSERT_EQ(outbox.size(), 1);
    EXPECT_EQ(outbox.front().status, OutboxStatus::PROCESSED);
    EXPECT_EQ(outbox.front().attempts, 1);
}
