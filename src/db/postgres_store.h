#pragma once

#include "models/models.h"

#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace gatherly {

class IPostgresStore {
public:
    virtual ~IPostgresStore() = default;

    virtual int64_t NextUserId() = 0;
    virtual int64_t NextEventId() = 0;
    virtual int64_t NextParticipationId() = 0;
    virtual int64_t NextNotificationId() = 0;
    virtual int64_t NextOutboxId() = 0;

    virtual std::optional<User> GetUser(int64_t id) = 0;
    virtual std::optional<User> FindUserByLogin(const std::string& username_or_email) = 0;
    virtual bool UserExists(int64_t id) = 0;
    virtual bool UsernameExists(const std::string& username) = 0;
    virtual bool EmailExists(const std::string& email) = 0;
    virtual std::vector<User> ListUsers(int64_t limit, int64_t offset) = 0;

    virtual std::optional<Event> GetEvent(int64_t id) = 0;
    virtual std::vector<Event> ListPublicEvents(int64_t limit, int64_t offset) = 0;
    virtual std::vector<Event> ListFeed(int64_t user_id, int64_t limit, int64_t offset) = 0;

    virtual bool IsFollowing(int64_t follower_id, int64_t following_id) = 0;
    virtual std::vector<User> ListFollowers(int64_t user_id) = 0;
    virtual std::vector<User> ListFollowing(int64_t user_id) = 0;

    virtual std::optional<Participation> FindParticipation(int64_t user_id, int64_t event_id) = 0;
    virtual int64_t CountGoingParticipants(int64_t event_id) = 0;
    virtual std::vector<Participation> ListEventParticipations(int64_t event_id) = 0;
    virtual std::vector<Participation> ListUserParticipations(int64_t user_id, int64_t limit, int64_t offset) = 0;

    virtual std::optional<Notification> GetNotification(int64_t id) = 0;
    virtual std::vector<Notification> ListNotifications(int64_t user_id, int64_t limit, int64_t offset) = 0;
    virtual std::vector<NotificationOutboxRecord> ListOutbox(int64_t limit, int64_t offset) = 0;
    virtual std::vector<NotificationOutboxRecord> ListPendingOutbox() = 0;

    virtual void UpsertUser(const User& user) = 0;
    virtual void UpsertEvent(const Event& event) = 0;
    virtual void UpsertParticipation(const Participation& participation) = 0;
    virtual void RemoveParticipation(int64_t event_id, int64_t user_id) = 0;
    virtual void UpsertNotification(const Notification& notification) = 0;
    virtual void UpsertOutboxRecord(const NotificationOutboxRecord& record) = 0;
    virtual void AddFollow(int64_t follower_id, int64_t following_id) = 0;
    virtual void RemoveFollow(int64_t follower_id, int64_t following_id) = 0;
};

std::shared_ptr<IPostgresStore> CreatePostgresStore(const std::string& connection_string);

} // namespace gatherly
