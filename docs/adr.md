# Architecture Decision Records

## ADR-001: C++ backend

Статус: accepted.

Gatherly реализован на современном C++, чтобы показать backend engineering в менее типичном стеке. Проект демонстрирует HTTP handling, domain modeling, memory-safe ownership и CMake-based delivery.

## ADR-002: Outbox pattern

Статус: accepted.

Уведомления создаются через outbox-like record, а не только через прямой side effect. Это отделяет публикацию события от отказов notification channel и делает retries явными.

## ADR-003: Simple token auth для MVP

Статус: accepted.

MVP использует opaque tokens, возвращаемые при login. Так авторизация остаётся понятной и удобной для demo. Позже эту границу можно заменить JWT validation.

## ADR-004: In-app notifications first

Статус: accepted.

In-app уведомления не требуют email/SMS зависимостей и всё равно демонстрируют delivery semantics, read status, failure, retry и user-facing notification list.

## ADR-005: PostgreSQL как production-хранилище

Статус: accepted.

Production runtime использует PostgreSQL как единственный источник данных. Unit-тесты могут подменять `IPostgresStore` test double-реализацией, но production-код больше не содержит отдельную in-memory database.


## ADR-006: PostgreSQL schema first

Статус: accepted.

Docker-сборка ставит `libpqxx` и включает PostgreSQL store: сервис читает users, follows, events, participations, notifications и outbox из PostgreSQL перед доменными операциями, а write-операции сразу сохраняет обратно в PostgreSQL.

## ADR-007: Background worker без публичного admin API

Статус: accepted.

Outbox worker запускается вместе с сервисом и периодически повторяет pending/failed records. Admin endpoints для ручного переключения режимов и обработки outbox удалены из runtime API, чтобы MVP оставался пользовательским продуктом без отладочного HTTP-слоя.


## ADR-008: Drogon-only runtime

Статус: accepted.

Production-like запуск в Docker использует Drogon. Старый самописный fallback server удалён: в проекте остаётся один runtime-путь, а локальная сборка нужна для быстрых unit-тестов доменной логики.
