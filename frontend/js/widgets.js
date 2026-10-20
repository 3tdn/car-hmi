/**
 * widgets.js — renders and updates gauge and bar widgets.
 */

// Canvas and Plotly share the same palette as the surrounding UI.
const chartTheme = (() => {
  const styles = getComputedStyle(document.documentElement);
  const color = (name) => styles.getPropertyValue(name).trim();
  return {
    accent: color('--accent'),
    warning: color('--warn'),
    critical: color('--crit'),
    track: color('--gauge-bg'),
    border: color('--border'),
    text: color('--text'),
    muted: color('--muted'),
    surface: color('--surface'),
    font: color('--font'),
  };
})();

/**
 * Draw an arc-gauge on a <canvas> element.
 * @param {HTMLCanvasElement} canvas
 * @param {number} value
 * @param {number} min
 * @param {number} max
 */
function drawGauge(canvas, value, min, max) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const cx = w / 2, cy = h * 0.6;
  const r  = Math.min(w, h) * 0.4;
  const startAngle = Math.PI * 0.75;
  const endAngle   = Math.PI * 2.25;
  const pct = Math.max(0, Math.min(1, (value - min) / (max - min)));

  ctx.clearRect(0, 0, w, h);

  // Background arc
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, endAngle);
  ctx.strokeStyle = chartTheme.track;
  ctx.lineWidth   = 14;
  ctx.lineCap     = "round";
  ctx.stroke();

  // Value arc
  const color = pct > 0.85 ? chartTheme.critical : pct > 0.65 ? chartTheme.warning : chartTheme.accent;
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, startAngle + pct * (endAngle - startAngle));
  ctx.strokeStyle = color;
  ctx.lineWidth   = 14;
  ctx.lineCap     = "round";
  ctx.stroke();
}

/**
 * Update a gauge widget DOM element.
 * @param {HTMLElement} el
 * @param {number} value
 */
function updateGaugeWidget(el, value) {
  const canvas = el.querySelector(".gauge__canvas");
  const valueEl = el.querySelector(".gauge__value");
  const unit   = el.dataset.unit || "";
  const min    = parseFloat(el.dataset.min  ?? 0);
  const max    = parseFloat(el.dataset.max  ?? 100);

  drawGauge(canvas, value, min, max);
  valueEl.textContent = `${value.toFixed(1)} ${unit}`;
}

/**
 * Update a bar widget DOM element.
 * @param {HTMLElement} el
 * @param {number} value
 */
function updateBarWidget(el, value) {
  const fill   = el.querySelector(".bar__fill");
  const valueEl = el.querySelector(".bar__value");
  const unit   = el.dataset.unit || "";
  const min    = parseFloat(el.dataset.min ?? 0);
  const max    = parseFloat(el.dataset.max ?? 100);
  const pct    = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));

  fill.style.width = `${pct}%`;
  fill.style.background = pct > 85 ? chartTheme.critical : pct > 65 ? chartTheme.warning : chartTheme.accent;
  valueEl.textContent = `${value.toFixed(1)} ${unit}`;
}

/**
 * Draw a lightweight sparkline of recent signal values.
 * @param {HTMLCanvasElement} canvas
 * @param {number[]} values
 */
function drawSparkline(canvas, values) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!values || values.length < 2) return;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  ctx.beginPath();
  values.forEach((v, idx) => {
    const x = (idx / (values.length - 1)) * (w - 2) + 1;
    const y = h - ((v - min) / range) * (h - 2) - 1;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = chartTheme.accent;
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

/**
 * Map signal name → widget element and update it.
 * @param {string} signalName
 * @param {number} value
 */
const _widgetElementsCache = new Map();

function _getWidgetElements(signalName) {
  let elements = _widgetElementsCache.get(signalName);
  if (!elements) {
    elements = Array.from(document.querySelectorAll(`[data-signal="${signalName}"]`));
    _widgetElementsCache.set(signalName, elements);
  }
  return elements;
}

function updateWidget(signalName, value) {
  const elements = _getWidgetElements(signalName);
  if (!elements.length) return;
  elements.forEach((el) => {
    if (el.classList.contains("gauge")) updateGaugeWidget(el, value);
    else if (el.classList.contains("bar")) updateBarWidget(el, value);
  });
}
