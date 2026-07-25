/**
 * app.js — main entry-point: boots the WebSocket connection, polls REST
 * as fallback, and wires up the dashboard.
 */

const badge      = document.getElementById("connection-badge");
const statusText = document.getElementById("status-text");
let reconnectTimer = null;

// Frontend mode: 'dev' => show all signals; 'user' => restrict to whitelist
const FRONTEND_MODE = (window.FRONTEND_MODE || 'dev').toLowerCase();
let currentProfile = null;
let availableProfiles = [];
let profileSessions = [];
let profileSessionStats = [];
let profileSessionStatsByName = new Map();
let profileSessionsOnlineTotal = 0;
let profileSessionsOfflineTotal = 0;
let permissionNoticeTimer = null;
let profileModalState = { mode: 'create', name: null, sectionId: null };
let pendingDangerAction = { key: null, expiresAt: 0 };
let profileHeartbeatTimer = null;
let realtimeDisconnected = false;

// Whitelist of signals to show in `user` mode (provided by user request).
const USER_SIGNAL_WHITELIST = [
  "HMI_CrashSeverity",
  "HMI_CrashImpactTrigger",
  "HMI_SILG_ActivationRequest",
  "HB_ActivationSync",
  "Actuator_Manual_Seat_Function_enable",
  "HB_DynamicTargetTemp",
  "HB_ManualTargetTemp",
  "ABL_FL_RetractRequest",
  "ABL_FR_RetractRequest",
  "ABL_RL1_RetractRequest",
  "ABL_RL2_RetractRequest",
  "ACR_FR_RetractRequest",
  "ACR_RL1_RetractRequest",
  "ACR_RL2_RetractRequest",
  "ELK_FR_LockingRequest",
  "ELK_RL1_LockingRequest",
  "ELK_RL2_LockingRequest",
  "ISB_FL_ColorBlue",
  "ISB_FL_ColorRed",
  "ISB_FL_Intensity",
  "ISB_FL_Normalization",
  "ISB_FL_Transitionspeed",
  "ISB_FL_GroupOrModule",
  "ISB_FL_AdressByte0",
  "ISB_FL_AdressByte1",
  "ISB_FR_ColorGreen",
  "ISB_FR_ColorBlue",
  "ISB_FR_ColorRed",
  "ISB_FR_Intensity",
  "ISB_FR_Normalization",
  "ISB_FR_Transitionspeed",
  "ISB_FR_GroupOrModule",
  "ISB_FR_AdressByte0",
  "ISB_FR_AdressByte1",
  "ISB_RL1_ColorGreen",
  "ISB_RL1_ColorBlue",
  "ISB_RL1_ColorRed",
  "ISB_RL1_Intensity",
  "ISB_RL1_Normalization",
  "ISB_RL1_Transitionspeed",
  "ISB_RL1_GroupOrModule",
  "ISB_RL1_AdressByte0",
  "ISB_RL1_AdressByte1",
  "ISB_RL2_ColorGreen",
  "ISB_RL2_ColorBlue",
  "ISB_RL2_ColorRed",
  "ISB_RL2_Intensity",
  "ISB_RL2_Normalization",
  "ISB_RL2_Transitionspeed",
  "ISB_RL2_GroupOrModule",
  "ISB_RL2_AdressByte0",
  "ISB_RL2_AdressByte1",
  "OMS_FL_HandsOnWheel",
  "OMS_FL_OccupantClassification",
  "OMS_FL_OutOfPosition",
  "OMS_FL_OccupantWeight",
  "OMS_FL_OccupantLength",
  "OMS_FL_OccupantGender",
  "OMS_FR_OccupantClassification",
  "OMS_FR_OutOfPosition",
  "OMS_FR_OccupantWeight",
  "OMS_FR_OccupantLength",
  "OMS_FR_OccupantGender",
  "SPS_FL_SeatDirectionX",
  "SPS_FL_SeatDirectionZ",
  "SPS_FL_SeatBackRestPosition",
  "SPS_FL_FootRestPosition",
  "SPS_FL_HeadRestPosition",
  "SPS_FR_SeatDirectionX",
  "SPS_FR_SeatDirectionZ",
  "SPS_FR_SeatBackRestPosition",
  "SPS_FR_FootRestPosition",
  "SPS_FR_HeadRestPosition",
  "ABL_FL_S0SensorStatus",
  "ABL_FL_ActivationLevelStatus",
  "ABL_FL_ActivationPhase",
  "ABL_FR_S0SensorStatus",
  "ABL_FR_ActivationLevelStatus",
  "ABL_FR_ActivationPhase",
  "ABL_RL1_S0SensorStatus",
  "ABL_RL1_ActivationLevelStatus",
  "ABL_RL1_ActivationPhase",
  "ABL_RL2_S0SensorStatus",
  "ABL_RL2_ActivationLevelStatus",
  "ABL_RL2_ActivationPhase",
  "ACR_FL_ActivationPhase",
  "ACR_FL_SpoolFasterClutch",
  "ACR_FR_ActivationLevelStatus",
  "ACR_FR_ActivationPhase",
  "ACR_FR_SpoolFasterClutch",
  "ACR_RL1_ActivationLevelStatus",
  "ACR_RL1_ActivationPhase",
  "ACR_RL1_SpoolFasterClutch",
  "ACR_RL2_ActivationLevelStatus",
  "ACR_RL2_ActivationPhase",
  "ACR_RL2_SpoolFasterClutch",
  "WMS_FL_SpoolAngle",
  "WMS_FL_SensorStatus",
  "WMS_FR_WebbingMovement",
  "WMS_FR_SpoolAngle",
  "WMS_FR_SensorStatus",
  "WMS_RL1_WebbingMovement",
  "WMS_RL1_SpoolAngle",
  "WMS_RL1_SensorStatus",
  "WMS_RL2_WebbingMovement",
  "WMS_RL2_SpoolAngle",
  "WMS_RL2_SensorStatus",
  "ELK_FR_LockingStatus",
  "ELK_RL1_LockingStatus",
  "ELK_RL2_LockingStatus",
  "BSW_FR_BuckleStatus",
  "BSW_RL1_BuckleStatus",
  "BSW_RL2_BuckleStatus",
  "HB_FR_ActivationLevel",
  "HB_RL1_ActivationLevel",
  "HB_RL2_ActivationLevel"
];

function isSignalAllowed(name, std_name) {
  if (FRONTEND_MODE === 'dev') return true;
  return USER_SIGNAL_WHITELIST.includes(name) || USER_SIGNAL_WHITELIST.includes(std_name);
}

function normalizePermissionList(rawPermissions) {
  const raw = Array.isArray(rawPermissions) ? rawPermissions : [];
  const normalized = [];
  if (raw.includes('full')) return ['full'];
  if (raw.includes('read')) normalized.push('read');
  if (raw.includes('write')) normalized.push('write');
  return normalized.length ? normalized : ['read'];
}

function summarizeProfilePermissions(profile) {
  const signals = Array.isArray(profile?.signals) ? profile.signals : [];
  const union = new Set();
  signals.forEach((item) => {
    const permission = normalizePermissionList(item?.permission || []);
    permission.forEach((p) => union.add(p));
  });
  return normalizePermissionList(Array.from(union));
}

function getProfileSignals() {
  if (!Array.isArray(currentProfile?.signals)) return [];
  return currentProfile.signals
    .map((item) => {
      if (!item) return null;
      const name = String(item.name || '').trim();
      if (!name) return null;
      return { name, permission: normalizePermissionList(item.permission) };
    })
    .filter(Boolean);
}

function getSignalPermissions(signalName, stdName) {
  const signals = getProfileSignals();
  const permissionUnion = new Set();
  signals.forEach((item) => {
    if (item.name === signalName || (!!stdName && item.name === stdName)) {
      normalizePermissionList(item.permission).forEach((permission) => permissionUnion.add(permission));
    }
  });
  return Array.from(permissionUnion);
}

function getProfilePermissions() {
  const union = new Set();
  getProfileSignals().forEach((item) => {
    normalizePermissionList(item.permission).forEach((permission) => union.add(permission));
  });
  return normalizePermissionList(Array.from(union));
}

function hasProfilePermission(required) {
  const permissions = new Set(getProfilePermissions());
  if (permissions.has('full')) return true;
  return permissions.has(required);
}

function isSignalInProfileScope(signalName, stdName) {
  if (!currentProfile) return true;
  const allowed = new Set(getProfileSignals().map((item) => item.name));
  return allowed.has(signalName) || (!!stdName && allowed.has(stdName));
}

function getSignalAccessState(signalName, stdName, writable) {
  const signalPermissions = new Set(getSignalPermissions(signalName, stdName));
  const inScope = isSignalInProfileScope(signalName, stdName);
  const canRead = inScope && (signalPermissions.has('read') || signalPermissions.has('full'));
  const canWrite = writable && inScope && (signalPermissions.has('write') || signalPermissions.has('full'));
  let reason = '';
  let required = null;
  if (!inScope) {
    reason = 'Signal nằm ngoài phạm vi profile hiện tại';
    required = 'read/write';
  } else if (!canRead) {
    reason = 'Signal hiện tại thiếu quyền read';
    required = 'read';
  } else if (writable && !canWrite) {
    reason = 'Signal hiện tại thiếu quyền write';
    required = 'write';
  }
  return { canRead, canWrite, reason, required };
}

function ensurePermissionNotice() {
  let el = document.getElementById('permission-notice');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'permission-notice';
  el.className = 'permission-notice';
  const header = document.querySelector('.hmi-header');
  if (header && header.parentNode) {
    header.parentNode.insertBefore(el, header.nextSibling);
  }
  return el;
}

function showPermissionWarnings(warnings, source = 'permission') {
  const list = Array.isArray(warnings) ? warnings.filter(Boolean) : [];
  if (!list.length) return;
  const el = ensurePermissionNotice();
  const text = list.map((warning) => {
    if (typeof warning === 'string') return warning;
    const code = warning.code ? `[${warning.code}] ` : '';
    return `${code}${warning.message || 'Permission warning'}`;
  }).join(' | ');
  el.textContent = `${source}: ${text}`;
  el.classList.add('permission-notice--visible');
  clearTimeout(permissionNoticeTimer);
  permissionNoticeTimer = setTimeout(() => {
    el.classList.remove('permission-notice--visible');
  }, 5000);
}

function showUiNotice(message, options = {}) {
  const { level = 'info', source = 'ui', code = 'ui_notice' } = options;
  showPermissionWarnings([{ code, message }], source);
  if (level === 'error') {
    console.error(`[${source}] ${message}`);
  }
}

function requestDangerConfirmation(actionKey, message, ttlMs = 5000) {
  const now = Date.now();
  if (pendingDangerAction.key === actionKey && now <= pendingDangerAction.expiresAt) {
    pendingDangerAction = { key: null, expiresAt: 0 };
    return true;
  }
  pendingDangerAction = { key: actionKey, expiresAt: now + ttlMs };
  showUiNotice(`${message} (bấm lại trong ${Math.round(ttlMs / 1000)} giây để xác nhận)`, {
    level: 'warning',
    source: 'confirm',
    code: 'confirm_required',
  });
  return false;
}

window.showUiNotice = showUiNotice;
window.requestDangerConfirmation = requestDangerConfirmation;

function normalizeWarnings(payload) {
  if (!payload) return [];
  if (Array.isArray(payload.warnings)) return payload.warnings;
  if (payload.detail && typeof payload.detail === 'object') return [payload.detail];
  return [];
}

function ensureProfileSelector() {
  let wrap = document.getElementById('profile-selector-wrap');
  if (wrap) return wrap;
  const target = document.querySelector('.hmi-header > div');
  if (!target) return null;
  wrap = document.createElement('label');
  wrap.id = 'profile-selector-wrap';
  wrap.className = 'profile-selector';
  wrap.innerHTML = `
    <span class="profile-selector__label">Profile</span>
    <select id="profile-select" aria-label="Working profile"></select>
    <span id="profile-permission-chip" class="profile-permission-chip">No profile</span>
  `;
  target.appendChild(wrap);
  wrap.querySelector('#profile-select').addEventListener('change', async (event) => {
    const selectedName = event.target.value;
    const previousName = currentProfile?.name || getProfileName() || '';
    try {
      const switched = await setActiveProfile(selectedName, { devMode: FRONTEND_MODE === 'dev' });
      if (switched?.warnings?.length) {
        showPermissionWarnings(switched.warnings, 'profile');
      }
      setProfileName(switched?.active || selectedName);
      await refreshProfileContext();
      refreshPermissionDecorations();
      showPermissionWarnings([{ code: 'profile_selected', message: `Using profile '${selectedName}'` }], 'profile');
      connect();
    } catch (error) {
      event.target.value = previousName;
      showPermissionWarnings(normalizeWarnings(error.payload || error), 'profile');
      showUiNotice(`Cannot activate profile '${selectedName}': ${error.message}`, {
        level: 'error',
        source: 'profile',
        code: 'profile_activate_failed',
      });
    }
  });
  return wrap;
}

function renderProfileSelector() {
  const wrap = ensureProfileSelector();
  if (!wrap) return;
  const select = wrap.querySelector('#profile-select');
  const chip = wrap.querySelector('#profile-permission-chip');
  if (!select || !chip) return;

  const formatProfileLabel = (profileName) => {
    const stat = profileSessionStatsByName.get(profileName);
    if (!stat) return profileName;
    return `${profileName} (${stat.online}/${stat.total})`;
  };

  select.innerHTML = availableProfiles.map((profile) => {
    const selected = currentProfile?.name === profile.name ? ' selected' : '';
    return `<option value="${profile.name}"${selected}>${formatProfileLabel(profile.name)}</option>`;
  }).join('');
  chip.textContent = currentProfile ? getProfilePermissions().join(', ') : 'No profile';
  chip.className = `profile-permission-chip ${hasProfilePermission('full') ? 'profile-permission-chip--full' : hasProfilePermission('write') ? 'profile-permission-chip--write' : 'profile-permission-chip--read'}`;
}

function getKnownSignalNames() {
  const names = new Set([
    ...Array.from(signalMetadataCache.keys()),
    ...Array.from(signalUnits.keys()),
    ...USER_SIGNAL_WHITELIST,
    ...getProfileSignals().map((item) => item.name),
  ]);
  return Array.from(names).filter(Boolean).sort((a, b) => a.localeCompare(b));
}

function getKnownSignalTags() {
  const tags = new Set();
  signalMetadataCache.forEach((meta, signalName) => {
    const metaTags = Array.isArray(meta?.tag) && meta.tag.length
      ? meta.tag
      : signalName.split('_').filter((part) => /^[A-Z]+$/.test(part));
    metaTags.forEach((tag) => {
      if (tag) tags.add(tag);
    });
  });
  return Array.from(tags).sort((a, b) => a.localeCompare(b));
}

function getSignalsForTag(tag) {
  if (!tag) return [];
  return Array.from(signalMetadataCache.values())
    .filter((meta) => Array.isArray(meta?.tag) && meta.tag.includes(tag))
    .map((meta) => meta.signal_name)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));
}

function ensureProfileManagerModal() {
  let modal = document.getElementById('profile-manager-modal');
  if (modal) return modal;

  modal = document.createElement('div');
  modal.id = 'profile-manager-modal';
  modal.className = 'modal-shell';
  modal.style.display = 'none';
  modal.innerHTML = `
    <div class="modal-backdrop"></div>
    <div class="modal profile-manager-modal">
      <div class="profile-manager-modal__header">
        <h3>Profile Management</h3>
        <button id="profile-modal-close" class="btn">Close</button>
      </div>
      <div class="profile-manager-modal__body">
        <aside class="profile-manager-sidebar">
          <div class="profile-manager-sidebar__top">
            <button id="profile-new-btn" class="btn">New Profile</button>
          </div>
          <div id="profile-list" class="profile-list"></div>
        </aside>
        <section class="profile-manager-editor">
          <p id="profile-session-summary" class="profile-form-meta"></p>
          <div class="profile-form-grid">
            <label>
              <span>Name</span>
              <input id="profile-form-name" type="text" />
            </label>
            <label>
              <span>Description</span>
              <input id="profile-form-description" type="text" />
            </label>
          </div>
          <div class="profile-permission-group">
            <span>Default permission for new signals</span>
            <label><input type="checkbox" value="read" data-permission> read</label>
            <label><input type="checkbox" value="write" data-permission> write</label>
            <label><input type="checkbox" value="full" data-permission> full</label>
          </div>
          <div class="profile-signal-tools">
            <label class="profile-signal-tools__picker">
              <span>Known signal</span>
              <select id="profile-signal-select"></select>
            </label>
            <button id="profile-add-signal-btn" class="btn">Add Signal</button>
            <label class="profile-signal-tools__picker">
              <span>Known tag</span>
              <select id="profile-tag-select"></select>
            </label>
            <button id="profile-add-tag-btn" class="btn">Add Tag</button>
          </div>
          <label class="profile-signal-editor">
            <span>Signals</span>
            <textarea id="profile-form-signals" rows="14" placeholder="One signal per line"></textarea>
          </label>
          <div class="profile-manager-modal__actions">
            <button id="profile-save-btn" class="btn">Save</button>
            <button id="profile-delete-btn" class="btn">Delete</button>
          </div>
          <p id="profile-form-meta" class="profile-form-meta"></p>
        </section>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  modal.querySelector('.modal-backdrop').addEventListener('click', closeProfileManagementModal);
  modal.querySelector('#profile-modal-close').addEventListener('click', closeProfileManagementModal);
  modal.querySelector('#profile-new-btn').addEventListener('click', () => {
    profileModalState = { mode: 'create', name: null, sectionId: null };
    renderProfileManager();
  });
  modal.querySelector('#profile-add-signal-btn').addEventListener('click', addSelectedSignalToProfileForm);
  modal.querySelector('#profile-add-tag-btn').addEventListener('click', addSelectedTagToProfileForm);
  modal.querySelector('#profile-save-btn').addEventListener('click', saveProfileFromModal);
  modal.querySelector('#profile-delete-btn').addEventListener('click', deleteProfileFromModal);
  modal.querySelector('#profile-form-signals').addEventListener('input', renderProfileSignalPicker);
  return modal;
}

function closeProfileManagementModal() {
  const modal = document.getElementById('profile-manager-modal');
  if (modal) modal.style.display = 'none';
}

function readSignalsFromForm() {
  const textarea = document.getElementById('profile-form-signals');
  if (!textarea) return [];
  const defaultPermission = normalizePermissionList(getProfileFormPermissions());
  const parsed = new Map();

  textarea.value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const [nameRaw, permissionRaw] = line.split('|').map((part) => (part || '').trim());
      if (!nameRaw) return;
      const linePermission = permissionRaw
        ? normalizePermissionList(permissionRaw.split(',').map((item) => item.trim()).filter(Boolean))
        : defaultPermission;
      parsed.set(nameRaw, { name: nameRaw, permission: linePermission });
    });

  return Array.from(parsed.values());
}

function writeSignalsToForm(signals) {
  const textarea = document.getElementById('profile-form-signals');
  if (!textarea) return;
  const lines = (signals || []).map((item) => {
    const name = String(item?.name || '').trim();
    if (!name) return '';
    const permission = normalizePermissionList(item.permission).join(',');
    return `${name} | ${permission}`;
  }).filter(Boolean);
  textarea.value = lines.join('\n');
}

function getProfileFormPermissions() {
  return Array.from(document.querySelectorAll('#profile-manager-modal [data-permission]:checked')).map((el) => el.value);
}

function setProfileFormPermissions(permissions) {
  const allowed = new Set(permissions || []);
  document.querySelectorAll('#profile-manager-modal [data-permission]').forEach((el) => {
    el.checked = allowed.has(el.value);
  });
}

function getEditingProfile() {
  return availableProfiles.find((profile) => profile.name === profileModalState.name) || null;
}

function fillProfileForm(profile) {
  const isEdit = !!profile;
  const nameInput = document.getElementById('profile-form-name');
  const descInput = document.getElementById('profile-form-description');
  if (nameInput) {
    nameInput.value = profile?.name || '';
    nameInput.disabled = isEdit;
  }
  if (descInput) descInput.value = profile?.description || '';
  setProfileFormPermissions(profile ? summarizeProfilePermissions(profile) : ['read']);
  writeSignalsToForm(profile?.signals || []);
  const meta = document.getElementById('profile-form-meta');
  if (meta) {
    meta.textContent = isEdit
      ? `Editing '${profile.name}' • section_id ${profile.section_id}`
      : 'Creating a new profile';
  }
}

function renderProfileList() {
  const list = document.getElementById('profile-list');
  if (!list) return;

  const formatProfileLabel = (profileName) => {
    const stat = profileSessionStatsByName.get(profileName);
    if (!stat) return profileName;
    return `${profileName} (${stat.online}/${stat.total})`;
  };

  list.innerHTML = availableProfiles.map((profile) => {
    const isSelected = profile.name === profileModalState.name;
    const isCurrent = profile.name === currentProfile?.name;
    const profilePermissions = summarizeProfilePermissions(profile);
    return `
      <button class="profile-list__item ${isSelected ? 'profile-list__item--selected' : ''}" data-profile-name="${profile.name}">
        <span class="profile-list__name">${formatProfileLabel(profile.name)}</span>
        <span class="profile-list__meta">${profilePermissions.join(', ')}${isCurrent ? ' • active' : ''}</span>
      </button>
    `;
  }).join('');
  list.querySelectorAll('[data-profile-name]').forEach((el) => {
    el.addEventListener('click', async () => {
      const name = el.getAttribute('data-profile-name');
      const profile = await fetchProfile(name);
      profileModalState = { mode: 'edit', name: profile.name, sectionId: profile.section_id };
      fillProfileForm(profile);
      renderProfileList();
    });
  });
}

function renderProfileSignalPicker() {
  const select = document.getElementById('profile-signal-select');
  if (!select) return;
  const currentSignals = new Set(readSignalsFromForm().map((item) => item.name));
  const options = getKnownSignalNames().filter((name) => !currentSignals.has(name));
  select.innerHTML = options.length
    ? options.map((name) => `<option value="${name}">${name}</option>`).join('')
    : '<option value="">No more known signals</option>';
}

function renderProfileTagPicker() {
  const select = document.getElementById('profile-tag-select');
  if (!select) return;
  const options = getKnownSignalTags();
  select.innerHTML = options.length
    ? options.map((tag) => `<option value="${tag}">${tag}</option>`).join('')
    : '<option value="">No known tags</option>';
}

function renderProfileManager() {
  ensureProfileManagerModal();
  renderProfileList();
  renderProfileSessionSummary();
  fillProfileForm(profileModalState.mode === 'edit' ? getEditingProfile() : null);
  renderProfileSignalPicker();
  renderProfileTagPicker();
  const deleteBtn = document.getElementById('profile-delete-btn');
  if (deleteBtn) deleteBtn.style.display = profileModalState.mode === 'edit' ? 'inline-flex' : 'none';
}

async function refreshProfileSessions() {
  try {
    const sessionsData = await listProfileSessions({ devMode: FRONTEND_MODE === 'dev' });
    profileSessions = sessionsData.sessions || [];
    profileSessionStats = sessionsData.by_profile || [];
    profileSessionStatsByName = new Map(profileSessionStats.map((item) => [item.profile_name, item]));
    profileSessionsOnlineTotal = Number(sessionsData.online_total || 0);
    profileSessionsOfflineTotal = Number(sessionsData.offline_total || 0);
  } catch (error) {
    profileSessions = [];
    profileSessionStats = [];
    profileSessionStatsByName = new Map();
    profileSessionsOnlineTotal = 0;
    profileSessionsOfflineTotal = 0;
    showPermissionWarnings(normalizeWarnings(error.payload || error), 'profile-sessions');
  }
}

function renderProfileSessionSummary() {
  const el = document.getElementById('profile-session-summary');
  if (!el) return;

  const clientId = typeof getClientId === 'function' ? getClientId() : '';
  const ownSession = profileSessions.find((session) => session.client_id === clientId);
  const ownText = ownSession
    ? `Client ${clientId} -> ${ownSession.active}`
    : `Client ${clientId || 'unknown'} -> global fallback`;

  const activeProfileName = currentProfile?.name || getProfileName() || '';
  const activeProfileStat = activeProfileName ? profileSessionStatsByName.get(activeProfileName) : null;
  const activeProfileText = activeProfileStat
    ? `${activeProfileName}: ${activeProfileStat.online}/${activeProfileStat.total} devices online`
    : (activeProfileName ? `${activeProfileName}: 0 devices` : 'No active profile');

  const others = profileSessions
    .filter((session) => session.client_id !== clientId)
    .slice(0, 5)
    .map((session) => `${session.client_id}: ${session.active}`)
    .join(' | ');
  const othersText = others || 'No other client sessions';

  el.textContent = `${ownText}. Devices online: ${profileSessionsOnlineTotal}/${profileSessions.length} (offline: ${profileSessionsOfflineTotal}). ${activeProfileText}. Others: ${othersText}`;
}

async function sendProfileHeartbeat() {
  // Fallback heartbeat: cho phép khi chưa từng xác nhận mất kết nối realtime.
  // Khi đã nhận event disconnect, dừng heartbeat để tránh giữ session online sai.
  if (!isRealtimeConnected() && realtimeDisconnected) return;
  try {
    await heartbeatProfileSession();
  } catch (error) {
    console.debug('profile heartbeat failed', error?.message || error);
  }
}

function startProfileHeartbeat() {
  if (profileHeartbeatTimer) clearInterval(profileHeartbeatTimer);
  sendProfileHeartbeat();
  profileHeartbeatTimer = setInterval(sendProfileHeartbeat, 30000);
}

function addSelectedSignalToProfileForm() {
  const select = document.getElementById('profile-signal-select');
  if (!select || !select.value) return;
  const signals = readSignalsFromForm();
  if (!signals.some((item) => item.name === select.value)) {
    signals.push({ name: select.value, permission: normalizePermissionList(getProfileFormPermissions()) });
  }
  writeSignalsToForm(signals);
  renderProfileSignalPicker();
}

function addSelectedTagToProfileForm() {
  const select = document.getElementById('profile-tag-select');
  if (!select || !select.value) return;
  const tag = select.value;
  const current = readSignalsFromForm();
  const byName = new Map(current.map((item) => [item.name, item]));
  getSignalsForTag(tag).forEach((signalName) => {
    if (!byName.has(signalName)) {
      byName.set(signalName, { name: signalName, permission: normalizePermissionList(getProfileFormPermissions()) });
    }
  });
  writeSignalsToForm(Array.from(byName.values()).sort((a, b) => a.name.localeCompare(b.name)));
  renderProfileSignalPicker();
  renderProfileTagPicker();
}

async function openProfileManagementModal() {
  await refreshProfileContext();
  await refreshProfileSessions();
  profileModalState = currentProfile
    ? { mode: 'edit', name: currentProfile.name, sectionId: currentProfile.section_id }
    : { mode: 'create', name: null, sectionId: null };
  renderProfileManager();
  const modal = ensureProfileManagerModal();
  modal.style.display = 'block';
}

async function saveProfileFromModal() {
  const name = document.getElementById('profile-form-name')?.value?.trim();
  const description = document.getElementById('profile-form-description')?.value?.trim() || null;
  const signals = readSignalsFromForm();

  if (!name) {
    showPermissionWarnings([{ code: 'profile_name_required', message: 'Profile name is required' }], 'profile');
    return;
  }
  if (!signals.length) {
    showPermissionWarnings([{ code: 'profile_signal_required', message: 'Add at least one signal entry' }], 'profile');
    return;
  }

  try {
    if (profileModalState.mode === 'edit') {
      const existing = await fetchProfile(profileModalState.name);
      await updateProfile({
        name: existing.name,
        signals,
        description,
        section_id: existing.section_id,
      });
    } else {
      await createProfile({ name, signals, description });
      profileModalState = { mode: 'edit', name, sectionId: null };
    }
    await refreshProfileContext();
    await refreshProfileSessions();
    renderProfileManager();
    refreshPermissionDecorations();
    connect();
    await loadSnapshot();
    showPermissionWarnings([{ code: 'profile_saved', message: `Profile '${name}' saved` }], 'profile');
  } catch (error) {
    showPermissionWarnings(normalizeWarnings(error.payload || error), 'profile');
    showUiNotice(`Profile save failed: ${error.message}`, {
      level: 'error',
      source: 'profile',
      code: 'profile_save_failed',
    });
  }
}

async function deleteProfileFromModal() {
  if (profileModalState.mode !== 'edit' || !profileModalState.name) return;
  const confirmed = window.confirm(`Delete profile '${profileModalState.name}'? This cannot be undone.`);
  if (!confirmed) {
    return;
  }
  try {
    await deleteProfile(profileModalState.name);
    if (getProfileName() === profileModalState.name) setProfileName('');
    await refreshProfileContext();
    await refreshProfileSessions();
    profileModalState = currentProfile
      ? { mode: 'edit', name: currentProfile.name, sectionId: currentProfile.section_id }
      : { mode: 'create', name: null, sectionId: null };
    renderProfileManager();
    refreshPermissionDecorations();
    connect();
    await loadSnapshot();
    showPermissionWarnings([{ code: 'profile_deleted', message: 'Profile deleted' }], 'profile');
  } catch (error) {
    showPermissionWarnings(normalizeWarnings(error.payload || error), 'profile');
    showUiNotice(`Profile delete failed: ${error.message}`, {
      level: 'error',
      source: 'profile',
      code: 'profile_delete_failed',
    });
  }
}

async function refreshProfileContext() {
  try {
    const profilesData = await listProfiles();
    availableProfiles = profilesData.profiles || [];
    const selected = profilesData.active || getProfileName() || availableProfiles[0]?.name || '';
    if (selected) setProfileName(selected);
    currentProfile = selected ? await fetchProfile(selected) : null;
    await refreshProfileSessions();
    renderProfileSelector();
  } catch (error) {
    currentProfile = null;
    availableProfiles = [];
    showPermissionWarnings(normalizeWarnings(error.payload || error), 'profile');
  }
}

function updateSignalRowAccess(row, signalName, writable = false) {
  if (!row) return;
  const meta = getSignalMetadata(signalName) || signalMetadataCache.get(signalName) || {};
  const stdName = meta.std_name || null;
  const access = getSignalAccessState(signalName, stdName, writable);
  row.classList.toggle('signal-row--blocked-read', !access.canRead);
  row.classList.toggle('signal-row--blocked-write', access.canRead && writable && !access.canWrite);
  const indicator = row.querySelector('.signal-access-indicator');
  if (indicator) {
    indicator.textContent = access.reason ? '⚠' : '';
    indicator.title = access.reason || '';
    indicator.classList.toggle('signal-access-indicator--visible', !!access.reason);
  }
  const btn = row.querySelector('.write-btn');
  if (btn) {
    btn.classList.toggle('write-btn--warn', writable && !access.canWrite);
    btn.title = access.reason || btn.dataset.defaultTitle || 'Write signal';
  }
}

function refreshPermissionDecorations() {
  document.querySelectorAll('#signal-table-body tr').forEach((row) => {
    updateSignalRowAccess(row, row.dataset.signalName, row.dataset.writable === 'true');
  });
  const settingsBtn = document.getElementById('btn-settings');
  const alarmsBtn = document.getElementById('btn-alarms');
  const profilesBtn = document.getElementById('btn-profiles');
  [settingsBtn, alarmsBtn, profilesBtn].forEach((btn) => {
    if (!btn) return;
    const allowed = hasProfilePermission('full');
    btn.classList.toggle('btn--permission-warn', !allowed);
    btn.title = allowed ? '' : 'Profile hiện tại thiếu quyền full';
  });
}

// ── Signal tracking for the table + fast gauges ────────────────────────────

const signalUnits = new Map();
const signalHistory = new Map();
const lastUpdateTs = new Map();
const lastSparkRenderMs = new Map();
const FAST_SIGNAL_THRESHOLD_S = 0.25;
const FAST_SIGNAL_MAX = 4;
const SIGNAL_HISTORY_LEN = 60;
const SPARKLINE_MIN_RENDER_INTERVAL_MS = 120;

// WS update queue to keep UI responsive under high-frequency streams.
const pendingSignalUpdates = new Map();
let uiFlushScheduled = false;
const UI_UPDATES_PER_FRAME = 120;

// fast-changing signals feature removed; no container present
const fastSignalsContainer = null;
const signalTableBody = document.getElementById("signal-table-body");

function sanitizeId(name) {
  return name.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function createSignalRow(signalName, unit, writable = false, states = null) {
  const row = document.createElement("tr");
  row.id = `signal-row-${sanitizeId(signalName)}`;
  row.dataset.signalName = signalName;
  row.dataset.writable = writable ? 'true' : 'false';

  let writeCell;
  if (!writable) {
    writeCell = `<td class="signal-write signal-write--ro">—</td>`;
  } else if (states && states.length > 0) {
    // Enum signal: render a <select> with named states
    const options = states
      .map((s) => `<option value="${s.value}">${s.value} — ${s.description}</option>`)
      .join("");
    writeCell = `<td class="signal-write">
        <select class="write-select" aria-label="Write value for ${signalName}">
          ${options}
        </select>
        <button class="write-btn btn" data-signal="${signalName}">Set</button>
       </td>`;
  } else {
    // Continuous numeric signal: render a number input
    writeCell = `<td class="signal-write">
        <input class="write-input" type="number" step="any"
               aria-label="Write value for ${signalName}" />
        <button class="write-btn btn" data-signal="${signalName}">Set</button>
       </td>`;
  }

  // Get std_name to display alongside canonical name
  const stdName = getSignalMetadata(signalName)?.std_name;
  const nameDisplay = stdName && stdName !== signalName 
    ? `${signalName}<br/><small class="signal-std-name">${stdName}</small>`
    : signalName;

  row.innerHTML = `
    <td class="signal-name">${nameDisplay}<span class="signal-access-indicator" aria-hidden="true"></span></td>
    <td class="signal-value">— ${unit || ""}</td>
    <td class="signal-history"><canvas class="sparkline" width="140" height="28"></canvas></td>
    ${writeCell}
  `;
  if (writable) {
    const btn = row.querySelector(".write-btn");
    btn.dataset.defaultTitle = 'Write signal';
    btn.addEventListener("click", () => handleWriteSignal(signalName, row));
    const inp = row.querySelector(".write-input, .write-select");
    if (inp && inp.tagName === "INPUT") {
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleWriteSignal(signalName, row);
      });
    }
  }
  signalTableBody.appendChild(row);
  updateSignalRowAccess(row, signalName, writable);
  return row;
}

async function handleWriteSignal(signalName, row) {
  const inp = row.querySelector(".write-input");
  const sel = row.querySelector(".write-select");
  const btn = row.querySelector(".write-btn");
  const raw = sel ? sel.value : (inp ? inp.value.trim() : "");
  if (raw === "") return;
  const value = parseFloat(raw);
  if (isNaN(value)) {
    if (inp) inp.classList.add("write-input--error");
    return;
  }
  if (inp) inp.classList.remove("write-input--error");
  btn.disabled = true;
  btn.textContent = "…";
  try {
    await writeSignal(signalName, value);
    btn.textContent = "✓";
    btn.classList.add("write-btn--ok");
  } catch (e) {
    console.error("writeSignal failed:", e);
    btn.textContent = "✗";
    btn.classList.add("write-btn--err");
    showPermissionWarnings(normalizeWarnings(e.payload || e), 'write');
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "Set";
      btn.classList.remove("write-btn--ok", "write-btn--err");
    }, 1500);
  }
}

function markFastSignal(/* signalName, unit */) {
  // no-op: fast-changing signals layout removed
}

function unmarkFastSignal(/* signalName */) {
  // no-op: fast-changing signals layout removed
}

function updateSignalRow(signalName, value, timestamp = Date.now() / 1000, unit = "", writable = false, states = null) {
  if (unit) signalUnits.set(signalName, unit);

  const nowTs = timestamp;
  const prevTs = lastUpdateTs.get(signalName);
  const delta = prevTs ? nowTs - prevTs : Infinity;
  lastUpdateTs.set(signalName, nowTs);

  const history = signalHistory.get(signalName) || [];
  history.push(value);
  if (history.length > SIGNAL_HISTORY_LEN) history.shift();
  signalHistory.set(signalName, history);

  if (delta < FAST_SIGNAL_THRESHOLD_S) {
    markFastSignal(signalName, unit);
  } else {
    unmarkFastSignal(signalName);
  }

  let row = document.getElementById(`signal-row-${sanitizeId(signalName)}`);
  if (!row) {
    row = createSignalRow(signalName, unit, writable, states);
  }
  updateSignalRowAccess(row, signalName, writable);
  const valueEl = row.querySelector(".signal-value");
  if (valueEl) {
    const meta = getSignalMetadata(signalName) || signalMetadataCache.get(signalName) || {};
    const access = getSignalAccessState(signalName, meta.std_name || null, writable);
    valueEl.textContent = access.canRead ? `${value.toFixed(2)} ${unit || ""}` : `⚠ restricted ${unit || ""}`;
  }
  const canvas = row.querySelector("canvas.sparkline");
  if (canvas) {
    const nowMs = performance.now();
    const lastMs = lastSparkRenderMs.get(signalName) || 0;
    // Throttle sparkline redraws; value cell still updates every flush.
    if ((nowMs - lastMs) >= SPARKLINE_MIN_RENDER_INTERVAL_MS) {
      drawSparkline(canvas, history);
      lastSparkRenderMs.set(signalName, nowMs);
    }
  }
}

function enqueueSignalUpdate(signalName, value, timestamp, std_name) {
  pendingSignalUpdates.set(signalName, {
    value,
    timestamp,
    std_name,
  });
  if (!uiFlushScheduled) {
    uiFlushScheduled = true;
    requestAnimationFrame(flushPendingSignalUpdates);
  }
}

function flushPendingSignalUpdates() {
  if (!pendingSignalUpdates.size) {
    uiFlushScheduled = false;
    return;
  }

  let processed = 0;
  for (const [name, upd] of pendingSignalUpdates) {
    pendingSignalUpdates.delete(name);
    if (!isSignalAllowed(name, upd.std_name)) continue;

    const meta = signalMetadataCache.get(name);
    updateWidget(name, upd.value);
    updateSignalRow(
      name,
      upd.value,
      upd.timestamp,
      signalUnits.get(name) || "",
      !!(meta && meta.writable),
      (meta && meta.states) || null,
    );

    processed += 1;
    if (processed >= UI_UPDATES_PER_FRAME) break;
  }

  if (pendingSignalUpdates.size) {
    requestAnimationFrame(flushPendingSignalUpdates);
  } else {
    uiFlushScheduled = false;
  }
}

// ── Signal metadata cache (populated once from /signals/available) ──────────

/** @type {Map<string, object>} signal_name → full metadata object */
const signalMetadataCache = new Map();

// ── Initial REST snapshot ──────────────────────────────────────────────────

async function loadSnapshot() {
  // 1. Fetch full metadata (heavy, once)
  try {
    const { signals_info, warnings } = await fetchAvailableSignals();
    // Populate std_name → signal_name registry for resolving names
    populateSignalRegistry(signals_info);
    if (warnings?.length) showPermissionWarnings(warnings, 'signals');
    signals_info.forEach((meta) => {
      // Use canonical signal_name as the key for metadata cache
      signalMetadataCache.set(meta.signal_name, meta);
      const unit = meta.unit || "";
      if (unit) signalUnits.set(meta.signal_name, unit);
      let row = document.getElementById(`signal-row-${sanitizeId(meta.signal_name)}`);
      if (!row && isSignalAllowed(meta.signal_name, meta.std_name)) {
        row = createSignalRow(meta.signal_name, unit, !!meta.writable, meta.states || null);
      }
      if (meta.value != null && isSignalAllowed(meta.signal_name, meta.std_name)) {
        updateWidget(meta.signal_name, meta.value);
        updateSignalRow(meta.signal_name, meta.value, meta.timestamp || 0, unit, !!meta.writable, meta.states || null);
      } else if (row) {
        updateSignalRowAccess(row, meta.signal_name, !!meta.writable);
      }
    });
    console.info(`Loaded metadata for ${signals_info.length} signals; std_name registry populated`);
  } catch (e) {
    console.warn("Available signals fetch failed, falling back to /signals:", e);
    // Fallback to legacy snapshot
    try {
      const { items, warnings } = await fetchSignals();
      if (warnings?.length) showPermissionWarnings(warnings, 'signals');
      items.forEach(({ signal_name, std_name, value, unit, timestamp }) => {
        if (!isSignalAllowed(signal_name, std_name)) return;
        updateWidget(signal_name, value);
        updateSignalRow(signal_name, value, timestamp, unit || "");
      });
    } catch (e2) {
      console.warn("Snapshot fetch also failed:", e2);
    }
  }
  try {
    const { items } = await fetchActiveAlarms();
    // Only render up to 3 alarms in the UI
    (items || []).slice(0, 3).forEach(renderAlarm);
  } catch (e) {
    console.warn("Alarm fetch failed:", e);
    showPermissionWarnings(normalizeWarnings(e.payload || e), 'alarms');
  }
}

// ── WebSocket handler (new subscribe protocol + legacy fallback) ───────────

/** @type {{ ws: WebSocket, subscribe: function, unsubscribe: function } | null} */
let subConn = null;
/** @type {WebSocket | null} */
let legacySock = null;

function isRealtimeConnected() {
  if (subConn?.ws && subConn.ws.readyState === WebSocket.OPEN) return true;
  if (legacySock && legacySock.readyState === WebSocket.OPEN) return true;
  return false;
}

async function handleRealtimeDisconnect() {
  try {
    await markProfileSessionOffline();
  } catch (error) {
    console.debug('profile offline update failed', error?.message || error);
  }

  try {
    await refreshProfileSessions();
    renderProfileSelector();
    const modal = document.getElementById('profile-manager-modal');
    if (modal && modal.style.display === 'block') {
      renderProfileList();
      renderProfileSessionSummary();
    }
  } catch (error) {
    console.debug('profile session refresh after disconnect failed', error?.message || error);
  }
}

function connect() {
  clearTimeout(reconnectTimer);

  if (subConn?.ws && subConn.ws.readyState <= WebSocket.OPEN) {
    try { subConn.ws.close(); } catch (e) {}
  }
  if (legacySock && legacySock.readyState <= WebSocket.OPEN) {
    try { legacySock.close(); } catch (e) {}
    legacySock = null;
  }

  try {
    subConn = openSubscriptionWS(handleMessage, () => {
      realtimeDisconnected = false;
      badge.textContent   = "Connected";
      badge.className     = "badge badge--connected";
      statusText.textContent = "Live — subscribe protocol active";

      // Subscribe channels depending on frontend mode.
      if (FRONTEND_MODE === 'dev') {
        subConn.subscribe(["*", "alarms", "metrics"], "continuous");
      } else {
        // subscribe only to whitelist signals plus alarms and metrics
        const channels = USER_SIGNAL_WHITELIST.slice();
        // channels.push('alarms', 'metrics');
        subConn.subscribe(channels, 'continuous');
      }
        // If metrics polling was started earlier, stop it since subscribe will push metrics.
        try {
          if (typeof _metricsPollTimer !== 'undefined' && _metricsPollTimer) {
            clearInterval(_metricsPollTimer);
            _metricsPollTimer = null;
          }
        } catch (e) {}
    });

    subConn.ws.addEventListener("close", () => {
      badge.textContent   = "Disconnected";
      badge.className     = "badge badge--disconnected";
      statusText.textContent = "Reconnecting in 5 s…";
      realtimeDisconnected = true;
      subConn = null;
      void handleRealtimeDisconnect();
      reconnectTimer = setTimeout(connect, 5000);
    });

    subConn.ws.addEventListener("error", (evt) => {
      console.warn("WebSocket error", evt);
      subConn.ws.close();
    });
  } catch (e) {
    console.warn("Subscribe WS failed, falling back to legacy:", e);
    connectLegacy();
  }
}

function connectLegacy() {
  clearTimeout(reconnectTimer);
  legacySock = openWebSocket("all", handleMessage);
  const sock = legacySock;

  sock.addEventListener("open", () => {
    realtimeDisconnected = false;
    badge.textContent   = "Connected";
    badge.className     = "badge badge--connected";
    statusText.textContent = "Live — receiving data via WebSocket (legacy)";
  });

  sock.addEventListener("close", () => {
    badge.textContent   = "Disconnected";
    badge.className     = "badge badge--disconnected";
    statusText.textContent = "Reconnecting in 5 s…";
    realtimeDisconnected = true;
    legacySock = null;
    void handleRealtimeDisconnect();
    reconnectTimer = setTimeout(connect, 5000);
  });

  sock.addEventListener("error", (evt) => {
    console.warn("WebSocket error", evt);
    sock.close();
  });
}

function handleMessage(msg) {
  // Unified signal frame: {timestamp: "ISO8601", signals: [{name, std_name, value}]}
  if (Array.isArray(msg.signals) && !msg.type) {
    const parsedTs = msg.timestamp ? (new Date(msg.timestamp).getTime() / 1000) : NaN;
    const ts = Number.isFinite(parsedTs) ? parsedTs : Date.now() / 1000;
    msg.signals.forEach(({ name, value, std_name }) => {
      enqueueSignalUpdate(name, value, ts, std_name);
    });
    return;
  }
  if (msg.type === "alarm") {
    renderAlarm(msg);
  } else if (msg.type === "metrics") {
    renderMetrics(msg);
  } else if (msg.type === "subscribe_ack") {
    // Current backend ack format.
    console.debug("Subscribe ack:", msg.channels, "count:", msg.count);
    if (msg.warnings?.length) showPermissionWarnings(msg.warnings, 'subscribe');
  } else if (msg.type === "subscribed") {
    // Backward-compat fallback.
    console.debug("Subscribed:", msg.signals, "count:", msg.count);
  } else if (msg.type === "pong") {
    // keepalive response — no action needed
  }
}

// ── Boot ───────────────────────────────────────────────────────────────────

(async () => {
  document.getElementById('btn-profiles')?.addEventListener('click', openProfileManagementModal);
  await refreshProfileContext();
  await loadSnapshot();
  // Update signal panel title based on mode
  try {
    const sigTitle = document.querySelector('#signal-panel h2');
    if (sigTitle) sigTitle.textContent = FRONTEND_MODE === 'dev' ? 'All Signals' : 'User Signals';
  } catch(e) {}
  connect();
  refreshPermissionDecorations();
  startProfileHeartbeat();
  // Metrics polling as fallback — WS subscribe also pushes metrics.
  // Keep REST fallback in case WS doesn't cover metrics yet.
  startMetricsPolling();
  initCameraStream();
})();

window.addEventListener('beforeunload', () => {
  // Best-effort: fire and forget so backend can reduce online count quickly.
  void markProfileSessionOffline();
});

// ── Camera Stream ───────────────────────────────────────────────────────────

const CAMERA_STATUS_POLL_INTERVAL_MS = 5000;
const CAMERA_RETRY_DELAY_MS = 4000;

function initCameraStream() {
  const img       = document.getElementById("camera-stream-img");
  const offlineEl = document.getElementById("camera-stream-offline");
  const badge     = document.getElementById("camera-status-badge");
  const infoEl    = document.getElementById("camera-stream-info");
  if (!img) return;

  let retryTimer = null;

  function startStream() {
    if (offlineEl) offlineEl.style.display = "none";
    img.style.display = "block";
    // Cache-bust so <img> reopens a fresh multipart connection to the proxy.
    img.src = `${cameraStreamUrl()}?t=${Date.now()}`;
  }

  img.addEventListener("error", () => {
    img.style.display = "none";
    if (offlineEl) offlineEl.style.display = "flex";
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(startStream, CAMERA_RETRY_DELAY_MS);
  });

  async function pollStatus() {
    try {
      const status = await fetchCameraStatus();
      if (badge) {
        badge.textContent = status.connected ? "Connected" : "Reconnecting…";
        badge.className = `badge ${status.connected ? "badge--connected" : "badge--disconnected"}`;
      }
      if (infoEl) {
        infoEl.textContent = `${status.stream_url}  |  Viewers: ${status.viewer_count}` +
          (status.last_error ? `  |  Last error: ${status.last_error}` : "");
      }
    } catch (err) {
      if (badge) {
        badge.textContent = "Unavailable";
        badge.className = "badge badge--disconnected";
      }
      if (infoEl) infoEl.textContent = "Camera stream not configured.";
    }
  }

  startStream();
  pollStatus();
  setInterval(pollStatus, CAMERA_STATUS_POLL_INTERVAL_MS);
}


// ── CarPC System Metrics Polling ───────────────────────────────────────────

const METRICS_POLL_INTERVAL_MS = 3000;
let _prevNetSent = 0;
let _prevNetRecv = 0;
let _prevNetTs = 0;
let _metricsPollTimer = null;
// Metrics history for sparklines
const METRICS_HISTORY_LEN = 60;
const metricsHistory = new Map();

function pushMetricHistory(key, value) {
  let arr = metricsHistory.get(key) || [];
  arr.push(typeof value === 'number' ? value : 0);
  if (arr.length > METRICS_HISTORY_LEN) arr.shift();
  metricsHistory.set(key, arr);
  return arr;
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes.toFixed(0) + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + " MB";
  return (bytes / 1073741824).toFixed(2) + " GB";
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function setBarFill(barId, percent) {
  const el = document.getElementById(barId);
  if (!el) return;
  el.style.width = Math.min(percent, 100) + "%";
  el.classList.remove("carpc-bar__fill--warn", "carpc-bar__fill--crit");
  if (percent > 90) el.classList.add("carpc-bar__fill--crit");
  else if (percent > 70) el.classList.add("carpc-bar__fill--warn");
}

function renderMetrics(d) {
  // CPU (system)
  const cpuEl = document.getElementById("carpc-cpu-percent");
  if (cpuEl) cpuEl.textContent = d.cpu_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-cpu-spark'), pushMetricHistory('cpu_percent', d.cpu_percent)); } catch(e){}
  setBarFill("carpc-cpu-bar", d.cpu_percent);
  const cpuDetail = document.getElementById("carpc-cpu-detail");
  if (cpuDetail) cpuDetail.textContent =
    `${d.cpu_count_physical}C/${d.cpu_count_logical}T  ${d.cpu_freq_current_mhz} MHz`;

  // Process CPU
  const procCpu = document.getElementById("carpc-proc-cpu");
  if (procCpu) procCpu.textContent = d.process_cpu_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-proc-cpu-spark'), pushMetricHistory('process_cpu_percent', d.process_cpu_percent)); } catch(e){}
  setBarFill("carpc-proc-cpu-bar", Math.min(d.process_cpu_percent, 100));
  const procDetail = document.getElementById("carpc-proc-detail");
  if (procDetail) procDetail.textContent =
    `PID ${d.process_pid}  Threads: ${d.process_threads}  Files: ${d.process_open_files >= 0 ? d.process_open_files : "N/A"}`;

  // RAM
  const ramEl = document.getElementById("carpc-ram-percent");
  if (ramEl) ramEl.textContent = d.ram_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-ram-spark'), pushMetricHistory('ram_percent', d.ram_percent)); } catch(e){}
  setBarFill("carpc-ram-bar", d.ram_percent);
  const ramDetail = document.getElementById("carpc-ram-detail");
  if (ramDetail) ramDetail.textContent =
    `${d.ram_used_mb.toFixed(0)} / ${d.ram_total_mb.toFixed(0)} MB (avail ${d.ram_available_mb.toFixed(0)} MB)`;

  // Process Memory
  const procMem = document.getElementById("carpc-proc-mem");
  if (procMem) procMem.textContent = d.process_memory_rss_mb.toFixed(1) + " MB";
  try { drawSparkline(document.getElementById('carpc-proc-mem-spark'), pushMetricHistory('process_memory_rss_mb', d.process_memory_rss_mb)); } catch(e){}
  setBarFill("carpc-proc-mem-bar", d.process_memory_percent);
  const procMemDetail = document.getElementById("carpc-proc-mem-detail");
  if (procMemDetail) procMemDetail.textContent =
    `RSS ${d.process_memory_rss_mb.toFixed(1)} MB  VMS ${d.process_memory_vms_mb.toFixed(1)} MB  (${d.process_memory_percent.toFixed(1)}%)`;

  // Disk
  const diskEl = document.getElementById("carpc-disk-percent");
  if (diskEl) diskEl.textContent = d.disk_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-disk-spark'), pushMetricHistory('disk_percent', d.disk_percent)); } catch(e){}
  setBarFill("carpc-disk-bar", d.disk_percent);
  const diskDetail = document.getElementById("carpc-disk-detail");
  if (diskDetail) diskDetail.textContent =
    `${d.disk_used_gb.toFixed(1)} / ${d.disk_total_gb.toFixed(1)} GB (free ${d.disk_free_gb.toFixed(1)} GB)`;

  // Swap
  const swapEl = document.getElementById("carpc-swap-percent");
  if (swapEl) swapEl.textContent = d.swap_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-swap-spark'), pushMetricHistory('swap_percent', d.swap_percent)); } catch(e){}
  setBarFill("carpc-swap-bar", d.swap_percent);
  const swapDetail = document.getElementById("carpc-swap-detail");
  if (swapDetail) swapDetail.textContent =
    `${d.swap_used_mb.toFixed(0)} / ${d.swap_total_mb.toFixed(0)} MB`;

  // Queue
  const queueEl = document.getElementById("carpc-queue-percent");
  if (queueEl) queueEl.textContent = d.queue_usage_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-queue-spark'), pushMetricHistory('queue_usage_percent', d.queue_usage_percent)); } catch(e){}
  setBarFill("carpc-queue-bar", d.queue_usage_percent);
  const queueDetail = document.getElementById("carpc-queue-detail");
  if (queueDetail) queueDetail.textContent =
    `${d.queue_size} / ${d.queue_maxsize} items`;

  // Heap / GC
  const heapEl = document.getElementById("carpc-heap");
  if (heapEl) heapEl.textContent = d.heap_allocated_mb.toFixed(1) + " MB";
  try { drawSparkline(document.getElementById('carpc-heap-spark'), pushMetricHistory('heap_allocated_mb', d.heap_allocated_mb)); } catch(e){}
  // Use process memory percent for bar approximation
  setBarFill("carpc-heap-bar", d.process_memory_percent);
  const gcDetail = document.getElementById("carpc-gc-detail");
  if (gcDetail) gcDetail.textContent = `GC objects: ${d.gc_objects.toLocaleString()}`;

  // Network I/O (calculate rate)
  const netEl = document.getElementById("carpc-net");
  const netDetail = document.getElementById("carpc-net-detail");
  const now = d.timestamp;
  if (_prevNetTs > 0) {
    const dt = now - _prevNetTs;
    if (dt > 0) {
      const txRate = (d.net_bytes_sent - _prevNetSent) / dt;
      const rxRate = (d.net_bytes_recv - _prevNetRecv) / dt;
      if (netEl) netEl.textContent = `TX ${formatBytes(Math.max(0, txRate))}/s`;
      if (netDetail) netDetail.textContent =
        `RX ${formatBytes(Math.max(0, rxRate))}/s  Total: TX ${formatBytes(d.net_bytes_sent)} / RX ${formatBytes(d.net_bytes_recv)}`;
    }
  } else {
    if (netEl) netEl.textContent = `TX ${formatBytes(d.net_bytes_sent)}`;
    if (netDetail) netDetail.textContent =
      `RX ${formatBytes(d.net_bytes_recv)}  (cumulative since boot)`;
  }
  try { drawSparkline(document.getElementById('carpc-net-spark'), pushMetricHistory('net_tx_rate', _prevNetSent>0 ? (d.net_bytes_sent - _prevNetSent) : 0)); } catch(e){}
  _prevNetSent = d.net_bytes_sent;
  _prevNetRecv = d.net_bytes_recv;
  _prevNetTs = now;

  // Async Tasks
  const taskEl = document.getElementById("carpc-tasks");
  if (taskEl) taskEl.textContent = d.asyncio_tasks;
  try { drawSparkline(document.getElementById('carpc-tasks-spark'), pushMetricHistory('asyncio_tasks', d.asyncio_tasks)); } catch(e){}
  const taskDetail = document.getElementById("carpc-tasks-detail");
  if (taskDetail) taskDetail.textContent = "running asyncio tasks";

  // Uptime
  const uptimeEl = document.getElementById("carpc-uptime");
  if (uptimeEl) uptimeEl.textContent = formatUptime(d.uptime_seconds);
  const uptimeDetail = document.getElementById("carpc-uptime-detail");
  if (uptimeDetail) uptimeDetail.textContent = `${d.uptime_seconds.toFixed(0)}s total`;

  // Platform
  const platEl = document.getElementById("carpc-platform");
  if (platEl) platEl.textContent = d.platform || "—";
  const platDetail = document.getElementById("carpc-platform-detail");
  if (platDetail) platDetail.textContent = `Python ${d.python_version}`;
}

function startMetricsPolling() {
  async function poll() {
    try {
      const data = await fetchSystemMetrics();
      renderMetrics(data);
    } catch (e) {
      console.warn("Metrics poll failed:", e);
    }
  }
  // If subscribe WS is active (subConn), rely on server push and skip polling.
  if (subConn) {
    // already using subscription; no polling needed
    return;
  }
  poll(); // immediate first fetch
  _metricsPollTimer = setInterval(poll, METRICS_POLL_INTERVAL_MS);
}
