'use strict';

// ============================================================
// State
// ============================================================
const state = {
  step: 1,
  files: [],        // File[]
  settings: null,   // settings object from API
  result: null,     // last convert response
  uploadToken: null,       // /api/upload-and-detect で発行されたトークン
  duplicates: [],          // 検出された同名異コード [{name, codes:[{code,usageCount,...}]}]
  dedupChoices: {},        // {取引先名: {action, target_code}} ユーザー選択
};

// 相対 URL を使用: Python GUI バックエンドと同一オリジン前提のため
// 'http://localhost:8765' のようなハードコードを避ける
const BASE = '';

// ============================================================
// Toast helper
// ============================================================
let toastContainer = null;
function getToastContainer() {
  if (!toastContainer) {
    toastContainer = document.createElement('div');
    toastContainer.className = 'toast-container';
    document.body.appendChild(toastContainer);
  }
  return toastContainer;
}

function showToast(msg, type = '', duration = 3000) {
  const el = document.createElement('div');
  el.className = 'toast' + (type ? ' ' + type : '');
  el.textContent = msg;
  getToastContainer().appendChild(el);
  setTimeout(() => {
    el.style.animation = 'none';
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 300);
  }, duration);
}

// ============================================================
// API helpers
// ============================================================
async function apiGetSettings() {
  try {
    const res = await fetch(BASE + '/api/settings');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } catch (e) {
    console.warn('GET /api/settings failed:', e.message);
    return null;
  }
}

async function apiSaveSettings(obj) {
  const res = await fetch(BASE + '/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj),
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return await res.json();
}

async function apiConvert(formData) {
  const res = await fetch(BASE + '/api/convert', {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error('HTTP ' + res.status + (txt ? ': ' + txt : ''));
  }
  return await res.json();
}

async function apiHistory() {
  try {
    const res = await fetch(BASE + '/api/history');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } catch (e) {
    console.warn('GET /api/history failed:', e.message);
    return [];
  }
}

async function apiShutdown() {
  try {
    await fetch(BASE + '/api/shutdown', { method: 'POST' });
  } catch (_) {
    // サーバーが落ちると接続エラーになる — 正常
  }
}

async function apiUploadAndDetect(formData) {
  const res = await fetch(BASE + '/api/upload-and-detect', {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error('HTTP ' + res.status + (txt ? ': ' + txt : ''));
  }
  return await res.json();
}

async function apiConvertWithChoices(uploadToken, dedupChoices) {
  const res = await fetch(BASE + '/api/convert-with-choices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uploadToken, dedupChoices }),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error('HTTP ' + res.status + (txt ? ': ' + txt : ''));
  }
  return await res.json();
}

// ============================================================
// Step indicator
// ============================================================
function updateStepIndicator(step) {
  document.querySelectorAll('.step-item').forEach(el => {
    const n = parseInt(el.dataset.step, 10);
    el.classList.toggle('active', n === step);
    el.classList.toggle('done', n < step);
  });
}

// ============================================================
// File utilities
// ============================================================
function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
}

function addFiles(fileList) {
  Array.from(fileList).forEach(f => {
    if (!state.files.find(x => x.name === f.name && x.size === f.size)) {
      state.files.push(f);
    }
  });
  renderFileList();
}

function removeFile(index) {
  state.files.splice(index, 1);
  renderFileList();
}

function renderFileList() {
  const listEl = document.getElementById('file-list');
  const ul = document.getElementById('file-list-ul');
  const nextBtn = document.getElementById('btn-step1-next');

  if (state.files.length === 0) {
    listEl.style.display = 'none';
    nextBtn.disabled = true;
    return;
  }

  listEl.style.display = 'block';
  nextBtn.disabled = false;
  ul.innerHTML = '';
  state.files.forEach((f, i) => {
    const li = document.createElement('li');
    li.className = 'file-item';
    li.innerHTML = `
      <span class="file-item-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
      </span>
      <span class="file-item-name">${escHtml(f.name)}</span>
      <span class="file-item-size">${formatSize(f.size)}</span>
      <button type="button" class="btn-file-remove" title="削除" data-index="${i}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="18" y1="6" x2="6" y2="18"/>
          <line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>`;
    ul.appendChild(li);
  });

  ul.querySelectorAll('.btn-file-remove').forEach(btn => {
    btn.addEventListener('click', () => removeFile(parseInt(btn.dataset.index, 10)));
  });
}

// ============================================================
// Step 1 — File selection
// ============================================================
function initStep1() {
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  const browseBtn = document.getElementById('btn-browse');
  const nextBtn = document.getElementById('btn-step1-next');

  browseBtn.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
  dropzone.addEventListener('click', (e) => {
    if (e.target === browseBtn || browseBtn.contains(e.target)) return;
    fileInput.click();
  });

  fileInput.addEventListener('change', () => { addFiles(fileInput.files); fileInput.value = ''; });

  dropzone.addEventListener('dragenter', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragover',  (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', (e) => {
    if (!dropzone.contains(e.relatedTarget)) dropzone.classList.remove('dragover');
  });
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    addFiles(e.dataTransfer.files);
  });

  nextBtn.addEventListener('click', () => goToStep(2));
}

// ============================================================
// Step 2 — Options
// ============================================================
function initStep2() {
  const allPeriodCb  = document.getElementById('all-period');
  const dateFrom     = document.getElementById('date-from');
  const dateTo       = document.getElementById('date-to');
  const partnersCb   = document.getElementById('output-partners');
  const prefixGroup  = document.getElementById('partners-prefix-group');
  const dedupGroup   = document.getElementById('dedup-strategy-group');

  allPeriodCb.addEventListener('change', () => {
    const disabled = allPeriodCb.checked;
    dateFrom.disabled = disabled;
    dateTo.disabled   = disabled;
    if (disabled) { dateFrom.value = ''; dateTo.value = ''; }
  });

  partnersCb.addEventListener('change', () => {
    prefixGroup.style.display = partnersCb.checked ? 'flex' : 'none';
    dedupGroup.style.display  = partnersCb.checked ? 'block' : 'none';
  });

  document.getElementById('btn-step2-back').addEventListener('click', () => goToStep(1));
  document.getElementById('btn-save-settings').addEventListener('click', saveSettings);
  document.getElementById('btn-execute').addEventListener('click', executeConvert);
}

function applySettingsToForm(s) {
  if (!s) return;
  if (s.outputPrefix    !== undefined) document.getElementById('output-prefix').value   = s.outputPrefix;
  if (s.rowsPerFile     !== undefined) document.getElementById('rows-per-file').value   = s.rowsPerFile;
  if (s.outputPartners  !== undefined) {
    document.getElementById('output-partners').checked = !!s.outputPartners;
    document.getElementById('partners-prefix-group').style.display = s.outputPartners ? 'flex' : 'none';
    document.getElementById('dedup-strategy-group').style.display  = s.outputPartners ? 'block' : 'none';
  }
  if (s.partnersPrefix  !== undefined) document.getElementById('partners-prefix').value = s.partnersPrefix;
  if (s.encoding        !== undefined) document.getElementById('encoding').value         = s.encoding;
  if (s.dedupStrategy   !== undefined) document.getElementById('dedup-strategy').value   = s.dedupStrategy;
}

function collectFormValues() {
  return {
    outputPrefix:   document.getElementById('output-prefix').value.trim(),
    rowsPerFile:    parseInt(document.getElementById('rows-per-file').value, 10) || 10000,
    outputPartners: document.getElementById('output-partners').checked,
    partnersPrefix: document.getElementById('partners-prefix').value.trim(),
    encoding:       document.getElementById('encoding').value,
    dedupStrategy:  document.getElementById('dedup-strategy').value,
  };
}

async function saveSettings() {
  const vals = collectFormValues();
  try {
    await apiSaveSettings(vals);
    showToast('設定を保存しました', 'success');
  } catch (e) {
    showToast('設定の保存に失敗しました: ' + e.message, 'error');
  }
}

// ============================================================
// Step 3 — Execute & Result
// ============================================================
async function executeConvert() {
  const opts = collectFormValues();

  // custom 戦略: upload-and-detect → モーダル表示
  if (opts.outputPartners && opts.dedupStrategy === 'custom') {
    await executeConvertCustomFlow(opts);
    return;
  }

  // 通常フロー
  goToStep(3);
  showLoading(true);

  const formData = buildFormData(opts);

  try {
    const result = await apiConvert(formData);
    state.result = result;
    showLoading(false);
    if (result.success) {
      renderResultSuccess(result);
    } else {
      renderResultError(result.error || '変換に失敗しました', result.stderr || '');
    }
  } catch (e) {
    showLoading(false);
    renderResultError(e.message, '');
  }

  await renderHistory();
}

function buildFormData(opts) {
  const formData = new FormData();
  state.files.forEach(f => formData.append('inputFiles', f, f.name));
  formData.append('outputPrefix',   opts.outputPrefix);
  formData.append('rowsPerFile',    String(opts.rowsPerFile));
  formData.append('outputPartners', opts.outputPartners ? '1' : '0');
  formData.append('partnersPrefix', opts.partnersPrefix);
  formData.append('encoding',       opts.encoding);
  formData.append('dedupStrategy',  opts.dedupStrategy || 'warn-only');
  const dateFrom = document.getElementById('date-from').value;
  const dateTo   = document.getElementById('date-to').value;
  if (dateFrom) formData.append('dateFrom', dateFrom);
  if (dateTo)   formData.append('dateTo',   dateTo);
  return formData;
}

async function executeConvertCustomFlow(opts) {
  // Phase 1: アップロード & 同名異コード検出
  showLoading(true);
  goToStep(3);

  const formData = buildFormData(opts);

  let detectResult;
  try {
    detectResult = await apiUploadAndDetect(formData);
  } catch (e) {
    showLoading(false);
    renderResultError('ファイルアップロードに失敗しました: ' + e.message, '');
    return;
  }

  showLoading(false);

  if (!detectResult.success) {
    renderResultError(detectResult.error || 'ファイルの解析に失敗しました', '');
    return;
  }

  state.uploadToken = detectResult.uploadToken;
  state.duplicates  = detectResult.duplicates || [];
  state.dedupChoices = {};

  if (state.duplicates.length === 0) {
    // 同名異コードなし → そのまま変換実行
    showLoading(true);
    try {
      const result = await apiConvertWithChoices(state.uploadToken, {});
      state.result = result;
      showLoading(false);
      if (result.success) {
        renderResultSuccess(result);
      } else {
        renderResultError(result.error || '変換に失敗しました', result.stderr || '');
      }
    } catch (e) {
      showLoading(false);
      renderResultError(e.message, '');
    }
    await renderHistory();
    return;
  }

  // 同名異コードあり → モーダル表示 (Step 3 を非表示に戻してモーダルを出す)
  goToStep(2);
  openDedupModal(state.duplicates);
}

// ============================================================
// Dedup modal
// ============================================================
function openDedupModal(duplicates) {
  const modal = document.getElementById('dedup-modal');
  const desc  = document.getElementById('dedup-modal-desc');
  const container = document.getElementById('dedup-cards-container');

  desc.textContent = `同一名称に複数の取引先コードが見つかりました (${duplicates.length} 件)。各取引先の対処方法を選んでください。`;

  container.innerHTML = '';
  duplicates.forEach((dup, dupIdx) => {
    const card = buildDedupCard(dup, dupIdx);
    container.appendChild(card);
  });

  modal.style.display = 'flex';
}

function buildDedupCard(dup, dupIdx) {
  const card = document.createElement('div');
  card.className = 'dedup-card';
  card.dataset.name = dup.name;

  // ヘッダー
  const header = document.createElement('div');
  header.className = 'dedup-card-header';
  header.innerHTML = `<span class="dedup-card-name">${escHtml(dup.name)}</span>`;
  card.appendChild(header);

  // ボディ
  const body = document.createElement('div');
  body.className = 'dedup-card-body';

  // コード一覧テーブル
  const maxUsage = Math.max(...dup.codes.map(c => c.usageCount));
  let tableHtml = `
    <table class="dedup-codes-table">
      <thead><tr><th>コード</th><th>借方使用</th><th>貸方使用</th><th>合計</th></tr></thead>
      <tbody>`;
  dup.codes.forEach(c => {
    const isMost = c.usageCount === maxUsage;
    tableHtml += `<tr>
      <td class="${isMost ? 'usage-most' : ''}">${escHtml(c.code)}${isMost ? ' ★' : ''}</td>
      <td>${c.debitUsage}</td><td>${c.creditUsage}</td>
      <td class="${isMost ? 'usage-most' : ''}">${c.usageCount}</td>
    </tr>`;
  });
  tableHtml += '</tbody></table>';
  body.innerHTML = tableHtml;

  // ラジオボタン群
  const actionsLabel = document.createElement('div');
  actionsLabel.className = 'dedup-actions-label';
  actionsLabel.textContent = '対処方法:';
  body.appendChild(actionsLabel);

  const radioGroup = document.createElement('div');
  radioGroup.className = 'dedup-radio-group';

  // merge 選択肢 (各コードへの統合)
  const mostUsedCode = dup.codes.reduce((a, b) => b.usageCount > a.usageCount ? b : a).code;
  dup.codes.forEach(c => {
    const label = document.createElement('label');
    label.className = 'dedup-radio-label';
    const isDefault = c.code === mostUsedCode;
    label.innerHTML = `
      <input type="radio" name="dedup_${dupIdx}" value="merge_${escAttr(c.code)}" ${isDefault ? 'checked' : ''}>
      コード <strong>${escHtml(c.code)}</strong> に統合 (他コードの仕訳を ${escHtml(c.code)} に書き換え)`;
    radioGroup.appendChild(label);
  });

  // rename
  const renameLabel = document.createElement('label');
  renameLabel.className = 'dedup-radio-label';
  renameLabel.innerHTML = `
    <input type="radio" name="dedup_${dupIdx}" value="rename">
    別名化 (_2, _3 を付与して全コードをマスタに残す)`;
  radioGroup.appendChild(renameLabel);

  // skip
  const skipLabel = document.createElement('label');
  skipLabel.className = 'dedup-radio-label';
  skipLabel.innerHTML = `
    <input type="radio" name="dedup_${dupIdx}" value="skip">
    マスタから除外 (freee で手動対処)`;
  radioGroup.appendChild(skipLabel);

  // ラジオ選択変更時に selected クラスをトグル
  radioGroup.querySelectorAll('input[type="radio"]').forEach(radio => {
    radio.addEventListener('change', () => {
      radioGroup.querySelectorAll('.dedup-radio-label').forEach(lbl => lbl.classList.remove('selected'));
      radio.parentElement.classList.add('selected');
    });
  });
  // デフォルト選択を反映
  const checked = radioGroup.querySelector('input:checked');
  if (checked) checked.parentElement.classList.add('selected');

  body.appendChild(radioGroup);
  card.appendChild(body);
  return card;
}

function collectDedupChoices() {
  const choices = {};
  const container = document.getElementById('dedup-cards-container');
  container.querySelectorAll('.dedup-card').forEach(card => {
    const name = card.dataset.name;
    const checked = card.querySelector('input[type="radio"]:checked');
    if (!checked) return;
    const val = checked.value;
    if (val.startsWith('merge_')) {
      choices[name] = { action: 'merge', target_code: val.slice(6) };
    } else if (val === 'rename') {
      choices[name] = { action: 'rename' };
    } else if (val === 'skip') {
      choices[name] = { action: 'skip' };
    }
  });
  return choices;
}

function initDedupModal() {
  const modal    = document.getElementById('dedup-modal');
  const btnCancel  = document.getElementById('btn-dedup-cancel');
  const btnExecute = document.getElementById('btn-dedup-execute');
  const btnAutoAll = document.getElementById('btn-dedup-auto-all');

  // キャンセル
  btnCancel.addEventListener('click', () => {
    modal.style.display = 'none';
  });

  // 全自動推奨: 各カードの最頻コード merge をチェック
  btnAutoAll.addEventListener('click', () => {
    const container = document.getElementById('dedup-cards-container');
    container.querySelectorAll('.dedup-card').forEach(card => {
      // 最初の merge radio (デフォルトが最頻) をチェック
      const firstMerge = card.querySelector('input[value^="merge_"]');
      if (firstMerge) {
        firstMerge.checked = true;
        firstMerge.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    showToast('全て最頻使用コードに統合を設定しました', 'success');
  });

  // 決定して変換実行
  btnExecute.addEventListener('click', async () => {
    modal.style.display = 'none';
    state.dedupChoices = collectDedupChoices();

    goToStep(3);
    showLoading(true);

    try {
      const result = await apiConvertWithChoices(state.uploadToken, state.dedupChoices);
      state.result = result;
      showLoading(false);
      if (result.success) {
        renderResultSuccess(result);
      } else {
        renderResultError(result.error || '変換に失敗しました', result.stderr || '');
      }
    } catch (e) {
      showLoading(false);
      renderResultError(e.message, '');
    }
    await renderHistory();
  });
}

function showLoading(on) {
  document.getElementById('result-loading').style.display = on ? 'block' : 'none';
  document.getElementById('result-success').style.display = 'none';
  document.getElementById('result-error').style.display   = 'none';
}

function renderResultSuccess(r) {
  document.getElementById('result-success').style.display = 'block';
  const s = r.summary || {};

  // Summary cards
  const grid = document.getElementById('summary-grid');
  grid.innerHTML = '';
  const items = [
    { value: s.slipFiles || 0,           label: '仕訳CSVファイル数' },
    { value: (s.totalSlipRows || 0).toLocaleString(), label: '合計行数' },
  ];
  if (r.outputs && r.outputs.some(o => o.type === 'partner')) {
    items.push({ value: (s.totalPartnerCount || 0).toLocaleString(), label: '取引先マスタ件数' });
  }
  const auditTotal = (s.auditA || []).length + (s.auditB || []).length
                   + (s.balanceWarnings || []).length + (s.negativeWarnings || []).length;
  items.push({ value: auditTotal, label: '要確認件数', warn: auditTotal > 0 });

  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'summary-item';
    div.innerHTML = `
      <div class="summary-item-value" style="${item.warn && item.value > 0 ? 'color:var(--orange)' : ''}">
        ${item.value}
      </div>
      <div class="summary-item-label">${escHtml(item.label)}</div>`;
    grid.appendChild(div);
  });

  // Output files table
  const tbody = document.getElementById('output-table-body');
  tbody.innerHTML = '';
  (r.outputs || []).forEach(o => {
    const tr = document.createElement('tr');
    const badge = o.type === 'partner'
      ? '<span class="badge badge-green">取引先</span>'
      : '<span class="badge badge-blue">仕訳</span>';
    tr.innerHTML = `
      <td>${escHtml(o.filename)}</td>
      <td>${badge}</td>
      <td>${(o.rows || 0).toLocaleString()}</td>
      <td>${formatSize(o.size || 0)}</td>
      <td>
        <a href="${BASE}/api/download?path=${encodeURIComponent(o.path)}"
           class="btn btn-success btn-sm" download="${escAttr(o.filename)}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          DL
        </a>
      </td>`;
    tbody.appendChild(tr);
  });
  // btn-sm style inline
  tbody.querySelectorAll('.btn-sm').forEach(b => { b.style.padding = '5px 10px'; b.style.fontSize = '12px'; });

  // Audit section
  renderAuditSection(s);

  // Dedup audit section
  if (r.dedupAudit && r.dedupAudit.length > 0) {
    renderDedupAuditSection(r.dedupAudit);
  }

  // Update upload step numbers for partners
  const hasPartners = r.outputs && r.outputs.some(o => o.type === 'partner');
  document.getElementById('step-partners-upload').style.display = hasPartners ? 'flex' : 'none';
  document.getElementById('slip-step-number').textContent    = hasPartners ? '2' : '1';
  document.getElementById('cleanup-step-number').textContent = hasPartners ? '3' : '2';
}

function renderAuditSection(s) {
  const container = document.getElementById('audit-section');
  container.innerHTML = '';

  const sections = [
    { key: 'balanceWarnings', label: '[要確認 1/2] 借貸不一致',          cols: ['日付', '伝票No', '内容'] },
    { key: 'negativeWarnings', label: '[要確認 2/2] 課売上マイナス',      cols: ['日付', '科目', '金額'] },
    { key: 'auditA',           label: '[要確認 取引先マスタ A] 同コード異名', cols: ['コード', '名称1', '名称2'] },
    { key: 'auditB',           label: '[要確認 取引先マスタ B] 同名異コード', cols: ['名称', 'コード1', 'コード2'] },
  ];

  const nonEmpty = sections.filter(sec => (s[sec.key] || []).length > 0);
  if (nonEmpty.length === 0) return;

  const wrapper = document.createElement('div');
  wrapper.className = 'card audit-section';
  wrapper.innerHTML = '<h3 class="audit-title">監査ログ</h3>';

  nonEmpty.forEach(sec => {
    const rows = s[sec.key] || [];
    const item = document.createElement('div');
    item.className = 'audit-item';

    const header = document.createElement('div');
    header.className = 'audit-item-header';
    header.innerHTML = `
      <span class="audit-item-title">${escHtml(sec.label)}</span>
      <span class="audit-badge audit-badge-warn">${rows.length} 件</span>
      <svg style="width:16px;height:16px;color:var(--gray-400);flex-shrink:0;transition:transform 0.2s" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"/>
      </svg>`;
    item.appendChild(header);

    const body = document.createElement('div');
    body.className = 'audit-item-body';

    const table = document.createElement('table');
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr>' + sec.cols.map(c => `<th>${escHtml(c)}</th>`).join('') + '</tr>';
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    rows.slice(0, 200).forEach(row => {
      const tr = document.createElement('tr');
      if (Array.isArray(row)) {
        row.forEach(cell => {
          const td = document.createElement('td');
          td.textContent = cell != null ? String(cell) : '';
          tr.appendChild(td);
        });
      } else {
        // row is object — use values
        Object.values(row).forEach(cell => {
          const td = document.createElement('td');
          td.textContent = cell != null ? String(cell) : '';
          tr.appendChild(td);
        });
      }
      tbody.appendChild(tr);
    });
    if (rows.length > 200) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = sec.cols.length;
      td.textContent = `... 他 ${rows.length - 200} 件`;
      td.style.color = 'var(--gray-400)';
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    body.appendChild(table);
    item.appendChild(body);

    // toggle
    const chevron = header.querySelector('svg');
    header.addEventListener('click', () => {
      const open = body.classList.toggle('open');
      chevron.style.transform = open ? 'rotate(180deg)' : 'rotate(0deg)';
    });

    wrapper.appendChild(item);
  });

  container.appendChild(wrapper);
}

function renderDedupAuditSection(dedupAudit) {
  // audit-section の後に dedup-result-section を挿入
  const auditSection = document.getElementById('audit-section');
  let dedupSection = document.getElementById('dedup-result-section');
  if (dedupSection) dedupSection.remove();

  dedupSection = document.createElement('div');
  dedupSection.id = 'dedup-result-section';
  dedupSection.className = 'card dedup-result-section';

  const titleHtml = document.createElement('div');
  titleHtml.className = 'audit-item';
  const header = document.createElement('div');
  header.className = 'audit-item-header';
  header.innerHTML = `
    <span class="audit-item-title">同名異コード対処結果</span>
    <span class="audit-badge" style="background:var(--blue-lt);color:var(--blue)">${dedupAudit.length} 件</span>
    <svg style="width:16px;height:16px;color:var(--gray-400);flex-shrink:0;transition:transform 0.2s" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="6 9 12 15 18 9"/>
    </svg>`;

  const body = document.createElement('div');
  body.className = 'audit-item-body';
  const table = document.createElement('table');
  table.innerHTML = `
    <thead><tr><th>取引先名</th><th>元コード</th><th>対処内容</th><th>結果</th></tr></thead>`;
  const tbody = document.createElement('tbody');
  dedupAudit.forEach(entry => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escHtml(entry.name || '')}</td>
      <td class="dedup-result-codes">${escHtml((entry.oldCodes || []).join(' / '))}</td>
      <td>${escHtml(entry.action || '')}</td>
      <td>${escHtml(entry.result || '')}</td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  body.appendChild(table);

  const chevron = header.querySelector('svg');
  header.addEventListener('click', () => {
    const open = body.classList.toggle('open');
    chevron.style.transform = open ? 'rotate(180deg)' : 'rotate(0deg)';
  });

  titleHtml.appendChild(header);
  titleHtml.appendChild(body);
  dedupSection.appendChild(titleHtml);
  auditSection.parentNode.insertBefore(dedupSection, auditSection.nextSibling);
}

function renderResultError(msg, stderr) {
  document.getElementById('result-error').style.display = 'block';
  document.getElementById('error-message').textContent  = msg;
  document.getElementById('stderr-text').textContent    = stderr || '(なし)';
}

// ============================================================
// History
// ============================================================
async function renderHistory() {
  const listEl = document.getElementById('history-list');
  listEl.innerHTML = '<p class="history-empty">読み込み中...</p>';
  const items = await apiHistory();
  if (!items || items.length === 0) {
    listEl.innerHTML = '<p class="history-empty">実行ヒストリはありません。</p>';
    return;
  }
  listEl.innerHTML = '';
  const container = document.createElement('div');
  container.className = 'history-list';

  items.forEach((item, idx) => {
    const s = item.summary || {};
    const slipRows = (s.totalSlipRows || 0).toLocaleString();
    const fileCount = (item.outputs || []).length;
    const partnerCount = s.totalPartnerCount != null ? `/ 取引先 ${s.totalPartnerCount}件` : '';
    const ts = item.timestamp ? formatTimestamp(item.timestamp) : '(不明)';

    const card = document.createElement('div');
    card.className = 'history-card';

    const headerHtml = `
      <div class="history-card-header" data-idx="${idx}">
        <span class="history-ts">${escHtml(ts)}</span>
        <span class="history-summary-text">
          ${escHtml(item.summary_text || `仕訳 ${slipRows} 行 / ${fileCount} ファイル ${partnerCount}`)}
        </span>
        <svg class="history-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </div>`;

    const bodyHtml = document.createElement('div');
    bodyHtml.className = 'history-card-body';
    bodyHtml.innerHTML = `
      <div style="font-size:13px;color:var(--gray-600);margin-bottom:8px;">
        仕訳 <strong>${slipRows}</strong> 行 ${partnerCount}
        ${s.slipFiles ? `/ ${s.slipFiles} ファイル` : ''}
      </div>
      <div class="history-outputs"></div>`;

    const outputsContainer = bodyHtml.querySelector('.history-outputs');
    (item.outputs || []).forEach(o => {
      const div = document.createElement('div');
      div.className = 'history-output-item';
      const badge = o.type === 'partner'
        ? '<span class="badge badge-green" style="font-size:11px">取引先</span>'
        : '<span class="badge badge-blue" style="font-size:11px">仕訳</span>';
      div.innerHTML = `
        ${badge}
        <span>${escHtml(o.filename)}</span>
        <span style="color:var(--gray-400)">${(o.rows||0).toLocaleString()}行 / ${formatSize(o.size||0)}</span>
        <a href="${BASE}/api/download?path=${encodeURIComponent(o.path)}"
           class="btn btn-success" style="padding:4px 10px;font-size:12px" download="${escAttr(o.filename)}">DL</a>`;
      outputsContainer.appendChild(div);
    });

    card.innerHTML = headerHtml;
    card.appendChild(bodyHtml);

    const hdr = card.querySelector('.history-card-header');
    const chevron = hdr.querySelector('.history-chevron');
    hdr.addEventListener('click', () => {
      const open = bodyHtml.classList.toggle('open');
      chevron.classList.toggle('open', open);
    });

    container.appendChild(card);
  });

  listEl.appendChild(container);
}

function formatTimestamp(ts) {
  try {
    const d = new Date(ts.replace ? ts.replace(' ', 'T') : ts);
    if (isNaN(d.getTime())) return ts;
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch (_) { return ts; }
}

// ============================================================
// Collapsible
// ============================================================
function initCollapsibles() {
  document.querySelectorAll('[data-target]').forEach(header => {
    const target = document.getElementById(header.dataset.target);
    if (!target) return;
    const chevron = header.querySelector('.chevron');
    const defaultOpen = header.dataset.defaultOpen === 'true';

    if (!defaultOpen) { target.classList.add('hidden'); }

    header.addEventListener('click', () => {
      const hidden = target.classList.toggle('hidden');
      if (chevron) {
        chevron.classList.toggle('open', !hidden);
      }
    });
  });
}

// ============================================================
// Step navigation
// ============================================================
function goToStep(n) {
  state.step = n;
  updateStepIndicator(n);

  for (let i = 1; i <= 3; i++) {
    const el = document.getElementById('step-' + i);
    if (el) el.style.display = i === n ? 'block' : 'none';
  }

  if (n === 3) {
    document.getElementById('result-loading').style.display = 'none';
    document.getElementById('result-success').style.display = 'none';
    document.getElementById('result-error').style.display   = 'none';
  }
}

// ============================================================
// Shutdown modal
// ============================================================
function initShutdownModal() {
  const modal   = document.getElementById('shutdown-modal');
  const btnOpen = document.getElementById('btn-shutdown');
  const btnCancel  = document.getElementById('btn-shutdown-cancel');
  const btnConfirm = document.getElementById('btn-shutdown-confirm');

  btnOpen.addEventListener('click', () => { modal.style.display = 'flex'; });
  btnCancel.addEventListener('click', () => { modal.style.display = 'none'; });
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.style.display = 'none'; });

  btnConfirm.addEventListener('click', async () => {
    btnConfirm.disabled = true;
    btnConfirm.textContent = '停止中...';
    await apiShutdown();
    modal.style.display = 'none';
    showToast('サーバーを停止しました。ブラウザを閉じてください。', 'success', 8000);
  });
}

// ============================================================
// Restart
// ============================================================
function initRestart() {
  document.getElementById('btn-restart').addEventListener('click', () => {
    state.result = null;
    goToStep(1);
  });
}

// ============================================================
// Escape helpers
// ============================================================
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function escAttr(s) { return escHtml(s); }

// ============================================================
// Init
// ============================================================
window.addEventListener('DOMContentLoaded', async () => {
  // Setup UI components
  initStep1();
  initStep2();
  initCollapsibles();
  initShutdownModal();
  initDedupModal();
  initRestart();

  // Load settings from server
  const settings = await apiGetSettings();
  state.settings = settings;
  if (settings) {
    applySettingsToForm(settings);
  }

  // Load history
  await renderHistory();

  // Step 3 restart button (already visible in DOM, just re-bind handled by initRestart)

  console.log('[obc_to_freee] GUI initialized.');
});
