# Sequence diagrams

## Публикация события с созданием уведомлений

```mermaid
sequenceDiagram
    participant Alice
    participant API
    participant Service
    participant Outbox
    participant Notifications

    Alice->>API: POST /events/{id}/publish
    API->>Service: PublishEvent(alice, event)
    Service->>Service: validate organizer and DRAFT status
    Note over Service,Outbox: One transaction: event becomes PUBLISHED + outbox rows
    Service->>Outbox: create records for followers
    Note over Service,Outbox: COMMIT
    Service->>Notifications: best-effort delivery after commit
    Note over Outbox,Notifications: One transaction: insert notification + mark PROCESSED
    API-->>Alice: 200 PUBLISHED
```

## Присоединение к событию

```mermaid
sequenceDiagram
    participant Bob
    participant API
    participant Service

    Bob->>API: POST /events/{id}/participants
    API->>Service: JoinEvent(bob, event)
    Note over Service: BEGIN + advisory lock
    Service->>Service: check visibility and duplicates
    Service->>Service: check participant limit
    Service->>Service: save participation and COMMIT
    Service-->>API: GOING or WAITLISTED
    API-->>Bob: 201 participation
```

## Отказ уведомлений и повторная обработка

```mermaid
sequenceDiagram
    participant Alice
    participant Service
    participant Outbox
    participant Bob

    Note over Service: notification mode = FAILING
    Alice->>Service: publish event
    Service->>Outbox: create PENDING records
    Service->>Outbox: mark FAILED
    Service-->>Alice: event remains PUBLISHED
    Note over Service: notification mode = RECOVERY
    Service->>Service: background worker polls outbox
    Service->>Bob: create in-app notification
    Service->>Outbox: mark PROCESSED
```

При SQL-ошибке доставки партия откатывается и повторяется worker; публикация
уже сохранена. FAILED/RECOVERY выше — режимы демонстрационного канала в сервисных
тестах, а не публичные HTTP-переключатели.
