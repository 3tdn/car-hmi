/**
 * adaptive.js — Adaptive Restraint Systems page controller.
 * Box plot rendered with Plotly.js to match streamlit_webapp_V3.py appearance.
 * Filters auto-update the chart on every checkbox change (debounced).
 */

let ADAPTIVE_INITIALIZED = false;
let _updateTimer = null;

// ── Colour palette per system (matches Plotly default palette) ───────────────
const SYSTEM_COLORS = {
  fusion:    "#636efa",
  camera:    "#ffa15a",
  non_adapt: "#ef553b",
};

function _seriesColor(colName) {
  for (const [key, color] of Object.entries(SYSTEM_COLORS)) {
    if (colName.includes(key)) return color;
  }
  return "#19d3f3";
}

// ── Tab wiring ────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const tabDashboard  = document.getElementById("tab-dashboard");
  const tabAdaptive   = document.getElementById("tab-adaptive");
  const viewDashboard = document.getElementById("view-dashboard");
  const viewAdaptive  = document.getElementById("view-adaptive");

  if (!tabDashboard || !tabAdaptive) return;

  tabDashboard.addEventListener("click", () => {
    tabDashboard.classList.add("active");
    tabAdaptive.classList.remove("active");
    viewDashboard.style.display = "grid";
    viewAdaptive.style.display  = "none";
  });

  tabAdaptive.addEventListener("click", () => {
    tabAdaptive.classList.add("active");
    tabDashboard.classList.remove("active");
    viewDashboard.style.display = "none";
    viewAdaptive.style.display  = "grid";
    if (!ADAPTIVE_INITIALIZED) initAdaptivePage();
  });

  const btnApply = document.getElementById("btn-apply-filters");
  if (btnApply) btnApply.addEventListener("click", () => updateAdaptiveAnalysis());

  const chkRaw = document.getElementById("chk-raw-data");
  if (chkRaw) chkRaw.addEventListener("change", () => updateAdaptiveAnalysis());
});

// ── Debounced schedule ────────────────────────────────────────────────────────
function scheduleUpdate() {
  clearTimeout(_updateTimer);
  _updateTimer = setTimeout(() => updateAdaptiveAnalysis(), 600);
}

// ── Page init ─────────────────────────────────────────────────────────────────
async function initAdaptivePage() {
  ADAPTIVE_INITIALIZED = true;
  _setStatus("Loading filters...");

  try {
    const data = await fetchAdaptiveAvailable();

    _renderCheckboxes("filter-system",   data.System,   true);
    _renderCheckboxes("filter-age",      data.Age,      ["35y"]);
    _renderCheckboxes("filter-seatbelt", data.Seatbelt, true);
    _renderCheckboxes("filter-velocity", data.Velocity, true);
    _renderCheckboxes("filter-weight",   data.Weight,   true);
    _renderCheckboxes("filter-height",   data.Height,   true);
    _renderCheckboxes("filter-distance", data.Distance, true);

    _setStatus("Ready");
    await updateAdaptiveAnalysis();
  } catch (err) {
    console.error(err);
    _setStatus("Error loading filters: " + err.message);
  }
}

// ── Checkbox helpers ──────────────────────────────────────────────────────────
function _renderCheckboxes(containerId, items, defaultVal) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";

  items.forEach(val => {
    const label = document.createElement("label");
    label.className = "cb-item";

    const cb = document.createElement("input");
    cb.type  = "checkbox";
    cb.value = val;
    cb.checked = defaultVal === true
      ? true
      : Array.isArray(defaultVal) && defaultVal.map(String).includes(String(val));

    cb.addEventListener("change", scheduleUpdate);

    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + val));
    container.appendChild(label);
  });
}

function _checked(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return [];
  return Array.from(el.querySelectorAll("input:checked")).map(cb => cb.value);
}

// ── Availability highlight ────────────────────────────────────────────────────
// Map API dimension key → filter container ID
const _AVAIL_CONTAINERS = {
  Velocity: "filter-velocity",
  Weight:   "filter-weight",
  Height:   "filter-height",
  Distance: "filter-distance",
  Seatbelt: "filter-seatbelt",
};

function _applyAvailability(available) {
  if (!available) return;
  for (const [dim, containerId] of Object.entries(_AVAIL_CONTAINERS)) {
    const container = document.getElementById(containerId);
    if (!container) continue;
    const availSet = new Set((available[dim] || []).map(String));
    container.querySelectorAll("label.cb-item").forEach(label => {
      const cb = label.querySelector("input[type=checkbox]");
      if (!cb) return;
      label.classList.toggle("not-available", !availSet.has(String(cb.value)));
    });
  }
}

function _setStatus(msg) {
  const el = document.getElementById("status-text");
  if (el) el.textContent = msg;
}

// ── Main update ───────────────────────────────────────────────────────────────
async function updateAdaptiveAnalysis() {
  _setStatus("Querying database...");

  const rawDataEl = document.getElementById("chk-raw-data");
  const wantRaw   = rawDataEl ? rawDataEl.checked : true;

  // Show/hide the raw data card immediately
  const rawCard = document.getElementById("raw-data-card");
  if (rawCard) rawCard.style.display = wantRaw ? "" : "none";

  const params = {
    System:   _checked("filter-system"),
    Age:      _checked("filter-age"),
    Seatbelt: _checked("filter-seatbelt"),
    Velocity: _checked("filter-velocity"),
    Weight:   _checked("filter-weight"),
    Height:   _checked("filter-height"),
    Distance: _checked("filter-distance"),
    RawData:  wantRaw,
  };

  try {
    const data = await fetchAdaptiveChartInfo(params);

    let totalRows = data.raw_rows ? data.raw_rows.length : 0;
    for (const item of (data.datas || [])) {
      const s = Object.values(item)[0];
      if (s && s.values) { totalRows = s.values.length; break; }
    }
    const countEl = document.getElementById("rows-count");
    if (countEl) countEl.textContent = `Rows matched: ${totalRows}`;

    _drawPlotlyBox("adaptive-chart", data.datas);
    _renderMetricsTable(data.datas);
    if (wantRaw) _renderRawTable(data.raw_rows);
    _applyAvailability(data.available_options);

    _setStatus("Ready");
  } catch (err) {
    console.error(err);
    _setStatus("Error: " + err.message);
  }
}

// ── Plotly box chart ──────────────────────────────────────────────────────────
function _drawPlotlyBox(divId, seriesList) {
  if (typeof Plotly === "undefined") {
    console.warn("Plotly.js not loaded yet");
    return;
  }

  const el = document.getElementById(divId);
  if (!el) return;

  if (!seriesList || !seriesList.length) {
    Plotly.purge(el);
    el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:200px;color:var(--muted);font-style:italic;">No data matches current filters.</div>';
    return;
  }

  const traces = seriesList.map(item => {
    const colName = Object.keys(item)[0];
    const stats   = item[colName];
    const color   = _seriesColor(colName);
    const yPct = (stats.values || []).map(v => v * 100);

    return {
      type: "box",
      name: colName.replace("injury_risk_", ""),
      y:    yPct,
      boxpoints: "outliers",
      jitter:    0.3,
      marker:  { color: color, size: 3, opacity: 0.5 },
      line:    { color: color, width: 1.5 },
      fillcolor: color + "33",
    };
  });

  const layout = {
    title: { text: "Injury Risk Distribution", font: { color: "#e6edf3", size: 15 } },
    yaxis: {
      title:        { text: "Injury Risk (%)", font: { color: "#8b949e" } },
      ticksuffix:   "%",
      gridcolor:    "#30363d",
      zerolinecolor:"#30363d",
      tickfont:     { color: "#8b949e" },
    },
    xaxis: {
      tickfont:  { color: "#8b949e" },
      gridcolor: "#30363d",
    },
    boxmode:       "group",
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor:  "#161b22",
    font:          { color: "#e6edf3", family: "Segoe UI, system-ui, sans-serif" },
    legend: {
      font:        { color: "#e6edf3" },
      bgcolor:     "rgba(0,0,0,0)",
      bordercolor: "#30363d",
    },
    height: 480,
    margin: { t: 48, r: 24, b: 48, l: 64 },
  };

  Plotly.react(el, traces, layout, {
    responsive: true,
    displayModeBar: true,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
    displaylogo: false,
  });
}

// ── Metrics table ─────────────────────────────────────────────────────────────
function _renderMetricsTable(seriesList) {
  const tbody = document.getElementById("metrics-table-body");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!seriesList || !seriesList.length) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--muted);font-style:italic;">No data.</td></tr>';
    return;
  }

  seriesList.forEach(item => {
    const colName = Object.keys(item)[0];
    const stats   = item[colName];
    const vals    = stats.values || [];

    const mean = vals.length
      ? vals.reduce((s, v) => s + v, 0) / vals.length * 100
      : 0;

    const sorted = [...vals].sort((a, b) => a - b);
    const idx997 = Math.min(sorted.length - 1, Math.ceil(0.997 * sorted.length) - 1);
    const p997   = sorted.length ? sorted[Math.max(0, idx997)] * 100 : 0;

    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td style="font-weight:600">' + colName.replace("injury_risk_", "") + '</td>' +
      '<td style="color:var(--ok)">' + mean.toFixed(3) + ' %</td>' +
      '<td style="color:var(--warn)">' + p997.toFixed(3) + ' %</td>';
    tbody.appendChild(tr);
  });
}

// ── Raw datatable ─────────────────────────────────────────────────────────────
function _renderRawTable(rows) {
  const header = document.getElementById("raw-table-header");
  const body   = document.getElementById("raw-table-body");
  if (!header || !body) return;
  header.innerHTML = "";
  body.innerHTML   = "";

  if (!rows || !rows.length) {
    body.innerHTML = '<tr><td style="text-align:center;color:var(--muted);font-style:italic;">No data.</td></tr>';
    return;
  }

  const columns = Object.keys(rows[0]);
  columns.forEach(col => {
    const th = document.createElement("th");
    th.textContent = col;
    header.appendChild(th);
  });

  rows.forEach(row => {
    const tr = document.createElement("tr");
    columns.forEach(col => {
      const td  = document.createElement("td");
      const val = row[col];
      td.textContent = (typeof val === "number" && col.startsWith("injury_risk_"))
        ? (val * 100).toFixed(3) + "%"
        : (val != null ? val : "");
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}
