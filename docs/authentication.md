# Authentication

API по-прежнему принимает `Authorization: Bearer <token>`.
`POST /api/v1/auth/login` возвращает `user` и `token`.

## Пароли

Новые пароли хранятся как PBKDF2-HMAC-SHA256: 600 000 итераций,
случайная 16-байтная соль и 32-байтный результат. Реализация использует
OpenSSL, который также нужен для JWT; отдельная crypto-библиотека не требуется.
Параметры и соль сохраняются вместе с хешем в существующем `password_hash`.

Старые записи `plain:...` автоматически заменяются хешем после успешного входа.
Неправильный пароль не меняет запись. До первого успешного входа старые записи
остаются в прежнем формате. Таблицы и аккаунты не удаляются.

## Токены

jwt-cpp проверяет JWT с алгоритмом HS256, issuer `gatherly` и типом `JWT`.
`sub` содержит ID пользователя, `iat` — время выдачи, `exp` — срок действия.
Токен действует 24 часа. Приложение требует `iat`/`exp`, проверяет положительный
ID и наличие пользователя в хранилище. Алгоритм `none` не разрешён.

Секрет задаётся переменной `GATHERLY_AUTH_SECRET` и должен содержать минимум
32 байта. Для постоянного локального ключа можно создать `.env`:

```bash
(umask 077; set -C; printf 'GATHERLY_AUTH_SECRET=%s\n' "$(openssl rand -hex 32)" > .env)
docker compose up --build
```

Команда откажется перезаписывать существующий `.env`. Файл исключён из Git
и Docker build context. Секрет не выводится в логи.

Без переменной сервер создаёт случайный временный ключ. Это сохраняет простой
локальный запуск, но после перезапуска потребуется войти снова. На VM постоянный
ключ будет задаваться Ansible. Замена ключа также аннулирует выданные токены.

Старые DEV-HASH токены больше не принимаются. Logout сейчас удаляет токен только
из браузера: серверный отзыв и refresh tokens не реализованы.

## Сборка и проверка

Локальной CMake-сборке нужны заголовки OpenSSL (`openssl-devel` в Fedora,
`libssl-dev` в Debian/Ubuntu). CMake загружает jwt-cpp v0.7.1 с проверкой SHA256;
первая настройка требует доступа к GitHub. Dockerfile устанавливает зависимости.

```bash
cmake -S . -B build
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Тесты проверяют salted hashing, неправильные пароли, обновление старых записей,
подпись JWT, обязательные claims и границу срока действия без ожидания по часам.

Источники: [OpenSSL PBKDF2](https://docs.openssl.org/3.0/man3/PKCS5_PBKDF2_HMAC/),
[OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
[jwt-cpp](https://github.com/Thalhammer/jwt-cpp/tree/v0.7.1).
