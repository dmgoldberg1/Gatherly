const STORAGE_KEY = "gatherly.session";
const PAGE_QUERY = "?limit=100&offset=0";
const AUTO_REFRESH_MS = 10000;

const state = {
  session: loadSession(),
  users: [],
  followers: [],
  following: [],
  followingIds: new Set(),
  publicEvents: [],
  feedEvents: [],
  history: [],
  selectedDetail: null,
  selectedHomeEventId: null,
  homeEventParticipants: new Map(),
  toastTimer: 0,
  autoRefreshTimer: 0,
  refreshPromise: null
};

const $ = (id) => document.getElementById(id);

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch (_) {
    return null;
  }
}

function saveSession(session) {
  state.session = session;
  if (session) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.className = `toast${isError ? " error" : ""}`;
  toast.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

function messageFor(error) {
  const text = error && error.message ? error.message : String(error);
  const map = {
    "username already exists.": "Такое имя пользователя уже занято.",
    "email already exists.": "Этот email уже используется.",
    "invalid credentials.": "Неверное имя пользователя или пароль.",
    "Authorization header is required.": "Нужно войти в аккаунт.",
    "invalid token.": "Сессия истекла, войдите снова.",
    "user is already followed.": "Вы уже подписаны на этого пользователя.",
    "follow relation not found.": "Подписка уже удалена.",
    "user cannot follow himself.": "На себя подписаться нельзя.",
    "participation already exists.": "Вы уже записаны на это мероприятие.",
    "participation not found.": "Запись на мероприятие не найдена.",
    "only published event can be joined.": "Записаться можно только на опубликованное мероприятие."
  };
  return map[text] || text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function itemsOf(value) {
  return Array.isArray(value) ? value : (value.items || []);
}

function authHeaders() {
  return state.session ? { Authorization: `Bearer ${state.session.token}` } : {};
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: {
      "Content-Type": "application/json",
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : authHeaders())
    },
    body: options.body ? JSON.stringify(options.body) : undefined
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    if (response.status === 401) {
      saveSession(null);
      renderShell();
    }
    throw new Error(data.error || response.statusText);
  }
  return data;
}

async function run(action) {
  try {
    return await action();
  } catch (error) {
    showToast(messageFor(error), true);
    throw error;
  }
}

function startAutoRefresh() {
  if (state.autoRefreshTimer) {
    return;
  }
  state.autoRefreshTimer = setInterval(() => {
    if (!state.session || document.hidden) {
      return;
    }
    refreshAll({ silent: true }).catch(() => {});
  }, AUTO_REFRESH_MS);
}

function stopAutoRefresh() {
  if (!state.autoRefreshTimer) {
    return;
  }
  clearInterval(state.autoRefreshTimer);
  state.autoRefreshTimer = 0;
}

function switchAuth(mode) {
  const login = mode === "login";
  $("loginTab").classList.toggle("active", login);
  $("registerTab").classList.toggle("active", !login);
  $("loginForm").hidden = !login;
  $("registerForm").hidden = login;
}

function switchScreen(screenId) {
  document.querySelectorAll(".screen").forEach((screen) => {
    screen.classList.toggle("active-screen", screen.id === screenId);
  });
  document.querySelectorAll(".main-nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.screen === screenId);
  });
  const titles = {
    homeScreen: "Главная",
    searchScreen: "Поиск",
    createScreen: "Создать",
    cabinetScreen: "Кабинет"
  };
  $("screenTitle").textContent = titles[screenId] || "Gatherly";
}

function renderShell() {
  const signedIn = Boolean(state.session && state.session.token);
  $("authView").hidden = signedIn;
  $("appView").hidden = !signedIn;
  if (signedIn) {
    $("currentUsername").textContent = `@${state.session.user.username}`;
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
}

function initials(user) {
  return (user.username || "G").slice(0, 2).toUpperCase();
}

function avatarHtml(user) {
  if (user.avatar_url) {
    return `<div class="avatar"><img src="${escapeHtml(user.avatar_url)}" alt=""></div>`;
  }
  return `<div class="avatar">${escapeHtml(initials(user))}</div>`;
}

function statusBadge(status) {
  const labels = {
    PUBLISHED: "Опубликовано",
    DRAFT: "Черновик",
    CANCELLED: "Отменено",
    GOING: "Записан",
    WAITLISTED: "Лист ожидания",
    MAYBE: "Возможно",
    DECLINED: "Не иду"
  };
  const color = status === "PUBLISHED" || status === "GOING" ? "green" :
    status === "WAITLISTED" || status === "MAYBE" ? "yellow" :
    status === "CANCELLED" || status === "DECLINED" ? "red" : "gray";
  return `<span class="badge ${color}">${escapeHtml(labels[status] || status || "")}</span>`;
}

function formatDate(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function toIsoFromLocal(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString();
}

function userById(id) {
  return state.users.find((user) => user.id === id) || null;
}

function eventById(id) {
  return allSearchableEvents().find((event) => event.id === id) || null;
}

function organizerName(event) {
  const user = userById(event.organizer_id);
  return user ? `@${user.username}` : `пользователь ${event.organizer_id}`;
}

function myParticipationFor(eventId) {
  return state.history.find((item) => item.event && item.event.id === eventId) || null;
}

function allSearchableEvents() {
  const byId = new Map();
  [...state.publicEvents, ...state.feedEvents, ...state.history.map((item) => item.event)].forEach((event) => {
    if (event && !byId.has(event.id)) {
      byId.set(event.id, event);
    }
  });
  return [...byId.values()];
}

function ownedEvents() {
  const currentId = state.session.user.id;
  return state.publicEvents.filter((event) => event.organizer_id === currentId);
}

async function refreshOwnedParticipantCache() {
  const events = ownedEvents();
  const eventIds = new Set(events.map((event) => event.id));
  [...state.homeEventParticipants.keys()].forEach((eventId) => {
    if (!eventIds.has(eventId)) {
      state.homeEventParticipants.delete(eventId);
    }
  });

  await Promise.all(events.map(async (event) => {
    const participants = await api(`/api/v1/events/${event.id}/participants`);
    state.homeEventParticipants.set(event.id, itemsOf(participants));
  }));
}

function homeEvents() {
  const byId = new Map();
  [...ownedEvents(), ...state.history.map((item) => item.event), ...state.feedEvents].forEach((event) => {
    if (event && !byId.has(event.id)) {
      byId.set(event.id, event);
    }
  });
  return [...byId.values()];
}

function userListItem(user, options = {}) {
  const followed = state.followingIds.has(user.id);
  const clickable = options.clickable !== false;
  const itemClass = clickable ? "item clickable" : "item";
  const selectAttr = clickable ? ` data-select-user="${user.id}"` : "";
  return `<article class="${itemClass}"${selectAttr}>
    <div class="item-header">
      <div class="item-person">
        ${avatarHtml(user)}
        <div>
          <div class="item-title">@${escapeHtml(user.username)}</div>
          <div class="item-meta">${escapeHtml(user.email)}</div>
        </div>
      </div>
      ${followed && !options.hideFollowBadge ? `<span class="badge green">Подписка</span>` : ""}
    </div>
    <div class="item-meta">${escapeHtml(user.bio || "Профиль без описания")}</div>
  </article>`;
}

function eventListItem(event, options = {}) {
  const participation = myParticipationFor(event.id);
  const badge = participation ? statusBadge(participation.participation.status) : statusBadge(event.status);
  const eventAttr = options.home ? `data-home-event="${event.id}"` : `data-select-event="${event.id}"`;
  return `<article class="item clickable" ${eventAttr}>
    <div class="item-header">
      <div>
        <div class="item-title">${escapeHtml(event.title)}</div>
        <div class="item-meta">${escapeHtml(organizerName(event))} · ${escapeHtml(event.category || "без категории")}</div>
      </div>
      ${badge}
    </div>
    <div class="item-meta">${escapeHtml(event.location)} · ${escapeHtml(formatDate(event.starts_at))}</div>
    ${options.description ? `<div>${escapeHtml(event.description || "Без описания")}</div>` : ""}
  </article>`;
}

function ownedEventListItem(event) {
  const selected = state.selectedHomeEventId === event.id;
  const participants = state.homeEventParticipants.get(event.id);
  const count = participants ? participants.length : 0;
  const countLabel = participants ? `${count} ${count === 1 ? "участник" : "участников"}` : "Участники";
  return `<article class="item clickable${selected ? " selected" : ""}" data-home-event="${event.id}">
    <div class="item-header">
      <div>
        <div class="item-title">${escapeHtml(event.title)}</div>
        <div class="item-meta">${escapeHtml(event.category || "без категории")}</div>
      </div>
      <span class="badge gray">${escapeHtml(countLabel)}</span>
    </div>
    <div class="item-meta">${escapeHtml(event.location)} · ${escapeHtml(formatDate(event.starts_at))}</div>
    <div>${escapeHtml(event.description || "Без описания")}</div>
  </article>`;
}

function renderEmpty(id, text) {
  const element = $(id);
  element.className = "item-list empty-state";
  element.textContent = text;
}

function renderHome() {
  renderOwnedEvents();

  if (!state.history.length) {
    renderEmpty("registeredEventsList", "Вы пока не записаны ни на одно мероприятие");
  } else {
    $("registeredEventsList").className = "item-list event-list-grid";
    $("registeredEventsList").innerHTML = state.history
      .map((item) => eventListItem(item.event, { description: true, home: true }))
      .join("");
  }

  if (!state.feedEvents.length) {
    renderEmpty("feedEventsList", "Подпишитесь на людей в поиске, и здесь появятся их мероприятия");
  } else {
    $("feedEventsList").className = "item-list event-list-grid";
    $("feedEventsList").innerHTML = state.feedEvents
      .map((event) => eventListItem(event, { description: true, home: true }))
      .join("");
  }

  if (state.selectedHomeEventId && !homeEvents().some((event) => event.id === state.selectedHomeEventId)) {
    closeHomeEventModal(false);
  }
  renderHomeEventModal();
}

function renderOwnedEvents() {
  const events = ownedEvents();
  if (!events.length) {
    renderEmpty("ownedEventsList", "Создайте первое мероприятие, и оно появится здесь");
    return;
  }

  $("ownedEventsList").className = "item-list event-list-grid";
  $("ownedEventsList").innerHTML = events.map((event) => ownedEventListItem(event)).join("");
}

function closeHomeEventModal(render = true) {
  state.selectedHomeEventId = null;
  if (render) {
    renderHomeEventModal();
  }
}

function participantRow(participation) {
  const user = userById(participation.user_id);
  const fallback = { id: participation.user_id, username: `user-${participation.user_id}`, email: "", bio: "", avatar_url: "" };
  const profile = user || fallback;
  return `<div class="participant-row">
    <div class="item-person">
      ${avatarHtml(profile)}
      <div>
        <div class="item-title">@${escapeHtml(profile.username)}</div>
        <div class="item-meta">${escapeHtml(profile.email || `пользователь ${participation.user_id}`)}</div>
      </div>
    </div>
    ${statusBadge(participation.status)}
  </div>`;
}

function ownerParticipantsHtml(event) {
  if (event.organizer_id !== state.session.user.id) {
    return "";
  }
  const participants = state.homeEventParticipants.get(event.id) || [];
  return `<div class="owner-participants">
    <div class="detail-title-row">
      <div>
        <p class="eyebrow">Участники</p>
        <h3>${participants.length} ${participants.length === 1 ? "участник" : "участников"}</h3>
      </div>
    </div>
    <div class="participant-list">
      ${participants.length ? participants.map(participantRow).join("") : `<div class="empty-state small-empty">Пока никто не записался</div>`}
    </div>
  </div>`;
}

function renderHomeEventModal() {
  const overlay = $("homeEventOverlay");
  const target = $("homeEventModalCard");
  const event = state.selectedHomeEventId
    ? homeEvents().find((item) => item.id === state.selectedHomeEventId)
    : null;

  if (!event) {
    overlay.hidden = true;
    target.innerHTML = "";
    return;
  }

  overlay.hidden = false;
  target.className = "profile-card";
  target.innerHTML = `${eventDetailHtml(event)}${ownerParticipantsHtml(event)}`;
}

async function selectHomeEvent(eventId) {
  const event = homeEvents().find((item) => item.id === eventId);
  if (!event) {
    return;
  }

  state.selectedHomeEventId = eventId;
  $("homeEventOverlay").hidden = false;
  $("homeEventModalCard").innerHTML = `<div class="empty-state small-empty">Загружаем мероприятие</div>`;

  if (event.organizer_id === state.session.user.id && !state.homeEventParticipants.has(eventId)) {
    const participants = await api(`/api/v1/events/${eventId}/participants`);
    state.homeEventParticipants.set(eventId, itemsOf(participants));
  }
  renderHome();
}

function renderSearch() {
  renderSearchResults();
}

function searchQuery() {
  return $("searchQuery").value.trim().toLowerCase();
}

function matchingUsers(query) {
  const currentId = state.session.user.id;
  if (!query) {
    return [];
  }
  return state.users
    .filter((user) => user.id !== currentId)
    .filter((user) => {
      const haystack = `${user.username} ${user.email} ${user.bio}`.toLowerCase();
      return haystack.includes(query);
    });
}

function matchingEvents(query) {
  if (!query) {
    return [];
  }
  return allSearchableEvents().filter((event) => {
    const haystack = `${event.title} ${event.description} ${event.location} ${event.category}`.toLowerCase();
    return haystack.includes(query);
  });
}

function renderUserSearchResults(query = searchQuery()) {
  const users = matchingUsers(query);
  $("usersResultsPanel").hidden = !users.length;
  if (!users.length) {
    $("usersList").innerHTML = "";
    return;
  }

  $("usersList").className = "item-list compact-list";
  $("usersList").innerHTML = users.map((user) => userListItem(user)).join("");
}

function renderEventSearchResults(query = searchQuery()) {
  const events = matchingEvents(query);
  $("eventsResultsPanel").hidden = !events.length;
  if (!events.length) {
    $("eventsSearchList").innerHTML = "";
    return;
  }

  $("eventsSearchList").className = "item-list compact-list";
  $("eventsSearchList").innerHTML = events.map((event) => eventListItem(event)).join("");
}

function updateSearchEmptyState(query = searchQuery()) {
  const hasResults = matchingUsers(query).length > 0 || matchingEvents(query).length > 0;
  $("searchEmpty").hidden = !query || hasResults;
  $("searchEmpty").textContent = "Ничего не найдено";
}

function renderSearchResults() {
  const query = searchQuery();
  const users = matchingUsers(query);
  const events = matchingEvents(query);
  renderUserSearchResults(query);
  renderEventSearchResults(query);
  updateSearchEmptyState(query);

  if (!query || (!users.length && !events.length)) {
    state.selectedDetail = null;
  } else if (state.selectedDetail) {
    const selectedId = state.selectedDetail.id;
    const visible = state.selectedDetail.type === "user"
      ? users.some((user) => user.id === selectedId)
      : events.some((event) => event.id === selectedId);
    if (!visible) {
      state.selectedDetail = null;
    }
  }

  run(renderSearchDetail);
}

async function renderSearchDetail() {
  const panel = $("searchDetailPanel");
  const target = $("searchDetailCard");
  if (!state.selectedDetail) {
    panel.hidden = true;
    target.innerHTML = "";
    target.className = "";
    return;
  }
  panel.hidden = false;

  if (state.selectedDetail.type === "user") {
    const user = userById(state.selectedDetail.id);
    if (!user) {
      state.selectedDetail = null;
      return renderSearchDetail();
    }
    const [followers, following] = await Promise.all([
      api(`/api/v1/users/${user.id}/followers`),
      api(`/api/v1/users/${user.id}/following`)
    ]);
    const followed = state.followingIds.has(user.id);
    target.className = "profile-card";
    target.innerHTML = `<div class="profile-head">
      <div class="brand-row">
        ${avatarHtml(user)}
        <div class="profile-name">
          <h3>@${escapeHtml(user.username)}</h3>
          <div class="profile-meta">${escapeHtml(user.email)}</div>
        </div>
      </div>
      ${followed ? `<span class="badge green">Подписка</span>` : ""}
    </div>
    <p>${escapeHtml(user.bio || "Профиль без описания")}</p>
    <div class="metric-row">
      <div><span class="profile-meta">Подписчики</span><strong>${itemsOf(followers).length}</strong></div>
      <div><span class="profile-meta">Подписки</span><strong>${itemsOf(following).length}</strong></div>
    </div>
    <div class="profile-actions">
      <button type="button" data-follow-user="${user.id}">${followed ? "Отписаться" : "Подписаться"}</button>
    </div>`;
    return;
  }

  const event = eventById(state.selectedDetail.id);
  if (!event) {
    state.selectedDetail = null;
    return renderSearchDetail();
  }
  target.className = "profile-card";
  target.innerHTML = eventDetailHtml(event);
}

function eventDetailHtml(event) {
  const participation = myParticipationFor(event.id);
  const owner = event.organizer_id === state.session.user.id;
  const canJoin = !owner && !participation && event.status === "PUBLISHED";
  const limit = event.participant_limit > 0 ? `${event.participant_limit}` : "без лимита";
  return `<div class="detail-title-row">
    <div>
      <p class="eyebrow">Мероприятие</p>
      <h3>${escapeHtml(event.title)}</h3>
    </div>
    ${participation ? statusBadge(participation.participation.status) : statusBadge(event.status)}
  </div>
  <p>${escapeHtml(event.description || "Без описания")}</p>
  <div class="detail-table">
    <div><span>Организатор</span><strong>${escapeHtml(organizerName(event))}</strong></div>
    <div><span>Место</span><strong>${escapeHtml(event.location)}</strong></div>
    <div><span>Начало</span><strong>${escapeHtml(formatDate(event.starts_at))}</strong></div>
    <div><span>Конец</span><strong>${escapeHtml(formatDate(event.ends_at) || "не указано")}</strong></div>
    <div><span>Категория</span><strong>${escapeHtml(event.category || "без категории")}</strong></div>
    <div><span>Лимит</span><strong>${escapeHtml(limit)}</strong></div>
    <div><span>Видимость</span><strong>${escapeHtml(event.visibility)}</strong></div>
    <div><span>Статус</span><strong>${escapeHtml(event.status)}</strong></div>
  </div>
  <div class="profile-actions">
    ${canJoin ? `<button type="button" data-join-event="${event.id}">Записаться</button>` : ""}
    ${participation ? `<button class="light-button" type="button" data-leave-event="${event.id}">Отменить участие</button>` : ""}
    ${owner ? `<span class="profile-meta">Это ваше мероприятие</span>` : ""}
  </div>`;
}

function renderCabinet() {
  const user = state.session.user;
  $("myProfileCard").innerHTML = `<div class="profile-card">
    <div class="profile-head">
      <div class="brand-row">
        ${avatarHtml(user)}
        <div class="profile-name">
          <h3>@${escapeHtml(user.username)}</h3>
          <div class="profile-meta">${escapeHtml(user.email)}</div>
        </div>
      </div>
    </div>
    <p>${escapeHtml(user.bio || "Профиль без описания")}</p>
    <div class="metric-row">
      <div><span class="profile-meta">Подписчиков</span><strong>${state.followers.length}</strong></div>
      <div><span class="profile-meta">Записей</span><strong>${state.history.length}</strong></div>
    </div>
  </div>`;

  if (!state.history.length) {
    renderEmpty("historyList", "История мероприятий пуста");
  } else {
    $("historyList").className = "item-list";
    $("historyList").innerHTML = state.history.map((item) => eventListItem(item.event, { description: true })).join("");
  }

  if (!state.followers.length) {
    renderEmpty("followersList", "Подписчиков пока нет");
  } else {
    $("followersList").className = "item-list compact-list";
    $("followersList").innerHTML = state.followers
      .map((user) => userListItem(user, { hideFollowBadge: true, clickable: false }))
      .join("");
  }

}

function renderCreate() {
  renderFriendChecklist();
  updateFriendPicker();
}

function renderFriendChecklist() {
  const target = $("friendChecklist");
  const selectedIds = new Set(selectedNotifyUserIds());
  if (!state.followers.length) {
    target.className = "empty-state small-empty";
    target.textContent = "Подписчиков пока нет";
    return;
  }
  target.className = "friend-checklist";
  target.innerHTML = state.followers.map((user) => `<label class="check-row">
    <input type="checkbox" value="${user.id}"${selectedIds.has(user.id) ? " checked" : ""}>
    <span>@${escapeHtml(user.username)}</span>
  </label>`).join("");
}

function selectedNotifyUserIds() {
  return [...document.querySelectorAll('#friendChecklist input[type="checkbox"]:checked')]
    .map((input) => Number(input.value));
}

function notifyMode() {
  const checked = document.querySelector('input[name="notifyMode"]:checked');
  return checked ? checked.value : "all";
}

function updateFriendPicker() {
  $("friendPicker").hidden = notifyMode() !== "selected";
}

function renderAll() {
  renderShell();
  if (!state.session) {
    return;
  }
  renderHome();
  renderSearch();
  renderCreate();
  renderCabinet();
}

async function refreshAll(options = {}) {
  if (state.refreshPromise) {
    return state.refreshPromise;
  }

  state.refreshPromise = (async () => {
    if (!state.session) {
      renderShell();
      return;
    }

    const currentId = state.session.user.id;
    const [me, users, followers, following, publicEvents, feedEvents, history] = await Promise.all([
      api("/api/v1/me"),
      api(`/api/v1/users${PAGE_QUERY}`),
      api(`/api/v1/users/${currentId}/followers`),
      api(`/api/v1/users/${currentId}/following`),
      api(`/api/v1/events${PAGE_QUERY}`),
      api(`/api/v1/feed${PAGE_QUERY}`),
      api(`/api/v1/me/participations${PAGE_QUERY}`)
    ]);

    saveSession({ ...state.session, user: me });
    state.users = itemsOf(users);
    state.followers = itemsOf(followers);
    state.following = itemsOf(following);
    state.followingIds = new Set(state.following.map((user) => user.id));
    state.publicEvents = itemsOf(publicEvents);
    state.feedEvents = itemsOf(feedEvents);
    state.history = itemsOf(history);

    await refreshOwnedParticipantCache();
    renderAll();
  })();

  try {
    return await state.refreshPromise;
  } catch (error) {
    if (!options.silent) {
      throw error;
    }
  } finally {
    state.refreshPromise = null;
  }
}

async function login(username, password) {
  const result = await api("/api/v1/auth/login", {
    method: "POST",
    token: "",
    body: { username, password }
  });
  saveSession(result);
  await refreshAll();
  showToast("Вы вошли в Gatherly");
}

function eventPayload() {
  return {
    title: $("eventTitle").value.trim(),
    description: $("eventDescription").value.trim(),
    location: $("eventLocation").value.trim(),
    starts_at: toIsoFromLocal($("eventStarts").value),
    ends_at: toIsoFromLocal($("eventEnds").value),
    photo_url: "",
    category: $("eventCategory").value.trim(),
    visibility: "PUBLIC",
    participant_limit: Number($("eventLimit").value || 0)
  };
}

function resetEventForm() {
  $("eventForm").reset();
  $("eventLimit").value = "0";
  const notifyAll = document.querySelector('input[name="notifyMode"][value="all"]');
  if (notifyAll) {
    notifyAll.checked = true;
  }
  document.querySelectorAll('#friendChecklist input[type="checkbox"]').forEach((input) => {
    input.checked = false;
  });
  updateFriendPicker();
}

function resetAuthForms() {
  $("loginForm").reset();
  $("registerForm").reset();
}

async function followUser(userId) {
  if (state.followingIds.has(userId)) {
    await api(`/api/v1/users/${userId}/follow`, { method: "DELETE" });
    showToast("Подписка удалена");
  } else {
    await api(`/api/v1/users/${userId}/follow`, { method: "POST" });
    showToast("Подписка оформлена");
  }
  await refreshAll();
}

async function joinEvent(eventId) {
  await api(`/api/v1/events/${eventId}/participants`, { method: "POST" });
  state.homeEventParticipants.delete(eventId);
  showToast("Вы записаны на мероприятие");
  await refreshAll();
  if (state.selectedHomeEventId === eventId) {
    await selectHomeEvent(eventId);
  }
}

async function leaveEvent(eventId) {
  await api(`/api/v1/events/${eventId}/participants/me`, { method: "DELETE" });
  state.homeEventParticipants.delete(eventId);
  showToast("Участие отменено");
  await refreshAll();
  if (state.selectedHomeEventId === eventId) {
    await selectHomeEvent(eventId);
  }
}

$("loginTab").onclick = () => switchAuth("login");
$("registerTab").onclick = () => switchAuth("register");

$("loginForm").onsubmit = (event) => {
  event.preventDefault();
  run(() => login($("loginName").value.trim(), $("loginPassword").value));
};

$("registerForm").onsubmit = (event) => {
  event.preventDefault();
  run(async () => {
    const username = $("registerUsername").value.trim();
    const password = $("registerPassword").value;
    await api("/api/v1/auth/register", {
      method: "POST",
      token: "",
      body: {
        username,
        email: $("registerEmail").value.trim(),
        password,
        bio: $("registerBio").value.trim(),
        avatar_url: $("registerAvatar").value.trim()
      }
    });
    await login(username, password);
  });
};

$("logoutButton").onclick = () => {
  saveSession(null);
  state.selectedDetail = null;
  closeHomeEventModal(false);
  state.homeEventParticipants.clear();
  resetEventForm();
  resetAuthForms();
  switchAuth("login");
  renderShell();
  showToast("Вы вышли из аккаунта");
};

$("refreshButton").onclick = () => run(refreshAll);
$("searchQuery").oninput = renderSearchResults;
document.querySelectorAll('input[name="notifyMode"]').forEach((input) => {
  input.onchange = updateFriendPicker;
});

$("eventForm").onsubmit = (event) => {
  event.preventDefault();
  run(async () => {
    const notifyUserIds = selectedNotifyUserIds();
    if (notifyMode() === "selected" && !notifyUserIds.length) {
      showToast("Выберите хотя бы одного подписчика", true);
      return;
    }
    const draft = await api("/api/v1/events", { method: "POST", body: eventPayload() });
    const publishOptions = notifyMode() === "selected" ? { body: { notify_user_ids: notifyUserIds } } : {};
    const published = await api(`/api/v1/events/${draft.id}/publish`, { method: "POST", ...publishOptions });
    state.selectedDetail = { type: "event", id: published.id };
    resetEventForm();
    showToast("Мероприятие опубликовано");
    await refreshAll();
    switchScreen("searchScreen");
  });
};

document.querySelectorAll(".main-nav button").forEach((button) => {
  button.onclick = () => {
    switchScreen(button.dataset.screen);
    if (button.dataset.screen === "homeScreen") {
      run(refreshAll);
    }
  };
});

document.addEventListener("click", (event) => {
  if (event.target === $("homeEventOverlay")) {
    closeHomeEventModal();
    return;
  }

  const homeEvent = event.target.closest("[data-home-event]");
  if (homeEvent) {
    run(() => selectHomeEvent(Number(homeEvent.dataset.homeEvent)));
    return;
  }

  const selectUser = event.target.closest("[data-select-user]");
  if (selectUser) {
    state.selectedDetail = { type: "user", id: Number(selectUser.dataset.selectUser) };
    switchScreen("searchScreen");
    run(renderSearchDetail);
    return;
  }

  const selectEvent = event.target.closest("[data-select-event]");
  if (selectEvent) {
    state.selectedDetail = { type: "event", id: Number(selectEvent.dataset.selectEvent) };
    switchScreen("searchScreen");
    run(renderSearchDetail);
    return;
  }

  const follow = event.target.closest("[data-follow-user]");
  if (follow) {
    run(() => followUser(Number(follow.dataset.followUser)));
    return;
  }

  const join = event.target.closest("[data-join-event]");
  if (join) {
    run(() => joinEvent(Number(join.dataset.joinEvent)));
    return;
  }

  const leave = event.target.closest("[data-leave-event]");
  if (leave) {
    run(() => leaveEvent(Number(leave.dataset.leaveEvent)));
  }
});

renderShell();
if (state.session) {
  run(refreshAll);
}
