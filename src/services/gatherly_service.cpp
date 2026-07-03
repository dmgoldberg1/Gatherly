#include "services/gatherly_service.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <set>

namespace gatherly {

namespace {

bool ContainsAtSign(const std::string& value) {
    const size_t pos = value.find('@');
    return pos != std::string::npos && pos > 0 && pos + 1 < value.size();
}

bool HasOnlyUserNameChars(const std::string& value) {
    return std::all_of(value.begin(), value.end(), [](unsigned char ch) {
        return std::isalnum(ch) || ch == '_' || ch == '-';
    });
}

} // namespace

ServiceError::ServiceError(int status_code, const std::string& message)
    : std::runtime_error(message),
      status_code_(status_code) {}

int ServiceError::StatusCode() const {
    return status_code_;
}

GatherlyService::GatherlyService(std::shared_ptr<IPostgresStore> postgres_store)
    : postgres_store_(std::move(postgres_store)),
      token_service_("gatherly-local-dev-secret") {
    if (!postgres_store_) {
        throw std::invalid_argument("postgres store is required");
    }
    StartOutboxWorker();
}

GatherlyService::~GatherlyService() {
    StopOutboxWorker();
}

User GatherlyService::RegisterUser(const RegisterUserCommand& command) {
    ValidateRegisterCommand(command);
    if (postgres_store_->UsernameExists(command.username)) {
        throw ServiceError(409, "username already exists.");
    }
    if (postgres_store_->EmailExists(command.email)) {
        throw ServiceError(409, "email already exists.");
    }

    const std::string now = NowIso();
    User user;
    user.id = postgres_store_->NextUserId();
    user.username = command.username;
    user.email = command.email;
    user.password_hash = "plain:" + command.password;
    user.bio = command.bio;
    user.avatar_url = command.avatar_url;
    user.created_at = now;
    postgres_store_->UpsertUser(user);
    return user;
}

LoginResult GatherlyService::Login(const std::string& username_or_email, const std::string& password) {
    const std::optional<User> user = postgres_store_->FindUserByLogin(username_or_email);
    if (user.has_value() && user->password_hash == "plain:" + password) {
        return LoginResult{user.value(), token_service_.IssueToken(user->id)};
    }
    throw ServiceError(401, "invalid credentials.");
}

std::optional<int64_t> GatherlyService::Authenticate(const std::string& token) const {
    const std::optional<int64_t> user_id = token_service_.VerifyToken(token);
    if (!user_id.has_value() || !postgres_store_->UserExists(user_id.value())) {
        return std::nullopt;
    }
    return user_id;
}

User GatherlyService::GetUser(int64_t id) const {
    const std::optional<User> user = postgres_store_->GetUser(id);
    if (!user.has_value()) {
        throw ServiceError(404, "user not found.");
    }
    return user.value();
}

std::vector<User> GatherlyService::ListUsers() const {
    return ListUsers(PageRequest{});
}

std::vector<User> GatherlyService::ListUsers(PageRequest page) const {
    page = NormalizePage(page);
    return postgres_store_->ListUsers(page.limit, page.offset);
}

void GatherlyService::FollowUser(int64_t current_user_id, int64_t user_id) {
    if (!postgres_store_->UserExists(current_user_id) || !postgres_store_->UserExists(user_id)) {
        throw ServiceError(404, "user not found.");
    }
    if (current_user_id == user_id) {
        throw ServiceError(400, "user cannot follow himself.");
    }
    if (postgres_store_->IsFollowing(current_user_id, user_id)) {
        throw ServiceError(409, "user is already followed.");
    }
    postgres_store_->AddFollow(current_user_id, user_id);
}

void GatherlyService::UnfollowUser(int64_t current_user_id, int64_t user_id) {
    if (!postgres_store_->IsFollowing(current_user_id, user_id)) {
        throw ServiceError(404, "follow relation not found.");
    }
    postgres_store_->RemoveFollow(current_user_id, user_id);
}

std::vector<User> GatherlyService::ListFollowers(int64_t user_id) const {
    if (!postgres_store_->UserExists(user_id)) {
        throw ServiceError(404, "user not found.");
    }
    return postgres_store_->ListFollowers(user_id);
}

std::vector<User> GatherlyService::ListFollowing(int64_t user_id) const {
    if (!postgres_store_->UserExists(user_id)) {
        throw ServiceError(404, "user not found.");
    }
    return postgres_store_->ListFollowing(user_id);
}

Event GatherlyService::CreateEvent(int64_t organizer_id, const EventCommand& command) {
    ValidateEventCommand(command);
    if (!postgres_store_->UserExists(organizer_id)) {
        throw ServiceError(404, "organizer not found.");
    }

    const std::string now = NowIso();
    Event event;
    event.id = postgres_store_->NextEventId();
    event.organizer_id = organizer_id;
    event.title = command.title;
    event.description = command.description;
    event.location = command.location;
    event.starts_at = command.starts_at;
    event.ends_at = command.ends_at;
    event.photo_url = command.photo_url;
    event.category = command.category;
    event.visibility = command.visibility;
    event.participant_limit = command.participant_limit;
    event.status = EventStatus::DRAFT;
    event.created_at = now;
    event.updated_at = now;
    postgres_store_->UpsertEvent(event);
    return event;
}

Event GatherlyService::UpdateEvent(int64_t current_user_id, int64_t event_id, const EventCommand& command) {
    ValidateEventCommand(command);
    std::optional<Event> stored_event = postgres_store_->GetEvent(event_id);
    if (!stored_event.has_value()) {
        throw ServiceError(404, "event not found.");
    }
    Event event = stored_event.value();
    EnsureOrganizer(current_user_id, event);
    if (event.status != EventStatus::DRAFT && event.status != EventStatus::PUBLISHED) {
        throw ServiceError(409, "only draft or published event can be updated.");
    }

    const bool time_or_location_changed = event.status == EventStatus::PUBLISHED &&
        (event.starts_at != command.starts_at || event.ends_at != command.ends_at || event.location != command.location);

    event.title = command.title;
    event.description = command.description;
    event.location = command.location;
    event.starts_at = command.starts_at;
    event.ends_at = command.ends_at;
    event.photo_url = command.photo_url;
    event.category = command.category;
    event.visibility = command.visibility;
    event.participant_limit = command.participant_limit;
    event.updated_at = NowIso();

    postgres_store_->UpsertEvent(event);
    if (time_or_location_changed) {
        AddParticipantsOutbox(event, "EVENT_UPDATED", "Событие обновлено", "У события " + event.title + " изменились время или место.");
        ProcessOutbox();
    }
    return event;
}

Event GatherlyService::PublishEvent(int64_t current_user_id, int64_t event_id) {
    std::optional<Event> stored_event = postgres_store_->GetEvent(event_id);
    if (!stored_event.has_value()) {
        throw ServiceError(404, "event not found.");
    }
    Event event = stored_event.value();
    EnsureOrganizer(current_user_id, event);
    if (event.status != EventStatus::DRAFT) {
        throw ServiceError(409, "only draft event can be published.");
    }
    event.status = EventStatus::PUBLISHED;
    event.updated_at = NowIso();
    postgres_store_->UpsertEvent(event);
    AddFollowersPublishOutbox(event);
    ProcessOutbox();
    return event;
}

Event GatherlyService::PublishEventForUsers(int64_t current_user_id, int64_t event_id, const std::vector<int64_t>& user_ids) {
    std::optional<Event> stored_event = postgres_store_->GetEvent(event_id);
    if (!stored_event.has_value()) {
        throw ServiceError(404, "event not found.");
    }
    Event event = stored_event.value();
    EnsureOrganizer(current_user_id, event);
    if (event.status != EventStatus::DRAFT) {
        throw ServiceError(409, "only draft event can be published.");
    }
    event.status = EventStatus::PUBLISHED;
    event.updated_at = NowIso();
    AddSelectedPublishOutbox(event, user_ids);
    postgres_store_->UpsertEvent(event);
    ProcessOutbox();
    return event;
}

Event GatherlyService::CancelEvent(int64_t current_user_id, int64_t event_id) {
    std::optional<Event> stored_event = postgres_store_->GetEvent(event_id);
    if (!stored_event.has_value()) {
        throw ServiceError(404, "event not found.");
    }
    Event event = stored_event.value();
    EnsureOrganizer(current_user_id, event);
    if (event.status == EventStatus::CANCELLED) {
        throw ServiceError(409, "event is already cancelled.");
    }
    event.status = EventStatus::CANCELLED;
    event.updated_at = NowIso();
    postgres_store_->UpsertEvent(event);
    AddParticipantsOutbox(event, "EVENT_CANCELLED", "Событие отменено", "Событие " + event.title + " отменено.");
    ProcessOutbox();
    return event;
}

Event GatherlyService::GetEvent(int64_t current_user_id, int64_t event_id) const {
    const std::optional<Event> event = postgres_store_->GetEvent(event_id);
    if (!event.has_value()) {
        throw ServiceError(404, "event not found.");
    }
    if (!CanViewEvent(current_user_id, event.value())) {
        throw ServiceError(403, "event is not visible for current user.");
    }
    return event.value();
}

std::vector<Event> GatherlyService::ListPublicEvents() const {
    return ListPublicEvents(PageRequest{});
}

std::vector<Event> GatherlyService::ListPublicEvents(PageRequest page) const {
    page = NormalizePage(page);
    return postgres_store_->ListPublicEvents(page.limit, page.offset);
}

std::vector<Event> GatherlyService::ListFeed(int64_t current_user_id) const {
    return ListFeed(current_user_id, PageRequest{});
}

std::vector<Event> GatherlyService::ListFeed(int64_t current_user_id, PageRequest page) const {
    page = NormalizePage(page);
    return postgres_store_->ListFeed(current_user_id, page.limit, page.offset);
}

Participation GatherlyService::JoinEvent(int64_t current_user_id, int64_t event_id) {
    std::optional<Event> stored_event = postgres_store_->GetEvent(event_id);
    if (!stored_event.has_value()) {
        throw ServiceError(404, "event not found.");
    }
    const Event& event = stored_event.value();
    if (event.status != EventStatus::PUBLISHED) {
        throw ServiceError(409, "only published event can be joined.");
    }
    if (!CanViewEvent(current_user_id, event)) {
        throw ServiceError(403, "event is not visible for current user.");
    }
    if (postgres_store_->FindParticipation(current_user_id, event_id).has_value()) {
        throw ServiceError(409, "participation already exists.");
    }

    const bool full = event.participant_limit > 0 && postgres_store_->CountGoingParticipants(event_id) >= event.participant_limit;
    const std::string now = NowIso();
    Participation participation;
    participation.id = postgres_store_->NextParticipationId();
    participation.event_id = event_id;
    participation.user_id = current_user_id;
    participation.status = full ? ParticipationStatus::WAITLISTED : ParticipationStatus::GOING;
    participation.created_at = now;
    participation.updated_at = now;
    postgres_store_->UpsertParticipation(participation);
    return participation;
}

Participation GatherlyService::SetMyParticipationStatus(int64_t current_user_id, int64_t event_id, ParticipationStatus status) {
    std::optional<Participation> participation = postgres_store_->FindParticipation(current_user_id, event_id);
    if (!participation.has_value()) {
        throw ServiceError(404, "participation not found.");
    }
    if (status == ParticipationStatus::GOING) {
        const std::optional<Event> event = postgres_store_->GetEvent(event_id);
        if (!event.has_value()) {
            throw ServiceError(404, "event not found.");
        }
        if (event->participant_limit > 0 && postgres_store_->CountGoingParticipants(event_id) >= event->participant_limit &&
            participation->status != ParticipationStatus::GOING) {
            status = ParticipationStatus::WAITLISTED;
        }
    }
    participation->status = status;
    participation->updated_at = NowIso();
    postgres_store_->UpsertParticipation(participation.value());
    return participation.value();
}

void GatherlyService::LeaveEvent(int64_t current_user_id, int64_t event_id) {
    const std::optional<Participation> participation = postgres_store_->FindParticipation(current_user_id, event_id);
    if (!participation.has_value()) {
        throw ServiceError(404, "participation not found.");
    }
    const bool was_going = participation->status == ParticipationStatus::GOING;
    postgres_store_->RemoveParticipation(event_id, current_user_id);
    if (was_going) {
        PromoteFirstWaitlisted(event_id);
    }
}

std::vector<Participation> GatherlyService::ListParticipants(int64_t current_user_id, int64_t event_id) const {
    const std::optional<Event> event = postgres_store_->GetEvent(event_id);
    if (!event.has_value()) {
        throw ServiceError(404, "event not found.");
    }
    EnsureOrganizer(current_user_id, event.value());
    return postgres_store_->ListEventParticipations(event_id);
}

std::vector<Participation> GatherlyService::ListMyParticipations(int64_t current_user_id, PageRequest page) const {
    page = NormalizePage(page);
    if (!postgres_store_->UserExists(current_user_id)) {
        throw ServiceError(404, "user not found.");
    }
    return postgres_store_->ListUserParticipations(current_user_id, page.limit, page.offset);
}

std::vector<Notification> GatherlyService::ListNotifications(int64_t current_user_id) const {
    return ListNotifications(current_user_id, PageRequest{});
}

std::vector<Notification> GatherlyService::ListNotifications(int64_t current_user_id, PageRequest page) const {
    page = NormalizePage(page);
    return postgres_store_->ListNotifications(current_user_id, page.limit, page.offset);
}

Notification GatherlyService::MarkNotificationRead(int64_t current_user_id, int64_t notification_id) {
    std::optional<Notification> notification = postgres_store_->GetNotification(notification_id);
    if (!notification.has_value() || notification->user_id != current_user_id) {
        throw ServiceError(404, "notification not found.");
    }
    notification->status = NotificationStatus::READ;
    notification->read_at = NowIso();
    postgres_store_->UpsertNotification(notification.value());
    return notification.value();
}

NotificationMode GatherlyService::SetNotificationMode(NotificationMode mode) {
    notification_mode_ = mode;
    return mode;
}

int64_t GatherlyService::ProcessOutbox() {
    int64_t delivered = 0;
    for (NotificationOutboxRecord record : postgres_store_->ListPendingOutbox()) {
        if (notification_mode_ == NotificationMode::FAILING) {
            record.status = OutboxStatus::FAILED;
            record.last_error = "fake notification channel failure";
            ++record.attempts;
            postgres_store_->UpsertOutboxRecord(record);
            continue;
        }

        Notification notification;
        notification.id = postgres_store_->NextNotificationId();
        notification.user_id = record.user_id;
        notification.type = record.type;
        notification.title = record.title;
        notification.text = record.text;
        notification.status = NotificationStatus::NEW;
        notification.created_at = NowIso();
        postgres_store_->UpsertNotification(notification);
        ++delivered;

        record.status = OutboxStatus::PROCESSED;
        record.processed_at = NowIso();
        record.last_error.reset();
        ++record.attempts;
        postgres_store_->UpsertOutboxRecord(record);
    }
    return delivered;
}

std::vector<NotificationOutboxRecord> GatherlyService::ListOutbox() const {
    return ListOutbox(PageRequest{});
}

std::vector<NotificationOutboxRecord> GatherlyService::ListOutbox(PageRequest page) const {
    page = NormalizePage(page);
    return postgres_store_->ListOutbox(page.limit, page.offset);
}

void GatherlyService::StartOutboxWorker() {
    bool expected = false;
    if (!worker_running_.compare_exchange_strong(expected, true)) {
        return;
    }
    worker_thread_ = std::thread([this] {
        int ticks = 0;
        while (worker_running_) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            if (!worker_running_) {
                break;
            }
            if (++ticks < 10) {
                continue;
            }
            ticks = 0;
            try {
                ProcessOutbox();
            } catch (...) {
            }
        }
    });
}

void GatherlyService::StopOutboxWorker() {
    bool expected = true;
    if (!worker_running_.compare_exchange_strong(expected, false)) {
        return;
    }
    if (worker_thread_.joinable()) {
        worker_thread_.join();
    }
}

bool GatherlyService::CanViewEvent(int64_t current_user_id, const Event& event) const {
    if (event.organizer_id == current_user_id || postgres_store_->FindParticipation(current_user_id, event.id).has_value()) {
        return true;
    }
    if (event.status != EventStatus::PUBLISHED) {
        return false;
    }
    if (event.visibility == EventVisibility::PUBLIC) {
        return true;
    }
    if (event.visibility == EventVisibility::FOLLOWERS_ONLY) {
        return postgres_store_->IsFollowing(current_user_id, event.organizer_id);
    }
    return false;
}

PageRequest GatherlyService::NormalizePage(PageRequest page) const {
    if (page.limit <= 0) {
        page.limit = 50;
    }
    if (page.limit > 100) {
        page.limit = 100;
    }
    if (page.offset < 0) {
        page.offset = 0;
    }
    return page;
}

void GatherlyService::ValidateRegisterCommand(const RegisterUserCommand& command) const {
    if (command.username.empty() || command.email.empty() || command.password.empty()) {
        throw ServiceError(400, "username, email and password are required.");
    }
    if (command.username.size() < 3 || command.username.size() > 32 || !HasOnlyUserNameChars(command.username)) {
        throw ServiceError(400, "username must be 3-32 chars and contain only letters, digits, '_' or '-'.");
    }
    if (!ContainsAtSign(command.email) || command.email.size() > 120) {
        throw ServiceError(400, "email must be valid enough for MVP.");
    }
    if (command.password.size() < 6) {
        throw ServiceError(400, "password must contain at least 6 characters.");
    }
}

void GatherlyService::ValidateEventCommand(const EventCommand& command) const {
    if (command.title.empty() || command.location.empty() || command.starts_at.empty()) {
        throw ServiceError(400, "title, location and starts_at are required.");
    }
    if (command.title.size() > 120) {
        throw ServiceError(400, "title is too long.");
    }
    if (command.location.size() > 160) {
        throw ServiceError(400, "location is too long.");
    }
    if (command.description.size() > 2000) {
        throw ServiceError(400, "description is too long.");
    }
    if (!command.ends_at.empty() && command.ends_at <= command.starts_at) {
        throw ServiceError(400, "ends_at must be later than starts_at.");
    }
    if (command.participant_limit < 0) {
        throw ServiceError(400, "participant_limit cannot be negative.");
    }
    if (command.participant_limit > 10000) {
        throw ServiceError(400, "participant_limit is too large.");
    }
}

void GatherlyService::EnsureOrganizer(int64_t current_user_id, const Event& event) const {
    if (event.organizer_id != current_user_id) {
        throw ServiceError(403, "only organizer can perform this action.");
    }
}

void GatherlyService::AddOutboxRecord(int64_t user_id, const std::string& type, const std::string& title, const std::string& text) {
    NotificationOutboxRecord record;
    record.id = postgres_store_->NextOutboxId();
    record.user_id = user_id;
    record.type = type;
    record.title = title;
    record.text = text;
    record.status = OutboxStatus::PENDING;
    record.created_at = NowIso();
    postgres_store_->UpsertOutboxRecord(record);
}

void GatherlyService::AddFollowersPublishOutbox(const Event& event) {
    for (const User& follower : postgres_store_->ListFollowers(event.organizer_id)) {
        AddOutboxRecord(follower.id, "EVENT_PUBLISHED", "Новое событие от пользователя из подписок", "Опубликовано событие: " + event.title + ".");
    }
}

void GatherlyService::AddSelectedPublishOutbox(const Event& event, const std::vector<int64_t>& user_ids) {
    const std::set<int64_t> unique_user_ids(user_ids.begin(), user_ids.end());
    for (const int64_t user_id : unique_user_ids) {
        if (!postgres_store_->UserExists(user_id)) {
            throw ServiceError(404, "user not found.");
        }
        if (!postgres_store_->IsFollowing(user_id, event.organizer_id)) {
            throw ServiceError(403, "notification recipient must follow organizer.");
        }
    }
    for (const int64_t user_id : unique_user_ids) {
        AddOutboxRecord(user_id, "EVENT_PUBLISHED", "Новое событие от пользователя из подписок", "Опубликовано событие: " + event.title + ".");
    }
}

void GatherlyService::AddParticipantsOutbox(const Event& event, const std::string& type, const std::string& title, const std::string& text) {
    for (const Participation& participation : postgres_store_->ListEventParticipations(event.id)) {
        if (participation.user_id != event.organizer_id && participation.status != ParticipationStatus::DECLINED) {
            AddOutboxRecord(participation.user_id, type, title, text);
        }
    }
}

void GatherlyService::PromoteFirstWaitlisted(int64_t event_id) {
    for (Participation participation : postgres_store_->ListEventParticipations(event_id)) {
        if (participation.status == ParticipationStatus::WAITLISTED) {
            participation.status = ParticipationStatus::GOING;
            participation.updated_at = NowIso();
            postgres_store_->UpsertParticipation(participation);
            return;
        }
    }
}

} // namespace gatherly
