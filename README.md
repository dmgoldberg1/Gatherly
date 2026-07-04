# Gatherly

Gatherly — небольшая социальная платформа для организации неформальных встреч с друзьями: настолки, футбол, кино, прогулки, вечеринки и учебные встречи.

Приложение предоставляет пользовательский интерфейс, HTTP API и PostgreSQL-хранилище для сценариев с подписками, событиями, участием и уведомлениями.

## Возможности

- Регистрация и вход пользователей через bearer token.
- Подписки и отписки от пользователей.
- Создание, обновление, публикация и отмена событий.
- Публичная лента событий и feed по организаторам, на которых подписан пользователь.
- Участие в событии с лимитом мест и waitlist.
- Встроенные уведомления внутри приложения.
- OpenAPI-контракт и Swagger UI.

## Технологический стек

- C++23
- CMake
- Drogon
- PostgreSQL
- libpqxx
- HTML/CSS/JavaScript
- Docker Compose

## Локальная сборка и тесты

Локальная CMake-сборка собирает доменное ядро и unit-тесты.

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

## Docker

Основной запуск поднимает приложение и PostgreSQL с применением schema init:

```bash
docker compose up --build
```

Приложение будет доступно на `http://localhost:8080`.

Swagger UI для `docs/openapi.yaml`:

```bash
docker compose --profile docs up swagger-ui
```

Swagger UI будет доступен на `http://localhost:8081`.

## Документация

- [requirements.md](docs/requirements.md)
- [use-cases.md](docs/use-cases.md)
- [er-diagram.md](docs/er-diagram.md)
- [sequence-diagram.md](docs/sequence-diagram.md)
- [openapi.yaml](docs/openapi.yaml)
