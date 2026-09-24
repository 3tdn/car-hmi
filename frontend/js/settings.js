// Policy-driven system settings (supports an arbitrary number of CAN buses).
const settingsModal = document.createElement('div');
settingsModal.id = 'settings-modal';
settingsModal.style.display = 'none';
settingsModal.innerHTML = `
  <div class="modal-backdrop"></div>
  <div class="modal settings-modal__panel">
    <div class="settings-modal__head">
      <div>
        <h3>System Settings</h3>
        <p>Base shows common settings; Expanded includes advanced and read-only values.</p>
      </div>
      <button id="settings-cancel" class="btn" type="button">Close</button>
    </div>
    <div class="settings-toolbar">
      <label class="settings-mode-switch" for="settings-mode">
        <span class="settings-mode-switch__label">Base</span>
        <input id="settings-mode" type="checkbox" role="switch" aria-label="Show expanded settings">
        <span class="settings-mode-switch__track" aria-hidden="true"><span class="settings-mode-switch__thumb"></span></span>
        <span class="settings-mode-switch__label">Expand</span>
      </label>
      <button id="settings-refresh" class="btn" type="button">Refresh</button>
      <button id="settings-backup" class="btn" type="button">Backup</button>
      <button id="settings-reload" class="btn" type="button">Live reload</button>
      <button id="settings-reset" class="btn btn--danger" type="button">Reset</button>
      <button id="settings-reboot" class="btn btn--danger" type="button">Reboot Car-HMI</button>
    </div>
    <div id="settings-status" class="settings-status">Loading…</div>
    <div id="settings-form" class="settings-form"></div>
    <section class="settings-backups">
      <h4>Backups</h4>
      <div id="settings-backup-list" class="settings-backup-list"></div>
    </section>
    <div class="settings-modal__footer">
      <button id="settings-save" class="btn" type="button">Save changes</button>
    </div>
  </div>
`;
document.body.appendChild(settingsModal);

let settingsSnapshot = null;
let settingsOriginal = null;
let settingsWorking = null;
let settingsViewMode = 'base';

const cloneJson = (value) => JSON.parse(JSON.stringify(value));
const escapeHtml = (value) => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#39;');
const pathSegments = (path) => path.split('.').map((item) => /^\d+$/.test(item) ? Number(item) : item);
const setAtPath = (target, path, value) => {
  const parts = pathSegments(path);
  const last = parts.pop();
  let cursor = target;
  parts.forEach((part) => { cursor = cursor[part]; });
  cursor[last] = value;
};
const findPolicy = (path) => (settingsSnapshot?.fields || []).find((item) => {
  const expected = item.path.split('.');
  const actual = path.split('.');
  return expected.length === actual.length && expected.every((part, i) => part === '*' || part === actual[i]);
});
const isPolicyVisible = (policy) => settingsViewMode === 'expand' || policy?.setting_mode === 'base';
const enumValuesFor = (path, value) => {
  const direct = findPolicy(path)?.validation?.enum;
  if (Array.isArray(direct)) return direct;
  if (Array.isArray(value)) {
    const itemEnum = findPolicy(`${path}.0`)?.validation?.enum;
    if (Array.isArray(itemEnum)) return itemEnum;
  }
  return null;
};
const snapshotValuesFor = (source) => {
  if (!source) return null;
  let value = settingsSnapshot;
  for (const segment of source.split('.')) {
    if (!value || typeof value !== 'object' || !(segment in value)) return null;
    value = value[segment];
  }
  return Array.isArray(value) ? value : null;
};
const suggestionLabelFor = (option, details) => {
  const matches = (details || []).filter((item) => item?.channel === option);
  if (!matches.length) return '';
  return matches.map((item) => {
    const state = String(item.state || 'unknown').toUpperCase();
    const operstate = String(item.operstate || 'unknown').toUpperCase();
    return `${item.interface || 'unknown'} · ${state} · OPERSTATE ${operstate}`;
  }).join(' / ');
};
const levelBadge = (policy) => {
  const level = policy?.reload_level || 'immutable';
  const label = level.toUpperCase();
  return `<span class="settings-level settings-level--${level}">${label}</span>`;
};
const metadataBadges = (policy) => {
  if (!policy) {
    return '<span class="settings-meta settings-meta--readonly">UNSUPPORTED</span>';
  }
  const editable = policy.editable === true;
  const mode = policy.setting_mode === 'base' ? 'base' : 'expand';
  return `${levelBadge(policy)}
    <span class="settings-meta settings-meta--${editable ? 'editable' : 'readonly'}">EDITABLE: ${editable ? 'TRUE' : 'FALSE'}</span>
    <span class="settings-meta settings-meta--${mode}">MODE: ${mode.toUpperCase()}</span>`;
};
const parseInput = (input, original, path) => {
  const enumValues = enumValuesFor(path, original);
  if (enumValues) {
    if (Array.isArray(original)) {
      return Array.from(input.selectedOptions, (option) => cloneJson(enumValues[Number(option.value)]));
    }
    return cloneJson(enumValues[Number(input.value)]);
  }
  if (typeof original === 'boolean') return input.checked;
  if (typeof original === 'number') return Number(input.value);
  if (Array.isArray(original)) return JSON.parse(input.value);
  return input.value;
};
const validationAttributes = (policy) => {
  const validation = policy?.validation || {};
  const attributes = [];
  if (validation.minimum !== undefined) attributes.push(`min="${escapeHtml(validation.minimum)}"`);
  if (validation.maximum !== undefined) attributes.push(`max="${escapeHtml(validation.maximum)}"`);
  if (validation.minLength !== undefined) attributes.push(`minlength="${escapeHtml(validation.minLength)}"`);
  if (validation.maxLength !== undefined) attributes.push(`maxlength="${escapeHtml(validation.maxLength)}"`);
  if (validation.pattern) attributes.push(`pattern="${escapeHtml(validation.pattern)}"`);
  if (policy?.type === 'integer') attributes.push('step="1"');
  if (policy?.type === 'number') attributes.push('step="any"');
  if (policy?.examples?.length) attributes.push(`placeholder="${escapeHtml(policy.examples[0])}"`);
  return attributes.join(' ');
};
const renderScalar = (key, value, path) => {
  const policy = findPolicy(path);
  if (!isPolicyVisible(policy)) return '';
  const locked = !policy?.editable;
  const control = policy?.ui?.control;
  const inputType = control === 'password' ? 'password' : policy?.type === 'boolean' ? 'checkbox' : ['integer', 'number'].includes(policy?.type) ? 'number' : 'text';
  const displayValue = Array.isArray(value) ? JSON.stringify(value) : String(value ?? '');
  const label = policy?.path?.endsWith('.*') ? key : (policy?.title || key);
  const enumValues = enumValuesFor(path, value);
  const suggestedValues = control === 'combobox' ? snapshotValuesFor(policy?.ui?.options_source) : null;
  const suggestedDetails = control === 'combobox' ? snapshotValuesFor(policy?.ui?.option_details_source) : null;
  const allowCustom = policy?.ui?.allow_custom !== false;
  const dataListId = `settings-options-${path.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
  const constraints = validationAttributes(policy);
  return `
    <label class="settings-field ${locked ? 'settings-field--locked' : ''}">
      <span class="settings-field__label">
        <span>${escapeHtml(label)}</span>
        <span class="settings-field__metadata">${metadataBadges(policy)}</span>
      </span>
      ${enumValues
        ? `<select data-config-path="${escapeHtml(path)}" ${Array.isArray(value) ? 'multiple' : ''} ${locked ? 'disabled' : ''}>${enumValues.map((option, index) => `<option value="${index}" ${(Array.isArray(value) ? value.includes(option) : option === value) ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}</select>`
        : suggestedValues
        ? allowCustom
          ? `<input data-config-path="${escapeHtml(path)}" type="text" list="${escapeHtml(dataListId)}" value="${escapeHtml(displayValue)}" ${constraints} ${locked ? 'disabled' : ''}>
            <datalist id="${escapeHtml(dataListId)}">${suggestedValues.map((option) => `<option value="${escapeHtml(option)}" label="${escapeHtml(suggestionLabelFor(option, suggestedDetails))}"></option>`).join('')}</datalist>`
          : `<select data-config-path="${escapeHtml(path)}" ${locked ? 'disabled' : ''}>${suggestedValues.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? 'selected' : ''}>${escapeHtml(option)}${suggestionLabelFor(option, suggestedDetails) ? ` — ${escapeHtml(suggestionLabelFor(option, suggestedDetails))}` : ''}</option>`).join('')}</select>`
        : Array.isArray(value)
        ? `<textarea data-config-path="${escapeHtml(path)}" ${locked ? 'disabled' : ''}>${escapeHtml(displayValue)}</textarea>`
        : `<input data-config-path="${escapeHtml(path)}" type="${inputType}" ${inputType === 'checkbox' && value ? 'checked' : ''} ${inputType !== 'checkbox' ? `value="${escapeHtml(displayValue)}"` : ''} ${constraints} ${locked ? 'disabled' : ''}>`}
      <small>${escapeHtml(policy?.description || 'Unsupported field')}</small>
    </label>`;
};
const renderObject = (value, path) => Object.entries(value).map(([key, child]) => {
  const childPath = path ? `${path}.${key}` : key;
  if (child && typeof child === 'object' && !Array.isArray(child)) {
    const content = renderObject(child, childPath);
    return content ? `<div class="settings-subgroup"><h5>${escapeHtml(key)}</h5>${content}</div>` : '';
  }
  return renderScalar(key, child, childPath);
}).join('');
const renderCan = (buses) => {
  const policy = findPolicy('can');
  if (!isPolicyVisible(policy)) return '';
  const locked = !policy?.editable;
  return `<section class="settings-section settings-section--can">
    <div class="settings-section__head">
      <div class="settings-section__title"><h4>CAN channels</h4><span class="settings-field__metadata">${metadataBadges(policy)}</span></div>
      <button id="settings-add-can" class="btn" type="button" ${locked ? 'disabled' : ''}>+ Add CAN</button>
    </div>
    <div class="settings-can-grid">${buses.map((bus, index) => `
      <article class="settings-can-card">
        <div class="settings-section__head"><h5>CAN ${index + 1}: ${escapeHtml(bus.channel)}</h5><button class="btn settings-remove-can" data-can-index="${index}" type="button" ${locked || buses.length === 1 ? 'disabled' : ''}>Remove</button></div>
        ${renderObject(bus, `can.${index}`)}
      </article>`).join('')}</div>
  </section>`;
};
const wireSettingsInputs = () => {
  document.querySelectorAll('[data-config-path]').forEach((input) => {
    input.addEventListener('change', () => {
      const path = input.dataset.configPath;
      const parts = pathSegments(path);
      let original = settingsWorking;
      parts.forEach((part) => { original = original[part]; });
      try {
        setAtPath(settingsWorking, path, parseInput(input, original, path));
        input.setCustomValidity('');
      } catch (error) {
        input.setCustomValidity(`Invalid value: ${error.message}`);
        input.reportValidity();
      }
    });
  });
  document.querySelectorAll('.settings-remove-can').forEach((button) => button.addEventListener('click', () => {
    settingsWorking.can.splice(Number(button.dataset.canIndex), 1);
    renderSettingsForm();
  }));
  document.getElementById('settings-add-can')?.addEventListener('click', () => {
    const next = settingsWorking.can.length;
    settingsWorking.can.push({ interface: 'virtual', channel: `vcan${next}`, bitrate: 500000, can_db_file: settingsWorking.can[0]?.can_db_file || 'db/can_db/Interface_Panther_To_CarPC_v8.dbc', channel_tracking_signals: [] });
    renderSettingsForm();
  });
};
const renderSettingsForm = () => {
  const form = document.getElementById('settings-form');
  if (!settingsWorking) return;
  form.innerHTML = renderCan(settingsWorking.can) + Object.entries(settingsWorking)
    .filter(([key]) => key !== 'can')
    .map(([key, value]) => {
      const content = value && typeof value === 'object' && !Array.isArray(value)
        ? renderObject(value, key)
        : renderScalar(key, value, key);
      return content ? `<section class="settings-section"><h4>${escapeHtml(key)}</h4>${content}</section>` : '';
    })
    .join('');
  wireSettingsInputs();
};
const renderBackups = async () => {
  const list = document.getElementById('settings-backup-list');
  const payload = await listSystemConfigBackups();
  list.innerHTML = payload.backups.length ? payload.backups.map((item) => `
    <div class="settings-backup-item">
      <span><strong>${escapeHtml(item.created_at)}</strong><small>${escapeHtml(item.id)}</small></span>
      <div class="settings-backup-actions">
        <button class="btn settings-restore" data-backup-id="${escapeHtml(item.id)}" type="button">Restore</button>
        <button class="btn btn--danger settings-delete-backup" data-backup-id="${escapeHtml(item.id)}" type="button">Delete</button>
      </div>
    </div>`).join('') : '<span class="settings-empty">No backups yet.</span>';
  document.querySelectorAll('.settings-restore').forEach((button) => button.addEventListener('click', async () => {
    if (!window.requestDangerConfirmation?.(`settings_restore:${button.dataset.backupId}`, `Restore backup ${button.dataset.backupId}?`)) return;
    const result = await runSettingsAction(() => restoreSystemConfigBackup(button.dataset.backupId), 'Backup restored.');
    if (result) await loadSettings();
  }));
  document.querySelectorAll('.settings-delete-backup').forEach((button) => button.addEventListener('click', async () => {
    if (!window.requestDangerConfirmation?.(`settings_delete_backup:${button.dataset.backupId}`, `Delete backup ${button.dataset.backupId}? This cannot be undone.`)) return;
    const result = await runSettingsAction(() => deleteSystemConfigBackup(button.dataset.backupId), 'Backup deleted.');
    if (result) await renderBackups();
  }));
};
const loadSettings = async () => {
  document.getElementById('settings-status').textContent = 'Loading…';
  try {
    settingsSnapshot = await getSystemConfig();
    settingsOriginal = cloneJson(settingsSnapshot.config);
    settingsWorking = cloneJson(settingsSnapshot.config);
    renderSettingsForm();
    await renderBackups();
    document.getElementById('settings-status').textContent = 'Ready';
  } catch (error) {
    document.getElementById('settings-status').textContent = `Failed to load: ${error.message || error}`;
  }
};
const diffPatch = (oldValue, newValue) => {
  if (JSON.stringify(oldValue) === JSON.stringify(newValue)) return undefined;
  if (Array.isArray(oldValue) || Array.isArray(newValue) || typeof oldValue !== 'object' || typeof newValue !== 'object' || oldValue === null || newValue === null) return newValue;
  const result = {};
  Object.keys(newValue).forEach((key) => {
    const child = diffPatch(oldValue[key], newValue[key]);
    if (child !== undefined) result[key] = child;
  });
  return Object.keys(result).length ? result : undefined;
};
const runSettingsAction = async (action, successText) => {
  try {
    const result = await action();
    const reboot = result?.reboot_required ? ' Reboot is required for some changes.' : '';
    document.getElementById('settings-status').textContent = `${successText}${reboot}`;
    window.showUiNotice?.(`${successText}${reboot}`, { source: 'settings', code: 'settings_action_ok' });
    return result;
  } catch (error) {
    showPermissionWarnings(normalizeWarnings(error.payload || error), 'settings');
    document.getElementById('settings-status').textContent = `Failed: ${error.message || error}`;
    window.showUiNotice?.(`Settings action failed: ${error.message || error}`, { level: 'error', source: 'settings', code: 'settings_action_failed' });
    return null;
  }
};

document.getElementById('btn-settings').addEventListener('click', async () => {
  settingsModal.style.display = 'block';
  await loadSettings();
});
document.getElementById('settings-cancel').addEventListener('click', () => settingsModal.style.display = 'none');
document.getElementById('settings-refresh').addEventListener('click', loadSettings);
document.getElementById('settings-mode').addEventListener('change', (event) => {
  settingsViewMode = event.target.checked ? 'expand' : 'base';
  renderSettingsForm();
});
document.getElementById('settings-backup').addEventListener('click', async () => {
  const result = await runSettingsAction(createSystemConfigBackup, 'Backup created.');
  if (result) await renderBackups();
});
document.getElementById('settings-reload').addEventListener('click', () => runSettingsAction(reloadSystemConfig, 'Live fields reloaded.'));
document.getElementById('settings-reboot').addEventListener('click', async () => {
  if (!window.requestDangerConfirmation?.('settings_reboot', 'Reboot Car-HMI now?')) return;
  await runSettingsAction(rebootCarHmi, 'Reboot requested.');
});
document.getElementById('settings-reset').addEventListener('click', async () => {
  if (!window.requestDangerConfirmation || !window.requestDangerConfirmation('settings_reset', 'Reset the configuration to defaults? This will overwrite config/system.json')) return;
  const result = await runSettingsAction(resetSystemConfig, 'System configuration reset from project template.');
  if (result) await loadSettings();
});
document.getElementById('settings-save').addEventListener('click', async () => {
  const invalid = document.querySelector('#settings-form :invalid');
  if (invalid) return void invalid.reportValidity();
  const patch = diffPatch(settingsOriginal, settingsWorking);
  if (!patch) return void (document.getElementById('settings-status').textContent = 'No changes.');
  const result = await runSettingsAction(() => patchSystemConfig(patch), 'System configuration updated.');
  if (result) await loadSettings();
});
