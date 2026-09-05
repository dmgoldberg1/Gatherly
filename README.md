# Gatherly

Демо: [открыть Gatherly](https://meet-greet.158.160.194.229.sslip.io).

Платформа неформальных событий на C++: регистрация и JWT, профили, подписки,
публикация событий, участие с лимитом мест и FIFO waitlist, уведомления через
persistent outbox. Интерфейс на обычном HTML/CSS/JavaScript.

- Drogon → сервисный слой → PostgreSQL; транзакции защищают публикацию и capacity.
- PBKDF2-SHA256 для паролей, JWT HS256, корректные Unicode JSON и UTC timestamps.
- Multi-stage Docker, Nginx, миграции, liveness/readiness, backup/restore.
- Одна небольшая Yandex VM: Terraform создаёт инфраструктуру, Ansible развёртывает Compose.
- Prometheus, node/postgres exporters и provisioned Grafana: HTTP, outbox, CPU, память, диск.
- GitHub Actions: сборка, тесты отказов, проверка инфраструктуры, образы в GHCR.

```bash
docker compose up --build -d
# http://localhost:8080
python3 scripts/smoke.py http://localhost:8080
```

Swagger UI: `docker compose --profile docs up -d`, затем http://localhost:8081.
Пароль локальной БД — `gatherly`; production-секреты хранятся вне Git.

Для native unit tests нужны CMake, C++23, OpenSSL headers, GTest и доступ к
FetchContent-зависимостям. Drogon/libpqxx нужны для серверного executable;
Docker устанавливает всё необходимое и выполняет тесты при сборке.

```bash
cmake -S . -B build
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Документация: [архитектура](docs/architecture.md), [развёртывание и HTTPS](docs/deployment.md),
[мониторинг и диагностика](docs/monitoring.md), [аутентификация](docs/authentication.md),
[OpenAPI](docs/openapi.yaml). Исходные [требования](docs/requirements.md),
[сценарии](docs/use-cases.md), [ER diagram](docs/er-diagram.md) и
[sequence diagram](docs/sequence-diagram.md) сохранены.

Стек: C++23, Drogon, libpqxx, PostgreSQL 16, OpenSSL, jwt-cpp, nlohmann/json,
GoogleTest, Docker Compose, Nginx, Terraform, Ansible, Prometheus, Grafana.
