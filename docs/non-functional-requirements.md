# Нефункциональные требования

- Локальный запуск должен занимать секунды и требовать только CMake и C++ compiler.
- Core service должен быть независим от HTTP-транспорта.
- Бизнес-правила должны быть детерминированными и покрытыми тестами.
- Доставка уведомлений должна быть eventually recoverable.
- API errors должны быть явными и использовать стабильные status codes.
- MVP хранит данные в памяти, поэтому durability пока не гарантируется.
- Docker Compose содержит app service, Swagger UI profile и PostgreSQL profile с init schema.
- Runtime persistence пока in-memory, но схема PostgreSQL уже описывает целевую модель.
- Background worker outbox запускается в сервисе и может управляться через admin endpoints.
