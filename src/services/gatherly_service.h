#pragma once

#include "db/postgres_store.h"
#include "models/models.h"
#include "utils/auth.h"

#include <atomic>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace gatherly {

class ServiceError : public std::runtime_error {
public:
    ServiceError(int status_code, const std::string& message);
    int StatusCode() const;

protected:
    int status_code_;
};

struct LoginResult {
    User user;
    std::string token;
};

struct RegisterUserCommand {
    std::string username;
    std::string email;
    std::string password;
    std::string bio;
    std::string avatar_url;
};

struct PageRequest {
    int64_t limit = 50;
    int64_t offset = 0;
};

struct EventCommand {
    std::string title;
    std::string description;
    std::string location;
    std::string starts_at;
    std::string ends_at;
    std::string photo_url;
    std::string category;
    EventVisibility visibility = EventVisibility::PUBLIC;
    int64_t participant_limit = 0;
};

class GatherlyService {
public:
    explicit GatherlyService(std::shared_ptr<IPostgresStore> postgres_store);
    ~GatherlyService();

    User RegisterUser(const RegisterUserCommand& command);
    LoginResult Login(const std::string& username_or_email, const std::string& password);
    std::optional<int64_t> Authenticate(const std::string& token) const;

    User GetUser(int64_t id) const;
    std::vector<User> ListUsers() const;
    std::vector<User> ListUsers(PageRequest page) const;
    void FollowUser(int64_t current_user_id, int64_t user_id);
    void UnfollowUser(int64_t current_user_id, int64_t user_id);
    std::vector<User> ListFollowers(int64_t user_id) const;
    std::vector<User> ListFollowing(int64_t user_id) const;

    Event CreateEvent(int64_t organizer_id, const EventCommand& command);
    Event UpdateEvent(int64_t current_user_id, int64_t event_id, const EventCommand& command);
    Event PublishEvent(int64_t current_user_id, int64_t event_id);
    Event PublishEventForUsers(int64_t current_user_id, int64_t event_id, const std::vector<int64_t>& user_ids);
    Event CancelEvent(int64_t current_user_id, int64_t event_id);
    Event GetEvent(int64_t current_user_id, int64_t event_id) const;
    std::vector<Event> ListPublicEvents() const;
    std::vector<Event> ListPublicEvents(PageRequest page) const;
    std::vector<Event> ListFeed(int64_t current_user_id) const;
    std::vector<Event> ListFeed(int64_t current_user_id, PageRequest page) const;

    Participation JoinEvent(int64_t current_user_id, int64_t event_id);
    Participation SetMyParticipationStatus(int64_t current_user_id, int64_t event_id, ParticipationStatus status);
    void LeaveEvent(int64_t current_user_id, int64_t event_id);
    std::vector<Participation> ListParticipants(int64_t current_user_id, int64_t event_id) const;
    std::vector<Participation> ListMyParticipations(int64_t current_user_id, PageRequest page) const;

    std::vector<Notification> ListNotifications(int64_t current_user_id) const;
    std::vector<Notification> ListNotifications(int64_t current_user_id, PageRequest page) const;
    Notification MarkNotificationRead(int64_t current_user_id, int64_t notification_id);
    NotificationMode SetNotificationMode(NotificationMode mode);
    int64_t ProcessOutbox();
    std::vector<NotificationOutboxRecord> ListOutbox() const;
    std::vector<NotificationOutboxRecord> ListOutbox(PageRequest page) const;

protected:
    bool CanViewEvent(int64_t current_user_id, const Event& event) const;

    PageRequest NormalizePage(PageRequest page) const;
    void ValidateRegisterCommand(const RegisterUserCommand& command) const;
    void ValidateEventCommand(const EventCommand& command) const;
    void EnsureOrganizer(int64_t current_user_id, const Event& event) const;
    void AddOutboxRecord(int64_t user_id, const std::string& type, const std::string& title, const std::string& text);
    void AddFollowersPublishOutbox(const Event& event);
    void AddSelectedPublishOutbox(const Event& event, const std::vector<int64_t>& user_ids);
    void AddParticipantsOutbox(const Event& event, const std::string& type, const std::string& title, const std::string& text);
    void PromoteFirstWaitlisted(int64_t event_id);
    void StartOutboxWorker();
    void StopOutboxWorker();

    std::shared_ptr<IPostgresStore> postgres_store_;
    TokenService token_service_;
    NotificationMode notification_mode_ = NotificationMode::NORMAL;
    std::atomic<bool> worker_running_ = false;
    std::thread worker_thread_;
};

} // namespace gatherly
