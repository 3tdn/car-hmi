/* Signal inspection, sample freshness and write-request feedback. */
const configuredStaleSeconds = Number(window.SIGNAL_STALE_THRESHOLD_SEC);
const SIGNAL_STALE_AFTER_SECONDS = configuredStaleSeconds > 0 ? configuredStaleSeconds : 30;
const signalHistoryTimes = new Map();
const signalLatestValues = new Map();
const signalWriteRequests = new Map();
const metricHistoryTimes = new Map();
let lastMetricsSampleAt = 0;
let inspectedSignalName = null;
const DEFAULT_SIGNAL_CHART_WINDOW_SECONDS = 10;
const SIGNAL_DETAIL_WINDOW_SECONDS = 60 * 60;

function formatSignalNumber(value) {
  if (!Number.isFinite(value)) return '—';
  if (value !== 0 && Math.abs(value) < 0.0001) return value.toExponential(3);
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 4, useGrouping: false }).format(value);
}

function sampleAge(timestamp) {
  return timestamp > 0 ? Math.max(0, Date.now() / 1000 - timestamp) : Infinity;
}

function sampleFreshness(timestamp) {
  if (!isRealtimeConnected()) return 'offline';
  if (!(timestamp > 0)) return 'waiting';
  return sampleAge(timestamp) > SIGNAL_STALE_AFTER_SECONDS ? 'stale' : 'live';
}

function ageLabel(timestamp) {
  const age = sampleAge(timestamp);
  if (!Number.isFinite(age)) return 'No sample yet';
  if (age < 1) return 'Updated now';
  return `Updated ${Math.floor(age)}s ago`;
}

function signalMetadata(name) {
  return getSignalMetadata(name) || signalMetadataCache.get(name) || {};
}

function signalChartInfo(name, windowSeconds = DEFAULT_SIGNAL_CHART_WINDOW_SECONDS) {
  const allValues = signalHistory.get(name) || [];
  const allTimestamps = signalHistoryTimes.get(name) || [];
  const firstAvailable = signalHistoryStart?.get(name) || 0;
  const windowEnd = Date.now() / 1000;
  const windowStart = windowEnd - windowSeconds;
  let low = firstAvailable;
  let high = allTimestamps.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (allTimestamps[mid] < windowStart) low = mid + 1;
    else high = mid;
  }
  // A 100 ms view can fall between source ticks; carry the immediately prior
  // sample to the left edge so the shortest window does not flicker empty.
  if (windowSeconds <= 0.1 && low > firstAvailable && windowStart - allTimestamps[low - 1] <= 0.15) low -= 1;

  // Keep graph work bounded by its pixel width, including when viewing an hour.
  const sampleCount = Math.max(0, allTimestamps.length - low);
  const bucketCount = Math.min(sampleCount, 520);
  const values = [];
  const timestamps = [];
  if (bucketCount > 0) {
    let cursor = low;
    for (let bucket = 0; bucket < bucketCount; bucket += 1) {
      const bucketEnd = low + Math.ceil(((bucket + 1) * sampleCount) / bucketCount);
      let minValue = Infinity;
      let maxValue = -Infinity;
      let minIndex = cursor;
      let maxIndex = cursor;
      while (cursor < bucketEnd) {
        if (allValues[cursor] < minValue) { minValue = allValues[cursor]; minIndex = cursor; }
        if (allValues[cursor] > maxValue) { maxValue = allValues[cursor]; maxIndex = cursor; }
        cursor += 1;
      }
      if (minIndex <= maxIndex) {
        values.push(allValues[minIndex], allValues[maxIndex]);
        timestamps.push(Math.max(windowStart, allTimestamps[minIndex]), Math.max(windowStart, allTimestamps[maxIndex]));
      } else {
        values.push(allValues[maxIndex], allValues[minIndex]);
        timestamps.push(Math.max(windowStart, allTimestamps[maxIndex]), Math.max(windowStart, allTimestamps[minIndex]));
      }
    }
  }
  const latestTimestamp = allTimestamps.at(-1);
  if (values.length && latestTimestamp <= windowEnd && windowEnd - latestTimestamp <= 0.15) {
    values.push(allValues.at(-1));
    timestamps.push(windowEnd);
  }
  const meta = signalMetadata(name);
  const fixed = Number.isFinite(meta.min_value) && Number.isFinite(meta.max_value) && meta.max_value > meta.min_value;
  const min = fixed ? meta.min_value : values.length ? Math.min(...values) : 0;
  const max = fixed ? meta.max_value : values.length ? Math.max(...values) : 1;
  return {
    values,
    timestamps,
    min,
    max,
    windowStart,
    windowEnd,
    caption: `${formatSignalNumber(min)}–${formatSignalNumber(max)} · ${formatChartWindow(windowSeconds)}`,
    description: `${fixed ? 'Fixed signal range' : 'Observed range (auto scale)'}: ${formatSignalNumber(min)} to ${formatSignalNumber(max)}. Rolling ${formatChartWindow(windowSeconds)} window, sampled at 100 ms; ${sampleCount} samples available.`,
  };
}

function formatChartWindow(seconds) {
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${seconds / 60} min`;
  return '1 h';
}

function renderSignalValue(row, name, value, canRead) {
  const meta = signalMetadata(name);
  const number = row.querySelector('.signal-number');
  const state = row.querySelector('.signal-state');
  if (number) number.textContent = canRead ? formatSignalNumber(value) : '⚠ restricted';
  const enumState = canRead ? meta.states?.find((item) => Number(item.value) === value) : null;
  if (state) {
    state.textContent = enumState?.description || '';
    state.hidden = !state.textContent;
  }
  const cell = row.querySelector('.signal-value');
  if (cell) cell.title = canRead ? `${formatSignalNumber(value)}${meta.unit ? ' · Unit: ' + meta.unit : ''}${enumState ? ' · ' + enumState.description : ''}` : 'Read access restricted';
}

function refreshSignalFreshness() {
  const counts = { live: 0, stale: 0, offline: 0, waiting: 0 };
  let newest = 0;
  for (const row of signalTableBody?.children || []) {
    const timestamp = lastUpdateTs.get(row.dataset.signalName) || 0;
    const freshness = sampleFreshness(timestamp);
    row.dataset.freshness = freshness;
    counts[freshness] += 1;
    newest = Math.max(newest, timestamp);
    const status = row.querySelector('.signal-sample-age');
    if (status) {
      status.textContent = `${freshness === 'live' ? '' : freshness[0].toUpperCase() + freshness.slice(1) + ' · '}${ageLabel(timestamp)}`;
      status.title = `A signal becomes stale after ${SIGNAL_STALE_AFTER_SECONDS}s without a new source sample.`;
    }
  }
  const summary = document.getElementById('signal-freshness');
  if (summary) {
    const state = !isRealtimeConnected() ? 'offline' : counts.live ? 'live' : newest ? 'stale' : 'waiting';
    summary.dataset.freshness = state;
    summary.textContent = `${state[0].toUpperCase() + state.slice(1)} · ${counts.live} fresh · ${counts.stale} stale${counts.waiting ? ' · ' + counts.waiting + ' waiting' : ''} · ${ageLabel(newest)}`;
    summary.title = `Stale threshold: ${SIGNAL_STALE_AFTER_SECONDS}s. Status uses each signal's source timestamp.`;
  }
  const metrics = document.getElementById('metrics-freshness');
  if (metrics) {
    metrics.dataset.freshness = sampleFreshness(lastMetricsSampleAt);
    metrics.textContent = `${sampleFreshness(lastMetricsSampleAt)} · ${ageLabel(lastMetricsSampleAt)}`;
  }
  updateSignalDetails();
}

function setSignalWriteFeedback(name, state, message) {
  const row = document.getElementById(`signal-row-${sanitizeId(name)}`);
  const feedback = row?.querySelector('.signal-write-status');
  if (feedback) {
    feedback.dataset.state = state;
    feedback.textContent = message;
  }
  const request = signalWriteRequests.get(name);
  const button = row?.querySelector('.write-btn');
  const access = getSignalAccessState(name, signalMetadata(name).std_name, true);
  if (button) {
    button.disabled = !access.canWrite || state === 'sending' || state === 'queued';
    button.textContent = state === 'sending' ? 'Sending…' : state === 'queued' ? 'Queued…' : 'Set';
  }
  const input = row?.querySelector('.write-input');
  if (input) input.disabled = button?.disabled || false;
  if (request) Object.assign(request, { state, message });
  else signalWriteRequests.set(name, { state, message });
  updateSignalDetails();
}

function observeSignalWrite(name, value, timestamp) {
  const request = signalWriteRequests.get(name);
  if (!request || !['sending', 'queued'].includes(request.state) || timestamp <= request.startedAt) return;
  if (Math.abs(value - request.value) > Math.max(1e-6, Math.abs(request.value) * 1e-6)) return;
  request.observed = true;
  if (request.state === 'queued') {
    clearTimeout(request.timer);
    setSignalWriteFeedback(name, 'observed', 'Matching value received. Device acknowledgement is unavailable.');
  }
}

function signalWriteError(error) {
  const detail = error?.payload?.detail;
  return typeof detail === 'string' ? detail : detail?.message || error?.payload?.message || error?.message || 'Request failed. Please retry.';
}

function openSignalDetails(name) {
  inspectedSignalName = name;
  const dialog = document.getElementById('signal-detail-dialog');
  const input = document.getElementById('signal-detail-write-value');
  input.value = document.getElementById(`signal-row-${sanitizeId(name)}`)?.querySelector('.write-input')?.value || '';
  const meta = signalMetadata(name);
  for (const [attr, key] of [['min', 'min_value'], ['max', 'max_value']]) {
    if (Number.isFinite(meta[key])) input.setAttribute(attr, meta[key]);
    else input.removeAttribute(attr);
  }
  updateSignalDetails(true);
  if (!dialog.open) dialog.showModal();
}

function updateSignalDetails(force = false) {
  const dialog = document.getElementById('signal-detail-dialog');
  if (!inspectedSignalName || (!force && !dialog?.open)) return;
  const name = inspectedSignalName;
  const meta = signalMetadata(name);
  const value = signalLatestValues.get(name);
  const access = getSignalAccessState(name, meta.std_name || null, !!meta.writable);
  const timestamp = lastUpdateTs.get(name) || 0;
  const enumState = meta.states?.find((item) => Number(item.value) === value);
  document.getElementById('signal-detail-title').textContent = name;
  const fields = [
    ['Live value', access.canRead ? formatSignalNumber(value) : 'Restricted'],
    ['State', access.canRead ? enumState?.description || '—' : 'Restricted'],
    ['Unit', meta.unit || '—'],
    ['Valid range', `${meta.min_value ?? '—'} to ${meta.max_value ?? '—'}`],
    ['Freshness', `${sampleFreshness(timestamp)} · ${ageLabel(timestamp)}`],
    ['Last source sample', timestamp > 0 ? new Date(timestamp * 1000).toLocaleString() : 'No sample yet'],
    ['Write access', !meta.writable ? 'Read only' : access.canWrite ? 'Writable' : access.reason || 'Write access restricted'],
  ];
  const list = document.getElementById('signal-detail-fields');
  list.replaceChildren();
  for (const [label, text] of fields) {
    const term = document.createElement('dt'); term.textContent = label;
    const description = document.createElement('dd'); description.textContent = text;
    list.append(term, description);
  }
  const info = signalChartInfo(name, SIGNAL_DETAIL_WINDOW_SECONDS);
  drawSparkline(document.getElementById('signal-detail-chart'), access.canRead ? info.values : [], info);
  document.getElementById('signal-detail-chart-caption').textContent = access.canRead ? info.description : 'Read access restricted';
  const form = document.getElementById('signal-detail-write-form');
  form.hidden = !meta.writable;
  const request = signalWriteRequests.get(name);
  const busy = ['sending', 'queued'].includes(request?.state);
  document.getElementById('signal-detail-set').disabled = busy || !access.canWrite;
  document.getElementById('signal-detail-write-value').disabled = busy || !access.canWrite;
  document.getElementById('signal-detail-set').textContent = busy ? request.state === 'sending' ? 'Sending…' : 'Queued…' : 'Set';
  const feedback = document.getElementById('signal-detail-write-status');
  feedback.textContent = request?.message || (meta.writable && !access.canWrite ? access.reason || 'Write access restricted' : '');
  feedback.dataset.state = request?.state || '';
}

function initSignalTools() {
  const toggle = document.getElementById('signal-trend-toggle');
  toggle.checked = !matchMedia('(max-width: 700px)').matches;
  const applyTrendVisibility = () => document.getElementById('signal-panel').classList.toggle('signal-panel--hide-trends', !toggle.checked);
  toggle.addEventListener('change', applyTrendVisibility);
  applyTrendVisibility();
  document.getElementById('signal-detail-close').addEventListener('click', () => document.getElementById('signal-detail-dialog').close());
  document.getElementById('signal-detail-write-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const row = document.getElementById(`signal-row-${sanitizeId(inspectedSignalName)}`);
    const input = row?.querySelector('.write-input');
    if (input) {
      input.value = document.getElementById('signal-detail-write-value').value;
      void handleWriteSignal(inspectedSignalName, row);
    }
  });
  document.querySelector('.skip-link').addEventListener('click', (event) => {
    event.preventDefault();
    document.getElementById('tab-dashboard').click();
    const heading = document.getElementById('dashboard-heading');
    heading.focus();
    heading.scrollIntoView({ block: 'start' });
  });
  refreshSignalFreshness();
  setInterval(refreshSignalFreshness, 1000);
  setInterval(sampleSignalHistories, 100);
}
