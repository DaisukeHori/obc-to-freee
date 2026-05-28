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
  duplicates: [],          // 検出された同名異コード
  dedupChoices: {},        // {取引先名: {action, target_code}}
  nonTaxableMismatches: [],// 検出された非課税+税額矛盾 [{slip_no, date, side, tax_label, amount, tax_amount, summary, kamoku}]
  nonTaxableChoices: {},   // {"<伝票No>_<側>": {action: "change-to-taxable"|"zero-tax"|"keep"}}
  kauuriMismatches: [],    // 検出された課売上+マイナス起票 [{slip_no, date, side, orig_kamoku, amount, tax_rate, summary}]
  kauuriChoices: {},       // {"<slip_no>_<date>_<側>": {action: "auto-rebate"|"keep", kamoku?: string}}
  slipDetails: {},         // 元伝票詳細 {"伝票No": [{drKamoku, drAmount, ...}]}
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

async function apiConvertWithChoices(uploadToken, dedupChoices, nonTaxableChoices, kauuriChoices) {
  const res = await fetch(BASE + '/api/convert-with-choices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      uploadToken,
      dedupChoices: dedupChoices || {},
      nonTaxableChoices: nonTaxableChoices || {},
      kauuriChoices: kauuriChoices || {},
    }),
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
  const partnersBox  = document.getElementById('partners-options-box');

  allPeriodCb.addEventListener('change', () => {
    const disabled = allPeriodCb.checked;
    dateFrom.disabled = disabled;
    dateTo.disabled   = disabled;
    if (disabled) { dateFrom.value = ''; dateTo.value = ''; }
  });

  partnersCb.addEventListener('change', () => {
    partnersBox.style.display = partnersCb.checked ? 'block' : 'none';
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
    document.getElementById('partners-options-box').style.display = s.outputPartners ? 'block' : 'none';
  }
  if (s.partnersPrefix  !== undefined) document.getElementById('partners-prefix').value = s.partnersPrefix;
  if (s.encoding        !== undefined) document.getElementById('encoding').value         = s.encoding;
  if (s.dedupStrategy   !== undefined) document.getElementById('dedup-strategy').value   = s.dedupStrategy;
  if (s.nonTaxableStrategy !== undefined) {
    const el = document.getElementById('non-taxable-strategy');
    if (el) el.value = s.nonTaxableStrategy;
  }
  if (s.kauuriRebateStrategy !== undefined) {
    const el = document.getElementById('kauuri-rebate-strategy');
    if (el) { el.value = s.kauuriRebateStrategy; toggleGuiKauuriRebateOptions(); }
  }
  if (s.kauuriRebateKamoku !== undefined) {
    const el = document.getElementById('kauuri-rebate-kamoku');
    if (el) { el.value = s.kauuriRebateKamoku; toggleGuiKauuriCustomKamoku(); }
  }
}

function collectFormValues() {
  const ntEl    = document.getElementById('non-taxable-strategy');
  const krEl    = document.getElementById('kauuri-rebate-strategy');
  const krKEl   = document.getElementById('kauuri-rebate-kamoku');
  const krCEl   = document.getElementById('kauuri-custom-kamoku');
  let kauuriRebateKamoku = krKEl ? krKEl.value : '売上値引高';
  if (kauuriRebateKamoku === '__custom__' && krCEl) {
    kauuriRebateKamoku = krCEl.value.trim() || '売上値引高';
  }
  return {
    outputPrefix:   document.getElementById('output-prefix').value.trim(),
    rowsPerFile:    parseInt(document.getElementById('rows-per-file').value, 10) || 10000,
    outputPartners: document.getElementById('output-partners').checked,
    partnersPrefix: document.getElementById('partners-prefix').value.trim(),
    encoding:       document.getElementById('encoding').value,
    dedupStrategy:  document.getElementById('dedup-strategy').value,
    nonTaxableStrategy: ntEl ? ntEl.value : 'change-to-taxable',
    kauuriRebateStrategy: krEl ? krEl.value : 'warn-only',
    kauuriRebateKamoku,
  };
}

function toggleGuiKauuriRebateOptions() {
  const strat = (document.getElementById('kauuri-rebate-strategy') || {value: 'warn-only'}).value;
  const box = document.getElementById('kauuri-rebate-kamoku-box-gui');
  if (box) box.style.display = (strat === 'auto-rebate' || strat === 'custom') ? 'block' : 'none';
}

function toggleGuiKauuriCustomKamoku() {
  const sel = document.getElementById('kauuri-rebate-kamoku');
  const box = document.getElementById('kauuri-custom-kamoku-box-gui');
  if (sel && box) box.style.display = (sel.value === '__custom__') ? 'block' : 'none';
}

// ============================================================
// 元伝票詳細モーダル
// ============================================================
function showGuiSlipDetail(slipNo) {
  const details = state.slipDetails || {};
  const rows = details[slipNo];
  const modal = document.getElementById('slip-detail-modal');
  const title = document.getElementById('slip-detail-modal-title');
  const body  = document.getElementById('slip-detail-modal-body');

  if (!modal) return;

  title.textContent = `奉行原本 伝票詳細 — No.${slipNo}`;

  if (!rows || rows.length === 0) {
    body.innerHTML = '<p style="color:#6b7280;">データが見つかりません。</p>';
    modal.style.display = 'flex';
    return;
  }

  // drTaxLabelRaw = 奉行原本の税区分略称 (変換前), drTaxCode = freee変換後税区分
  const headings = ['側', '勘定科目', '補助科目', '部門', '奉行原本税区分', 'freee税区分', '金額', '税額', '取引先', '摘要'];
  let tbodyHtml = '';
  for (const r of rows) {
    const drActive = r.drKamoku || r.drAmount;
    if (drActive) {
      tbodyHtml += `<tr>
        <td style="color:#1d4ed8;font-weight:600">借方</td>
        <td>${escHtml(r.drKamoku||'')}</td>
        <td>${escHtml(r.drHojo||'')}</td>
        <td>${escHtml(r.drBumon||'')}</td>
        <td>${escHtml(r.drTaxLabelRaw||r.drTaxCode||'')}</td>
        <td style="color:#888;font-size:11px">${escHtml(r.drTaxCode||'')}</td>
        <td style="text-align:right">${escHtml(String(r.drAmount||''))}</td>
        <td style="text-align:right">${escHtml(String(r.drTaxAmount||''))}</td>
        <td>${escHtml(r.drPartner||'')}</td>
        <td>${escHtml((r.summary||'').slice(0,40))}</td>
      </tr>`;
    }
    const crActive = r.crKamoku || r.crAmount;
    if (crActive) {
      tbodyHtml += `<tr>
        <td style="color:#15803d;font-weight:600">貸方</td>
        <td>${escHtml(r.crKamoku||'')}</td>
        <td>${escHtml(r.crHojo||'')}</td>
        <td>${escHtml(r.crBumon||'')}</td>
        <td>${escHtml(r.crTaxLabelRaw||r.crTaxCode||'')}</td>
        <td style="color:#888;font-size:11px">${escHtml(r.crTaxCode||'')}</td>
        <td style="text-align:right">${escHtml(String(r.crAmount||''))}</td>
        <td style="text-align:right">${escHtml(String(r.crTaxAmount||''))}</td>
        <td>${escHtml(r.crPartner||'')}</td>
        <td>${escHtml((r.summary||'').slice(0,40))}</td>
      </tr>`;
    }
  }

  body.innerHTML = `
    <table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr>${headings.map(h=>`<th style="background:#f3f4f6;padding:5px 8px;text-align:left;font-weight:600;white-space:nowrap;">${escHtml(h)}</th>`).join('')}</tr></thead>
      <tbody>${tbodyHtml}</tbody>
    </table>`;
  body.querySelectorAll('tbody tr').forEach(tr => {
    tr.querySelectorAll('td').forEach(td => {
      td.style.cssText = td.style.cssText + ';padding:5px 8px;border-bottom:1px solid #f3f4f6;white-space:nowrap;';
    });
  });

  modal.style.display = 'flex';
}

function guiSlipDetailClose() {
  const modal = document.getElementById('slip-detail-modal');
  if (modal) modal.style.display = 'none';
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

  // custom 戦略: dedup / 非課税 / 課売返のいずれかが custom なら upload-and-detect → モーダル表示
  const dedupCustom = opts.outputPartners && opts.dedupStrategy === 'custom';
  const nonTaxableCustom = opts.nonTaxableStrategy === 'custom';
  const kauuriCustom = opts.kauuriRebateStrategy === 'custom';
  if (dedupCustom || nonTaxableCustom || kauuriCustom) {
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
  formData.append('nonTaxableStrategy', opts.nonTaxableStrategy || 'change-to-taxable');
  formData.append('kauuriRebateStrategy', opts.kauuriRebateStrategy || 'warn-only');
  formData.append('kauuriRebateKamoku',   opts.kauuriRebateKamoku   || '売上値引高');
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
  state.nonTaxableMismatches = detectResult.nonTaxableMismatches || [];
  state.nonTaxableChoices = {};
  state.kauuriMismatches = detectResult.kauuriMismatches || [];
  state.kauuriChoices = {};

  // モーダル順次フロー: dedup (取引先) → non-taxable (非課税) → kauuri (課売返) → 実行
  const needDedupModal = opts.outputPartners && opts.dedupStrategy === 'custom' && state.duplicates.length > 0;
  const needNonTaxableModal = opts.nonTaxableStrategy === 'custom' && state.nonTaxableMismatches.length > 0;
  const needKauuriModal = opts.kauuriRebateStrategy === 'custom' && state.kauuriMismatches.length > 0;

  // 最後のステップ実行関数
  async function proceedToKauuriOrExecute() {
    if (needKauuriModal) {
      goToStep(2);
      openKauuriModal(state.kauuriMismatches, opts.kauuriRebateKamoku);
    } else {
      await runConvertWithChoices();
    }
  }

  if (needDedupModal) {
    goToStep(2);
    // dedup モーダル決定後に非課税モーダル or kauuri or 実行へ進む
    openDedupModal(state.duplicates, {
      onAccept: async () => {
        if (needNonTaxableModal) {
          openNonTaxableModal(state.nonTaxableMismatches, { onAccept: proceedToKauuriOrExecute });
        } else {
          await proceedToKauuriOrExecute();
        }
      }
    });
    return;
  }
  if (needNonTaxableModal) {
    goToStep(2);
    openNonTaxableModal(state.nonTaxableMismatches, { onAccept: proceedToKauuriOrExecute });
    return;
  }
  if (needKauuriModal) {
    goToStep(2);
    openKauuriModal(state.kauuriMismatches, opts.kauuriRebateKamoku);
    return;
  }
  // いずれも検出なし → そのまま実行
  await runConvertWithChoices();
}

async function runConvertWithChoices() {
  showLoading(true);
  goToStep(3);
  try {
    const result = await apiConvertWithChoices(
      state.uploadToken,
      state.dedupChoices,
      state.nonTaxableChoices,
      state.kauuriChoices || {}
    );
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

// ============================================================
// Dedup modal
// ============================================================
function openDedupModal(duplicates, options) {
  const modal = document.getElementById('dedup-modal');
  const desc  = document.getElementById('dedup-modal-desc');
  const container = document.getElementById('dedup-cards-container');

  desc.textContent = `同一名称に複数の取引先コードが見つかりました (${duplicates.length} 件)。各取引先の対処方法を選んでください。`;

  container.innerHTML = '';
  duplicates.forEach((dup, dupIdx) => {
    const card = buildDedupCard(dup, dupIdx);
    container.appendChild(card);
  });

  // 確定時のコールバック (次のモーダルへ進むか実行するか)
  state._dedupOnAccept = options && options.onAccept ? options.onAccept : null;

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

  // キャンセル: モーダルを閉じて Step2 に戻す (非課税モーダルキャンセルと同じ挙動)
  btnCancel.addEventListener('click', () => {
    modal.style.display = 'none';
    goToStep(2);
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

  // 決定して次へ
  btnExecute.addEventListener('click', async () => {
    btnExecute.disabled = true;
    try {
      modal.style.display = 'none';
      state.dedupChoices = collectDedupChoices();

      // onAccept コールバックがあれば呼ぶ (次の非課税モーダル or 実行)
      if (state._dedupOnAccept) {
        const cb = state._dedupOnAccept;
        state._dedupOnAccept = null;
        await cb();
        return;
      }

      // フォールバック: 直接実行
      await runConvertWithChoices();
    } finally {
      btnExecute.disabled = false;
    }
  });
}

// ============================================================
// Non-taxable mismatch modal
// ============================================================
function openNonTaxableModal(mismatches, options) {
  const modal = document.getElementById('nontax-modal');
  const desc = document.getElementById('nontax-modal-desc');
  const container = document.getElementById('nontax-cards-container');

  desc.textContent = `税区分「非売上」(非課税売上) なのに税額が入っている行が ${mismatches.length} 件見つかりました。freee は「非課売上では税額を入力できません」エラーを返すため、各行の対処を選んでください。`;

  container.innerHTML = '';
  mismatches.forEach((m, i) => {
    const card = buildNonTaxableCard(m, i);
    container.appendChild(card);
  });

  // onAccept コールバックを state に保存
  state._nontaxOnAccept = options && options.onAccept ? options.onAccept : null;

  modal.style.display = 'flex';
}

// 税区分マスタ (CLI 側 TAX_CODES_PURCHASE / TAX_CODES_SALES と同期)
const TAX_CODES_PURCHASE = [
  '課対仕入10%', '課対仕入8%（軽）',
  '共対仕入10%', '共対仕入8%（軽）',
  '課対仕入（控80）10%', '課対仕入（控80）8%（軽）',
  '共対仕入（控80）10%', '共対仕入（控80）8%（軽）',
  '対象外',
];
const TAX_CODES_SALES = [
  '課税売上10%', '課税売上8%（軽）',
  '課税売返10%', '課税売返8%（軽）',
  '対象外',
];

function buildNonTaxableCard(m, idx) {
  const card = document.createElement('div');
  card.className = 'dedup-card';
  card.dataset.key = `${m.slip_no}_${m.side}`;  // dataset 経由なので XSS リスクなし

  // 非仕入は今回の修正で非対仕入10%に自動変換されるためここには来ない → 常に TAX_CODES_SALES
  const candidates = TAX_CODES_SALES;
  const summaryShort = (m.summary || '').slice(0, 30);
  const kamoku = m.kamoku || '';

  const radioName = `nontax_${idx}`;
  const selectId = `nontax_select_${idx}`;
  // option の value/text は TAX_CODES_* 内部マスタ由来で安全。escape 不要。
  const optionsHtml = candidates.map((c, i) =>
    `<option value="${c}"${i === 0 ? ' selected' : ''}>${escHtml(c)}${i === 0 ? ' (推奨)' : ''}</option>`
  ).join('');

  // 奉行原本由来データ (slip_no, date, side, tax_label, kamoku, summary) は escHtml で防御
  card.innerHTML = `
    <div class="dedup-card-header">
      <strong>伝票 No.${escHtml(m.slip_no)}</strong> <span style="color:#666">(${escHtml(m.date)}) ${escHtml(m.side)}</span>
    </div>
    <div class="dedup-card-codes" style="margin:8px 0; font-size:14px; color:#555">
      奉行原本: 税区分 <strong>${escHtml(m.tax_label)}</strong> / 本体 ${m.amount.toLocaleString()} / 税額 ${m.tax_amount.toLocaleString()}<br>
      勘定科目: ${escHtml(kamoku)} / 摘要: ${escHtml(summaryShort)}
    </div>
    <div class="dedup-card-options" style="display:flex; flex-direction:column; gap:6px; margin-top:8px">
      <label style="display:flex; align-items:center; gap:6px; flex-wrap:wrap">
        <input type="radio" name="${radioName}" value="change-to-taxable" checked>
        <span>税区分を変更:</span>
        <select id="${selectId}" class="form-control" style="display:inline-block; width:auto; max-width:260px">${optionsHtml}</select>
        <small style="color:#666">(推奨、税控除あり)</small>
      </label>
      <label><input type="radio" name="${radioName}" value="zero-tax"> 税額を 0 に修正 (税区分維持、税控除なし)</label>
      <label><input type="radio" name="${radioName}" value="keep"> そのまま (freee エラー継続)</label>
    </div>
  `;
  return card;
}

function collectNonTaxableChoices() {
  const result = {};
  const cards = document.querySelectorAll('#nontax-cards-container .dedup-card');
  cards.forEach(card => {
    const key = card.dataset.key;
    const checked = card.querySelector('input[type=radio]:checked');
    if (key && checked) {
      const entry = { action: checked.value };
      if (checked.value === 'change-to-taxable') {
        // select から target_code を取得
        const select = card.querySelector('select');
        if (select && select.value) entry.target_code = select.value;
      }
      result[key] = entry;
    }
  });
  return result;
}

function initNonTaxableModal() {
  const modal = document.getElementById('nontax-modal');
  const btnCancel = document.getElementById('btn-nontax-cancel');
  const btnExecute = document.getElementById('btn-nontax-execute');
  const btnAutoAll = document.getElementById('btn-nontax-auto-all');

  if (!modal) return;

  btnCancel.addEventListener('click', () => {
    modal.style.display = 'none';
    goToStep(2);
  });

  btnAutoAll.addEventListener('click', () => {
    const cards = document.querySelectorAll('#nontax-cards-container .dedup-card');
    cards.forEach(card => {
      const radio = card.querySelector('input[value="change-to-taxable"]');
      if (radio) radio.checked = true;
    });
    showToast('全て「課対仕入10%/課税売上10% に変更」を設定しました', 'success');
  });

  btnExecute.addEventListener('click', async () => {
    btnExecute.disabled = true;
    try {
      modal.style.display = 'none';
      state.nonTaxableChoices = collectNonTaxableChoices();

      // onAccept コールバックがあれば呼ぶ (次の kauuri モーダル or 実行)
      if (state._nontaxOnAccept) {
        const cb = state._nontaxOnAccept;
        state._nontaxOnAccept = null;
        await cb();
        return;
      }

      // フォールバック: 直接実行
      await runConvertWithChoices();
    } finally {
      btnExecute.disabled = false;
    }
  });
}

// ============================================================
// Kauuri rebate custom modal
// ============================================================

const GUI_KAMOKU_OPTIONS = [
  { label: '売上値引高', value: '売上値引高' },
  { label: '売上戻り高', value: '売上戻り高' },
  { label: '奉行原本科目を維持', value: '__original__' },
  { label: 'カスタム入力', value: '__custom__' },
];

function openKauuriModal(mismatches, defaultKamoku) {
  const modal = document.getElementById('kauuri-modal');
  const desc = document.getElementById('kauuri-modal-desc');
  const container = document.getElementById('kauuri-cards-container');

  desc.textContent = `課売上+マイナス起票が ${mismatches.length} 件見つかりました。各件の振替方法を選んでください。`;

  container.innerHTML = '';
  mismatches.forEach((m, i) => {
    const card = buildKauuriCard(m, i, defaultKamoku || '売上値引高');
    container.appendChild(card);
  });

  modal.style.display = 'flex';
}

function buildKauuriCard(m, idx, defaultKamoku) {
  const card = document.createElement('div');
  card.className = 'dedup-card';
  card.dataset.key = `${m.slip_no}_${m.date}_${m.side}`;

  const radioName = `kauuri_${idx}`;
  const selectId  = `kauuri_select_${idx}`;
  const customBoxId = `kauuri_custom_box_${idx}`;
  const customInputId = `kauuri_custom_input_${idx}`;
  const summaryShort = (m.summary || '').slice(0, 30);
  const taxSuffix = (m.tax_rate === '8') ? '8%（軽）' : '10%';

  const optionsHtml = GUI_KAMOKU_OPTIONS.map(opt => {
    const isDefault = opt.value === defaultKamoku || (opt.value === '売上値引高' && defaultKamoku !== '__original__' && defaultKamoku !== '売上戻り高' && defaultKamoku !== '__custom__');
    return `<option value="${opt.value}"${isDefault ? ' selected' : ''}>${escHtml(opt.label)}</option>`;
  }).join('');

  card.innerHTML = `
    <div class="dedup-card-header">
      <strong>伝票 No.${escHtml(m.slip_no)}</strong> <span style="color:#666">(${escHtml(m.date)}) ${escHtml(m.side)}</span>
    </div>
    <div class="dedup-card-codes" style="margin:8px 0; font-size:14px; color:#555">
      勘定科目: <strong>${escHtml(m.orig_kamoku||'')}</strong> / 金額: ${(m.amount||0).toLocaleString()} / 摘要: ${escHtml(summaryShort)}
    </div>
    <div class="dedup-card-options" style="display:flex; flex-direction:column; gap:6px; margin-top:8px">
      <label style="display:flex; align-items:center; gap:6px; flex-wrap:wrap">
        <input type="radio" name="${radioName}" value="auto-rebate" checked>
        <span>自動振替 (借方移動+符号反転+課税売返${taxSuffix}):</span>
        <select id="${selectId}" class="form-control" style="display:inline-block; width:auto; max-width:220px"
          onchange="guiKauuriKamokuChange('${selectId}','${customBoxId}')">${optionsHtml}</select>
      </label>
      <div id="${customBoxId}" style="display:none; margin-left:20px; margin-top:4px;">
        <input type="text" id="${customInputId}" class="form-control"
          placeholder="例: 売上控除" style="font-family:monospace; max-width:240px;">
      </div>
      <label><input type="radio" name="${radioName}" value="keep"> 変更なし (現状維持)</label>
    </div>
  `;
  return card;
}

function guiKauuriKamokuChange(selectId, boxId) {
  const sel = document.getElementById(selectId);
  const box = document.getElementById(boxId);
  if (sel && box) box.style.display = (sel.value === '__custom__') ? 'block' : 'none';
}

function collectKauuriChoices() {
  const result = {};
  const cards = document.querySelectorAll('#kauuri-cards-container .dedup-card');
  cards.forEach((card, cardIdx) => {
    const key = card.dataset.key;
    const checked = card.querySelector('input[type=radio]:checked');
    if (!key || !checked) return;
    const action = checked.value;
    const entry = { action };
    if (action === 'auto-rebate') {
      const sel = card.querySelector('select');
      if (sel) {
        if (sel.value === '__custom__') {
          // カスタム入力欄から取得 (同じカード内の input[type=text])
          const customInput = card.querySelector('input[type=text]');
          entry.kamoku = (customInput && customInput.value.trim()) ? customInput.value.trim() : '売上値引高';
        } else {
          entry.kamoku = sel.value;
        }
      }
    }
    result[key] = entry;
  });
  return result;
}

function initKauuriModal() {
  const modal = document.getElementById('kauuri-modal');
  const btnCancel  = document.getElementById('btn-kauuri-cancel');
  const btnExecute = document.getElementById('btn-kauuri-execute');
  const btnAutoAll = document.getElementById('btn-kauuri-auto-all');

  if (!modal) return;

  btnCancel.addEventListener('click', () => {
    modal.style.display = 'none';
    goToStep(2);
  });

  btnAutoAll.addEventListener('click', () => {
    const cards = document.querySelectorAll('#kauuri-cards-container .dedup-card');
    cards.forEach(card => {
      const radio = card.querySelector('input[value="auto-rebate"]');
      if (radio) radio.checked = true;
    });
    showToast('全件「自動振替」を設定しました', 'success');
  });

  btnExecute.addEventListener('click', async () => {
    btnExecute.disabled = true;
    try {
      modal.style.display = 'none';
      state.kauuriChoices = collectKauuriChoices();
      await runConvertWithChoices();
    } finally {
      btnExecute.disabled = false;
    }
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
  // businessReview は「参考情報 (対処任意)」のため要確認件数に含めない
  const auditTotal = (s.auditA || []).length + (s.auditB || []).length
                   + (s.balanceWarnings || []).length + (s.negativeWarnings || []).length
                   + (s.nonTaxableSalesWarnings || []).length;
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

  // Store slip details for modal access
  state.slipDetails = r.slipDetails || {};

  // Kauuri rebate results section
  if (r.kauuriRebateResults && r.kauuriRebateResults.length > 0) {
    renderKauuriRebateResultsSection(r.kauuriRebateResults, r.outputs);
  }

  // Non-taxable results section
  if (r.nonTaxableResults && r.nonTaxableResults.length > 0) {
    renderNonTaxableResultsSection(r.nonTaxableResults, r.outputs);
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
    { key: 'balanceWarnings',         label: '[要確認] 借貸不一致',                                              cols: ['日付', '伝票No', '内容'] },
    { key: 'negativeWarnings',        label: '[要確認] 課売上マイナス',                                          cols: ['日付', '科目', '金額'] },
    { key: 'nonTaxableSalesWarnings', label: '[要確認] 非課売上+税額あり (freeeエラー対象)',                      cols: ['伝票No', '日付', '内容'] },
    { key: 'businessReview',          label: '[参考情報 ※件数外] 非仕入+税額あり (freeeエラーなし・対処任意)',    cols: ['伝票No', '日付', '内容'], isRef: true },
    { key: 'auditA',                  label: '[要確認 取引先マスタ A] 同コード異名',                              cols: ['コード', '名称1', '名称2'] },
    { key: 'auditB',                  label: '[要確認 取引先マスタ B] 同名異コード',                              cols: ['名称', 'コード1', 'コード2'] },
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
    const badgeClass = sec.isRef ? 'audit-badge audit-badge-info' : 'audit-badge audit-badge-warn';
    const countLabel = sec.isRef ? `${rows.length} 件 (件数に算入しません)` : `${rows.length} 件`;
    header.innerHTML = `
      <span class="audit-item-title">${escHtml(sec.label)}</span>
      <span class="${badgeClass}">${countLabel}</span>
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
    // 伝票No クリックでモーダルを開けるセクション
    const hasSlipDetailClick = (sec.key === 'balanceWarnings' || sec.key === 'negativeWarnings');
    rows.slice(0, 200).forEach(row => {
      const tr = document.createElement('tr');
      if (Array.isArray(row)) {
        row.forEach((cell, ci) => {
          const td = document.createElement('td');
          td.textContent = cell != null ? String(cell) : '';
          tr.appendChild(td);
        });
      } else {
        // row is object — first value may be slip No (文字列エントリ)
        const cellVal = typeof row === 'string' ? row : (Object.values(row)[0] ?? '');
        const td = document.createElement('td');
        if (hasSlipDetailClick && typeof cellVal === 'string' && cellVal.startsWith('No. ')) {
          // "No. {slip_no} ..." → extract slip_no
          const parts = cellVal.split(' ');  // ['No.', '{slip_no}', ...]
          const slipNo = parts[1] || '';
          if (slipNo && state.slipDetails && state.slipDetails[slipNo]) {
            const btn = document.createElement('button');
            btn.className = 'slip-detail-btn-gui';
            btn.style.cssText = 'background:none;border:none;color:#2563eb;cursor:pointer;font-size:13px;font-weight:500;padding:0;text-decoration:underline;text-underline-offset:2px;';
            btn.textContent = slipNo;
            btn.title = '元伝票詳細を表示';
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              showGuiSlipDetail(slipNo);
            });
            // Render: [clickable slipNo button] + rest of text
            const rest = document.createTextNode(' ' + parts.slice(2).join(' '));
            td.appendChild(btn);
            td.appendChild(rest);
          } else {
            td.textContent = cellVal;
          }
          tr.appendChild(td);
        } else if (typeof row === 'string') {
          td.textContent = row;
          tr.appendChild(td);
        } else {
          td.remove();
          Object.values(row).forEach(cell => {
            const td2 = document.createElement('td');
            td2.textContent = cell != null ? String(cell) : '';
            tr.appendChild(td2);
          });
        }
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

function renderKauuriRebateResultsSection(results, outputs) {
  const existing = document.getElementById('kauuri-rebate-result-section');
  if (existing) existing.remove();
  if (!results || results.length === 0) return;

  const auditSection = document.getElementById('audit-section');
  const section = document.createElement('div');
  section.id = 'kauuri-rebate-result-section';
  section.className = 'card dedup-result-section';

  const titleItem = document.createElement('div');
  titleItem.className = 'audit-item';
  const header = document.createElement('div');
  header.className = 'audit-item-header';
  const converted = results.filter(e => e.action === 'auto-rebate').length;
  const kept      = results.filter(e => e.action === 'keep').length;
  header.innerHTML = `
    <span class="audit-item-title">課売返振替変換結果</span>
    <span class="audit-badge" style="background:#e0f2fe;color:#0369a1;">${results.length} 件 (変換: ${converted} / 維持: ${kept})</span>
    <svg style="width:16px;height:16px;color:var(--gray-400);flex-shrink:0;transition:transform 0.2s" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="6 9 12 15 18 9"/>
    </svg>`;

  const body = document.createElement('div');
  body.className = 'audit-item-body';
  const table = document.createElement('table');
  table.innerHTML = `
    <thead><tr>
      <th>伝票No</th><th>日付</th><th>元側</th>
      <th>元科目</th><th>元金額</th><th>税率</th>
      <th>処理</th><th>変換先科目</th><th>適用後税区分</th>
    </tr></thead>`;
  const tbody = document.createElement('tbody');
  results.forEach(row => {
    const tr = document.createElement('tr');
    const actionLabel = row.action === 'auto-rebate' ? '振替変換' : 'そのまま(警告のみ)';
    tr.innerHTML = `
      <td>${escHtml(row.slip_no || '')}</td>
      <td>${escHtml(row.date || '')}</td>
      <td>${escHtml(row.side || '')}</td>
      <td>${escHtml(row.orig_kamoku || '')}</td>
      <td style="text-align:right">${(row.orig_amount||0).toLocaleString()}</td>
      <td>${escHtml(row.tax_rate ? row.tax_rate + '%' : '')}</td>
      <td style="color:${row.action==='auto-rebate'?'#2563eb':'#6b7280'}">${escHtml(actionLabel)}</td>
      <td>${escHtml(row.result_kamoku || '')}</td>
      <td><strong>${escHtml(row.result_tax_code || '')}</strong></td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  body.appendChild(table);

  const chevron = header.querySelector('svg');
  header.addEventListener('click', () => {
    const open = body.classList.toggle('open');
    chevron.style.transform = open ? 'rotate(180deg)' : 'rotate(0deg)';
  });

  titleItem.appendChild(header);
  titleItem.appendChild(body);
  section.appendChild(titleItem);
  auditSection.parentNode.insertBefore(section, auditSection.nextSibling);
}

function renderNonTaxableResultsSection(results, outputs) {
  // 既存セクション削除
  const existing = document.getElementById('nontax-result-section');
  if (existing) existing.remove();

  if (!results || results.length === 0) return;

  // 元ファイル名を特定 (ダウンロードファイル名用)
  const firstOutput = outputs && outputs.length > 0 ? outputs[0].filename : '';
  const baseName = firstOutput.replace(/\.[^.]+$/, '') || '変換結果';
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const csvFilename = `非課税対処結果_${baseName}_${today}.csv`;

  // セクション本体
  const section = document.createElement('div');
  section.id = 'nontax-result-section';
  section.className = 'card dedup-result-section';

  // ヘッダー
  const titleItem = document.createElement('div');
  titleItem.className = 'audit-item';

  const header = document.createElement('div');
  header.className = 'audit-item-header';
  header.innerHTML = `
    <span class="audit-item-title">非課税対処結果 <small style="font-weight:normal;color:#888">(非課売上のみ対象 — 非仕入は自動変換済み)</small></span>
    <span class="audit-badge" style="background:#fff3e0;color:var(--orange)">${results.length} 件</span>
    <svg style="width:16px;height:16px;color:var(--gray-400);flex-shrink:0;transition:transform 0.2s" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="6 9 12 15 18 9"/>
    </svg>`;

  const body = document.createElement('div');
  body.className = 'audit-item-body open';

  // ダウンロード + コピーボタン行
  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;';

  const btnDl = document.createElement('a');
  btnDl.className = 'btn btn-success btn-sm';
  btnDl.style.cssText = 'padding:6px 14px;font-size:13px;cursor:pointer;';
  btnDl.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;margin-right:4px;">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <polyline points="7 10 12 15 17 10"/>
    <line x1="12" y1="15" x2="12" y2="3"/>
  </svg>CSVダウンロード`;
  btnDl.setAttribute('download', csvFilename);
  btnDl.addEventListener('click', (e) => {
    e.preventDefault();
    const csvContent = buildNonTaxableCsv(results);
    const bom = '﻿';
    const blob = new Blob([bom + csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = csvFilename;
    a.click();
    URL.revokeObjectURL(url);
  });

  const btnCopy = document.createElement('button');
  btnCopy.className = 'btn btn-ghost btn-sm';
  btnCopy.style.cssText = 'padding:6px 14px;font-size:13px;';
  btnCopy.textContent = 'テキストエリアを開く / 閉じる';
  let taVisible = false;
  const ta = document.createElement('textarea');
  ta.readOnly = true;
  ta.rows = 8;
  ta.style.cssText = 'width:100%;font-size:12px;font-family:monospace;margin-top:8px;display:none;resize:vertical;';
  ta.value = buildNonTaxableTabText(results);
  btnCopy.addEventListener('click', () => {
    taVisible = !taVisible;
    ta.style.display = taVisible ? 'block' : 'none';
    if (taVisible) ta.select();
  });

  btnRow.appendChild(btnDl);
  btnRow.appendChild(btnCopy);
  body.appendChild(btnRow);
  body.appendChild(ta);

  // テーブル
  const table = document.createElement('table');
  table.innerHTML = `
    <thead><tr>
      <th>伝票No</th><th>日付</th><th>側</th>
      <th>借方科目</th><th>貸方科目</th>
      <th>金額</th><th>税額</th>
      <th>元の税区分</th><th>処理</th><th>適用後の税区分</th>
    </tr></thead>`;
  const tbody = document.createElement('tbody');
  results.forEach(row => {
    const tr = document.createElement('tr');
    const actionLabel = {
      'change-to-taxable': '税区分変更',
      'zero-tax': '税額を0に修正',
      'keep': 'そのまま(警告のみ)',
    }[row.action] || row.action || '';
    tr.innerHTML = `
      <td>${escHtml(row.slip_no || '')}</td>
      <td>${escHtml(row.date || '')}</td>
      <td>${escHtml(row.side || '')}</td>
      <td>${escHtml(row.dr_kamoku || '')}</td>
      <td>${escHtml(row.cr_kamoku || '')}</td>
      <td style="text-align:right">${escHtml(String(row.amount || ''))}</td>
      <td style="text-align:right">${escHtml(String(row.tax_amount || ''))}</td>
      <td>${escHtml(row.orig_tax_code || '')}</td>
      <td><span style="color:var(--blue)">${escHtml(actionLabel)}</span></td>
      <td><strong>${escHtml(row.result_tax_code || '')}</strong></td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  body.appendChild(table);

  // toggle
  const chevron = header.querySelector('svg');
  header.addEventListener('click', () => {
    const open = body.classList.toggle('open');
    chevron.style.transform = open ? 'rotate(180deg)' : 'rotate(0deg)';
  });
  chevron.style.transform = 'rotate(180deg)'; // 初期展開状態

  titleItem.appendChild(header);
  titleItem.appendChild(body);
  section.appendChild(titleItem);

  // dedup-result-section の後 or audit-section の後に挿入
  const dedupSection = document.getElementById('dedup-result-section');
  const auditSection = document.getElementById('audit-section');
  const anchor = dedupSection || auditSection;
  if (anchor) {
    anchor.parentNode.insertBefore(section, anchor.nextSibling);
  } else {
    document.getElementById('result-success').appendChild(section);
  }
}

function buildNonTaxableCsv(results) {
  const headers = ['伝票No', '日付', '側', '借方科目', '貸方科目', '金額', '税額', '元の税区分', '処理', '適用後の税区分', '摘要'];
  const actionLabel = { 'change-to-taxable': '税区分変更', 'zero-tax': '税額を0に修正', 'keep': 'そのまま(警告のみ)' };
  const lines = [headers.join(',')];
  results.forEach(row => {
    const cells = [
      row.slip_no || '',
      row.date || '',
      row.side || '',
      row.dr_kamoku || '',
      row.cr_kamoku || '',
      String(row.amount || ''),
      String(row.tax_amount || ''),
      row.orig_tax_code || '',
      actionLabel[row.action] || row.action || '',
      row.result_tax_code || '',
      row.summary || '',
    ].map(v => `"${String(v).replace(/"/g, '""')}"`);
    lines.push(cells.join(','));
  });
  return lines.join('\r\n');
}

function buildNonTaxableTabText(results) {
  const headers = ['伝票No', '日付', '側', '借方科目', '貸方科目', '金額', '税額', '元の税区分', '処理', '適用後の税区分', '摘要'];
  const actionLabel = { 'change-to-taxable': '税区分変更', 'zero-tax': '税額を0に修正', 'keep': 'そのまま(警告のみ)' };
  const lines = [headers.join('\t')];
  results.forEach(row => {
    lines.push([
      row.slip_no || '',
      row.date || '',
      row.side || '',
      row.dr_kamoku || '',
      row.cr_kamoku || '',
      String(row.amount || ''),
      String(row.tax_amount || ''),
      row.orig_tax_code || '',
      actionLabel[row.action] || row.action || '',
      row.result_tax_code || '',
      row.summary || '',
    ].join('\t'));
  });
  return lines.join('\n');
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
  initNonTaxableModal();
  initKauuriModal();
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
