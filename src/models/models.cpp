#include "models/models.h"

#include <chrono>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace gatherly {

namespace {

std::string Upper(std::string value) {
    for (char& ch : value) {
        if (ch >= 'a' && ch <= 'z') {
            ch = static_cast<char>(ch - 'a' + 'A');
        }
    }
    return value;
}

} // namespace

std::string ToString(EventVisibility visibility) {
    switch (visibility) {
        case EventVisibility::PUBLIC:
            return "PUBLIC";
        case EventVisibility::FOLLOWERS_ONLY:
            return "FOLLOWERS_ONLY";
        case EventVisibility::PRIVATE:
            return "PRIVATE";
    }
    return "PUBLIC";
}

std::string ToString(EventStatus status) {
    switch (status) {
        case EventStatus::DRAFT:
            return "DRAFT";
        case EventStatus::PUBLISHED:
            return "PUBLISHED";
        case EventStatus::CANCELLED:
            return "CANCELLED";
        case EventStatus::FINISHED:
            return "FINISHED";
        case EventStatus::ARCHIVED:
            return "ARCHIVED";
    }
    return "DRAFT";
}

std::string ToString(ParticipationStatus status) {
    switch (status) {
        case ParticipationStatus::GOING:
            return "GOING";
        case ParticipationStatus::MAYBE:
            return "MAYBE";
        case ParticipationStatus::DECLINED:
            return "DECLINED";
        case ParticipationStatus::WAITLISTED:
            return "WAITLISTED";
    }
    return "GOING";
}

std::string ToString(NotificationStatus status) {
    switch (status) {
        case NotificationStatus::NEW:
            return "NEW";
        case NotificationStatus::READ:
            return "READ";
    }
    return "NEW";
}

std::string ToString(OutboxStatus status) {
    switch (status) {
        case OutboxStatus::PENDING:
            return "PENDING";
        case OutboxStatus::PROCESSED:
            return "PROCESSED";
        case OutboxStatus::FAILED:
            return "FAILED";
    }
    return "PENDING";
}

EventVisibility EventVisibilityFromString(const std::string& value) {
    const std::string normalized = Upper(value);
    if (normalized == "PUBLIC") {
        return EventVisibility::PUBLIC;
    }
    if (normalized == "FOLLOWERS_ONLY") {
        return EventVisibility::FOLLOWERS_ONLY;
    }
    if (normalized == "PRIVATE") {
        return EventVisibility::PRIVATE;
    }
    throw std::runtime_error("Unknown event visibility: " + value);
}

EventStatus EventStatusFromString(const std::string& value) {
    const std::string normalized = Upper(value);
    if (normalized == "DRAFT") {
        return EventStatus::DRAFT;
    }
    if (normalized == "PUBLISHED") {
        return EventStatus::PUBLISHED;
    }
    if (normalized == "CANCELLED") {
        return EventStatus::CANCELLED;
    }
    if (normalized == "FINISHED") {
        return EventStatus::FINISHED;
    }
    if (normalized == "ARCHIVED") {
        return EventStatus::ARCHIVED;
    }
    throw std::runtime_error("Unknown event status: " + value);
}

ParticipationStatus ParticipationStatusFromString(const std::string& value) {
    const std::string normalized = Upper(value);
    if (normalized == "GOING") {
        return ParticipationStatus::GOING;
    }
    if (normalized == "MAYBE") {
        return ParticipationStatus::MAYBE;
    }
    if (normalized == "DECLINED") {
        return ParticipationStatus::DECLINED;
    }
    if (normalized == "WAITLISTED") {
        return ParticipationStatus::WAITLISTED;
    }
    throw std::runtime_error("Unknown participation status: " + value);
}

NotificationStatus NotificationStatusFromString(const std::string& value) {
    const std::string normalized = Upper(value);
    if (normalized == "NEW") {
        return NotificationStatus::NEW;
    }
    if (normalized == "READ") {
        return NotificationStatus::READ;
    }
    throw std::runtime_error("Unknown notification status: " + value);
}

OutboxStatus OutboxStatusFromString(const std::string& value) {
    const std::string normalized = Upper(value);
    if (normalized == "PENDING") {
        return OutboxStatus::PENDING;
    }
    if (normalized == "PROCESSED") {
        return OutboxStatus::PROCESSED;
    }
    if (normalized == "FAILED") {
        return OutboxStatus::FAILED;
    }
    throw std::runtime_error("Unknown outbox status: " + value);
}

std::string NowIso() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t time = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
#if defined(_WIN32)
    gmtime_s(&tm, &time);
#else
    gmtime_r(&time, &tm);
#endif
    std::ostringstream output;
    output << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
    return output.str();
}

} // namespace gatherly
