# Use cases

[Русский](use-cases.md) | **English**

## User registration

1. The user submits a username, email, and password.
2. The system validates required fields and uniqueness.
3. The system creates the user and returns their profile.

## Following another user

1. Bob is signed in.
2. Bob follows Alice.
3. The system rejects self-following and duplicate follows.
4. Alice's future published events can appear in Bob's feed.

## Creating and publishing an event

1. Alice creates a draft event.
2. Alice updates the draft details as needed.
3. Alice publishes the event.
4. The system creates notification outbox records for Alice's followers.
5. The notification processor delivers in-app notifications when the channel is operational.

## Joining an event

1. Bob opens a visible published event.
2. Bob joins the event.
3. The system creates a participation with status `GOING` when capacity remains.
4. When the event is full, Bob receives status `WAITLISTED`.

## Event update notification

1. Alice changes the event time or location.
2. The system saves the changes.
3. The system creates outbox records for participants.
4. Participants receive in-app notifications after outbox processing.

## Notification delivery failure and recovery

1. The service's internal notification mode is `FAILING`.
2. Alice publishes an event.
3. The event is published successfully, but outbox records receive status `FAILED`.
4. The notification mode changes to `RECOVERY`.
5. The built-in worker or an explicit service-level processing call retries the outbox.
6. Failed records are processed again and notifications are delivered.
