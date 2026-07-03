#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace gatherly {

enum class EventVisibility {
    PUBLIC,
    FOLLOWERS_ONLY,
    PRIVATE
};

enum class EventStatus {
    DRAFT,
    PUBLISHED,
    CANCELLED,
    FINISHED,
    ARCHIVED
};

enum class ParticipationStatus {
    GOING,
    MAYBE,
    DECLINED,
    WAITLISTED
};

enum class NotificationStatus {
    NEW,
    READ
};

enum class OutboxStatus {
    PENDING,
    PROCESSED,
    FAILED
};

enum class NotificationMode {
    NORMAL,
    FAILING,
    RECOVERY
};

struct User {
    int64_t id = 0;
    std::string username;
    std::string email;
    std::string password_hash;
    std::string bio;
    std::string avatar_url;
    std::string created_at;
};

struct Follow {
    int64_t follower_id = 0;
    int64_t following_id = 0;
    std::string created_at;
};

struct Event {
    int64_t id = 0;
    int64_t organizer_id = 0;
    std::string title;
    std::string description;
    std::string location;
    std::string starts_at;
    std::string ends_at;
    std::string photo_url;
    std::string category;
    EventVisibility visibility = EventVisibility::PUBLIC;
    int64_t participant_limit = 0;
    EventStatus status = EventStatus::DRAFT;
    std::string created_at;
    std::string updated_at;
};

struct Participation {
    int64_t id = 0;
    int64_t event_id = 0;
    int64_t user_id = 0;
    ParticipationStatus status = ParticipationStatus::GOING;
    std::string created_at;
    std::string updated_at;
};

struct Notification {
    int64_t id = 0;
    int64_t user_id = 0;
    std::string type;
    std::string title;
    std::string text;
    NotificationStatus status = NotificationStatus::NEW;
    std::string created_at;
    std::optional<std::string> read_at;
};

struct NotificationOutboxRecord {
    int64_t id = 0;
    int64_t user_id = 0;
    std::string type;
    std::string title;
    std::string text;
    OutboxStatus status = OutboxStatus::PENDING;
    int64_t attempts = 0;
    std::string created_at;
    std::optional<std::string> processed_at;
    std::optional<std::string> last_error;
};

std::string ToString(EventVisibility visibility);
std::string ToString(EventStatus status);
std::string ToString(ParticipationStatus status);
std::string ToString(NotificationStatus status);
std::string ToString(OutboxStatus status);

EventVisibility EventVisibilityFromString(const std::string& value);
EventStatus EventStatusFromString(const std::string& value);
ParticipationStatus ParticipationStatusFromString(const std::string& value);
NotificationStatus NotificationStatusFromString(const std::string& value);
OutboxStatus OutboxStatusFromString(const std::string& value);

std::string NowIso();

} // namespace gatherly
