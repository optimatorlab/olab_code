const map = L.map('map').setView([40.7, -74.0], 9);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

const markers = new Map();
const trails = new Map();
const trackList = document.getElementById('tracks');
const statusEl = document.getElementById('status');
const hardwareEl = document.getElementById('hardware');
const sessionEl = document.getElementById('session');
const spectrumPresetEl = document.getElementById('spectrum-preset');
const spectrumPeaksEl = document.getElementById('spectrum-peaks');
const spectrumEventsEl = document.getElementById('spectrum-events');
const spectrumMeterEl = document.getElementById('spectrum-meter');
const spectrumWatchEl = document.getElementById('spectrum-watch');
const frequencyRangeEl = document.getElementById('frequency-range');
const frequencyCandidateViewEl = document.getElementById('frequency-candidate-view');
const frequencyScanGainEl = document.getElementById('frequency-scan-gain');
const frequencyScanStatusEl = document.getElementById('frequency-scan-status');
const spectrumCanvas = document.getElementById('spectrum-canvas');
const spectrumCtx = spectrumCanvas.getContext('2d');
const waterfallCanvas = document.getElementById('waterfall-canvas');
const waterfallCtx = waterfallCanvas.getContext('2d');
const waterfallTooltipEl = document.getElementById('waterfall-tooltip');
let fallbackTimer = null;
let spectrumPresets = [];
let spectrumAnnotations = [];
let latestFrequencyScan = null;
let latestFrequencyScanRenderKey = '';
let latestSpectrumMeterText = '';
let spectrumErrorMessage = '';
let latestStatus = null;
let spectrumControlsReady = false;
let spectrumRestartTimer = null;
let waterfallHoverModel = null;
let listenHistory = [];
let favoriteFrequencies = [];
let latestWatch = null;
let persistedSpectrumEvents = [];
let persistedEventsLoadedAt = 0;
let persistedEventsRequest = null;

for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === tab));
    document.querySelectorAll('.view-panel').forEach(panel => {
      panel.classList.toggle('active', panel.id === tab.dataset.view);
    });
    if (tab.dataset.view === 'map') {
      setTimeout(() => map.invalidateSize(), 0);
    }
  });
}

document.getElementById('start').addEventListener('click', startSelectedMode);

async function startSelectedMode() {
  const mode = document.getElementById('mode').value;
  await startMode(mode);
}

async function startMode(mode) {
  const response = await fetch('/api/session/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(startPayload(mode))
  });
  if (!response.ok) {
    const payload = await responsePayload(response);
    statusEl.textContent = payload.detail || response.statusText;
    return false;
  }
  await refresh();
  return true;
}

document.getElementById('stop').addEventListener('click', async () => {
  await fetch('/api/session/stop', {method: 'POST'});
  await refresh();
});

document.getElementById('check').addEventListener('click', refreshHardware);
document.getElementById('watch-frequency-apply').addEventListener('click', applyManualWatchFrequency);
document.getElementById('frequency-baseline-start').addEventListener('click', startFrequencyBaseline);
document.getElementById('frequency-scan-start').addEventListener('click', startFrequencyScan);
frequencyCandidateViewEl.addEventListener('change', () => {
  latestFrequencyScanRenderKey = '';
  renderFrequencyScan(latestFrequencyScan);
});

async function refresh() {
  try {
    const [status, session, tracks, spectrum, frequencyScan] = await Promise.all([
      fetchJson('/api/status'),
      fetchJson('/api/session'),
      fetchJson('/api/tracks'),
      fetchJson('/api/spectrum'),
      fetchJson('/api/frequency/scan').catch(() => null)
    ]);
    renderStatus(status);
    renderSession(session, status);
    renderTracks(tracks);
    renderSpectrum(spectrum);
    renderFrequencyScan(frequencyScan);
  } catch (error) {
    statusEl.textContent = error.message;
  }
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await responsePayload(response);
    throw new Error(payload.detail || response.statusText);
  }
  return response.json();
}

async function responsePayload(response) {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }
  return {detail: await response.text()};
}

function connectStream() {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${window.location.host}/ws/tracks`);

  socket.addEventListener('open', () => {
    if (fallbackTimer) {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }
  });

  socket.addEventListener('message', event => {
    const payload = JSON.parse(event.data);
    renderStatus(payload.status);
    renderSession(payload.session, payload.status);
    renderTracks(payload.tracks);
    renderSpectrum(payload.spectrum);
    renderFrequencyScan(payload.frequency_scan);
  });

  socket.addEventListener('close', () => {
    if (!fallbackTimer) {
      fallbackTimer = setInterval(refresh, 3000);
    }
  });
}

function startPayload(mode) {
  if (mode !== 'spectrum') {
    return {mode};
  }
  return {
    mode,
    preset_id: spectrumPresetEl.value,
    start_hz: mhzToHz(document.getElementById('spectrum-start').value),
    stop_hz: mhzToHz(document.getElementById('spectrum-stop').value),
    bin_hz: khzToHz(document.getElementById('spectrum-bin').value),
    interval_s: document.getElementById('spectrum-interval').value,
    gain_db: frequencyScanGainEl.value,
    sample_rate_hz: document.getElementById('spectrum-sample-rate').value,
    threshold_db: document.getElementById('spectrum-threshold').value
  };
}

function mhzToHz(value) {
  return value ? Math.round(Number(value) * 1000000) : null;
}

function khzToHz(value) {
  return value ? Math.round(Number(value) * 1000) : null;
}

function hzToMhz(value) {
  return Number(value / 1000000).toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

function favoriteForFrequency(frequencyHz) {
  const frequency = Number(frequencyHz);
  if (!frequency) {
    return null;
  }
  return favoriteFrequencies.find(item => Math.abs(Number(item.frequency_hz) - frequency) <= 2500) || null;
}

function annotationForFrequency(frequencyHz) {
  const frequency = Number(frequencyHz);
  if (!frequency) {
    return null;
  }
  const centered = spectrumAnnotations.find(item => (
    item.center_hz && Math.abs(Number(item.center_hz) - frequency) <= Number(item.tolerance_hz || 0)
  ));
  if (centered) {
    return centered;
  }
  return spectrumAnnotations.find(item => (
    item.start_hz && item.stop_hz && frequency >= Number(item.start_hz) && frequency <= Number(item.stop_hz)
  )) || null;
}

function frequencyHeading(frequencyHz) {
  const favorite = favoriteForFrequency(frequencyHz);
  const annotation = annotationForFrequency(frequencyHz);
  const frequencyText = `${hzToMhz(frequencyHz)} MHz`;
  const label = favorite?.label || annotation?.label || '';
  if (!label) {
    return `<strong>${frequencyText}</strong>`;
  }
  return `<strong>${escapeHtml(label)}</strong><span class="frequency-label">${frequencyText}</span>`;
}

function presetLabel(presetId) {
  const preset = spectrumPresets.find(item => item.id === presetId || item.preset_id === presetId);
  return preset?.label || presetId || '';
}

function frequencyDetail(frequencyHz, fallback) {
  const favorite = favoriteForFrequency(frequencyHz);
  const annotation = annotationForFrequency(frequencyHz);
  return escapeHtml(favorite?.label && annotation?.label ? annotation.label : fallback || '');
}

async function loadSpectrumPresets() {
  const [catalog, favorites] = await Promise.all([
    fetchJson('/api/frequency/catalog'),
    fetchJson('/api/spectrum/favorites').catch(() => [])
  ]);
  spectrumPresets = catalog.ranges || [];
  favoriteFrequencies = favorites;
  spectrumAnnotations = spectrumPresets.flatMap(range => [
    {
      label: range.label,
      modulation: range.default_modulation,
      start_hz: range.min_freq_hz,
      stop_hz: range.max_freq_hz,
      tolerance_hz: range.default_bin_size_hz || 2500
    },
    ...(range.channels || []).map(channel => ({
      label: channel.label,
      modulation: channel.modulation || range.default_modulation,
      center_hz: channel.frequency_hz,
      tolerance_hz: Math.max(2500, Number(range.default_bin_size_hz || 5000) / 2)
    }))
  ]);
  spectrumPresetEl.innerHTML = spectrumPresets.map(preset => (
    `<option value="${preset.id}">${preset.label}</option>`
  )).join('');
  frequencyRangeEl.innerHTML = spectrumPresets.map(range => (
    `<option value="${range.id}">${range.label}</option>`
  )).join('');
  spectrumPresetEl.addEventListener('change', () => {
    applySelectedPreset();
    scheduleSpectrumRestart();
  });
  frequencyRangeEl.addEventListener('change', () => {
    spectrumPresetEl.value = frequencyRangeEl.value;
    applySelectedPreset();
  });
  document.querySelectorAll('.spectrum-controls input').forEach(input => {
    input.addEventListener('change', scheduleSpectrumRestart);
  });
  applySelectedPreset();
  spectrumControlsReady = true;
}

function applySelectedPreset() {
  const preset = spectrumPresets.find(item => item.id === spectrumPresetEl.value);
  if (!preset) {
    return;
  }
  document.getElementById('spectrum-start').value = hzToMhz(preset.min_freq_hz);
  document.getElementById('spectrum-stop').value = hzToMhz(preset.max_freq_hz);
  document.getElementById('spectrum-bin').value = Number((preset.default_bin_size_hz || 25000) / 1000).toString();
}

function renderSpectrum(spectrum) {
  if (!spectrum) {
    return;
  }
  loadPersistedSpectrumEvents();
  spectrumErrorMessage = spectrum.error || '';
  const bins = spectrum.bins || [];
  const peakHold = spectrum.peak_hold || [];
  drawSpectrum(bins, peakHold);
  drawWaterfall(spectrum.history || []);
  renderSpectrumMeter(spectrum);
  renderSpectrumWatch(spectrum.watch);
  renderSpectrumEvents(mergedSpectrumEvents(spectrum.events || []), bins);
  const peaks = spectrum.peaks || [];
  spectrumPeaksEl.innerHTML = peaks.length
    ? peaks.map(peak => `<button class="peak" type="button" data-frequency-hz="${peak.center_hz}" title="Use ${hzToMhz(peak.center_hz)} MHz as the center for a narrower sweep."><span>${frequencyHeading(peak.center_hz)}</span><span>${peak.power_db.toFixed(1)} dB</span></button>`).join('')
    : `<div class="empty">${escapeHtml(spectrumErrorMessage || 'No sweep data yet.')}</div>`;
  spectrumPeaksEl.querySelectorAll('.peak').forEach(button => {
    button.addEventListener('click', () => selectPeak(Number(button.dataset.frequencyHz), bins));
  });
}

async function startFrequencyBaseline() {
  await postFrequencyScan('/api/frequency/baseline');
}

async function startFrequencyScan() {
  await postFrequencyScan('/api/frequency/scan');
}

async function postFrequencyScan(url) {
  const range = spectrumPresets.find(item => item.id === frequencyRangeEl.value);
  if (!range) {
    statusEl.textContent = 'select a frequency range first';
    return;
  }
  const payload = {
    range_id: range.id,
    min_freq_hz: range.min_freq_hz,
    max_freq_hz: range.max_freq_hz,
    bin_size_hz: khzToHz(document.getElementById('spectrum-bin').value) || range.default_bin_size_hz || 25000,
    duration_sec: Number(document.getElementById('frequency-scan-duration').value || 20),
    channel_width_hz: range.default_bin_size_hz || 25000,
    gain_db: frequencyScanGainEl.value,
    sample_rate_hz: document.getElementById('spectrum-sample-rate').value,
    resume_previous: true
  };
  const response = await fetchJson(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  renderFrequencyScan(response);
}

function renderFrequencyScan(scan) {
  latestFrequencyScan = scan;
  if (!frequencyScanStatusEl) {
    return;
  }
  const renderKey = JSON.stringify({scan: scan || null, view: frequencyCandidateViewEl.value});
  if (renderKey === latestFrequencyScanRenderKey) {
    return;
  }
  latestFrequencyScanRenderKey = renderKey;
  if (!scan) {
    frequencyScanStatusEl.innerHTML = '<div class="empty">No frequency discovery scan running.</div>';
    return;
  }
  const candidates = frequencyScanCandidates(scan);
  const percent = Math.round(Number(scan.progress || 0) * 100);
  const request = scan.request || {};
  const viewLabel = frequencyCandidateViewEl.value === 'matched' ? 'matched channels' : 'all candidates';
  frequencyScanStatusEl.innerHTML = `<div class="scan-summary">
      <strong>${escapeHtml(scan.status || 'unknown')}</strong>
      <span>${percent}% · ${Number(scan.elapsed_sec || 0).toFixed(1)}s · ${scan.sweeps_completed || 0} sweeps</span>
      <span>${hzToMhz(request.min_freq_hz || 0)}-${hzToMhz(request.max_freq_hz || 0)} MHz</span>
      <span>${candidates.length} ${viewLabel}</span>
      ${frequencyScanDetails(scan)}
    </div>
    ${candidates.length ? `<table class="event-table">
      <thead><tr><th>Rank</th><th>Frequency</th><th>Signal</th><th>Margin</th><th>Action</th></tr></thead>
      <tbody>${candidates.slice(0, 10).map((candidate, index) => frequencyCandidateRow(candidate, index)).join('')}</tbody>
    </table>` : `<div class="empty">${frequencyScanEmptyMessage(scan)}</div>`}`;
  frequencyScanStatusEl.querySelectorAll('[data-scan-watch]').forEach(button => {
    button.addEventListener('click', () => setWatchFrequency(Number(button.dataset.frequencyHz), button.dataset.modulation));
  });
}

function frequencyScanDetails(scan) {
  const request = scan.request || {};
  const command = (scan.command || []).join(' ');
  const details = [
    `backend ${request.backend || scan.decoder || 'unknown'}`,
    `bin ${request.bin_size_hz || 'n/a'} Hz`,
    `gain ${request.gain_db === null || request.gain_db === undefined ? 'auto' : `${request.gain_db} dB`}`,
    `sample ${request.sample_rate_hz || 'default'} Hz`
  ].join(' · ');
  return `<details class="scan-details">
      <summary>Request</summary>
      <div>${escapeHtml(details)}</div>
      ${command ? `<pre>${escapeHtml(command)}</pre>` : ''}
    </details>`;
}

function frequencyScanCandidates(scan) {
  const candidates = scan.candidates || [];
  if (frequencyCandidateViewEl.value !== 'matched') {
    return candidates;
  }
  return candidates
    .filter(candidate => candidate.matched_frequency_hz)
    .sort((left, right) => candidateRankValue(right) - candidateRankValue(left));
}

function candidateRankValue(candidate) {
  if (candidate.margin_db !== null && candidate.margin_db !== undefined) {
    return Number(candidate.margin_db);
  }
  return Number(candidate.power_db || 0);
}

function frequencyScanEmptyMessage(scan) {
  if (frequencyCandidateViewEl.value === 'matched' && (scan.candidates || []).length) {
    return 'No matched channel candidates.';
  }
  return 'No candidates yet.';
}

function frequencyCandidateRow(candidate, index) {
  const margin = candidate.margin_db === null || candidate.margin_db === undefined
    ? 'n/a'
    : `+${Number(candidate.margin_db).toFixed(1)} dB`;
  const observed = Number(candidate.observed_frequency_hz || candidate.frequency_hz);
  const matched = candidate.matched_frequency_hz ? Number(candidate.matched_frequency_hz) : null;
  const frequencyText = matched
    ? `${frequencyHeading(matched)}<span class="frequency-label">observed ${hzToMhz(observed)} MHz · offset ${Number(candidate.frequency_offset_hz || 0)} Hz</span>`
    : frequencyHeading(observed);
  return `<tr>
    <td>${index + 1}</td>
    <td>${frequencyText}</td>
    <td>${Number(candidate.power_db || 0).toFixed(1)} dB</td>
    <td>${margin}</td>
    <td><button type="button" data-scan-watch data-frequency-hz="${matched || observed}" data-modulation="${escapeHtml(candidate.modulation || currentPresetModulation())}">Listen</button></td>
  </tr>`;
}

function renderSpectrumEvents(events, bins) {
  const recent = events.slice(-12);
  spectrumEventsEl.innerHTML = recent.length
    ? `<table class="event-table">
        <thead><tr><th>Time</th><th>Frequency</th><th>Signal</th><th>Level</th><th>Actions</th></tr></thead>
        <tbody>${recent.map(event => eventRow(event)).join('')}</tbody>
      </table>`
    : '<div class="empty">No events above threshold yet.</div>';
  spectrumEventsEl.querySelectorAll('[data-event-watch]').forEach(button => {
    button.addEventListener('click', () => selectPeak(Number(button.dataset.frequencyHz), bins));
  });
  spectrumEventsEl.querySelectorAll('[data-event-favorite]').forEach(button => {
    button.addEventListener('click', () => saveFrequencyFavorite(Number(button.dataset.frequencyHz)));
  });
}

function eventRow(event) {
  const detail = escapeHtml(event.annotation_label || event.range_label || frequencyDetail(event.center_hz, presetLabel(event.preset_id)));
  const source = event.persisted ? 'Saved event' : 'Live event';
  return `<tr>
    <td>${formatEventTime(event.captured_at)}</td>
    <td>${eventFrequencyHeading(event)}</td>
    <td>${detail}<span class="event-margin">${source} · +${event.margin_db.toFixed(1)} dB</span></td>
    <td>${event.power_db.toFixed(1)} dB</td>
    <td class="event-row-actions">
      <button type="button" data-event-watch data-frequency-hz="${event.center_hz}" title="Use ${hzToMhz(event.center_hz)} MHz as the watch frequency.">Watch</button>
      <button type="button" data-event-favorite data-frequency-hz="${event.center_hz}" title="Save ${hzToMhz(event.center_hz)} MHz as a favorite.">Favorite</button>
    </td>
  </tr>`;
}

function eventFrequencyHeading(event) {
  if (!event.label) {
    return frequencyHeading(event.center_hz);
  }
  return `<strong>${escapeHtml(event.label)}</strong><span class="frequency-label">${hzToMhz(event.center_hz)} MHz</span>`;
}

function formatEventTime(value) {
  if (!value) {
    return 'n/a';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return escapeHtml(value);
  }
  return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

function mergedSpectrumEvents(liveEvents) {
  const byKey = new Map();
  for (const event of persistedSpectrumEvents) {
    byKey.set(eventKey(event), {...event, persisted: true});
  }
  for (const event of liveEvents) {
    byKey.set(eventKey(event), {...event, persisted: false});
  }
  return [...byKey.values()].sort((left, right) => (
    Date.parse(left.captured_at || 0) - Date.parse(right.captured_at || 0)
  ));
}

async function loadPersistedSpectrumEvents() {
  const now = Date.now();
  if (persistedEventsRequest || now - persistedEventsLoadedAt < 5000) {
    return;
  }
  persistedEventsRequest = fetchJson('/api/spectrum/events?limit=50');
  try {
    persistedSpectrumEvents = await persistedEventsRequest;
  } catch {
    persistedSpectrumEvents = [];
  } finally {
    persistedEventsLoadedAt = now;
    persistedEventsRequest = null;
  }
}

function eventKey(event) {
  return `${event.captured_at || ''}:${event.center_hz || ''}:${event.power_db || ''}`;
}

async function selectPeak(centerHz, bins) {
  if (!centerHz) {
    return;
  }
  const binStepHz = bins.length > 1 ? Math.abs(bins[1].center_hz - bins[0].center_hz) : khzToHz(document.getElementById('spectrum-bin').value) || 25000;
  const windowHz = Math.max(binStepHz * 8, 250000);
  spectrumPresetEl.value = 'custom';
  document.getElementById('spectrum-start').value = hzToMhz(centerHz - windowHz / 2);
  document.getElementById('spectrum-stop').value = hzToMhz(centerHz + windowHz / 2);
  document.getElementById('spectrum-bin').value = Number(binStepHz / 1000).toString();
  statusEl.textContent = `custom sweep centered on ${hzToMhz(centerHz)} MHz`;
  await setWatchFrequency(centerHz, annotationForFrequency(centerHz)?.modulation);
  scheduleSpectrumRestart();
}

async function setWatchFrequency(centerHz, modulation) {
  const watch = await fetchJson('/api/spectrum/watch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      frequency_hz: centerHz,
      modulation: modulation || currentPresetModulation()
    })
  });
  renderSpectrumWatch(watch);
}

async function applyManualWatchFrequency() {
  const frequencyHz = mhzToHz(document.getElementById('watch-frequency').value);
  if (!frequencyHz) {
    statusEl.textContent = 'enter a watch frequency first';
    return;
  }
  await setWatchFrequency(frequencyHz, annotationForFrequency(frequencyHz)?.modulation);
  statusEl.textContent = `watch set to ${hzToMhz(frequencyHz)} MHz`;
}

function currentPresetModulation() {
  const preset = spectrumPresets.find(item => item.id === spectrumPresetEl.value);
  return preset ? preset.default_modulation : 'nfm';
}

function renderSpectrumWatch(watch) {
  if (!watch || !watch.frequency_hz) {
    latestWatch = null;
    spectrumWatchEl.innerHTML = '<div class="empty">No watch frequency selected.</div>';
    renderListenLists();
    return;
  }
  latestWatch = watch;
  const command = watch.command ? watch.command.join(' ') : '';
  const playCommand = watch.play_command || '';
  const listening = latestStatus?.mode === 'listen' && latestStatus?.process_running;
  spectrumWatchEl.innerHTML = `${frequencyHeading(watch.frequency_hz)} · ${escapeHtml(watch.modulation || 'n/a')}<br>
    decoder: ${escapeHtml(watch.demod_path || 'rtl_fm')}
    <div class="listen-note">Starting Listen stops Spectrum; the RTL-SDR can only be opened by one decoder at a time.</div>
    <div class="watch-actions">
      <button id="start-listen" type="button">${listening ? 'Stop Listen' : 'Start Listen'}</button>
      <button id="save-favorite" type="button">Save Favorite</button>
    </div>
    ${command ? commandBlock('Decoder command', 'Runs rtl_fm and writes raw audio samples to stdout. Use this when you want to pipe or record audio yourself.', command) : ''}
    ${playCommand ? commandBlock('Playback command', 'Runs the decoder and pipes its raw audio into aplay so you hear it through local speakers.', playCommand) : ''}
    <div class="listen-lists">
      <div><strong>Listened This Page</strong><div id="listen-history"></div></div>
      <div><strong>Favorites</strong><div id="listen-favorites"></div></div>
    </div>`;
  document.getElementById('start-listen').addEventListener('click', startListen);
  document.getElementById('save-favorite').addEventListener('click', () => saveFavorite(watch));
  spectrumWatchEl.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', () => copyText(button.dataset.copy, button));
  });
  renderListenLists();
}

async function startListen() {
  const listening = latestStatus?.mode === 'listen' && latestStatus?.process_running;
  if (listening) {
    statusEl.textContent = 'stopping listen...';
    await fetch('/api/session/stop', {method: 'POST'});
    await refresh();
    return;
  }
  const response = await fetch('/api/session/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: 'listen'})
  });
  if (!response.ok) {
    const payload = await responsePayload(response);
    statusEl.textContent = payload.detail || response.statusText;
    return;
  }
  addListenHistory();
  await refresh();
}

function commandBlock(label, description, command) {
  return `<div class="command-row"><span>${label}</span><button type="button" data-copy="${escapeHtml(command)}">Copy</button></div><div class="command-help">${description}</div><pre>${escapeHtml(command)}</pre>`;
}

function addListenHistory() {
  if (!latestWatch?.frequency_hz) {
    return;
  }
  const item = {
    frequency_hz: latestWatch.frequency_hz,
    modulation: latestWatch.modulation || currentPresetModulation(),
    label: favoriteForFrequency(latestWatch.frequency_hz)?.label || ''
  };
  listenHistory = [item, ...listenHistory.filter(existing => existing.frequency_hz !== item.frequency_hz)].slice(0, 12);
}

async function saveFavorite(watch) {
  await saveFrequencyFavorite(watch.frequency_hz, watch.modulation || currentPresetModulation());
}

async function saveFrequencyFavorite(frequencyHz, modulation) {
  const favorite = {
    frequency_hz: frequencyHz,
    modulation: modulation || annotationForFrequency(frequencyHz)?.modulation || currentPresetModulation()
  };
  const defaultLabel = annotationForFrequency(favorite.frequency_hz)?.label || `${hzToMhz(favorite.frequency_hz)} MHz`;
  const label = window.prompt('Favorite label', defaultLabel) || '';
  favorite.label = label;
  await fetchJson('/api/spectrum/favorites', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(favorite)
  }).catch(() => null);
  favoriteFrequencies = [
    favorite,
    ...favoriteFrequencies.filter(item => item.frequency_hz !== favorite.frequency_hz)
  ].slice(0, 24);
  renderListenLists();
}

function renderListenLists() {
  const historyEl = document.getElementById('listen-history');
  const favoritesEl = document.getElementById('listen-favorites');
  if (!historyEl || !favoritesEl) {
    return;
  }
  historyEl.innerHTML = listenHistory.length
    ? listenHistory.map(item => listenListButton(item)).join('')
    : '<div class="empty">No listens yet.</div>';
  favoritesEl.innerHTML = favoriteFrequencies.length
    ? favoriteFrequencies.map(item => favoriteListItem(item)).join('')
    : '<div class="empty">No favorites saved.</div>';
  spectrumWatchEl.querySelectorAll('[data-watch-frequency]').forEach(button => {
    button.addEventListener('click', () => applyWatchFrequency(Number(button.dataset.watchFrequency), button.dataset.watchModulation));
  });
  spectrumWatchEl.querySelectorAll('[data-delete-favorite]').forEach(button => {
    button.addEventListener('click', () => deleteFavorite(Number(button.dataset.deleteFavorite)));
  });
}

function listenListButton(item) {
  const label = item.label ? `${escapeHtml(item.label)} · ` : '';
  return `<button class="listen-item" type="button" data-watch-frequency="${item.frequency_hz}" data-watch-modulation="${escapeHtml(item.modulation || 'nfm')}">${label}${hzToMhz(item.frequency_hz)} MHz · ${escapeHtml(item.modulation || 'nfm')}</button>`;
}

function favoriteListItem(item) {
  return `<div class="favorite-item">${listenListButton(item)}<button type="button" title="Delete favorite" aria-label="Delete ${hzToMhz(item.frequency_hz)} MHz favorite" data-delete-favorite="${item.frequency_hz}">Remove</button></div>`;
}

async function deleteFavorite(frequencyHz) {
  if (!frequencyHz) {
    return;
  }
  await fetch(`/api/spectrum/favorites/${frequencyHz}`, {method: 'DELETE'}).catch(() => null);
  favoriteFrequencies = favoriteFrequencies.filter(item => item.frequency_hz !== frequencyHz);
  renderListenLists();
}

async function applyWatchFrequency(frequencyHz, modulation) {
  if (!frequencyHz) {
    return;
  }
  const binStepHz = khzToHz(document.getElementById('spectrum-bin').value) || 25000;
  const windowHz = Math.max(binStepHz * 8, 250000);
  spectrumPresetEl.value = 'custom';
  document.getElementById('spectrum-start').value = hzToMhz(frequencyHz - windowHz / 2);
  document.getElementById('spectrum-stop').value = hzToMhz(frequencyHz + windowHz / 2);
  await setWatchFrequency(frequencyHz, modulation);
  scheduleSpectrumRestart();
}

async function copyText(value, button) {
  if (!value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const textArea = document.createElement('textarea');
    textArea.value = value;
    document.body.appendChild(textArea);
    textArea.select();
    document.execCommand('copy');
    textArea.remove();
  }
  const original = button.textContent;
  button.textContent = 'Copied';
  setTimeout(() => {
    button.textContent = original;
  }, 1200);
}

function scheduleSpectrumRestart() {
  if (!spectrumControlsReady || !latestStatus || latestStatus.mode !== 'spectrum') {
    return;
  }
  if (spectrumRestartTimer) {
    clearTimeout(spectrumRestartTimer);
  }
  statusEl.textContent = latestStatus.process_running
    ? 'updating spectrum sweep...'
    : 'spectrum settings updated; start to apply';
  if (!latestStatus.process_running) {
    return;
  }
  spectrumRestartTimer = setTimeout(async () => {
    spectrumRestartTimer = null;
    await startMode('spectrum');
  }, 500);
}

function drawSpectrum(bins, peakHold) {
  const rect = spectrumCanvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width * ratio));
  const height = Math.max(1, Math.floor(rect.height * ratio));
  if (spectrumCanvas.width !== width || spectrumCanvas.height !== height) {
    spectrumCanvas.width = width;
    spectrumCanvas.height = height;
  }
  spectrumCtx.clearRect(0, 0, width, height);
  spectrumCtx.fillStyle = '#11191c';
  spectrumCtx.fillRect(0, 0, width, height);
  const plot = {
    left: 64 * ratio,
    right: 18 * ratio,
    top: 18 * ratio,
    bottom: 44 * ratio
  };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  if (!bins.length) {
    spectrumCtx.fillStyle = '#9fb0b7';
    spectrumCtx.font = `${14 * ratio}px system-ui, sans-serif`;
    spectrumCtx.fillText(spectrumErrorMessage || 'Waiting for rtl_power sweep data', plot.left, plot.top + 14 * ratio);
    drawSpectrumAxes(plot, plotWidth, plotHeight, ratio);
    return;
  }
  const minPower = Math.min(...bins.map(item => item.power_db));
  const maxPower = Math.max(...bins.map(item => item.power_db));
  const yMin = Math.floor((minPower - 3) / 10) * 10;
  const yMax = Math.ceil((maxPower + 3) / 10) * 10;
  const span = Math.max(1, yMax - yMin);
  const firstHz = bins[0].center_hz;
  const lastHz = bins[bins.length - 1].center_hz;
  drawSpectrumAxes(plot, plotWidth, plotHeight, ratio, {firstHz, lastHz, yMin, yMax});
  const slotWidth = plotWidth / Math.max(1, bins.length);
  const barGap = Math.max(1 * ratio, Math.min(4 * ratio, slotWidth * 0.18));
  const barWidth = Math.max(1 * ratio, slotWidth - barGap);
  spectrumCtx.fillStyle = '#42d392';
  bins.forEach((bin, index) => {
    const x = plot.left + index * slotWidth + barGap / 2;
    const y = plot.top + (1 - (bin.power_db - yMin) / span) * plotHeight;
    spectrumCtx.fillRect(x, y, barWidth, plot.top + plotHeight - y);
  });
  drawPeakHold(peakHold || [], plot, plotWidth, plotHeight, ratio, slotWidth, yMin, span);
  drawBinLabels(bins, plot, plotWidth, plotHeight, ratio, slotWidth);
}

function drawPeakHold(peakHold, plot, plotWidth, plotHeight, ratio, slotWidth, yMin, span) {
  if (!peakHold.length) {
    return;
  }
  spectrumCtx.strokeStyle = '#f4c542';
  spectrumCtx.lineWidth = 2 * ratio;
  peakHold.forEach((bin, index) => {
    if (index >= Math.floor(plotWidth / slotWidth)) {
      return;
    }
    const x = plot.left + index * slotWidth + slotWidth * 0.2;
    const y = plot.top + (1 - (bin.power_db - yMin) / span) * plotHeight;
    spectrumCtx.beginPath();
    spectrumCtx.moveTo(x, y);
    spectrumCtx.lineTo(x + slotWidth * 0.6, y);
    spectrumCtx.stroke();
  });
}

function drawWaterfall(history) {
  const rect = waterfallCanvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width * ratio));
  const height = Math.max(1, Math.floor(rect.height * ratio));
  if (waterfallCanvas.width !== width || waterfallCanvas.height !== height) {
    waterfallCanvas.width = width;
    waterfallCanvas.height = height;
  }
  waterfallCtx.clearRect(0, 0, width, height);
  waterfallCtx.fillStyle = '#11191c';
  waterfallCtx.fillRect(0, 0, width, height);
  waterfallHoverModel = null;
  const plot = {
    left: 64 * ratio,
    right: 18 * ratio,
    top: 12 * ratio,
    bottom: 34 * ratio
  };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const rows = history.filter(snapshot => (snapshot.bins || []).length);
  if (!rows.length) {
    waterfallCtx.fillStyle = '#9fb0b7';
    waterfallCtx.font = `${14 * ratio}px system-ui, sans-serif`;
    waterfallCtx.fillText('Waiting for sweep history', plot.left, plot.top + 16 * ratio);
    drawWaterfallAxes(plot, plotWidth, plotHeight, ratio);
    return;
  }
  const powers = rows.flatMap(snapshot => snapshot.bins.map(bin => bin.power_db));
  const minPower = Math.min(...powers);
  const maxPower = Math.max(...powers);
  const span = Math.max(1, maxPower - minPower);
  const rowHeight = plotHeight / rows.length;
  const latestBins = rows[rows.length - 1].bins || [];
  const firstHz = latestBins[0]?.center_hz;
  const lastHz = latestBins[latestBins.length - 1]?.center_hz;
  drawWaterfallAxes(plot, plotWidth, plotHeight, ratio, {firstHz, lastHz});
  rows.forEach((snapshot, rowIndex) => {
    const bins = snapshot.bins || [];
    const colWidth = plotWidth / bins.length;
    const y = plot.top + rowIndex * rowHeight;
    bins.forEach((bin, colIndex) => {
      waterfallCtx.fillStyle = powerColor((bin.power_db - minPower) / span);
      waterfallCtx.fillRect(plot.left + colIndex * colWidth, y, Math.ceil(colWidth), Math.ceil(rowHeight));
    });
  });
  waterfallHoverModel = {rows, plot, plotWidth, plotHeight, ratio};
}

function drawWaterfallAxes(plot, plotWidth, plotHeight, ratio, scale) {
  const axisColor = '#9fb0b7';
  waterfallCtx.strokeStyle = axisColor;
  waterfallCtx.lineWidth = 1;
  waterfallCtx.beginPath();
  waterfallCtx.moveTo(plot.left, plot.top);
  waterfallCtx.lineTo(plot.left, plot.top + plotHeight);
  waterfallCtx.lineTo(plot.left + plotWidth, plot.top + plotHeight);
  waterfallCtx.stroke();
  waterfallCtx.fillStyle = axisColor;
  waterfallCtx.font = `${11 * ratio}px system-ui, sans-serif`;
  waterfallCtx.textAlign = 'center';
  waterfallCtx.textBaseline = 'top';
  waterfallCtx.fillText('Frequency (MHz)', plot.left + plotWidth / 2, plot.top + plotHeight + 18 * ratio);
  waterfallCtx.save();
  waterfallCtx.translate(15 * ratio, plot.top + plotHeight / 2);
  waterfallCtx.rotate(-Math.PI / 2);
  waterfallCtx.textBaseline = 'middle';
  waterfallCtx.fillText('Recent sweeps', 0, 0);
  waterfallCtx.restore();
  if (!scale || !scale.firstHz || !scale.lastHz) {
    return;
  }
  waterfallCtx.textBaseline = 'top';
  waterfallCtx.fillText(hzToMhz(scale.firstHz), plot.left, plot.top + plotHeight + 4 * ratio);
  waterfallCtx.fillText(hzToMhz(scale.lastHz), plot.left + plotWidth, plot.top + plotHeight + 4 * ratio);
}

function powerColor(value) {
  const clamped = Math.max(0, Math.min(1, value));
  const hue = 220 - clamped * 180;
  const lightness = 18 + clamped * 52;
  return `hsl(${hue}, 86%, ${lightness}%)`;
}

function renderSpectrumMeter(spectrum) {
  const historyCount = (spectrum.history || []).length;
  const noise = spectrum.noise_floor_db;
  const noiseText = noise === null || noise === undefined ? 'noise floor: n/a' : `noise floor: ${noise.toFixed(1)} dB`;
  const meterText = `${noiseText} · threshold: +${Number(spectrum.event_threshold_db || 0).toFixed(1)} dB · sweeps: ${historyCount} · events: ${(spectrum.events || []).length}`;
  if (meterText === latestSpectrumMeterText) {
    return;
  }
  latestSpectrumMeterText = meterText;
  spectrumMeterEl.textContent = meterText;
}

function drawSpectrumAxes(plot, plotWidth, plotHeight, ratio, scale) {
  const axisColor = '#9fb0b7';
  const gridColor = '#33444a';
  spectrumCtx.strokeStyle = gridColor;
  spectrumCtx.lineWidth = 1;
  spectrumCtx.fillStyle = axisColor;
  spectrumCtx.font = `${11 * ratio}px system-ui, sans-serif`;
  spectrumCtx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i += 1) {
    const y = plot.top + (plotHeight / 4) * i;
    spectrumCtx.beginPath();
    spectrumCtx.moveTo(plot.left, y);
    spectrumCtx.lineTo(plot.left + plotWidth, y);
    spectrumCtx.stroke();
    if (scale) {
      const value = scale.yMax - ((scale.yMax - scale.yMin) / 4) * i;
      spectrumCtx.textAlign = 'right';
      spectrumCtx.fillText(`${value.toFixed(0)} dB`, plot.left - 8 * ratio, y);
    }
  }
  spectrumCtx.strokeStyle = axisColor;
  spectrumCtx.beginPath();
  spectrumCtx.moveTo(plot.left, plot.top);
  spectrumCtx.lineTo(plot.left, plot.top + plotHeight);
  spectrumCtx.lineTo(plot.left + plotWidth, plot.top + plotHeight);
  spectrumCtx.stroke();
  spectrumCtx.textAlign = 'center';
  spectrumCtx.textBaseline = 'top';
  spectrumCtx.fillText('Frequency (MHz)', plot.left + plotWidth / 2, plot.top + plotHeight + 30 * ratio);
  spectrumCtx.save();
  spectrumCtx.translate(15 * ratio, plot.top + plotHeight / 2);
  spectrumCtx.rotate(-Math.PI / 2);
  spectrumCtx.textBaseline = 'middle';
  spectrumCtx.fillText('Power (dB)', 0, 0);
  spectrumCtx.restore();
  if (!scale) {
    return;
  }
  spectrumCtx.textBaseline = 'top';
  spectrumCtx.fillText(hzToMhz(scale.firstHz), plot.left, plot.top + plotHeight + 7 * ratio);
  spectrumCtx.fillText(hzToMhz(scale.lastHz), plot.left + plotWidth, plot.top + plotHeight + 7 * ratio);
}

function drawBinLabels(bins, plot, plotWidth, plotHeight, ratio, slotWidth) {
  spectrumCtx.fillStyle = '#9fb0b7';
  spectrumCtx.font = `${10 * ratio}px system-ui, sans-serif`;
  spectrumCtx.textAlign = 'right';
  spectrumCtx.textBaseline = 'middle';
  const labelEvery = slotWidth > 42 * ratio ? 1 : Math.ceil((42 * ratio) / slotWidth);
  bins.forEach((bin, index) => {
    if (index % labelEvery !== 0) {
      return;
    }
    const x = plot.left + index * slotWidth + slotWidth / 2;
    const y = plot.top + plotHeight + 11 * ratio;
    spectrumCtx.save();
    spectrumCtx.translate(x, y);
    spectrumCtx.rotate(-Math.PI / 5);
    spectrumCtx.fillText(hzToMhz(bin.center_hz), 0, 0);
    spectrumCtx.restore();
  });
}

function renderTracks(tracks) {
  trackList.innerHTML = '';
  if (!tracks.length) {
    trackList.innerHTML = '<div class="empty">No tracks yet.</div>';
    return;
  }
  for (const track of tracks) {
    const existing = markers.get(track.track_id);
    const label = track.label || track.track_id;
    if (existing) {
      existing.setLatLng([track.lat, track.lon]);
      existing.setPopupContent(popup(track));
    } else {
      markers.set(track.track_id, L.marker([track.lat, track.lon]).addTo(map).bindPopup(popup(track)));
    }
    renderTrail(track);
    const item = document.createElement('button');
    item.className = 'track';
    item.textContent = `${label} · ${track.domain} · ${track.state}`;
    item.addEventListener('click', () => {
      map.setView([track.lat, track.lon], 11);
      markers.get(track.track_id).openPopup();
    });
    trackList.appendChild(item);
  }
}

function renderStatus(status) {
  latestStatus = status;
  const running = status.process_running ? 'running' : 'stopped';
  const count = status.message_count ?? 0;
  const error = status.mode === 'idle' ? '' : status.error;
  statusEl.textContent = `${status.mode} ${running} · ${count} messages${error ? ` · ${error}` : ''}`;
}

waterfallCanvas.addEventListener('mousemove', event => {
  if (!waterfallHoverModel) {
    waterfallTooltipEl.hidden = true;
    return;
  }
  const rect = waterfallCanvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) * waterfallHoverModel.ratio;
  const y = (event.clientY - rect.top) * waterfallHoverModel.ratio;
  const {rows, plot, plotWidth, plotHeight, ratio} = waterfallHoverModel;
  if (
    x < plot.left ||
    x > plot.left + plotWidth ||
    y < plot.top ||
    y > plot.top + plotHeight
  ) {
    waterfallTooltipEl.hidden = true;
    return;
  }
  const rowIndex = Math.min(rows.length - 1, Math.floor(((y - plot.top) / plotHeight) * rows.length));
  const bins = rows[rowIndex].bins || [];
  const colIndex = Math.min(bins.length - 1, Math.floor(((x - plot.left) / plotWidth) * bins.length));
  const bin = bins[colIndex];
  if (!bin) {
    waterfallTooltipEl.hidden = true;
    return;
  }
  waterfallTooltipEl.innerHTML = `${escapeHtml(hzToMhz(bin.center_hz))} MHz<br>${bin.power_db.toFixed(1)} dB`;
  waterfallTooltipEl.hidden = false;
  waterfallTooltipEl.style.left = `${(x / ratio) + 12}px`;
  waterfallTooltipEl.style.top = `${(y / ratio) + 12}px`;
});

waterfallCanvas.addEventListener('mouseleave', () => {
  waterfallTooltipEl.hidden = true;
});

function renderSession(session, status) {
  const commandWasOpen = document.getElementById('session-command')?.open || false;
  if (!session) {
    sessionEl.innerHTML = '<div class="empty">No active session.</div>';
    return;
  }
  const command = session.command ? session.command.join(' ') : '';
  sessionEl.innerHTML = `<div class="hardware-block">
      <strong>${session.mode}</strong> · ${session.status}<br>
      decoder: ${session.decoder || 'n/a'}<br>
      receiver: ${session.receiver_id}<br>
      messages: ${status.message_count ?? 0}<br>
      last: ${status.last_message_at || 'n/a'}
    </div>
    ${command ? `<details id="session-command"${commandWasOpen ? ' open' : ''}><summary>Command</summary><pre>${escapeHtml(command)}</pre></details>` : ''}`;
}

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function renderTrail(track) {
  const points = await fetch(`/api/tracks/${encodeURIComponent(track.track_id)}/trail`).then(r => r.json());
  const latLngs = points.map(point => [point.lat, point.lon]);
  const existing = trails.get(track.track_id);
  if (existing) {
    existing.setLatLngs(latLngs);
  } else {
    const color = track.domain === 'air' ? '#2563eb' : '#0f766e';
    trails.set(track.track_id, L.polyline(latLngs, {color, weight: 3, opacity: 0.8}).addTo(map));
  }
}

function popup(track) {
  return `<strong>${track.label || track.track_id}</strong><br>
    ${track.domain}/${track.protocol}<br>
    ${track.lat.toFixed(5)}, ${track.lon.toFixed(5)}<br>
    speed: ${track.speed_mps ?? 'n/a'} m/s<br>
    altitude: ${track.altitude_m ?? 'n/a'} m`;
}

async function refreshHardware() {
  const check = await fetch('/api/check').then(r => r.json());
  const tools = check.tools.map(tool => `${tool.name}: ${tool.found ? 'found' : 'missing'}`).join('<br>');
  const usb = check.rtlsdr_usb_devices.length
    ? check.rtlsdr_usb_devices.map(device => `${device.usb_id} ${device.description}`).join('<br>')
    : 'No RTL-SDR USB devices found';
  const probe = check.rtlsdr_probe.skipped
    ? `<div class="hardware-block"><strong>RTL-SDR probe</strong><br>Skipped: ${check.rtlsdr_probe.skipped}</div>`
    : '';
  const warnings = check.warnings.length
    ? `<div class="warnings">${check.warnings.map(warning => `<div>${warning}</div>`).join('')}</div>`
    : '';
  hardwareEl.innerHTML = `<div class="hardware-block"><strong>Tools</strong><br>${tools}</div>
    <div class="hardware-block"><strong>USB</strong><br>${usb}</div>
    <div class="hardware-block"><strong>DVB modules</strong><br>${check.kernel_dvb_modules.join(', ') || 'none'}</div>
    ${probe}
    ${warnings}`;
}

refresh();
refreshHardware();
loadSpectrumPresets();
connectStream();
