async function refreshFeatures() {
  if (!state.session) return;
  try {
    const result = await api('/api/v1/recommendations?limit=6');
    state.recommendations = result.items;
    $('recommendationHint').textContent = result.source === 'personalized'
      ? 'На основе ваших прошлых встреч' : 'Популярные предстоящие встречи';
    $('recommendationsList').innerHTML = result.items.length
      ? result.items.map((event) => eventListItem(event, {description: true, home: true})).join('')
      : '<p class="empty-state">Пока нет подходящих мероприятий</p>';
  } catch (error) {
    state.recommendations = [];
    $('recommendationHint').textContent = 'Подборка временно недоступна. Попробуйте обновить страницу позже.';
    $('recommendationsList').replaceChildren();
  }
  const selector = $('albumEvent');
  const previous = selector.value;
  const events = new Map([...allSearchableEvents(), ...state.owned].map((event) => [event.id, event]));
  selector.innerHTML = [...events.values()].map((event) => `<option value="${event.id}">${escapeHtml(event.title)}</option>`).join('');
  if ([...selector.options].some((option) => option.value === previous)) selector.value = previous;
  updateAlbumPermissions();
  await refreshAlbums();
}

function updateAlbumPermissions() {
  const id = Number($('albumEvent').value);
  const participation = myParticipationFor(id);
  const permitted = state.owned.some((event) => event.id === id) || participation?.participation.status === 'GOING';
  $('albumForm').hidden = !permitted;
}

async function refreshAlbums() {
  const id = $('albumEvent').value;
  if (!id || !state.session) {
    $('albumsList').innerHTML = '<p class="empty-state">Выберите мероприятие, чтобы посмотреть фотоальбомы</p>';
    return;
  }
  const result = await api(`/api/v1/albums?event_id=${encodeURIComponent(id)}`);
  const albums = await Promise.all(result.items.filter((album) => album.status === 'PUBLISHED').map((album) => api(`/api/v1/albums/${album.id}`)));
  if ($('albumEvent').value !== id) return;
  $('albumsList').innerHTML = albums.length ? albums.map((album) => `<article>
    <h4>${escapeHtml(album.title)}</h4>
    <div class="photo-gallery">${album.photos.map((photo) => `<a href="${escapeHtml(photo.gallery_url)}" target="_blank" rel="noopener"><img src="${escapeHtml(photo.thumbnail_url)}" alt="Фото из альбома ${escapeHtml(album.title)}" loading="lazy"></a>`).join('')}</div>
  </article>`).join('') : '<p class="empty-state">Опубликованных альбомов пока нет</p>';
}

$('albumEvent').onchange = () => { updateAlbumPermissions(); run(refreshAlbums); };
$('albumForm').onsubmit = (event) => {
  event.preventDefault();
  run(async () => {
    const files = [...$('albumFiles').files];
    if (!files.length || files.length > 100 || files.some((file) => file.size > 12 * 1024 * 1024)) {
      throw new Error('Выберите от 1 до 100 фотографий размером до 12 МБ каждая');
    }
    $('albumSubmit').disabled = true;
    try {
      const created = await api('/api/v1/albums', {method: 'POST', body: {event_id: Number($('albumEvent').value), title: $('albumTitle').value}});
      const receipts = [];
      for (const [index, file] of files.entries()) {
        $('albumProgress').textContent = `Загружается фото ${index + 1} из ${files.length}`;
        const response = await fetch(`/api/v1/albums/${created.album.id}/photos`, {method: 'POST', headers: {'Content-Type': file.type, 'X-Upload-Token': created.upload_token}, body: file});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Не удалось загрузить фото');
        receipts.push(result.receipt);
      }
      await api(`/api/v1/albums/${created.album.id}/publish`, {method: 'POST', body: {receipts}});
      $('albumTitle').value = '';
      $('albumFiles').value = '';
      $('albumProgress').textContent = 'Альбом опубликован';
      await refreshAlbums();
    } finally {
      $('albumSubmit').disabled = false;
    }
  }).catch(() => { $('albumProgress').textContent = 'Загрузка не завершена. Попробуйте ещё раз.'; });
};

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-event-albums]');
  if (!button) return;
  closeHomeEventModal();
  switchScreen('homeScreen');
  $('albumEvent').value = button.dataset.eventAlbums;
  updateAlbumPermissions();
  run(refreshAlbums);
  $('albumsPanel').scrollIntoView({behavior: 'smooth'});
});
