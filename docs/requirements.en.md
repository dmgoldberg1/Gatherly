# Gatherly requirements

[Русский](requirements.md) | **English**

## Functional requirements

- Users can register and sign in.
- Users can view profiles and browse the user list.
- Users can follow and unfollow other users.
- Users cannot follow themselves or create duplicate follows.
- Organizers can create, edit, publish, and cancel events.
- The public event list only shows published public events.
- Users have a separate feed of events from people they follow.
- Users can open events, join or leave them, and change their participation status.
- New participants enter the waitlist when an event has no remaining places.
- When a participant leaves, the first person on the waitlist gets the place.
- Users receive notifications about relevant event publications, updates, and cancellations.

## Nonfunctional requirements

- The application and background processes run on Python; unit tests use `python -m unittest discover -s tests_python -v`.
- The primary application launch uses Docker Compose.
- Application data is stored in PostgreSQL.
- The API returns meaningful HTTP status codes and JSON errors.
- Business logic is tested separately from the HTTP layer.
- A notification delivery failure must not break the user's primary action.
- Documentation remains short and clear.
