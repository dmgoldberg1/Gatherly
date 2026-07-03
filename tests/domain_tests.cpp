#include <gtest/gtest.h>

#include "services/gatherly_service.h"

#include <algorithm>
#include <map>
#include <mutex>
#include <set>

using namespace gatherly;

namespace {


template<typename T>
std::vector<T> ApplyPage(const std::vector<T>& values, int64_t limit, int64_t offset) {
    const int64_t start = std::min<int64_t>(offset, static_cast<int64_t>(values.size()));
    const int64_t end = std::min<int64_t>(start + limit, static_cast<int64_t>(values.size()));
    return std::vector<T>(values.begin() + start, values.begin() + end);
}

class FakePostgresStore : public IPostgresStore {
public:
    int64_t NextUserId() override {
        std::lock_guard lock(mutex_);
        return NextIdLocked(users_);
    }

    int64_t NextEventId() override {
        std::lock_guard lock(mutex_);
        return NextIdLocked(events_);
    }

    int64_t NextParticipationId() override {
        std::lock_guard lock(mutex_);
        return NextIdLocked(participations_);
    }

    int64_t NextNotificationId() override {
        std::lock_guard lock(mutex_);
        return NextIdLocked(notifications_);
    }

    int64_t NextOutboxId() override {
        std::lock_guard lock(mutex_);
        return NextIdLocked(outbox_);
    }

    std::optional<User> GetUser(int64_t id) override {
        std::lock_guard lock(mutex_);
        auto it = users_.find(id);
        if (it == users_.end()) {
            return std::nullopt;
        }
        return it->second;
    }

    std::optional<User> FindUserByLogin(const std::string& username_or_email) override {
        std::lock_guard lock(mutex_);
        for (const auto& [_, user] : users_) {
            if (user.username == username_or_email || user.email == username_or_email) {
                return user;
            }
        }
        return std::nullopt;
    }

    bool UserExists(int64_t id) override {
        std::lock_guard lock(mutex_);
        return users_.contains(id);
    }

    bool UsernameExists(const std::string& username) override {
        std::lock_guard lock(mutex_);
        return std::any_of(users_.begin(), users_.end(), [&](const auto& entry) { return entry.second.username == username; });
    }

    bool EmailExists(const std::string& email) override {
        std::lock_guard lock(mutex_);
        return std::any_of(users_.begin(), users_.end(), [&](const auto& entry) { return entry.second.email == email; });
    }

    std::vector<User> ListUsers(int64_t limit, int64_t offset) override {
        std::lock_guard lock(mutex_);
        std::vector<User> result;
        for (const auto& [_, user] : users_) {
            result.push_back(user);
        }
        return ApplyPage(result, limit, offset);
    }

    std::optional<Event> GetEvent(int64_t id) override {
        std::lock_guard lock(mutex_);
        auto it = events_.find(id);
        if (it == events_.end()) {
            return std::nullopt;
        }
        return it->second;
    }

    std::vector<Event> ListPublicEvents(int64_t limit, int64_t offset) override {
        std::lock_guard lock(mutex_);
        std::vector<Event> result;
        for (const auto& [_, event] : events_) {
            if (event.status == EventStatus::PUBLISHED && event.visibility == EventVisibility::PUBLIC) {
                result.push_back(event);
            }
        }
        return ApplyPage(result, limit, offset);
    }

    std::vector<Event> ListFeed(int64_t user_id, int64_t limit, int64_t offset) override {
        std::lock_guard lock(mutex_);
        std::vector<Event> result;
        for (const auto& [_, event] : events_) {
            if (event.status == EventStatus::PUBLISHED && follows_.contains({user_id, event.organizer_id}) &&
                (event.visibility == EventVisibility::PUBLIC || event.visibility == EventVisibility::FOLLOWERS_ONLY)) {
                result.push_back(event);
            }
        }
        return ApplyPage(result, limit, offset);
    }

    bool IsFollowing(int64_t follower_id, int64_t following_id) override {
        std::lock_guard lock(mutex_);
        return follows_.contains({follower_id, following_id});
    }

    std::vector<User> ListFollowers(int64_t user_id) override {
        std::lock_guard lock(mutex_);
        std::vector<User> result;
        for (const auto& [follower_id, following_id] : follows_) {
            if (following_id == user_id) {
                result.push_back(users_.at(follower_id));
            }
        }
        return result;
    }

    std::vector<User> ListFollowing(int64_t user_id) override {
        std::lock_guard lock(mutex_);
        std::vector<User> result;
        for (const auto& [follower_id, following_id] : follows_) {
            if (follower_id == user_id) {
                result.push_back(users_.at(following_id));
            }
        }
        return result;
    }

    std::optional<Participation> FindParticipation(int64_t user_id, int64_t event_id) override {
        std::lock_guard lock(mutex_);
        for (const auto& [_, participation] : participations_) {
            if (participation.user_id == user_id && participation.event_id == event_id) {
                return participation;
            }
        }
        return std::nullopt;
    }

    int64_t CountGoingParticipants(int64_t event_id) override {
        std::lock_guard lock(mutex_);
        return std::count_if(participations_.begin(), participations_.end(), [&](const auto& entry) {
            return entry.second.event_id == event_id && entry.second.status == ParticipationStatus::GOING;
        });
    }

    std::vector<Participation> ListEventParticipations(int64_t event_id) override {
        std::lock_guard lock(mutex_);
        std::vector<Participation> result;
        for (const auto& [_, participation] : participations_) {
            if (participation.event_id == event_id) {
                result.push_back(participation);
            }
        }
        return result;
    }

    std::vector<Participation> ListUserParticipations(int64_t user_id, int64_t limit, int64_t offset) override {
        std::lock_guard lock(mutex_);
        std::vector<Participation> result;
        for (const auto& [_, participation] : participations_) {
            if (participation.user_id == user_id) {
                result.push_back(participation);
            }
        }
        return ApplyPage(result, limit, offset);
    }

    std::optional<Notification> GetNotification(int64_t id) override {
        std::lock_guard lock(mutex_);
        auto it = notifications_.find(id);
        if (it == notifications_.end()) {
            return std::nullopt;
        }
        return it->second;
    }

    std::vector<Notification> ListNotifications(int64_t user_id, int64_t limit, int64_t offset) override {
        std::lock_guard lock(mutex_);
        std::vector<Notification> result;
        for (const auto& [_, notification] : notifications_) {
            if (notification.user_id == user_id) {
                result.push_back(notification);
            }
        }
        return ApplyPage(result, limit, offset);
    }

    std::vector<NotificationOutboxRecord> ListOutbox(int64_t limit, int64_t offset) override {
        std::lock_guard lock(mutex_);
        std::vector<NotificationOutboxRecord> result;
        for (const auto& [_, record] : outbox_) {
            result.push_back(record);
        }
        return ApplyPage(result, limit, offset);
    }

    std::vector<NotificationOutboxRecord> ListPendingOutbox() override {
        std::lock_guard lock(mutex_);
        std::vector<NotificationOutboxRecord> result;
        for (const auto& [_, record] : outbox_) {
            if (record.status != OutboxStatus::PROCESSED) {
                result.push_back(record);
            }
        }
        return result;
    }

    void UpsertUser(const User& user) override {
        std::lock_guard lock(mutex_);
        users_[user.id] = user;
    }

    void UpsertEvent(const Event& event) override {
        std::lock_guard lock(mutex_);
        events_[event.id] = event;
    }

    void UpsertParticipation(const Participation& participation) override {
        std::lock_guard lock(mutex_);
        for (auto& [_, stored] : participations_) {
            if (stored.event_id == participation.event_id && stored.user_id == participation.user_id) {
                stored = participation;
                return;
            }
        }
        participations_[participation.id] = participation;
    }

    void RemoveParticipation(int64_t event_id, int64_t user_id) override {
        std::lock_guard lock(mutex_);
        for (auto it = participations_.begin(); it != participations_.end(); ++it) {
            if (it->second.event_id == event_id && it->second.user_id == user_id) {
                participations_.erase(it);
                return;
            }
        }
    }

    void UpsertNotification(const Notification& notification) override {
        std::lock_guard lock(mutex_);
        notifications_[notification.id] = notification;
    }

    void UpsertOutboxRecord(const NotificationOutboxRecord& record) override {
        std::lock_guard lock(mutex_);
        outbox_[record.id] = record;
    }

    void AddFollow(int64_t follower_id, int64_t following_id) override {
        std::lock_guard lock(mutex_);
        follows_.insert({follower_id, following_id});
    }

    void RemoveFollow(int64_t follower_id, int64_t following_id) override {
        std::lock_guard lock(mutex_);
        follows_.erase({follower_id, following_id});
    }

protected:
    template<typename T>
    int64_t NextIdLocked(const std::map<int64_t, T>& values) const {
        if (values.empty()) {
            return 1;
        }
        return values.rbegin()->first + 1;
    }

    std::mutex mutex_;
    std::map<int64_t, User> users_;
    std::map<int64_t, Event> events_;
    std::map<int64_t, Participation> participations_;
    std::map<int64_t, Notification> notifications_;
    std::map<int64_t, NotificationOutboxRecord> outbox_;
    std::set<std::pair<int64_t, int64_t>> follows_;
};

RegisterUserCommand UserCommand(const std::string& username) {
    RegisterUserCommand command;
    command.username = username;
    command.email = username + "@gatherly.local";
    command.password = "password";
    command.bio = "demo user";
    return command;
}

EventCommand BoardGamesCommand(int64_t limit = 0) {
    EventCommand command;
    command.title = "Board games on Saturday";
    command.description = "Casual evening with friends.";
    command.location = "Alice home";
    command.starts_at = "2026-07-04T18:00:00Z";
    command.ends_at = "2026-07-04T22:00:00Z";
    command.category = "BOARD_GAMES";
    command.visibility = EventVisibility::PUBLIC;
    command.participant_limit = limit;
    return command;
}

void ExpectThrowsStatus(int status, const std::function<void()>& fn) {
    try {
        fn();
    } catch (const ServiceError& error) {
        EXPECT_EQ(error.StatusCode(), status);
        return;
    }
    FAIL() << "expected ServiceError";
}

} // namespace

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
