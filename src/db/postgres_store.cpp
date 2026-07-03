#include "db/postgres_store.h"

#include <pqxx/pqxx>

#include <utility>

namespace gatherly {

namespace {

constexpr const char* kUserColumns = "id, username, email, password_hash, bio, avatar_url, created_at::text AS created_at";
constexpr const char* kUserColumnsWithUserAlias = "u.id, u.username, u.email, u.password_hash, u.bio, u.avatar_url, u.created_at::text AS created_at";
constexpr const char* kEventColumns = "id, organizer_id, title, description, location, starts_at::text AS starts_at, ends_at::text AS ends_at, photo_url, category, visibility, participant_limit, status, created_at::text AS created_at, updated_at::text AS updated_at";
constexpr const char* kEventColumnsWithEventAlias = "e.id, e.organizer_id, e.title, e.description, e.location, e.starts_at::text AS starts_at, e.ends_at::text AS ends_at, e.photo_url, e.category, e.visibility, e.participant_limit, e.status, e.created_at::text AS created_at, e.updated_at::text AS updated_at";
constexpr const char* kParticipationColumns = "id, event_id, user_id, status, created_at::text AS created_at, updated_at::text AS updated_at";
constexpr const char* kNotificationColumns = "id, user_id, type, title, text, status, created_at::text AS created_at, read_at::text AS read_at";
constexpr const char* kOutboxColumns = "id, user_id, type, title, text, status, attempts, created_at::text AS created_at, processed_at::text AS processed_at, last_error";

std::string OptionalText(const std::optional<std::string>& value) {
    return value.value_or("");
}

std::string TextOrEmpty(const pqxx::row& row, const char* name) {
    const auto field = row[name];
    return field.is_null() ? std::string{} : field.as<std::string>();
}

User UserFromRow(const pqxx::row& row) {
    User user;
    user.id = row["id"].as<int64_t>();
    user.username = row["username"].as<std::string>();
    user.email = row["email"].as<std::string>();
    user.password_hash = row["password_hash"].as<std::string>();
    user.bio = row["bio"].as<std::string>();
    user.avatar_url = row["avatar_url"].as<std::string>();
    user.created_at = row["created_at"].as<std::string>();
    return user;
}

Event EventFromRow(const pqxx::row& row) {
    Event event;
    event.id = row["id"].as<int64_t>();
    event.organizer_id = row["organizer_id"].as<int64_t>();
    event.title = row["title"].as<std::string>();
    event.description = row["description"].as<std::string>();
    event.location = row["location"].as<std::string>();
    event.starts_at = row["starts_at"].as<std::string>();
    event.ends_at = TextOrEmpty(row, "ends_at");
    event.photo_url = row["photo_url"].as<std::string>();
    event.category = row["category"].as<std::string>();
    event.visibility = EventVisibilityFromString(row["visibility"].as<std::string>());
    event.participant_limit = row["participant_limit"].as<int64_t>();
    event.status = EventStatusFromString(row["status"].as<std::string>());
    event.created_at = row["created_at"].as<std::string>();
    event.updated_at = row["updated_at"].as<std::string>();
    return event;
}

Participation ParticipationFromRow(const pqxx::row& row) {
    Participation participation;
    participation.id = row["id"].as<int64_t>();
    participation.event_id = row["event_id"].as<int64_t>();
    participation.user_id = row["user_id"].as<int64_t>();
    participation.status = ParticipationStatusFromString(row["status"].as<std::string>());
    participation.created_at = row["created_at"].as<std::string>();
    participation.updated_at = row["updated_at"].as<std::string>();
    return participation;
}

Notification NotificationFromRow(const pqxx::row& row) {
    Notification notification;
    notification.id = row["id"].as<int64_t>();
    notification.user_id = row["user_id"].as<int64_t>();
    notification.type = row["type"].as<std::string>();
    notification.title = row["title"].as<std::string>();
    notification.text = row["text"].as<std::string>();
    notification.status = NotificationStatusFromString(row["status"].as<std::string>());
    notification.created_at = row["created_at"].as<std::string>();
    if (!row["read_at"].is_null()) {
        notification.read_at = row["read_at"].as<std::string>();
    }
    return notification;
}

NotificationOutboxRecord OutboxRecordFromRow(const pqxx::row& row) {
    NotificationOutboxRecord record;
    record.id = row["id"].as<int64_t>();
    record.user_id = row["user_id"].as<int64_t>();
    record.type = row["type"].as<std::string>();
    record.title = row["title"].as<std::string>();
    record.text = row["text"].as<std::string>();
    record.status = OutboxStatusFromString(row["status"].as<std::string>());
    record.attempts = row["attempts"].as<int64_t>();
    record.created_at = row["created_at"].as<std::string>();
    if (!row["processed_at"].is_null()) {
        record.processed_at = row["processed_at"].as<std::string>();
    }
    if (!row["last_error"].is_null()) {
        record.last_error = row["last_error"].as<std::string>();
    }
    return record;
}

class PostgresStore : public IPostgresStore {
public:
    explicit PostgresStore(std::string connection_string)
        : connection_(std::move(connection_string)) {}

    int64_t NextUserId() override { return NextId("users"); }
    int64_t NextEventId() override { return NextId("events"); }
    int64_t NextParticipationId() override { return NextId("participations"); }
    int64_t NextNotificationId() override { return NextId("notifications"); }
    int64_t NextOutboxId() override { return NextId("notification_outbox"); }

    std::optional<User> GetUser(int64_t id) override {
        pqxx::read_transaction tx(connection_);
        const pqxx::result result = tx.exec_params(std::string("SELECT ") + kUserColumns + " FROM users WHERE id=$1", id);
        if (result.empty()) {
            return std::nullopt;
        }
        return UserFromRow(result.front());
    }

    std::optional<User> FindUserByLogin(const std::string& username_or_email) override {
        pqxx::read_transaction tx(connection_);
        const pqxx::result result = tx.exec_params(std::string("SELECT ") + kUserColumns + " FROM users WHERE username=$1 OR email=$1 ORDER BY id LIMIT 1", username_or_email);
        if (result.empty()) {
            return std::nullopt;
        }
        return UserFromRow(result.front());
    }

    bool UserExists(int64_t id) override {
        pqxx::read_transaction tx(connection_);
        return tx.exec_params("SELECT 1 FROM users WHERE id=$1", id).size() > 0;
    }

    bool UsernameExists(const std::string& username) override {
        pqxx::read_transaction tx(connection_);
        return tx.exec_params("SELECT 1 FROM users WHERE username=$1", username).size() > 0;
    }

    bool EmailExists(const std::string& email) override {
        pqxx::read_transaction tx(connection_);
        return tx.exec_params("SELECT 1 FROM users WHERE email=$1", email).size() > 0;
    }

    std::vector<User> ListUsers(int64_t limit, int64_t offset) override {
        pqxx::read_transaction tx(connection_);
        std::vector<User> users;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kUserColumns + " FROM users ORDER BY id LIMIT $1 OFFSET $2", limit, offset)) {
            users.push_back(UserFromRow(row));
        }
        return users;
    }

    std::optional<Event> GetEvent(int64_t id) override {
        pqxx::read_transaction tx(connection_);
        const pqxx::result result = tx.exec_params(std::string("SELECT ") + kEventColumns + " FROM events WHERE id=$1", id);
        if (result.empty()) {
            return std::nullopt;
        }
        return EventFromRow(result.front());
    }

    std::vector<Event> ListPublicEvents(int64_t limit, int64_t offset) override {
        pqxx::read_transaction tx(connection_);
        std::vector<Event> events;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kEventColumns + " FROM events WHERE status=$1 AND visibility=$2 ORDER BY id LIMIT $3 OFFSET $4", ToString(EventStatus::PUBLISHED), ToString(EventVisibility::PUBLIC), limit, offset)) {
            events.push_back(EventFromRow(row));
        }
        return events;
    }

    std::vector<Event> ListFeed(int64_t user_id, int64_t limit, int64_t offset) override {
        pqxx::read_transaction tx(connection_);
        std::vector<Event> events;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kEventColumnsWithEventAlias + " FROM events e JOIN follows f ON f.following_id=e.organizer_id WHERE f.follower_id=$1 AND e.status=$2 AND e.visibility IN ($3,$4) ORDER BY e.id LIMIT $5 OFFSET $6", user_id, ToString(EventStatus::PUBLISHED), ToString(EventVisibility::PUBLIC), ToString(EventVisibility::FOLLOWERS_ONLY), limit, offset)) {
            events.push_back(EventFromRow(row));
        }
        return events;
    }

    bool IsFollowing(int64_t follower_id, int64_t following_id) override {
        pqxx::read_transaction tx(connection_);
        return tx.exec_params("SELECT 1 FROM follows WHERE follower_id=$1 AND following_id=$2", follower_id, following_id).size() > 0;
    }

    std::vector<User> ListFollowers(int64_t user_id) override {
        pqxx::read_transaction tx(connection_);
        std::vector<User> users;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kUserColumnsWithUserAlias + " FROM users u JOIN follows f ON f.follower_id=u.id WHERE f.following_id=$1 ORDER BY u.id", user_id)) {
            users.push_back(UserFromRow(row));
        }
        return users;
    }

    std::vector<User> ListFollowing(int64_t user_id) override {
        pqxx::read_transaction tx(connection_);
        std::vector<User> users;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kUserColumnsWithUserAlias + " FROM users u JOIN follows f ON f.following_id=u.id WHERE f.follower_id=$1 ORDER BY u.id", user_id)) {
            users.push_back(UserFromRow(row));
        }
        return users;
    }

    std::optional<Participation> FindParticipation(int64_t user_id, int64_t event_id) override {
        pqxx::read_transaction tx(connection_);
        const pqxx::result result = tx.exec_params(std::string("SELECT ") + kParticipationColumns + " FROM participations WHERE user_id=$1 AND event_id=$2 ORDER BY id LIMIT 1", user_id, event_id);
        if (result.empty()) {
            return std::nullopt;
        }
        return ParticipationFromRow(result.front());
    }

    int64_t CountGoingParticipants(int64_t event_id) override {
        pqxx::read_transaction tx(connection_);
        return tx.exec_params("SELECT COUNT(*) FROM participations WHERE event_id=$1 AND status=$2", event_id, ToString(ParticipationStatus::GOING))[0][0].as<int64_t>();
    }

    std::vector<Participation> ListEventParticipations(int64_t event_id) override {
        pqxx::read_transaction tx(connection_);
        std::vector<Participation> participations;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kParticipationColumns + " FROM participations WHERE event_id=$1 ORDER BY id", event_id)) {
            participations.push_back(ParticipationFromRow(row));
        }
        return participations;
    }

    std::vector<Participation> ListUserParticipations(int64_t user_id, int64_t limit, int64_t offset) override {
        pqxx::read_transaction tx(connection_);
        std::vector<Participation> participations;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kParticipationColumns + " FROM participations WHERE user_id=$1 ORDER BY id LIMIT $2 OFFSET $3", user_id, limit, offset)) {
            participations.push_back(ParticipationFromRow(row));
        }
        return participations;
    }

    std::optional<Notification> GetNotification(int64_t id) override {
        pqxx::read_transaction tx(connection_);
        const pqxx::result result = tx.exec_params(std::string("SELECT ") + kNotificationColumns + " FROM notifications WHERE id=$1", id);
        if (result.empty()) {
            return std::nullopt;
        }
        return NotificationFromRow(result.front());
    }

    std::vector<Notification> ListNotifications(int64_t user_id, int64_t limit, int64_t offset) override {
        pqxx::read_transaction tx(connection_);
        std::vector<Notification> notifications;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kNotificationColumns + " FROM notifications WHERE user_id=$1 ORDER BY id LIMIT $2 OFFSET $3", user_id, limit, offset)) {
            notifications.push_back(NotificationFromRow(row));
        }
        return notifications;
    }

    std::vector<NotificationOutboxRecord> ListOutbox(int64_t limit, int64_t offset) override {
        pqxx::read_transaction tx(connection_);
        std::vector<NotificationOutboxRecord> outbox;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kOutboxColumns + " FROM notification_outbox ORDER BY id LIMIT $1 OFFSET $2", limit, offset)) {
            outbox.push_back(OutboxRecordFromRow(row));
        }
        return outbox;
    }

    std::vector<NotificationOutboxRecord> ListPendingOutbox() override {
        pqxx::read_transaction tx(connection_);
        std::vector<NotificationOutboxRecord> outbox;
        for (const auto& row : tx.exec_params(std::string("SELECT ") + kOutboxColumns + " FROM notification_outbox WHERE status<>$1 ORDER BY id", ToString(OutboxStatus::PROCESSED))) {
            outbox.push_back(OutboxRecordFromRow(row));
        }
        return outbox;
    }

    void UpsertUser(const User& user) override {
        pqxx::work tx(connection_);
        tx.exec_params(
            "INSERT INTO users(id, username, email, password_hash, bio, avatar_url, created_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7::timestamptz) "
            "ON CONFLICT(id) DO UPDATE SET username=EXCLUDED.username,email=EXCLUDED.email,"
            "password_hash=EXCLUDED.password_hash,bio=EXCLUDED.bio,avatar_url=EXCLUDED.avatar_url",
            user.id, user.username, user.email, user.password_hash, user.bio, user.avatar_url, user.created_at
        );
        tx.commit();
    }

    void UpsertEvent(const Event& event) override {
        pqxx::work tx(connection_);
        tx.exec_params(
            "INSERT INTO events(id, organizer_id, title, description, location, starts_at, ends_at, photo_url, category, visibility, participant_limit, status, created_at, updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6::timestamptz,NULLIF($7,'')::timestamptz,$8,$9,$10,$11,$12,$13::timestamptz,$14::timestamptz) "
            "ON CONFLICT(id) DO UPDATE SET title=EXCLUDED.title,description=EXCLUDED.description,location=EXCLUDED.location,starts_at=EXCLUDED.starts_at,"
            "ends_at=EXCLUDED.ends_at,photo_url=EXCLUDED.photo_url,category=EXCLUDED.category,visibility=EXCLUDED.visibility,participant_limit=EXCLUDED.participant_limit,status=EXCLUDED.status,updated_at=EXCLUDED.updated_at",
            event.id, event.organizer_id, event.title, event.description, event.location, event.starts_at, event.ends_at,
            event.photo_url, event.category, ToString(event.visibility), event.participant_limit, ToString(event.status), event.created_at, event.updated_at
        );
        tx.commit();
    }

    void UpsertParticipation(const Participation& participation) override {
        pqxx::work tx(connection_);
        tx.exec_params(
            "INSERT INTO participations(id, event_id, user_id, status, created_at, updated_at) "
            "VALUES($1,$2,$3,$4,$5::timestamptz,$6::timestamptz) "
            "ON CONFLICT(event_id, user_id) DO UPDATE SET status=EXCLUDED.status,updated_at=EXCLUDED.updated_at",
            participation.id, participation.event_id, participation.user_id, ToString(participation.status), participation.created_at, participation.updated_at
        );
        tx.commit();
    }

    void RemoveParticipation(int64_t event_id, int64_t user_id) override {
        pqxx::work tx(connection_);
        tx.exec_params("DELETE FROM participations WHERE event_id=$1 AND user_id=$2", event_id, user_id);
        tx.commit();
    }

    void UpsertNotification(const Notification& notification) override {
        pqxx::work tx(connection_);
        tx.exec_params(
            "INSERT INTO notifications(id, user_id, type, title, text, status, created_at, read_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7::timestamptz,NULLIF($8,'')::timestamptz) "
            "ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status,read_at=EXCLUDED.read_at",
            notification.id, notification.user_id, notification.type, notification.title, notification.text,
            ToString(notification.status), notification.created_at, OptionalText(notification.read_at)
        );
        tx.commit();
    }

    void UpsertOutboxRecord(const NotificationOutboxRecord& record) override {
        pqxx::work tx(connection_);
        tx.exec_params(
            "INSERT INTO notification_outbox(id, user_id, type, title, text, status, attempts, created_at, processed_at, last_error) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8::timestamptz,NULLIF($9,'')::timestamptz,NULLIF($10,'')) "
            "ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status,attempts=EXCLUDED.attempts,processed_at=EXCLUDED.processed_at,last_error=EXCLUDED.last_error",
            record.id, record.user_id, record.type, record.title, record.text, ToString(record.status), record.attempts,
            record.created_at, OptionalText(record.processed_at), OptionalText(record.last_error)
        );
        tx.commit();
    }

    void AddFollow(int64_t follower_id, int64_t following_id) override {
        pqxx::work tx(connection_);
        tx.exec_params(
            "INSERT INTO follows(follower_id, following_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
            follower_id, following_id
        );
        tx.commit();
    }

    void RemoveFollow(int64_t follower_id, int64_t following_id) override {
        pqxx::work tx(connection_);
        tx.exec_params("DELETE FROM follows WHERE follower_id=$1 AND following_id=$2", follower_id, following_id);
        tx.commit();
    }

protected:
    int64_t NextId(const std::string& table) {
        pqxx::work tx(connection_);
        const pqxx::result result = tx.exec("SELECT COALESCE(MAX(id), 0) + 1 FROM " + table);
        const int64_t id = result[0][0].as<int64_t>();
        tx.commit();
        return id;
    }

    pqxx::connection connection_;
};

} // namespace

std::shared_ptr<IPostgresStore> CreatePostgresStore(const std::string& connection_string) {
    return std::make_shared<PostgresStore>(connection_string);
}

} // namespace gatherly
