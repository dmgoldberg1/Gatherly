# Gatherly

Gatherly — небольшая социальная платформа для организации неформальных встреч с друзьями: настолки, футбол, кино, прогулки, вечеринки и учебные встречи.

Проект сделан одновременно как backend pet project на C++ и как портфолио-кейс для системного анализа и system design.

## Возможности

- Регистрация и вход пользователей через простой token-based auth.
- Подписки и отписки от пользователей.
- Создание, обновление, публикация и отмена событий.
- Публичная лента событий и feed по организаторам, на которых подписан пользователь.
- Участие в событии с лимитом мест и waitlist.
- In-app уведомления через внутренний outbox для публикации и обновления событий.
- OpenAPI-контракт, Swagger UI через Docker Compose и документация для системного анализа.
- JWT-like bearer tokens для MVP без server-side token storage.
- PostgreSQL schema migration в `db/migrations/001_init.sql`.
- Пагинация списковых endpoints через `limit` и `offset`.

## Технологический стек

- C++23
- CMake
- Drogon HTTP backend в Docker/runtime-сборке
- PostgreSQL store в runtime: состояние загружается из БД при старте и изменения сразу пишутся обратно
- Статический frontend на HTML/CSS/JS
- PostgreSQL DDL migration и runtime PostgreSQL container
- Docker Compose с приложением, PostgreSQL и Swagger UI profile

Runtime-сборка использует Drogon и PostgreSQL store как единственный production-источник данных. Unit-тесты используют test double для `IPostgresStore`, чтобы быстро проверять доменную логику без Docker.

## Локальная сборка и тесты

Локальная CMake-сборка собирает доменное ядро и unit-тесты. Приложение запускается через Docker Compose.

## Запуск тестов

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

## Docker

Основной запуск поднимает Drogon-приложение и PostgreSQL с применением schema init:

```bash
docker compose up --build
```

Swagger UI для `docs/openapi.yaml`:

```bash
docker compose --profile docs up swagger-ui
```

После запуска открыть `http://localhost:8081`.

## Демо-сценарий

1. Зарегистрировать Alice.
2. Зарегистрировать Bob.
3. Войти под обоими пользователями.
4. Bob подписывается на Alice.
5. Alice создаёт событие `Настолки в субботу`.
6. Alice публикует событие.
7. Bob видит событие в подборке от подписок.
8. Bob открывает событие и записывается.
9. Alice видит Bob в списке участников на главной вкладке.
10. Данные обновляются автоматически без ручной кнопки обновления.

## Примеры API

```bash
curl -s -X POST localhost:8080/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","email":"alice@gatherly.local","password":"password"}'
```

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"password"}' | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
```

```bash
curl -s -X POST localhost:8080/api/v1/events \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Настолки в субботу","location":"Дом Alice","starts_at":"2026-07-04T18:00:00Z","category":"BOARD_GAMES","visibility":"PUBLIC","participant_limit":4}'
```

## Документация

- [requirements.md](docs/requirements.md)
- [use-cases.md](docs/use-cases.md)
- [api-contract.md](docs/api-contract.md)
- [event-status-model.md](docs/event-status-model.md)
- [notification-outbox.md](docs/notification-outbox.md)
- [er-diagram.md](docs/er-diagram.md)
- [sequence-diagram.md](docs/sequence-diagram.md)
- [non-functional-requirements.md](docs/non-functional-requirements.md)
- [adr.md](docs/adr.md)
- [openapi.yaml](docs/openapi.yaml)

## Скриншоты

Скриншоты можно добавить после запуска локального UI:

- Главная вкладка с моими мероприятиями и подборкой.
- Поиск людей и событий.
- Личный кабинет и создание мероприятия.

## Польза для стажировок

Для backend-собеседований Gatherly показывает API design, сервисный слой с бизнес-правилами, границу авторизации, обработку ошибок, тесты, CMake и Docker.

Для собеседований по системному анализу проект содержит требования, use cases, модель статусов, ER-диаграмму, sequence diagrams, ADR и OpenAPI.

## Что уже закрыто из TODO

- Добавлена PostgreSQL schema migration: `db/migrations/001_init.sql`.
- Docker Compose умеет поднять PostgreSQL с init schema.
- Простые opaque tokens заменены на JWT-like bearer tokens для MVP.
- Добавлен background worker для polling outbox.
- Добавлен Swagger UI profile в Docker Compose.
- Добавлены `limit`/`offset` для списковых endpoints и более строгая валидация.

## Следующие улучшения

- Заменить dev-hash подпись JWT-like token на криптографический HMAC-SHA256.
- Добавить миграционный runner вместо init-only SQL.
- Добавить e2e smoke tests для HTTP API и UI.
