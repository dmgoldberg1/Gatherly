# API Contract

Base path: `/api/v1`.

Списковые endpoints поддерживают `limit` и `offset`, например `/events?limit=20&offset=0`.

Авторизация: для защищённых endpoints нужно передавать `Authorization: Bearer <token>`.

## Auth

- `POST /auth/register`
- `POST /auth/login`

## Личный кабинет

- `GET /me`
- `GET /me/participations`

## Пользователи и подписки

- `GET /users`
- `GET /users/{id}`
- `POST /users/{id}/follow`
- `DELETE /users/{id}/follow`
- `GET /users/{id}/followers`
- `GET /users/{id}/following`

## События

- `POST /events`
- `PATCH /events/{id}`
- `POST /events/{id}/publish` — без тела уведомляет всех подписчиков; с `notify_user_ids` уведомляет выбранных подписчиков
- `POST /events/{id}/cancel`
- `GET /events/{id}`
- `GET /events`
- `GET /feed`

## Участие

- `POST /events/{id}/participants`
- `PATCH /events/{id}/participants/me`
- `DELETE /events/{id}/participants/me`
- `GET /events/{id}/participants`

## Уведомления и outbox

- `GET /notifications`
- `PATCH /notifications/{id}/read`
