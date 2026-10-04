const viewEl = document.getElementById('view');
const toastEl = document.getElementById('toast');
const modalEl = document.getElementById('modal');
const modalTitleEl = document.getElementById('modalTitle');
const modalBodyEl = document.getElementById('modalBody');
const modalPanelEl = modalEl ? modalEl.querySelector('.modal-panel') : null;
const themeToggleEl = document.getElementById('themeToggle');
const catalogPathsEl = document.getElementById('catalogPaths');
const donateBtnEl = document.getElementById('donateBtn');

const ffmpegBannerEl = document.getElementById('ffmpegBanner');
const ffmpegBannerMsgEl = document.getElementById('ffmpegBannerMsg');
const ffmpegBannerRetryEl = document.getElementById('ffmpegBannerRetry');

const reencFloatingEl = document.getElementById('reencFloating');
const reencFloatBarEl = document.getElementById('reencFloatBar');
const reencFloatPctEl = document.getElementById('reencFloatPct');
const reencFloatStatusEl = document.getElementById('reencFloatStatus');
const reencFloatRestoreEl = document.getElementById('reencFloatRestore');

const moveCopyFloatingEl = document.getElementById('moveCopyFloating');
const moveCopyFloatTitleEl = document.getElementById('moveCopyFloatTitle');
const moveCopyFloatStatusEl = document.getElementById('moveCopyFloatStatus');

function createDefaultRecordingsFilter() {
  return {
    nameContains: '',
    negateNameContains: false,
    nameContainsRegex: false,
    minSizeBytes: null,
    maxSizeBytes: null,
    minDurationSeconds: null,
    maxDurationSeconds: null,
    minShortSide: null,
    maxShortSide: null,
    videoCodecs: [],
    missingLivePreview: false,
  };
}

function getStoredRecordingsFilter() {
  try {
    const raw = localStorage.getItem('camero.recordingsFilter');
    const base = createDefaultRecordingsFilter();
    if (!raw) return base;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return base;
    return {
      nameContains: String(parsed.nameContains || '').trim(),
      negateNameContains: !!(parsed.negateNameContains ?? parsed.negateFilters),
      nameContainsRegex: !!parsed.nameContainsRegex,
      minSizeBytes: Number.isFinite(parsed.minSizeBytes) ? Number(parsed.minSizeBytes) : null,
      maxSizeBytes: Number.isFinite(parsed.maxSizeBytes) ? Number(parsed.maxSizeBytes) : null,
      minDurationSeconds: Number.isFinite(parsed.minDurationSeconds) ? Number(parsed.minDurationSeconds) : null,
      maxDurationSeconds: Number.isFinite(parsed.maxDurationSeconds) ? Number(parsed.maxDurationSeconds) : null,
      minShortSide: Number.isInteger(parsed.minShortSide) ? Number(parsed.minShortSide) : null,
      maxShortSide: Number.isInteger(parsed.maxShortSide) ? Number(parsed.maxShortSide) : null,
      videoCodecs: Array.isArray(parsed.videoCodecs)
        ? parsed.videoCodecs.map(v => String(v || '').trim()).filter(Boolean)
        : [],
      missingLivePreview: !!parsed.missingLivePreview,
    };
  } catch {
    return createDefaultRecordingsFilter();
  }
}

function setStoredRecordingsFilter(value) {
  try {
    const next = {
      ...createDefaultRecordingsFilter(),
      ...(value || {}),
      nameContains: String(value?.nameContains || '').trim(),
      negateNameContains: !!value?.negateNameContains,
      nameContainsRegex: !!value?.nameContainsRegex,
      videoCodecs: Array.isArray(value?.videoCodecs)
        ? value.videoCodecs.map(v => String(v || '').trim()).filter(Boolean)
        : [],
      missingLivePreview: !!value?.missingLivePreview,
    };
    localStorage.setItem('camero.recordingsFilter', JSON.stringify(next));
  } catch {
    /* ignore */
  }
}

function getPreferredTheme() {
  const stored = localStorage.getItem('camero.theme');
  if (stored === 'light' || stored === 'dark') return stored;
  // First run / no stored preference: default to dark.
  return 'dark';
}

function applyTheme(theme) {
  const t = (theme === 'light') ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  localStorage.setItem('camero.theme', t);
  if (themeToggleEl) themeToggleEl.textContent = (t === 'light') ? '☀' : '🌙';
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme || 'dark';
  applyTheme(current === 'light' ? 'dark' : 'light');
}

// Apply theme ASAP.
applyTheme(getPreferredTheme());
if (themeToggleEl) themeToggleEl.addEventListener('click', toggleTheme);

const state = {
  pageSize: 40,
  offset: 0,
  search: '',
  recordingsAutoPreview: (localStorage.getItem('camero.recordingsAutoPreview') === '1'),
  recordingsSort: {
    by: 'RECORDED_AT',
    dir: 'DESC',
  },
  recordingsFilter: getStoredRecordingsFilter(),
  selected: new Set(),
  // Best-effort client-side hiding for items just deleted.
  // Some environments can show brief eventual consistency where the list still returns deleted ids.
  _recentlyDeleted: new Map(), // id -> expiresAtMs
  streamers: {
    pageSize: 40,
    offset: 0,
    search: '',
    _siteKey: null,
    sort: {
      by: 'NAME',
      dir: 'ASC',
    },
    startsWith: null, // 'A'..'Z' | '#' | null
  },
  ffmpeg: {
    checked: false,
    ok: true,
    path: null,
    error: null,
  },
  bulkFetchStreamerInfo: {
    jobId: null,
    timer: null,
    lastJob: null,
  },
  _recordingsFilterKey: null,
  scan: {
    jobId: null,
    timer: null,
    lastStatus: null,
    lastPhase: null,
    refreshList: null,
    listRefreshInFlight: false,
    lastListRefreshAt: 0,
    lastListRefreshScanned: 0,
    lastListRefreshAdded: 0,
    lastListRefreshUpdated: 0,
  },
  autoTag: {
    jobId: null,
    timer: null,
    refreshList: null,
    listRefreshInFlight: false,
    lastListRefreshAt: 0,
    lastListRefreshCompleted: 0,
  },
  reencode: {
    jobId: null,
    timer: null,
    deleteOriginal: false,
    wasCanceled: false,
    active: false,
    recordingIds: [],
    lastJob: null,
    lastOptions: null,
    modalDismissed: false,
    refreshedAfterDeleteOriginal: false,
    terminalToastShown: false,
    lastCurrentRecordingId: null,
  },
  moveCopy: {
    active: false,
    mode: null,
    count: 0,
    destinationPath: null,
  },
  cardPreview: {
    timers: {},
  },
};

function updateMoveCopyFloatingVisibility() {
  if (!moveCopyFloatingEl) return;
  const active = !!state.moveCopy?.active;
  const modalOpen = !!(modalEl && !modalEl.hidden);
  moveCopyFloatingEl.hidden = !active || modalOpen;
}

function updateMoveCopyFloatingContent() {
  if (!moveCopyFloatingEl) return;
  const mode = state.moveCopy?.mode || 'move';
  const count = Number(state.moveCopy?.count || 0);
  const title = mode === 'copy' ? 'Copy' : 'Move';
  if (moveCopyFloatTitleEl) moveCopyFloatTitleEl.textContent = title;
  if (moveCopyFloatStatusEl) {
    const noun = count === 1 ? 'recording' : 'recordings';
    moveCopyFloatStatusEl.textContent = `Processing ${count} ${noun}...`;
  }
}


function isTerminalBulkFetchStatus(s) {
  return ['done', 'error'].includes(normStatus(s));
}

function updateBulkFetchStreamerInfoProgressUI(job) {
  const wrap = document.getElementById('bulkFetchStreamerInfoWrap');
  if (!wrap) return;

  if (!job) {
    wrap.hidden = true;
    return;
  }

  wrap.hidden = false;
  const bar = document.getElementById('bulkFetchStreamerInfoBar');
  const pct = document.getElementById('bulkFetchStreamerInfoPct');
  const txt = document.getElementById('bulkFetchStreamerInfoText');

  const p = job.progress || {};
  const percent = typeof p.percent === 'number' ? p.percent : 0;
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent)).toFixed(1)}%`;
  if (pct) pct.textContent = `${Math.round(percent)}%`;

  const total = (typeof p.total === 'number') ? p.total : 0;
  const done = (typeof p.done === 'number') ? p.done : 0;
  const ok = (typeof p.ok === 'number') ? p.ok : 0;
  const failed = (typeof p.failed === 'number') ? p.failed : 0;
  const skipped = (typeof p.skipped === 'number') ? p.skipped : 0;
  const current = (p.current || '').trim();

  const line1 = total ? `Fetched ${done}/${total} streamers` : 'No streamers to fetch';
  const line2 = `OK ${ok}, failed ${failed}, skipped ${skipped}`;
  const line3 = current ? `Current: ${current}` : '';
  const line4 = job.error ? `Last error: ${job.error}` : '';
  if (txt) txt.textContent = [line1, line2, line3, line4].filter(Boolean).join(' • ');

  const btn = document.getElementById('bulkFetchStreamerInfoBtn');
  if (btn) {
    const running = (normStatus(job.status) === 'running' || normStatus(job.status) === 'queued');
    btn.disabled = running;
  }
}

function stopBulkFetchStreamerInfoPolling() {
  if (state.bulkFetchStreamerInfo.timer) {
    clearInterval(state.bulkFetchStreamerInfo.timer);
    state.bulkFetchStreamerInfo.timer = null;
  }
}

async function pollBulkFetchStreamerInfoJobOnce(jobId) {
  if (!jobId) return;
  const data = await gql(
    `query($jobId:String!){ bulkFetchStreamerInfoJob(jobId:$jobId){ jobId status error progress { total done ok failed skipped current percent } } }`,
    { jobId }
  );

  const job = data.bulkFetchStreamerInfoJob;
  state.bulkFetchStreamerInfo.lastJob = job || null;

  if (!job) {
    stopBulkFetchStreamerInfoPolling();
    state.bulkFetchStreamerInfo.jobId = null;
    updateBulkFetchStreamerInfoProgressUI(null);
    const btn = document.getElementById('bulkFetchStreamerInfoBtn');
    if (btn) btn.disabled = false;
    return;
  }

  updateBulkFetchStreamerInfoProgressUI(job);

  if (isTerminalBulkFetchStatus(job.status)) {
    stopBulkFetchStreamerInfoPolling();
    state.bulkFetchStreamerInfo.jobId = null;

    // Best-effort: refresh view so new avatars/urls show.
    render().catch(() => {});
    if (job.status === 'error') {
      showToast(job.error || 'Bulk fetch failed');
    } else {
      const p = job.progress || {};
      const ok = (typeof p.ok === 'number') ? p.ok : 0;
      const failed = (typeof p.failed === 'number') ? p.failed : 0;
      showToast(`Bulk fetch done. OK ${ok}, failed ${failed}.`);
    }
  }
}

function startBulkFetchStreamerInfoPolling(jobId) {
  stopBulkFetchStreamerInfoPolling();
  state.bulkFetchStreamerInfo.jobId = jobId;
  pollBulkFetchStreamerInfoJobOnce(jobId).catch(e => showToast(e.message));
  state.bulkFetchStreamerInfo.timer = setInterval(() => {
    pollBulkFetchStreamerInfoJobOnce(jobId).catch(() => {});
  }, 800);
}

async function startBulkFetchMissingStreamerInfo(siteName) {
  try {
    const vars = { siteName: (siteName || '').trim() || null };
    const res = await gql(
      `mutation($siteName:String){ startBulkFetchMissingStreamerInfo(siteName:$siteName){ jobId status error progress { total done ok failed skipped current percent } } }`,
      vars
    );
    const job = res.startBulkFetchMissingStreamerInfo;
    if (!job?.jobId) throw new Error('Failed to start bulk fetch');
    state.bulkFetchStreamerInfo.lastJob = job;
    updateBulkFetchStreamerInfoProgressUI(job);
    startBulkFetchStreamerInfoPolling(job.jobId);
  } catch (e) {
    showToast(e.message || String(e));
  }
}

function normStatus(s) {
  return String(s || '').trim().toLowerCase();
}

function isTerminalReencodeStatus(s) {
  return ['done', 'error', 'canceled', 'cancelled'].includes(normStatus(s));
}

function formatReencodeStatusLine(job) {
  if (!job) return '';
  const cr = (job.currentRecordingId != null) ? ` (current: #${job.currentRecordingId})` : '';
  return `${job.status || 'unknown'}${cr}${job.error ? ` — ${job.error}` : ''}`;
}

function reencodeStatusIcon(status) {
  const s = normStatus(status);
  if (s === 'done') return '✓';
  if (s === 'running' || s === 'processing') return '⏵';
  if (s === 'queued' || s === 'pending') return '⏳';
  if (s === 'canceled' || s === 'cancelled') return '⛔';
  if (s === 'error' || s === 'failed') return '⚠';
  return '•';
}

function renderReencodeItemsHtml(items, currentRecordingId = null) {
  const arr = Array.isArray(items) ? items : [];
  return arr.map(it => {
    const rid = it.recordingId;
    const name = (it.fileName || '').trim();
    const outName = (it.outputFileName || '').trim();
    const st = it.status || 'queued';
    const stNorm = normStatus(st);
    const p = Math.max(0, Math.min(100, Number(it.percent || 0)));

    const outHint = outName && outName !== name ? ` <span class="job-item-out">→ ${escapeHtml(outName)}</span>` : '';
    const label = escapeHtml(stNorm || 'queued');
    const icon = reencodeStatusIcon(stNorm);

    let badgeClass = 'badge-neutral';
    if (stNorm === 'done') badgeClass = 'badge-accent';
    else if (stNorm === 'running' || stNorm === 'processing') badgeClass = 'badge-accent';
    else if (stNorm === 'error' || stNorm === 'failed') badgeClass = 'badge-danger';
    else if (stNorm === 'canceled' || stNorm === 'cancelled') badgeClass = 'badge-neutral';

    const link = it.outputUrl
      ? `<a class="btn btn-ghost job-item-link" href="${it.outputUrl}" target="_blank" rel="noopener">Open</a>`
      : '';

    const err = it.error ? `<div class="job-item-error">${escapeHtml(it.error)}</div>` : '';

    const isCurrent = (currentRecordingId != null) && (Number(currentRecordingId) === Number(rid));
    const currentClass = isCurrent ? 'job-item-current' : '';

    return `
      <div class="job-item ${currentClass} ${stNorm === 'running' ? 'job-item-running' : ''} ${stNorm === 'error' ? 'job-item-erroring' : ''}" data-recording-id="${escapeHtml(String(rid ?? ''))}">
        <div class="job-item-top">
          <div class="job-item-name">${escapeHtml(name || ('#' + rid))}${outHint}</div>
          <div class="job-item-actions">
            <span class="badge ${badgeClass}"><span class="job-status-icon" aria-hidden="true">${icon}</span>${label}</span>
            <span class="subtle job-item-pct">${Math.round(p)}%</span>
            ${link}
          </div>
        </div>
        <div class="job-item-progress">
          <div class="progress"><div class="progress-bar" style="width:${p}%"></div></div>
        </div>
        ${err}
      </div>
    `;
  }).join('');
}

function scrollReencodeListToCurrent(currentRecordingId) {
  const listEl = document.getElementById('reencList');
  if (!listEl) return;
  if (currentRecordingId == null) return;
  const el = listEl.querySelector(`[data-recording-id="${CSS.escape(String(currentRecordingId))}"]`);
  if (!el) return;
  try {
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch {
    try { el.scrollIntoView(true); } catch { /* ignore */ }
  }
}

function updateReencodeFloatingVisibility() {
  if (!reencFloatingEl) return;
  const j = state.reencode?.lastJob;
  const terminal = j ? isTerminalReencodeStatus(j.status) : false;
  const modalTitle = (modalTitleEl && typeof modalTitleEl.textContent === 'string') ? modalTitleEl.textContent : '';
  const isTranscodeModalOpen = !!(modalEl && !modalEl.hidden && modalTitle === 'Transcode');
  const show = !!(
    state.reencode &&
    state.reencode.jobId &&
    !state.reencode.wasCanceled &&
    !terminal &&
    !isTranscodeModalOpen
  );
  reencFloatingEl.hidden = !show;
}

function updateReencodeProgressUI(job) {
  if (state.reencode) state.reencode.lastJob = job || null;

  // Update floating panel.
  if (reencFloatingEl) {
    const overall = Math.max(0, Math.min(100, Number(job?.percent || 0)));
    if (reencFloatBarEl) reencFloatBarEl.style.width = `${overall}%`;
    if (reencFloatPctEl) reencFloatPctEl.textContent = `${Math.round(overall)}%`;
    if (reencFloatStatusEl) reencFloatStatusEl.textContent = job ? formatReencodeStatusLine(job) : 'running';
    updateReencodeFloatingVisibility();
  }

  // Update modal UI if present.
  const wrap = document.getElementById('reencProgressWrap');
  const bar = document.getElementById('reencBar');
  const pctEl = document.getElementById('reencPct');
  const statusEl = document.getElementById('reencStatus');
  const listEl = document.getElementById('reencList');
  const cancelBtn = document.getElementById('reencCancelBtn');

  if (wrap) wrap.hidden = !job;
  if (job) {
    const overall = Math.max(0, Math.min(100, Number(job.percent || 0)));
    if (bar) bar.style.width = `${overall}%`;
    if (pctEl) pctEl.textContent = `${Math.round(overall)}%`;
    if (statusEl) statusEl.textContent = formatReencodeStatusLine(job);
    if (listEl) listEl.innerHTML = renderReencodeItemsHtml(job.items, job.currentRecordingId);

    // Auto-scroll to the active item when the current recording changes.
    try {
      const prev = state.reencode ? state.reencode.lastCurrentRecordingId : null;
      const cur = job.currentRecordingId ?? null;
      if (state.reencode) state.reencode.lastCurrentRecordingId = cur;
      const changed = (prev != null || cur != null) && String(prev) !== String(cur);
      if (changed) scrollReencodeListToCurrent(cur);
    } catch { /* ignore */ }

    const terminal = isTerminalReencodeStatus(job.status);
    if (cancelBtn) cancelBtn.disabled = terminal;
  }
}

async function pollReencodeJobOnce(jobId) {
  const data = await gql(
    `query($jobId:String!){ reencodeJob(jobId:$jobId){ jobId status currentRecordingId percent error items { recordingId fileName outputFileName status percent outputUrl error } } }`,
    { jobId }
  );
  return data.reencodeJob;
}

function stopReencodePolling() {
  if (state.reencode && state.reencode.timer) {
    clearInterval(state.reencode.timer);
    state.reencode.timer = null;
  }
}

async function refreshReencodeJob(jobId) {
  const id = jobId || state.reencode?.jobId;
  if (!id) return null;
  const prev = state.reencode?.lastJob || null;
  const prevTerminal = prev ? isTerminalReencodeStatus(prev.status) : false;
  const j = await pollReencodeJobOnce(id);
  if (!j) throw new Error('Transcode job not found');

  updateReencodeProgressUI(j);

  const terminal = isTerminalReencodeStatus(j.status);
  if (terminal) {
    stopReencodePolling();
    // Hide the floating panel once terminal, unless the modal is open.
    // (The modal already shows final status.)
    updateReencodeFloatingVisibility();

    // If delete-original was requested, recording(s) may have changed metadata.
    if (normStatus(j.status) === 'done' && state.reencode?.deleteOriginal && !state.reencode.refreshedAfterDeleteOriginal) {
      state.reencode.refreshedAfterDeleteOriginal = true;
      try { await render(); } catch { /* ignore */ }
    }

    // If the user dismissed the modal, notify completion once.
    try {
      const shouldToast = (modalEl && modalEl.hidden) && !prevTerminal && state.reencode && !state.reencode.terminalToastShown;
      if (shouldToast) {
        state.reencode.terminalToastShown = true;
        const st = normStatus(j.status);
        if (st === 'done') showToast('Transcode done');
        else if (st === 'canceled' || st === 'cancelled') showToast('Transcode cancelled');
        else if (st === 'error') showToast(j.error ? `Transcode error — ${j.error}` : 'Transcode error');
        else showToast(`Transcode ${j.status || 'finished'}`);
      }
    } catch { /* ignore */ }
  }
  return j;
}

function ensureReencodePolling() {
  const id = state.reencode?.jobId;
  const j = state.reencode?.lastJob;
  if (!id) return;
  if (j && isTerminalReencodeStatus(j.status)) {
    stopReencodePolling();
    return;
  }
  if (state.reencode.timer) return;
  state.reencode.timer = setInterval(() => {
    refreshReencodeJob(id).catch(e => showToast(e.message));
  }, 1000);
}

function restoreReencodeModalFromFloating() {
  const ids = Array.isArray(state.reencode?.recordingIds) ? state.reencode.recordingIds : [];
  if (!ids.length) {
    // Best-effort: show something rather than doing nothing.
    showToast('No recordings available to restore the Transcode dialog');
    return;
  }
  openReencodeModal(ids, { restore: true }).catch(e => showToast(e.message));
}

if (reencFloatRestoreEl) {
  reencFloatRestoreEl.addEventListener('click', (e) => {
    try { e.preventDefault(); } catch { /* ignore */ }
    try { e.stopPropagation(); } catch { /* ignore */ }
    restoreReencodeModalFromFloating();
  });
}

const PAGE_SIZES = [20, 40, 60, 100, 250, 500, 1000, 5000];

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function buildPageOptions(totalPages, currentPage) {
  const tp = Math.max(1, totalPages || 1);
  const cp = clamp(currentPage || 1, 1, tp);
  const windowSize = 50;

  let start = Math.max(1, cp - Math.floor(windowSize / 2));
  let end = Math.min(tp, start + windowSize - 1);
  start = Math.max(1, end - windowSize + 1);

  const pages = [];

  function addPage(p) {
    if (!pages.includes(p)) pages.push(p);
  }

  addPage(1);
  for (let p = start; p <= end; p++) addPage(p);
  addPage(tp);
  pages.sort((a, b) => a - b);

  const opts = [];
  let prev = null;
  for (const p of pages) {
    if (prev != null && p - prev > 1) {
      opts.push({ value: -1, label: '…', disabled: true });
    }
    opts.push({ value: p, label: `Page ${p}`, disabled: false });
    prev = p;
  }
  return opts;
}

function updateScanProgressUI(job) {
  const wrap = document.getElementById('scanProgressWrap');
  if (!wrap) return;

  const stopBtn = document.getElementById('stopLivePreviews');
  const stopScanBtn = document.getElementById('stopScan');
  const genMissingBtn = document.getElementById('generateMissingLivePreviews');

  if (!job) {
    wrap.hidden = true;
    if (stopBtn) stopBtn.hidden = true;
    if (stopScanBtn) stopScanBtn.hidden = true;
    if (genMissingBtn) {
      genMissingBtn.disabled = false;
    }
    return;
  }

  wrap.hidden = false;
  const bar = document.getElementById('scanProgressBar');
  const pct = document.getElementById('scanProgressPct');
  const txt = document.getElementById('scanProgressText');

  const p = job.progress || {};
  const percent = typeof p.percent === 'number' ? p.percent : 0;
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent)).toFixed(1)}%`;
  if (pct) pct.textContent = `${Math.round(percent)}%`;

  const phase = p.phase || 'scan';
  const total = p.totalFiles ?? null;
  const scanned = p.scannedFiles ?? 0;

  if (stopBtn) {
    const isRunning = (job.status === 'running' || job.status === 'queued');
    // If previews are actually running, keep the button visible even if the checkbox
    // gets toggled mid-scan.
    const enabled = !!document.getElementById('generateLivePreviews')?.checked;
    stopBtn.hidden = !(isRunning && (phase === 'live_previews' || enabled));
  }

  if (genMissingBtn) {
    const isRunning = (job.status === 'running' || job.status === 'queued');
    // While previews are generating, hide the "Generate" button (it should only appear when idle).
    genMissingBtn.hidden = !!(isRunning && phase === 'live_previews');
    // Also prevent triggering generation while any job is running.
    genMissingBtn.disabled = !!isRunning;
  }

  if (stopScanBtn) {
    const isRunning = (job.status === 'running' || job.status === 'queued');
    stopScanBtn.hidden = !(isRunning && phase !== 'live_previews' && phase !== 'auto_tagging');
  }

  let line1 = '';
  let file = '';

  if (phase === 'live_previews') {
    const tp = p.totalPreviews ?? null;
    const gp = p.generatedPreviews ?? 0;
    const fp = p.failedPreviews ?? 0;
    const suffix = fp ? ` (failed ${fp})` : '';
    line1 = tp != null ? `Animated previews ${gp}/${tp}${suffix}` : `Animated previews ${gp}${suffix}`;
    const preview = p.currentPreview ? `Preview: ${p.currentPreview}` : '';
    const rec = p.currentRecording ? `Recording: ${p.currentRecording}` : '';
    // Put the recording path on its own line (it can be very long).
    file = preview;
    if (rec) file = preview ? `${preview}\n${rec}` : `\n${rec}`;
  } else if (phase === 'auto_tagging') {
    const tp = p.totalPreviews ?? null;
    const gp = p.generatedPreviews ?? 0;
    const fp = p.failedPreviews ?? 0;
    const suffix = fp ? ` (failed ${fp})` : '';
    line1 = tp != null ? `Auto-tagging ${gp}/${tp}${suffix}` : `Auto-tagging ${gp}${suffix}`;
    file = p.currentRecording ? `Recording: ${p.currentRecording}` : '';
  } else {
    line1 = total != null ? `Scanned ${scanned}/${total} files` : `Scanned ${scanned} files`;
    file = p.currentFile ? `Current: ${p.currentFile}` : '';
  }

  let line2 = '';
  if (phase === 'live_previews') {
    line2 = `Scan: Added ${p.added ?? 0}, updated ${p.updated ?? 0}, skipped ${p.skipped ?? 0}`;
  } else {
    line2 = `Added ${p.added ?? 0}, updated ${p.updated ?? 0}, skipped ${p.skipped ?? 0}, errors ${p.errors ?? 0}`;
  }
  if (txt) txt.textContent = [line1, line2, file].filter(Boolean).join(' • ');
}

function stopScanPolling() {
  if (state.scan.timer) {
    clearInterval(state.scan.timer);
    state.scan.timer = null;
  }
}

async function pollScanJobOnce(jobId) {
  if (!jobId) return;
  const data = await gql(
      `query($jobId:String!){ scanJob(jobId:$jobId){ jobId status error progress { phase totalFiles scannedFiles added updated skipped errors currentFile currentRecording totalPreviews generatedPreviews failedPreviews currentPreview percent } result { scannedFiles added updated skipped errors } } }`,
    { jobId }
  );

  const job = data.scanJob;
  if (!job) {
    stopScanPolling();
    state.scan.jobId = null;
    setSettingsBusy(false);
    updateScanProgressUI(null);
    return;
  }

  state.scan.lastStatus = job.status;
  state.scan.lastPhase = job?.progress?.phase || null;
  updateScanProgressUI(job);
  syncAutoTagUIFromScanJob(job);
  setSettingsBusy(job.status === 'running' || job.status === 'queued');

  try {
    const p = job.progress || {};
    const phase = p.phase || 'scan';
    const scanned = (typeof p.scannedFiles === 'number') ? p.scannedFiles : 0;
    const added = (typeof p.added === 'number') ? p.added : 0;
    const updated = (typeof p.updated === 'number') ? p.updated : 0;
    const isRunning = (job.status === 'running' || job.status === 'queued');
    const refreshList = state.scan.refreshList;
    const canRefresh = (
      isRunning &&
      phase === 'scan' &&
      typeof refreshList === 'function' &&
      modalEl.hidden &&
      state.selected.size === 0 &&
      !state.scan.listRefreshInFlight
    );

    const now = Date.now();
    const dueByTime = (now - (state.scan.lastListRefreshAt || 0)) >= 2500;
    const dueByAdded = added > (state.scan.lastListRefreshAdded || 0);
    const dueByUpdated = updated > (state.scan.lastListRefreshUpdated || 0);
    const dueByProgress = scanned - (state.scan.lastListRefreshScanned || 0) >= 25;

    if (canRefresh && dueByTime && (dueByAdded || dueByUpdated || dueByProgress)) {
      state.scan.listRefreshInFlight = true;
      state.scan.lastListRefreshAt = now;
      state.scan.lastListRefreshScanned = scanned;
      state.scan.lastListRefreshAdded = added;
      state.scan.lastListRefreshUpdated = updated;
      Promise.resolve(refreshList())
        .catch(e => showToast(e.message || String(e)))
        .finally(() => {
          state.scan.listRefreshInFlight = false;
        });
    }
  } catch {
    // Best-effort only.
  }

  if (job.status === 'done') {
    stopScanPolling();
    state.scan.jobId = null;
    state.scan.lastPhase = null;
    setSettingsBusy(false);
    updateScanProgressUI(null);
    syncAutoTagUIFromScanJob(null);
    state.scan.listRefreshInFlight = false;
    state.scan.lastListRefreshAt = 0;
    state.scan.lastListRefreshScanned = 0;
    state.scan.lastListRefreshAdded = 0;
    state.scan.lastListRefreshUpdated = 0;
    const s = job.result;
    if (s) {
      const p = job.progress || {};
      const isPreviewsOnly = (p.phase === 'live_previews') && (p.totalFiles == null) && (s.scannedFiles === 0);
      if (isPreviewsOnly) {
        const tp = (typeof p.totalPreviews === 'number') ? p.totalPreviews : null;
        const gp = (typeof p.generatedPreviews === 'number') ? p.generatedPreviews : 0;
        const fp = (typeof p.failedPreviews === 'number') ? p.failedPreviews : 0;
        const suffix = fp ? ` (failed ${fp})` : '';
        showToast(tp != null ? `Animated previews done. ${gp}/${tp}${suffix}.` : `Animated previews done. ${gp}${suffix}.`);
      } else {
        showToast(`Scan done. Added ${s.added}, updated ${s.updated}, skipped ${s.skipped}, errors ${s.errors}.`);
      }
    }
    // Sync active jobs first so that any auto-started auto-tag job is reflected
    // in state.autoTag.jobId before render() sets up the button controls.
    syncActiveJobs().catch(() => {}).finally(() => {
      render().catch(e => showToast(e.message));
    });
  } else if (job.status === 'cancelled') {
    stopScanPolling();
    state.scan.jobId = null;
    state.scan.lastPhase = null;
    setSettingsBusy(false);
    updateScanProgressUI(null);
    syncAutoTagUIFromScanJob(null);
    state.scan.listRefreshInFlight = false;
    state.scan.lastListRefreshAt = 0;
    state.scan.lastListRefreshScanned = 0;
    state.scan.lastListRefreshAdded = 0;
    state.scan.lastListRefreshUpdated = 0;
    const s = job.result;
    if (s) {
      const p = job.progress || {};
      const isPreviewsOnly = (p.phase === 'live_previews') && (p.totalFiles == null) && (s.scannedFiles === 0);
      if (isPreviewsOnly) {
        const tp = (typeof p.totalPreviews === 'number') ? p.totalPreviews : null;
        const gp = (typeof p.generatedPreviews === 'number') ? p.generatedPreviews : 0;
        const fp = (typeof p.failedPreviews === 'number') ? p.failedPreviews : 0;
        const suffix = fp ? ` (failed ${fp})` : '';
        showToast(tp != null ? `Animated previews cancelled. ${gp}/${tp}${suffix}.` : `Animated previews cancelled. ${gp}${suffix}.`);
      } else {
        showToast(`Scan cancelled. Added ${s.added}, updated ${s.updated}, skipped ${s.skipped}, errors ${s.errors}.`);
      }
    }
    render().catch(e => showToast(e.message));
  } else if (job.status === 'error') {
    stopScanPolling();
    state.scan.jobId = null;
    state.scan.lastPhase = null;
    setSettingsBusy(false);
    updateScanProgressUI(null);
    syncAutoTagUIFromScanJob(null);
    state.scan.listRefreshInFlight = false;
    state.scan.lastListRefreshAt = 0;
    state.scan.lastListRefreshScanned = 0;
    state.scan.lastListRefreshAdded = 0;
    state.scan.lastListRefreshUpdated = 0;
    showToast(job.error || 'Scan failed');
  }
}

function startScanPolling(jobId) {
  stopScanPolling();
  state.scan.jobId = jobId;
  setSettingsBusy(true);
  // Immediate update + periodic polling.
  pollScanJobOnce(jobId).catch(e => showToast(e.message));
  state.scan.timer = setInterval(() => {
    pollScanJobOnce(jobId).catch(e => showToast(e.message));
  }, 750);
}

function stopAutoTagPolling() {
  if (state.autoTag.timer) {
    clearInterval(state.autoTag.timer);
    state.autoTag.timer = null;
  }
}

function isExternalScanAutoTagJobId(jobId) {
  return String(jobId || '').startsWith('scan:');
}

function buildAutoTagJobFromScanJob(job) {
  const phase = job?.progress?.phase || '';
  if (phase !== 'auto_tagging') return null;

  const p = job.progress || {};
  return {
    jobId: `scan:${job.jobId}`,
    status: (job.status === 'queued' || job.status === 'running') ? 'running' : (job.status || 'running'),
    error: job.error || null,
    externalSource: 'scan',
    progress: {
      total: (typeof p.totalPreviews === 'number') ? p.totalPreviews : null,
      completed: (typeof p.generatedPreviews === 'number') ? p.generatedPreviews : 0,
      failed: (typeof p.failedPreviews === 'number') ? p.failedPreviews : 0,
      currentFile: p.currentRecording || null,
      percent: (typeof p.percent === 'number') ? p.percent : 0,
      framesUsed: null,
      candidateCount: null,
    },
  };
}

function syncAutoTagUIFromScanJob(job) {
  const synthetic = buildAutoTagJobFromScanJob(job);
  if (synthetic) {
    stopAutoTagPolling();
    state.autoTag.jobId = synthetic.jobId;
    updateAutoTagProgressUI(synthetic);
    return;
  }

  if (isExternalScanAutoTagJobId(state.autoTag.jobId)) {
    state.autoTag.jobId = null;
    updateAutoTagProgressUI(null);
  }
}

function isAutoTagBusy() {
  return !!state.autoTag.jobId;
}

async function pollAutoTagJobOnce(jobId) {
  if (!jobId) return;
  let job;
  try {
    const data = await gql(
      `query($jobId:String!){ autoTagJob(jobId:$jobId){ jobId status error progress { total completed failed currentFile percent framesUsed candidateCount } } }`,
      { jobId }
    );
    job = data.autoTagJob;
  } catch (e) {
    // Network error — keep polling.
    return;
  }

  if (!job) {
    stopAutoTagPolling();
    state.autoTag.jobId = null;
    updateAutoTagProgressUI(null);
    return;
  }

  updateAutoTagProgressUI(job);

  // Progressive list refresh — throttled, similar to the scan job.
  try {
    const p = job.progress || {};
    const completed = (typeof p.completed === 'number') ? p.completed : 0;
    const isRunning = (job.status === 'running' || job.status === 'queued');
    const refreshList = state.autoTag.refreshList;
    const canRefresh = (
      isRunning &&
      typeof refreshList === 'function' &&
      modalEl.hidden &&
      state.selected.size === 0 &&
      !state.autoTag.listRefreshInFlight
    );
    const now = Date.now();
    const dueByTime = (now - (state.autoTag.lastListRefreshAt || 0)) >= 2500;
    const dueByProgress = completed > (state.autoTag.lastListRefreshCompleted || 0);

    if (canRefresh && dueByTime && dueByProgress) {
      state.autoTag.listRefreshInFlight = true;
      state.autoTag.lastListRefreshAt = now;
      state.autoTag.lastListRefreshCompleted = completed;
      Promise.resolve(refreshList())
        .catch(e => showToast(e.message || String(e)))
        .finally(() => { state.autoTag.listRefreshInFlight = false; });
    }
  } catch {
    // Best-effort only.
  }

  if (job.status === 'done') {
    stopAutoTagPolling();
    state.autoTag.jobId = null;
    updateAutoTagProgressUI(null);
    state.autoTag.listRefreshInFlight = false;
    state.autoTag.lastListRefreshAt = 0;
    state.autoTag.lastListRefreshCompleted = 0;
    const p = job.progress || {};
    showToast(`Auto-tagging done. Tagged ${p.completed ?? 0} recordings${p.failed ? `, failed ${p.failed}` : ''}.`);
    render().catch(() => {});
  } else if (job.status === 'cancelled') {
    stopAutoTagPolling();
    state.autoTag.jobId = null;
    updateAutoTagProgressUI(null);
    state.autoTag.listRefreshInFlight = false;
    state.autoTag.lastListRefreshAt = 0;
    state.autoTag.lastListRefreshCompleted = 0;
    const p = job.progress || {};
    showToast(`Auto-tagging cancelled. Tagged ${p.completed ?? 0} recordings.`);
    render().catch(() => {});
  } else if (job.status === 'error') {
    stopAutoTagPolling();
    state.autoTag.jobId = null;
    updateAutoTagProgressUI(null);
    state.autoTag.listRefreshInFlight = false;
    state.autoTag.lastListRefreshAt = 0;
    state.autoTag.lastListRefreshCompleted = 0;
    showToast(job.error || 'Auto-tagging failed');
  }
}

function startAutoTagPolling(jobId) {
  stopAutoTagPolling();
  state.autoTag.jobId = jobId;
  pollAutoTagJobOnce(jobId).catch(() => {});
  state.autoTag.timer = setInterval(() => {
    pollAutoTagJobOnce(jobId).catch(() => {});
  }, 1000);
}

function updateAutoTagProgressUI(job) {
  const autoTagUntaggedBtn = document.getElementById('autoTagUntagged');
  const stopAutoTagBtn = document.getElementById('stopAutoTag');
  const wrap = document.getElementById('autoTagProgressWrap');
  const isExternalScanJob = job?.externalSource === 'scan' || isExternalScanAutoTagJobId(job?.jobId);

  if (!job) {
    if (autoTagUntaggedBtn) { autoTagUntaggedBtn.hidden = false; autoTagUntaggedBtn.disabled = false; }
    if (stopAutoTagBtn) stopAutoTagBtn.hidden = true;
    if (wrap) wrap.hidden = true;
    return;
  }

  const isRunning = (job.status === 'running' || job.status === 'queued');
  if (autoTagUntaggedBtn) autoTagUntaggedBtn.hidden = isRunning;
  if (stopAutoTagBtn) stopAutoTagBtn.hidden = !isRunning;

  if (wrap) wrap.hidden = !isRunning;

  if (isRunning) {
    const p = job.progress || {};
    const total = p.total ?? null;
    const done = p.completed ?? 0;
    const failed = p.failed ?? 0;
    const percent = typeof p.percent === 'number' ? p.percent : 0;

    const bar = document.getElementById('autoTagProgressBar');
    const pct = document.getElementById('autoTagProgressPct');
    const txt = document.getElementById('autoTagProgressText');

    if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent)).toFixed(1)}%`;
    if (pct) pct.textContent = `${Math.round(percent)}%`;

    const suffix = failed ? ` (failed ${failed})` : '';
    const prefix = isExternalScanJob ? 'Auto-tagging during scan' : 'Auto-tagging';
    const line1 = total != null ? `${prefix} ${done}/${total}${suffix}` : `${prefix} ${done}${suffix}`;
    let framesInfo = '';
    if (p.framesUsed != null) {
      framesInfo = p.candidateCount != null
        ? ` · ${p.framesUsed} frames seleccionados (de ${p.candidateCount} candidatos)`
        : ` · ${p.framesUsed} frames`;
    }
    const line2 = p.currentFile ? `Current: ${p.currentFile}${framesInfo}` : '';
    if (txt) txt.textContent = line2 ? `${line1}\n${line2}` : line1;

    const label = `${line1}…`;
    if (stopAutoTagBtn) stopAutoTagBtn.textContent = label.length > 40 ? 'Stop auto-tagging' : label;
  } else {
    if (stopAutoTagBtn) stopAutoTagBtn.textContent = 'Stop auto-tagging';
  }
}

function showToast(message, opts = {}) {
  const msg = String(message ?? '');
  const timeoutMs = Number(opts.timeoutMs ?? 8000);
  toastEl.textContent = msg;
  toastEl.hidden = false;

  clearTimeout(showToast._t);
  if (timeoutMs > 0) {
    showToast._t = setTimeout(() => {
      toastEl.hidden = true;
    }, timeoutMs);
  }
}

async function startLivePreviewFromCard(recordingId, btn) {
  const id = parseInt(recordingId, 10);
  if (!id) return;
  if (state.cardPreview.timers[id]) {
    showToast('Animated preview already generating…');
    return;
  }

  const originalText = btn ? btn.textContent : '';
  let lastPctShown = -1;
  try {
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Generating…';
    }

    const res = await gql(
      `mutation($id:Int!){ startLivePreviewRecording(recordingId:$id){ jobId status } }`,
      { id }
    );
    const jobId = res.startLivePreviewRecording?.jobId;
    if (!jobId) throw new Error('Failed to start animated preview');

    showToast('Generating animated preview…');

    async function pollOnce() {
      const data = await gql(
        `query($jobId:String!){ livePreviewJob(jobId:$jobId){ jobId status percent outputUrl error } }`,
        { jobId }
      );
      const j = data.livePreviewJob;
      if (!j) throw new Error('Animated preview job not found');

      // Update button progress (best effort)
      try {
        const pct = Math.max(0, Math.min(100, Number(j.percent || 0)));
        const rounded = Math.round(pct);
        if (btn && btn.isConnected) {
          if (j.status === 'queued') {
            btn.textContent = 'Queued…';
          } else if (j.status === 'running') {
            if (rounded !== lastPctShown) {
              lastPctShown = rounded;
              btn.textContent = `Generating… ${rounded}%`;
            }
          }
        }
      } catch { /* ignore */ }

      if (j.status === 'done') return true;
      if (j.status === 'error') throw new Error(j.error || 'Animated preview failed');
      return false;
    }

    // Immediate poll + interval polling.
    await pollOnce().catch(() => {});
    state.cardPreview.timers[id] = setInterval(() => {
      pollOnce()
        .then(done => {
          if (!done) return;
          clearInterval(state.cardPreview.timers[id]);
          delete state.cardPreview.timers[id];
          showToast('Animated preview ready');
          try {
            if (btn && btn.isConnected) {
              btn.disabled = false;
              btn.textContent = 'Preview ready';
            }
          } catch { /* ignore */ }
          renderPreserveScroll().catch(() => {});
        })
        .catch(e => {
          clearInterval(state.cardPreview.timers[id]);
          delete state.cardPreview.timers[id];
          showToast(e.message);
          // Re-enable button if still in DOM.
          try {
            if (btn && btn.isConnected) {
              btn.disabled = false;
              btn.textContent = originalText || 'Create preview';
            }
          } catch { /* ignore */ }
        });
    }, 900);
  } catch (e) {
    showToast(e.message);
  } finally {
    // Keep disabled while polling; if we didn't start polling, restore.
    if (!state.cardPreview.timers[id] && btn) {
      btn.disabled = false;
      btn.textContent = originalText || 'Create preview';
    }
  }
}

function fmtBytes(bytes) {
  if (bytes == null) return 'Unknown';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtDuration(sec) {
  if (sec == null) return 'Unknown';
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(r).padStart(2,'0')}`;
  return `${m}:${String(r).padStart(2,'0')}`;
}

async function refreshCatalogInfo() {
  if (!catalogPathsEl) return;
  try {
    const data = await gql(`query { appConfig { catalogRoot filenameRegex catalogFolders { path } } }`);
    const cfg = data?.appConfig;
    const folders = Array.isArray(cfg?.catalogFolders) && cfg.catalogFolders.length
      ? cfg.catalogFolders
      : (cfg?.catalogRoot ? [{ path: cfg.catalogRoot }] : []);
    const paths = folders.map(f => String(f?.path || '').trim()).filter(Boolean);
    if (!paths.length) {
      catalogPathsEl.textContent = 'Not set';
      catalogPathsEl.title = '';
      return;
    }
    const joined = paths.join(' | ');
    catalogPathsEl.textContent = joined;
    catalogPathsEl.title = joined;
  } catch {
    catalogPathsEl.textContent = 'Unknown';
    catalogPathsEl.title = '';
  }
}

async function gql(query, variables = {}) {
  const res = await fetch('/graphql', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors && json.errors.length) {
    const msg = json.errors[0].message || 'GraphQL error';
    throw new Error(msg);
  }
  return json.data;
}

function updateTopbarHeightVar() {
  try {
    const topbar = document.querySelector('.topbar');
    const h = topbar ? topbar.offsetHeight : 0;
    document.documentElement.style.setProperty('--topbar-h', `${h}px`);
  } catch { /* ignore */ }
}

function setFfmpegBannerVisible(isVisible, msg = '') {
  if (!ffmpegBannerEl) return;
  ffmpegBannerEl.hidden = !isVisible;
  if (ffmpegBannerMsgEl) ffmpegBannerMsgEl.textContent = msg || '';
}

async function refreshFfmpegBanner({ force = false } = {}) {
  updateTopbarHeightVar();

  if (!force && state.ffmpeg?.checked) {
    if (state.ffmpeg.ok) setFfmpegBannerVisible(false);
    else {
      const hint = "Instala FFmpeg y añádelo al PATH, o coloca ffmpeg.exe junto al programa.";
      setFfmpegBannerVisible(true, `${state.ffmpeg.error || 'FFmpeg no encontrado.'} ${hint}`);
    }
    return;
  }

  try {
    const data = await gql(`query { ffmpegStatus { ok path error } }`);
    const status = data?.ffmpegStatus;
    if (!status) throw new Error('No se pudo comprobar FFmpeg');

    state.ffmpeg.checked = true;
    state.ffmpeg.ok = !!status.ok;
    state.ffmpeg.path = status.path || null;
    state.ffmpeg.error = status.error || null;

    if (status.ok) {
      setFfmpegBannerVisible(false);
    } else {
      const hint = "Instala FFmpeg y añádelo al PATH, o coloca ffmpeg.exe junto al programa.";
      const msg = `${status.error || 'FFmpeg no encontrado.'} ${hint}`;
      setFfmpegBannerVisible(true, msg);
    }
  } catch (e) {
    state.ffmpeg.checked = true;
    state.ffmpeg.ok = false;
    state.ffmpeg.path = null;
    state.ffmpeg.error = (e && e.message) ? e.message : String(e);
    setFfmpegBannerVisible(true, `No se pudo comprobar FFmpeg: ${state.ffmpeg.error}`);
  }
}

function setActiveNav(route) {
  document.querySelectorAll('.nav-item').forEach(a => {
    a.classList.toggle('active', a.dataset.route === route);
  });
}

function setSettingsBusy(isBusy) {
  const gearLink = document.querySelector('.nav-item-gear');
  if (!gearLink) return;
  gearLink.classList.toggle('busy', !!isBusy);
  gearLink.setAttribute('aria-busy', !!isBusy ? 'true' : 'false');
}

function openModal(title, html) {
  if (modalPanelEl) modalPanelEl.classList.remove('modal-panel-image');
  modalTitleEl.textContent = title;
  modalBodyEl.innerHTML = html;
  modalEl.hidden = false;
}

function openContactSheetModal(title, imageUrl) {
  const safeTitle = String(title || 'Contact sheet').trim() || 'Contact sheet';
  const safeUrl = String(imageUrl || '').trim();
  if (!safeUrl) {
    showToast('Contact sheet not available');
    return;
  }

  openModal(safeTitle, `
    <div class="contact-sheet-wrap">
      <img class="contact-sheet-image" src="${escapeHtml(safeUrl)}" alt="${escapeHtml(`Contact sheet for ${safeTitle}`)}" loading="eager" />
    </div>
  `);
  if (modalPanelEl) modalPanelEl.classList.add('modal-panel-image');
}

const DONATE_WALLETS = {
  btc: {
    key: 'btc',
    name: 'Bitcoin',
    icon: '/assets/cryptocoins/btc.svg',
    qr: '/static/donate/bitcoin.png',
    address: '1HKSxwF6cvgzcpkwjihGJEhfHa9XM2ecrr',
  },
  eth: {
    key: 'eth',
    name: 'Ethereum',
    icon: '/assets/cryptocoins/eth.svg',
    qr: '/static/donate/ethereum.png',
    address: '0x57364b806b31D33F3cd9d9476d97094A01Ad537b',
  },
  xmr: {
    key: 'xmr',
    name: 'Monero',
    icon: '/assets/cryptocoins/xmr.svg',
    qr: '/static/donate/monero.png',
    address: '4AZdr1RGD7VKfV7Et8Y5PyYwtZeQ5LhUahKPCae7zcZpCW7DgHAs6pXZrvCfH3EmTj6K5vispceUw9cY6GUtzJizRAiTr2L',
  },
};

function renderDonateColumn(info) {
  const address = escapeHtml(info.address || '');
  return `
    <div class="donate-card" data-coin="${escapeHtml(info.key || '')}">
      <div class="donate-header">
        <img class="donate-coin-icon" src="${info.icon}" alt="" aria-hidden="true" />
        <span class="donate-coin-name">${escapeHtml(info.name)}</span>
      </div>
      <img class="donate-qr" src="${info.qr}" alt="${escapeHtml(info.name)} donation QR" />
      <input class="input donate-address" type="text" value="${address}" readonly />
    </div>
  `;
}

function openDonateModal() {
  const columns = [DONATE_WALLETS.btc, DONATE_WALLETS.eth, DONATE_WALLETS.xmr]
    .map(renderDonateColumn)
    .join('');
  openModal('Donate', `
    <div class="donate-grid">
      ${columns}
    </div>
  `);
}
if (donateBtnEl) donateBtnEl.addEventListener('click', openDonateModal);

let modalKeydownHandler = null;

function setModalKeydownHandler(handler) {
  try {
    if (modalKeydownHandler) window.removeEventListener('keydown', modalKeydownHandler);
  } catch { /* ignore */ }

  modalKeydownHandler = (typeof handler === 'function') ? handler : null;

  try {
    if (modalKeydownHandler) window.addEventListener('keydown', modalKeydownHandler);
  } catch { /* ignore */ }
}

function closeModal() {
  // Clear modal-scoped hotkeys (e.g. recording player shortcuts).
  try { setModalKeydownHandler(null); } catch { /* ignore */ }

  // Important: dispose Video.js players before removing their DOM nodes.
  // Otherwise, reopening a recording can fail because Video.js still has a cached player for the same id.
  try {
    const vjs = window.videojs;
    if (modalBodyEl) {
      const vids = Array.from(modalBodyEl.querySelectorAll('video'));
      for (const v of vids) {
        const id = v.getAttribute('id') || '';
        // Dispose Video.js player if present.
        try {
          if (vjs && typeof vjs.getPlayer === 'function' && id) {
            const p = vjs.getPlayer(id);
            if (p) {
              try { p.pause(); } catch { /* ignore */ }
              try { p.dispose(); } catch { /* ignore */ }
              continue;
            }
          }
        } catch { /* ignore */ }

        // Fallback: stop HTML5 playback.
        try { v.pause(); } catch { /* ignore */ }
        try {
          v.removeAttribute('src');
          // Remove nested <source> tags too.
          Array.from(v.querySelectorAll('source')).forEach(s => s.remove());
          v.load();
        } catch { /* ignore */ }
      }
    }
  } catch { /* ignore */ }

  // Stop any background polling associated with modal flows.
  try {
    const wasReencode = (modalTitleEl && modalTitleEl.textContent === 'Transcode');

    // Important: only touch reencode state when closing the Transcode dialog.
    // Closing other modals (e.g. recording player/details) must not wipe transcode progress.
    if (wasReencode) {
      const clearSelectionAfterCancel = state.reencode && state.reencode.wasCanceled;

      // If a transcode job is running and the user closes the dialog without cancelling,
      // keep polling in the background and show the floating progress panel.
      if (state.reencode && state.reencode.jobId && !state.reencode.wasCanceled) {
        const j = state.reencode.lastJob;
        const terminal = j ? isTerminalReencodeStatus(j.status) : false;
        if (!terminal) {
          state.reencode.modalDismissed = true;
          // Keep polling alive.
          ensureReencodePolling();
        }
      }

      // If the reencode flow is still active (job running), do not clear state/timer.
      // Otherwise, fully reset it.
      const keepReencode = state.reencode && state.reencode.jobId && !state.reencode.wasCanceled && (!state.reencode.lastJob || !isTerminalReencodeStatus(state.reencode.lastJob.status));
      if (!keepReencode) {
        stopReencodePolling();
        if (state.reencode) state.reencode.jobId = null;
        if (state.reencode) {
          state.reencode.active = false;
          state.reencode.wasCanceled = false;
          state.reencode.recordingIds = [];
          state.reencode.lastJob = null;
          state.reencode.lastOptions = null;
          state.reencode.modalDismissed = false;
          state.reencode.refreshedAfterDeleteOriginal = false;
          state.reencode.terminalToastShown = false;
        }
      }

      if (clearSelectionAfterCancel) {
        state.selected.clear();
        // Refresh toolbar/selection UI.
        render().catch(e => showToast(e.message));
      }
    }
  } catch { /* ignore */ }

  modalEl.hidden = true;
  if (modalPanelEl) modalPanelEl.classList.remove('modal-panel-image');
  modalTitleEl.textContent = '';
  modalBodyEl.innerHTML = '';

  // After closing, decide whether the floating panel should be shown.
  try { updateReencodeFloatingVisibility(); } catch { /* ignore */ }
  try { updateMoveCopyFloatingVisibility(); } catch { /* ignore */ }
}

async function openMoveCopyDialog({ mode, ids, onSuccess }) {
  const modeNorm = String(mode || '').toLowerCase() === 'copy' ? 'copy' : 'move';
  const count = Array.isArray(ids) ? ids.length : 0;
  if (!count) return;

  const title = modeNorm === 'copy' ? 'Copy recordings' : 'Move recordings';
  openModal(title, `
    <div class="panel">
      <div class="subtle">Destination folder (any folder on this computer)</div>
      <div style="display:flex; gap:10px; margin-top:8px; align-items:center">
        <input id="mcDestPath" class="input" style="flex:1" placeholder="C:\\Path\\To\\Folder" />
        <button id="mcBrowse" class="btn" type="button">Browse…</button>
      </div>
      <label class="checkbox" style="margin-top:12px; display:flex; align-items:flex-start; gap:10px">
        <input id="mcPreserveAncestors" type="checkbox" />
        <span>
          Preserve folder structure
        </span>
      </label>
      <div class="subtle" style="margin-top:10px">
        ${modeNorm === 'move'
          ? 'Note: moving to a folder outside the catalog will remove these recordings from the catalog.'
          : 'Copy keeps the original recordings in the catalog.'}
      </div>
      <div id="mcStatus" class="subtle" style="margin-top:10px; display:flex; align-items:center; gap:8px" hidden>
        <span class="spinner" aria-hidden="true"></span>
        <span id="mcStatusText"></span>
      </div>
      <div style="display:flex; gap:10px; justify-content:flex-end; margin-top:14px">
        <button id="mcCancel" class="btn" type="button">Cancel</button>
        <button id="mcBackground" class="btn btn-ghost" type="button" hidden>Background</button>
        <button id="mcOk" class="btn btn-primary" type="button">OK</button>
      </div>
    </div>
  `);

  const inputEl = document.getElementById('mcDestPath');
  const browseEl = document.getElementById('mcBrowse');
  const preserveEl = document.getElementById('mcPreserveAncestors');
  const cancelEl = document.getElementById('mcCancel');
  const backgroundEl = document.getElementById('mcBackground');
  const okEl = document.getElementById('mcOk');
  const statusEl = document.getElementById('mcStatus');
  const statusTextEl = document.getElementById('mcStatusText');

  if (cancelEl) cancelEl.onclick = () => closeModal();

  if (browseEl) {
    browseEl.onclick = async () => {
      if (browseEl.disabled) return;
      const prevText = browseEl.textContent;
      browseEl.disabled = true;
      browseEl.textContent = 'Browsing…';
      try {
        const res = await fetch('/api/dialog/select-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: 'Select destination folder' }),
        });
        const json = await res.json();
        if (!res.ok) {
          if (res.status === 409) throw new Error('Folder dialog already open');
          throw new Error(json?.detail || 'Failed to open dialog');
        }
        if (json.path && inputEl) inputEl.value = json.path;
      } catch (e) {
        showToast(e.message || String(e));
      } finally {
        browseEl.disabled = false;
        browseEl.textContent = prevText;
      }
    };
  }

  if (okEl) {
    okEl.onclick = async () => {
      const destinationPath = (inputEl ? inputEl.value : '').trim();
      if (!destinationPath) return showToast('Destination folder is required');

      const preserveAncestors = !!(preserveEl && preserveEl.checked);

      const prevOkText = okEl.textContent;
      okEl.disabled = true;
      okEl.textContent = 'Working...';
      if (browseEl) browseEl.disabled = true;
      if (cancelEl) cancelEl.disabled = true;
      if (backgroundEl) backgroundEl.hidden = false;
      if (statusEl) {
        if (statusTextEl) statusTextEl.textContent = 'Working... This may take a while for large files.';
        statusEl.hidden = false;
      }

      state.moveCopy.active = true;
      state.moveCopy.mode = modeNorm;
      state.moveCopy.count = count;
      state.moveCopy.destinationPath = destinationPath;
      updateMoveCopyFloatingContent();
      updateMoveCopyFloatingVisibility();

      if (backgroundEl) {
        backgroundEl.onclick = () => {
          closeModal();
        };
      }
      try {
        const res = await gql(
          `mutation($ids:[Int!]!, $destinationPath:String!, $mode:String!, $preserveAncestors:Boolean!){ moveOrCopyRecordingsToPath(ids:$ids, destinationPath:$destinationPath, mode:$mode, preserveAncestors:$preserveAncestors){ ok message } }`,
          { ids, destinationPath, mode: modeNorm, preserveAncestors }
        );
        const r = res.moveOrCopyRecordingsToPath;
        if (r && r.ok === false) {
          showToast(r.message || 'Operation failed');
          okEl.disabled = false;
          okEl.textContent = prevOkText || 'OK';
          if (browseEl) browseEl.disabled = false;
          if (cancelEl) cancelEl.disabled = false;
          if (backgroundEl) backgroundEl.hidden = true;
          if (statusEl) statusEl.hidden = true;
          state.moveCopy.active = false;
          updateMoveCopyFloatingVisibility();
          return;
        }
        showToast(r?.message || 'Done');
        closeModal();

        state.moveCopy.active = false;
        updateMoveCopyFloatingVisibility();

        try {
          if (typeof onSuccess === 'function') await onSuccess({ mode: modeNorm, ids, destinationPath, preserveAncestors, result: r });
        } catch (e) {
          showToast(e?.message || String(e));
        }
      } catch (e) {
        showToast(e.message || String(e));
        okEl.disabled = false;
        okEl.textContent = prevOkText || 'OK';
        if (browseEl) browseEl.disabled = false;
        if (cancelEl) cancelEl.disabled = false;
        if (backgroundEl) backgroundEl.hidden = true;
        if (statusEl) statusEl.hidden = true;
        state.moveCopy.active = false;
        updateMoveCopyFloatingVisibility();
      }
    };
  }
}

function nextFrame() {
  return new Promise(resolve => requestAnimationFrame(resolve));
}

async function renderPreserveScroll() {
  const y = window.scrollY || 0;
  const r0 = route();
  await render();
  const r1 = route();
  if (r0.name !== r1.name || r0.arg !== r1.arg) return;
  await nextFrame();
  await nextFrame();
  window.scrollTo(0, y);
}

async function openTagPicker(recordingIds, initialSelectedTagNames = null) {
  const ids = Array.isArray(recordingIds) ? recordingIds : [];
  if (!ids.length) return showToast('No recordings selected');

  let tags = [];
  try {
    const data = await gql(`query { tags { id name } }`);
    tags = data.tags || [];
  } catch (e) {
    return showToast(e.message);
  }

  // Preload existing tags for the selected recordings so pills can show active state.
  // For multiple recordings, active means "present on all selected" (intersection).
  let selected = new Set();
  let mixed = new Set();
  try {
    const results = await Promise.all(
      ids.map(async (id) => {
        try {
          const r = await gql(`query($id:Int!){ recording(id:$id){ id tags { name } } }`, { id: Number(id) });
          return r?.recording?.tags?.map(t => String(t?.name || '').trim()).filter(Boolean) || [];
        } catch {
          return [];
        }
      })
    );

    const sets = results.map(arr => new Set(arr));
    const union = new Set();
    sets.forEach(s => s.forEach(x => union.add(x)));
    const intersection = new Set(union);
    sets.forEach(s => {
      for (const x of Array.from(intersection)) {
        if (!s.has(x)) intersection.delete(x);
      }
    });
    selected = new Set(intersection);
    mixed = new Set(Array.from(union).filter(x => !intersection.has(x)));
  } catch {
    selected = new Set();
    mixed = new Set();
  }

  // If the caller provided a selection (e.g. after creating a new tag), honor it.
  if (Array.isArray(initialSelectedTagNames)) {
    selected = new Set(initialSelectedTagNames.map(x => String(x || '').trim()).filter(Boolean));
  }

  const pills = tags
    .slice()
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    .map(t => {
      const name = String(t?.name || '').trim();
      if (!name) return '';
      const isActive = selected.has(name);
      const isMixed = !isActive && mixed.has(name);
      return `<button class="alpha-btn ${isActive ? 'active' : ''} ${isMixed ? 'mixed' : ''}" type="button" data-tag="${escapeHtml(name)}">${escapeHtml(name)}</button>`;
    })
    .filter(Boolean)
    .join('');

  openModal('Assign tags', `
    <div class="list">
      <div class="list-item">
        <div class="subtle">Click pills to toggle tags. Active = applied to all selected recordings.</div>
        <div id="tagPills" class="alpha-bar" style="margin-top:10px; max-height:280px; overflow:auto">
          ${pills || '<div class="subtle">No tags defined yet.</div>'}
        </div>
      </div>

      <div class="list-item">
        <div class="subtle">Create a new tag</div>
        <div style="display:flex; gap:10px; margin-top:10px">
          <input id="newTagName" class="input" style="width:100%" placeholder="e.g. Funny" />
          <button id="createTagBtn" class="btn">Create</button>
        </div>
        <div class="subtle" style="margin-top:10px">Then select it above and click Apply.</div>
      </div>

      <div style="display:flex; gap:10px; justify-content:flex-end">
        <button id="applyTagsBtn" class="btn btn-primary">Apply</button>
      </div>
    </div>
  `);

  function wireTagPillButton(btn) {
    if (!btn) return;
    btn.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const name = String(btn.dataset.tag || '').trim();
      if (!name) return;
      if (selected.has(name)) {
        selected.delete(name);
        btn.classList.remove('active');
      } else {
        selected.add(name);
        btn.classList.add('active');
      }
      btn.classList.remove('mixed');
    };
  }

  modalBodyEl.querySelectorAll('button[data-tag]').forEach(wireTagPillButton);

  document.getElementById('createTagBtn').onclick = async () => {
    const name = document.getElementById('newTagName').value.trim();
    if (!name) return showToast('Tag name is required');
    try {
      const resp = await gql(`mutation($name:String!){ createTag(name:$name){ id name } }`, { name });
      const createdName = String(resp?.createTag?.name || name).trim();
      if (!createdName) throw new Error('Failed to create tag');

      // Ensure the new tag appears as a selectable pill immediately.
      const pillsEl = document.getElementById('tagPills');
      if (pillsEl) {
        // If the list was previously empty, clear the placeholder message.
        if (pillsEl.querySelector('.subtle')) {
          pillsEl.innerHTML = '';
        }

        let existingBtn = null;
        pillsEl.querySelectorAll('button[data-tag]').forEach((b) => {
          const n = String(b.dataset.tag || '').trim();
          if (n && n.toLowerCase() === createdName.toLowerCase()) existingBtn = b;
        });

        if (!existingBtn) {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'alpha-btn';
          btn.dataset.tag = createdName;
          btn.textContent = createdName;
          pillsEl.appendChild(btn);
          existingBtn = btn;
        }

        // Mark it active in the selection.
        selected.add(createdName);
        existingBtn.classList.add('active');
        existingBtn.classList.remove('mixed');
        wireTagPillButton(existingBtn);
      }

      showToast('Tag created');
      document.getElementById('newTagName').value = '';
    } catch (e) {
      showToast(e.message);
    }
  };

  document.getElementById('applyTagsBtn').onclick = async () => {
    try {
      const res = await gql(
        `mutation($recordingIds:[Int!]!, $tagNames:[String!]!){ assignTags(recordingIds:$recordingIds, tagNames:$tagNames){ ok message } }`,
        { recordingIds: ids, tagNames: Array.from(selected) }
      );
      showToast(res.assignTags.message);

      // Update recording cards immediately (if present in current DOM).
      try {
        const names = Array.from(selected).map(x => String(x || '').trim()).filter(Boolean);
        const pills = names.map(n => `<span class="badge tag">${escapeHtml(n)}</span>`).join('');
        ids.forEach((rid) => {
          document.querySelectorAll(`[data-tags-line="${Number(rid)}"]`).forEach((el) => {
            el.innerHTML = pills;
          });
        });
      } catch { /* ignore */ }

      closeModal();
      await renderPreserveScroll();
    } catch (e) {
      showToast(e.message);
    }
  };
}

async function openSitePicker(recordingIds) {
  const ids = Array.isArray(recordingIds) ? recordingIds : [];
  if (!ids.length) return showToast('No recordings selected');

  let sites = [];
  try {
    const data = await gql(`query { siteOverviews { id name } }`);
    sites = data.siteOverviews || [];
  } catch (e) {
    return showToast(e.message);
  }

  let selectedSite = '';
  const sorted = sites
    .slice()
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  const pills = sorted
    .map(s => {
      const n = String(s?.name || '').trim();
      if (!n) return '';
      return `<button class="alpha-btn" type="button" data-site="${escapeHtml(n)}">${escapeHtml(n)}</button>`;
    })
    .filter(Boolean)
    .join('');

  openModal('Assign site', `
    <div class="list">
      <div class="list-item">
        <div class="subtle">Select an existing site (or type a new one below).</div>
        <div class="alpha-bar" style="margin-top:10px; max-height:280px; overflow:auto">
          ${pills || '<div class="subtle">No sites defined yet.</div>'}
        </div>
      </div>

      <div class="list-item">
        <div class="subtle">Create a new site</div>
        <div style="display:flex; gap:10px; margin-top:10px">
          <input id="newSiteName" class="input" style="width:100%" placeholder="e.g. Chaturbate" />
        </div>
        <div class="subtle" style="margin-top:10px">Then click Apply.</div>
      </div>

      <div style="display:flex; gap:10px; justify-content:flex-end">
        <button id="applySiteBtn" class="btn btn-primary">Apply</button>
      </div>
    </div>
  `);

  modalBodyEl.querySelectorAll('button[data-site]').forEach(b => {
    b.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      selectedSite = String(b.dataset.site || '').trim();
      modalBodyEl.querySelectorAll('button[data-site]').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
    };
  });

  document.getElementById('applySiteBtn').onclick = async () => {
    const typed = (document.getElementById('newSiteName')?.value || '').trim();
    const siteName = typed || selectedSite || '';
    if (!siteName) return showToast('Select or type a site name');

    try {
      const res = await gql(
        `mutation($recordingIds:[Int!]!, $siteName:String!){ assignSite(recordingIds:$recordingIds, siteName:$siteName){ ok message } }`,
        { recordingIds: ids, siteName }
      );
      showToast(res.assignSite.message);
      closeModal();
      await render();
    } catch (e) {
      showToast(e.message);
    }
  };
}

async function openStreamerSitePickerById(streamerId, streamerName, currentSiteName) {
  const id = Number(streamerId || 0);
  if (!id) return showToast('Streamer id is required');

  const name = String(streamerName || '').trim();
  if (!name) return showToast('Streamer name is required');

  let selectedSite = String(currentSiteName || '').trim();
  if (selectedSite.toLowerCase() === 'unknown') selectedSite = '';

  let sites = [];
  try {
    const data = await gql(`query { siteOverviews { id name } }`);
    sites = data.siteOverviews || [];
  } catch (e) {
    return showToast(e.message);
  }

  const sorted = sites
    .slice()
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  const pills = sorted
    .map(s => {
      const n = String(s?.name || '').trim();
      if (!n) return '';
      const isActive = selectedSite && (n.toLowerCase() === selectedSite.toLowerCase());
      return `<button class="alpha-btn ${isActive ? 'active' : ''}" type="button" data-site="${escapeHtml(n)}">${escapeHtml(n)}</button>`;
    })
    .filter(Boolean)
    .join('');

  openModal('Assign site', `
    <div class="list">
      <div class="list-item">
        <div class="subtle">Set site for <b>${escapeHtml(name)}</b>. This will also update the site for all their recordings.</div>
        <div class="alpha-bar" style="margin-top:10px; max-height:280px; overflow:auto">
          ${pills || '<div class="subtle">No sites defined yet.</div>'}
        </div>
      </div>

      <div class="list-item">
        <div class="subtle">Create a new site</div>
        <div style="display:flex; gap:10px; margin-top:10px">
          <input id="newSiteName" class="input" style="width:100%" placeholder="e.g. Chaturbate" />
        </div>
        <div class="subtle" style="margin-top:10px">Then click Apply.</div>
      </div>

      <div style="display:flex; gap:10px; justify-content:flex-end">
        <button id="applyStreamerSiteBtn" class="btn btn-primary">Apply</button>
      </div>
    </div>
  `);

  modalBodyEl.querySelectorAll('button[data-site]').forEach(b => {
    b.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      selectedSite = String(b.dataset.site || '').trim();
      modalBodyEl.querySelectorAll('button[data-site]').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
    };
  });

  document.getElementById('applyStreamerSiteBtn').onclick = async () => {
    const typed = (document.getElementById('newSiteName')?.value || '').trim();
    const siteName = typed || selectedSite || '';
    if (!siteName) return showToast('Select or type a site name');

    try {
      const res = await gql(
        `mutation($streamerId:Int!, $siteName:String!){ setStreamerSite(streamerId:$streamerId, siteName:$siteName){ ok message } }`,
        { streamerId: id, siteName }
      );
      showToast(res.setStreamerSite.message);
      closeModal();
      await render();
    } catch (e) {
      showToast(e.message);
    }
  };
}

async function openStreamerSitePicker(streamerName) {
  const name = String(streamerName || '').trim();
  if (!name) return showToast('Streamer name is required');

  let sites = [];
  try {
    const data = await gql(`query { siteOverviews { id name } }`);
    sites = data.siteOverviews || [];
  } catch (e) {
    return showToast(e.message);
  }

  let selectedSite = '';
  const sorted = sites
    .slice()
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  const pills = sorted
    .map(s => {
      const n = String(s?.name || '').trim();
      if (!n) return '';
      return `<button class="alpha-btn" type="button" data-site="${escapeHtml(n)}">${escapeHtml(n)}</button>`;
    })
    .filter(Boolean)
    .join('');

  openModal('Assign site', `
    <div class="list">
      <div class="list-item">
        <div class="subtle">Set site for <b>${escapeHtml(name)}</b>. This will also update the site for all their recordings.</div>
        <div class="alpha-bar" style="margin-top:10px; max-height:280px; overflow:auto">
          ${pills || '<div class="subtle">No sites defined yet.</div>'}
        </div>
      </div>

      <div class="list-item">
        <div class="subtle">Create a new site</div>
        <div style="display:flex; gap:10px; margin-top:10px">
          <input id="newSiteName" class="input" style="width:100%" placeholder="e.g. Chaturbate" />
        </div>
        <div class="subtle" style="margin-top:10px">Then click Apply.</div>
      </div>

      <div style="display:flex; gap:10px; justify-content:flex-end">
        <button id="applyStreamerSiteBtn" class="btn btn-primary">Apply</button>
      </div>
    </div>
  `);

  modalBodyEl.querySelectorAll('button[data-site]').forEach(b => {
    b.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      selectedSite = String(b.dataset.site || '').trim();
      modalBodyEl.querySelectorAll('button[data-site]').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
    };
  });

  document.getElementById('applyStreamerSiteBtn').onclick = async () => {
    const typed = (document.getElementById('newSiteName')?.value || '').trim();
    const siteName = typed || selectedSite || '';
    if (!siteName) return showToast('Select or type a site name');

    try {
      const res = await gql(
        `mutation($streamerName:String!, $siteName:String!){ assignStreamerSite(streamerName:$streamerName, siteName:$siteName){ ok message } }`,
        { streamerName: name, siteName }
      );
      showToast(res.assignStreamerSite.message);
      closeModal();
      await render();
    } catch (e) {
      showToast(e.message);
    }
  };
}

async function openReencodeModal(recordingIds, opts = {}) {
  const ids = Array.isArray(recordingIds) ? recordingIds : [];
  if (!ids.length) return showToast('No recordings selected');

  const restore = !!opts.restore;

  state.reencode.active = true;
  if (!restore) state.reencode.wasCanceled = false;
  state.reencode.recordingIds = ids.slice();
  state.reencode.modalDismissed = false;

  openModal('Transcode', `
    <div class="list">
      <div class="list-item">
        <div class="subtle">Transcode ${ids.length} recording(s) using ffmpeg.</div>
        <div class="subtle" style="margin-top:6px">Output: saved in the same folder as the original.</div>
        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; margin-top:12px">
          <label class="subtle">Container
            <select id="reenc-container" class="input">
              <option value="mp4">mp4</option>
              <option value="mkv">mkv</option>
              <option value="webm">webm</option>
              <option value="avi">avi</option>
            </select>
          </label>

          <label class="subtle">Video codec
            <select id="reenc-vcodec" class="input">
              <option value="h264" selected>h264</option>
              <option value="hevc">hevc (h265)</option>
              <option value="vp9">vp9</option>
              <option value="av1">av1</option>
              <option value="theora">theora</option>
              <option value="copy">copy</option>
            </select>
          </label>

          <label class="subtle">Audio codec
            <select id="reenc-acodec" class="input">
              <option value="aac" selected>aac</option>
              <option value="mp3">mp3</option>
              <option value="opus">opus</option>
              <option value="flac">flac</option>
              <option value="copy">copy</option>
              <option value="none">none</option>
            </select>
          </label>

          <label class="subtle">Quality
            <select id="reenc-quality" class="input">
              <option value="small">small</option>
              <option value="balanced" selected>balanced</option>
              <option value="high">high</option>
              <option value="lossless">lossless</option>
            </select>
          </label>

          <label class="subtle">Speed/preset
            <select id="reenc-speed" class="input">
              <option value="ultrafast">ultrafast</option>
              <option value="superfast">superfast</option>
              <option value="veryfast" selected>veryfast</option>
              <option value="faster">faster</option>
              <option value="fast">fast</option>
              <option value="medium">medium</option>
              <option value="slow">slow</option>
              <option value="slower">slower</option>
              <option value="veryslow">veryslow</option>
            </select>
          </label>

          <label class="subtle">Audio bitrate (optional)
            <input id="reenc-abitrate" class="input" style="width:50%" placeholder="e.g. 160k" />
          </label>
        </div>

        <label class="subtle" style="display:flex; align-items:center; gap:10px; margin-top:12px">
          <input id="reenc-deleteOriginal" type="checkbox" />
          Delete original on success (keeps only the transcoded file)
        </label>

        <label class="subtle" style="margin-top:12px; display:block">Extra ffmpeg args (appended last)
          <input id="reenc-extra" class="input" style="width:100%; margin-top:8px" placeholder="e.g. -vf scale=1280:-2" />
        </label>
      </div>

      <div id="reencProgressWrap" class="progress-wrap" style="margin-top:12px" hidden>
        <div class="progress-row">
          <div class="progress"><div id="reencBar" class="progress-bar" style="width:0%"></div></div>
          <div id="reencPct" class="subtle" style="min-width:62px; text-align:right">0%</div>
        </div>
        <div id="reencStatus" class="subtle" style="margin-top:6px"></div>
        <div id="reencList" class="job-list" style="margin-top:10px; display:grid; gap:8px"></div>
      </div>

      <div style="display:flex; gap:10px; justify-content:flex-end">
        <button id="reencCancelBtn" class="btn" disabled>Cancel</button>
        <button id="reencStartBtn" class="btn btn-primary">Start</button>
      </div>
    </div>
  `);

  const startBtn = document.getElementById('reencStartBtn');
  const cancelBtn = document.getElementById('reencCancelBtn');
  const wrap = document.getElementById('reencProgressWrap');

  function setInputsDisabled(disabled) {
    const idsToDisable = [
      'reenc-container',
      'reenc-quality',
      'reenc-vcodec',
      'reenc-speed',
      'reenc-acodec',
      'reenc-abitrate',
      'reenc-extra',
      'reenc-deleteOriginal',
    ];
    for (const id of idsToDisable) {
      const el = document.getElementById(id);
      if (el) el.disabled = !!disabled;
    }
  }

  function applyOptionsToForm(options, disabled) {
    const o = options || {};
    const setVal = (id, v) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = (v == null) ? '' : String(v);
    };
    setVal('reenc-container', o.container);
    setVal('reenc-quality', o.quality);
    setVal('reenc-vcodec', o.videoCodec);
    setVal('reenc-speed', o.speed);
    setVal('reenc-acodec', o.audioCodec);
    setVal('reenc-abitrate', o.audioBitrate);
    setVal('reenc-extra', o.extraFfmpegArgs);
    const del = document.getElementById('reenc-deleteOriginal');
    if (del) del.checked = !!o.deleteOriginal;
    setInputsDisabled(!!disabled);
  }

  function readOptions() {
    const container = (document.getElementById('reenc-container')?.value || 'mp4').trim();
    const quality = (document.getElementById('reenc-quality')?.value || 'balanced').trim();
    const videoCodec = (document.getElementById('reenc-vcodec')?.value || 'h264').trim();
    const speed = (document.getElementById('reenc-speed')?.value || 'veryfast').trim();
    const audioCodec = (document.getElementById('reenc-acodec')?.value || 'aac').trim();
    const audioBitrate = (document.getElementById('reenc-abitrate')?.value || '').trim() || null;
    const extraFfmpegArgs = (document.getElementById('reenc-extra')?.value || '').trim() || null;
    const deleteOriginal = !!document.getElementById('reenc-deleteOriginal')?.checked;
    return {
      container,
      quality,
      videoCodec,
      speed,
      audioCodec,
      audioBitrate,
      extraFfmpegArgs,
      deleteOriginal,
    };
  }

  function setBusy(isBusy) {
    if (startBtn) startBtn.disabled = !!isBusy;
  }

  async function startJob() {
    setBusy(true);
    if (cancelBtn) cancelBtn.disabled = false;

    const opt = readOptions();
    const options = {
      container: opt.container,
      quality: opt.quality,
      videoCodec: opt.videoCodec,
      speed: opt.speed,
      audioCodec: opt.audioCodec,
      audioBitrate: opt.audioBitrate,
      extraFfmpegArgs: opt.extraFfmpegArgs,
      deleteOriginal: opt.deleteOriginal,
    };

    state.reencode.deleteOriginal = !!opt.deleteOriginal;

    const res = await gql(
      `mutation($ids:[Int!]!, $options:ReencodeOptionsInput!){ startReencodeRecordings(ids:$ids, options:$options){ ok message job { jobId status percent } } }`,
      { ids, options }
    );

    const r = res.startReencodeRecordings;
    if (!r || !r.ok || !r.job?.jobId) {
      throw new Error(r?.message || 'Failed to start transcode job');
    }

    const jobId = r.job.jobId;
    state.reencode.jobId = jobId;
    state.reencode.lastOptions = options;
    state.reencode.refreshedAfterDeleteOriginal = false;
    state.reencode.terminalToastShown = false;

    await refreshReencodeJob(jobId);
    ensureReencodePolling();
  }

  if (startBtn) startBtn.onclick = async () => {
    try {
      await startJob();
    } catch (e) {
      setBusy(false);
      showToast(e.message);
    }
  };

  if (cancelBtn) cancelBtn.onclick = async () => {
    const jobId = state.reencode.jobId;
    if (!jobId) return;
    cancelBtn.disabled = true;
    try {
      const res = await gql(`mutation($jobId:String!){ cancelReencode(jobId:$jobId){ ok message } }`, { jobId });
      if (res.cancelReencode && res.cancelReencode.ok) state.reencode.wasCanceled = true;
      showToast(res.cancelReencode.message);
    } catch (e) {
      showToast(e.message);
      cancelBtn.disabled = false;
    }
  };

  // Restore mode / job already running: show progress and disable option editing.
  const hasJob = !!state.reencode.jobId;
  if (hasJob) {
    if (startBtn) startBtn.disabled = true;
    // If we have remembered options, display them as read-only for context.
    if (state.reencode.lastOptions) applyOptionsToForm(state.reencode.lastOptions, true);
    else setInputsDisabled(true);
    if (wrap) wrap.hidden = false;

    try {
      await refreshReencodeJob(state.reencode.jobId);
    } catch (e) {
      showToast(e.message);
    }
    ensureReencodePolling();
  } else {
    // New job: if we have stored options from a previous run, prefill (editable).
    if (state.reencode.lastOptions) applyOptionsToForm(state.reencode.lastOptions, false);
  }

  // When modal is visible, do not show floating panel.
  try { updateReencodeFloatingVisibility(); } catch { /* ignore */ }
}

modalEl.addEventListener('click', (e) => {
  const t = e.target;
  if (t && t.dataset && t.dataset.action === 'close') closeModal();
});

window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !modalEl.hidden) closeModal();
});

// Defensive: some embedded webviews can behave oddly with initial `hidden`.
closeModal();

function route() {
  const hash = (location.hash || '#recordings').slice(1);
  const [name, arg] = hash.split(':');
  return { name, arg };
}

async function render() {
  const r = route();
  setActiveNav(r.name);
  setSettingsBusy(!!state.scan.jobId);
  state.selected.clear();
  state.scan.refreshList = null;

  if (r.name === 'recordings') return renderRecordings();
  if (r.name === 'streamers') return renderStreamers(r.arg);
  if (r.name === 'sites') return renderSites();
  if (r.name === 'tags') return renderTags();
  if (r.name === 'settings') return renderSettings();
  if (r.name === 'streamer') return renderStreamerDetail(r.arg);
  if (r.name === 'site') return renderSite(r.arg);
  if (r.name === 'tag') return renderTag(r.arg);

  location.hash = '#recordings';
}

async function renderSettings() {
  const DEFAULT_FILENAME_TEMPLATE = '{streamer}_{site}_{YYYYMMDD}_{HHMMSS}{wildcard}.{ext}';
  const data = await gql(`query { appConfig { catalogRoot filenameRegex catalogFolders { key path filenameRegex filenameTemplate matchPath } generateLivePreviews livePreviewSegments periodicScanEnabled periodicScanIntervalMinutes autoTagAfterScan autoTagConfidence autoTagSceneThreshold autoTagFrameInterval backendHost backendPort authRequired authUsername authPassword } }`);
  const cfg = data.appConfig;

  let recordingsTotal = 0;
  if (cfg?.catalogRoot) {
    try {
      const r = await gql(`query { recordings(limit:1, offset:0){ total } }`);
      recordingsTotal = Number(r?.recordings?.total || 0);
    } catch {
      recordingsTotal = 0;
    }
  }
  const folders = Array.isArray(cfg.catalogFolders) && cfg.catalogFolders.length
    ? cfg.catalogFolders
    : (cfg.catalogRoot ? [{ key: null, path: cfg.catalogRoot, filenameRegex: cfg.filenameRegex }] : []);

  viewEl.innerHTML = `
    <div class="page-title">
      <div>
        <div class="h1">Settings</div>
        <div class="subtle">Configure your catalog and network options.</div>
      </div>
    </div>

    <div class="list">
      <div class="list-item">
        <div class="settings-tabwidget">
          <div class="settings-tabbar-wrap">
            <div class="settings-tabs" role="tablist" aria-label="Settings sections">
              <button id="settingsTabCatalog" class="settings-tab is-active" type="button" role="tab" aria-selected="true" aria-controls="settingsPanelCatalog" tabindex="0" data-settings-tab="catalog">Catalog</button>
              <button id="settingsTabNetwork" class="settings-tab" type="button" role="tab" aria-selected="false" aria-controls="settingsPanelNetwork" tabindex="-1" data-settings-tab="network">Network</button>
            </div>
          </div>

          <div class="settings-tabpanel panel">
          <section id="settingsPanelCatalog" class="settings-panel is-active" role="tabpanel" aria-labelledby="settingsTabCatalog" data-settings-panel="catalog">
            <div class="settings-panel-summary">
              <div class="settings-panel-eyebrow">Catalog</div>
              <div class="settings-panel-title">Library structure and scan rules</div>
              <div class="settings-panel-copy">Manage the folders that belong to the catalog, define filename parsing patterns, and choose how animated previews are generated after each scan.</div>
            </div>

            <div class="panel settings-section-panel">
              <div class="subtle">Catalog folders</div>
              <div id="catalogFolders" style="margin-top:8px; display:flex; flex-direction:column; gap:10px">
                ${folders.map((f, idx) => `
                  <div class="panel" style="padding:10px; border:1px solid var(--border); border-radius:12px">
                    <div class="subtle">Folder ${idx + 1}${idx === 0 ? ' (primary)' : ''}</div>
                    <div style="display:flex; gap:10px; margin-top:8px">
                      <input class="input" data-folder-path="${idx}" data-folder-key="${f.key ?? ''}" style="width:100%" placeholder="D:\\Videos\\Catalog" value="${escapeHtml(f.path ?? '')}" />
                      <button class="btn" data-folder-browse="${idx}">Browse…</button>
                      <button class="btn btn-danger" data-folder-remove="${idx}" ${idx===0 ? 'disabled title="Primary folder cannot be removed"' : ''}>Remove</button>
                    </div>
                    <div style="display:flex; align-items:center; gap:12px; margin-top:10px; flex-wrap:wrap">
                      <div class="subtle">Filename pattern template</div>
                      <label class="checkbox" style="margin:0">
                        <input type="checkbox" data-folder-match-path="${idx}" ${f.matchPath ? 'checked' : ''} />
                        Match against relative path (instead of filename)
                      </label>
                    </div>

                    <input class="input" data-folder-template="${idx}" style="width:100%; margin-top:8px" placeholder="${escapeHtml(DEFAULT_FILENAME_TEMPLATE)}" value="${escapeHtml(f.filenameTemplate || '')}" />

                    <div class="pattern-pill-row" style="margin-top:8px">
                      ${[
                        '{streamer}',
                        '{site}',
                        '{YYYY-MM-DD}',
                        '{YYYYMMDD}',
                        '{YYMMDD}',
                        '{HH_mm}',
                        '{HH-mm-ss}',
                        '{HHmmss}',
                        '{HH-MM-SS}',
                        '{HHMMSS}',
                        '{ext}',
                        '{wildcard}',
                        '{digit}',
                      ].map(tok => `<button class="pattern-pill" type="button" data-template-insert="${idx}" data-template-token="${escapeHtml(tok)}">${escapeHtml(tok)}</button>`).join('')}
                    </div>

                    <details style="margin-top:10px">
                      <summary class="small-link">Advanced: edit regex directly</summary>
                      <div style="margin-top:10px">
                        <div class="subtle">Generated regex (auto)</div>
                        <textarea class="input" data-template-regex="${idx}" rows="2" readonly style="width:100%; margin-top:8px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace"></textarea>
                        <div class="subtle" style="margin-top:8px">Regex (editable). Auto-fills from the template until you edit it.</div>
                        <input class="input" data-folder-regex="${idx}" data-regex-manual="0" style="width:100%; margin-top:8px" value="${escapeHtml(f.filenameRegex ?? cfg.filenameRegex)}" />
                        <div class="subtle" data-regex-manual-hint="${idx}" style="margin-top:6px"></div>
                        <div class="subtle" style="margin-top:8px">Leave template empty to use the regex as-is.</div>

                        <div class="subtle" style="margin-top:12px">Test against a real filename (uses Python's regex engine)</div>
                        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:8px">
                          <input class="input" data-regex-test-input="${idx}" style="flex:1; min-width:320px" placeholder="Paste a filename here (e.g. MyStreamer - 02_06_2019-13_44 1.mp4)" />
                          <button class="btn" type="button" data-regex-test-btn="${idx}">Test</button>
                        </div>
                        <div class="subtle" data-regex-test-out="${idx}" style="margin-top:8px"></div>
                      </div>
                    </details>
                  </div>
                `).join('')}
              </div>

              <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:12px">
                <button id="addCatalogFolder" class="btn">Add folder</button>
              </div>
            </div>

            <div class="panel settings-section-panel">
              <div class="subtle">Animated previews</div>

              <label class="checkbox" style="margin-top:10px">
                <input id="generateLivePreviews" type="checkbox" ${cfg.generateLivePreviews ? 'checked' : ''} />
                Generate animated previews after scan
              </label>

              <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-top:12px">
                <div class="subtle">Preview fragments (1 second each)</div>
                <select id="livePreviewSegments" class="input" style="width:auto; min-width:120px">
                  ${[5,10,15,20].map(n => `<option value="${n}" ${(Number(cfg.livePreviewSegments || 10) === n) ? 'selected' : ''}>${n}</option>`).join('')}
                </select>
              </div>
              <div class="subtle" style="margin-top:8px">More fragments = longer preview generation.</div>
            </div>

            <div class="panel settings-section-panel">
              <div class="subtle">Auto-tagging</div>

              <label class="checkbox" style="margin-top:10px">
                <input id="autoTagAfterScan" type="checkbox" ${cfg.autoTagAfterScan ? 'checked' : ''} />
                Automatically tag recordings after scan
              </label>

              <div style="display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; margin-top:12px">
                <label style="display:flex; flex-direction:column; gap:6px; min-width:160px">
                  <div class="subtle">Minimum confidence (0.1–1.0)</div>
                  <input id="autoTagConfidence" class="input" type="number" min="0.1" max="1.0" step="0.05" value="${escapeHtml(String(cfg.autoTagConfidence ?? 0.6))}" />
                </label>
                <label style="display:flex; flex-direction:column; gap:6px; min-width:160px">
                  <div class="subtle">Scene change threshold (0.0–1.0)</div>
                  <input id="autoTagSceneThreshold" class="input" type="number" min="0.0" max="1.0" step="0.05" value="${escapeHtml(String(cfg.autoTagSceneThreshold ?? 0.35))}" />
                </label>
                <label style="display:flex; flex-direction:column; gap:6px; min-width:140px">
                  <div class="subtle">Frame interval (seconds)</div>
                  <select id="autoTagFrameInterval" class="input" style="width:auto; min-width:120px">
                    ${['auto','10','15','30','60'].map(v => `<option value="${v}" ${(String(cfg.autoTagFrameInterval ?? 'auto') === v || (v !== 'auto' && parseFloat(String(cfg.autoTagFrameInterval)) === parseFloat(v))) ? 'selected' : ''}>${v === 'auto' ? 'Auto (scene detection)' : v + 's'}</option>`).join('')}
                  </select>
                </label>
              </div>
              <div class="subtle" style="margin-top:8px">Higher confidence = fewer but more accurate tags. Use "Auto" frame interval for scene-change-based sampling.</div>
            </div>

            <div class="panel settings-section-panel">
              <div class="subtle">Automatic catalog scan</div>

              <label class="checkbox" style="margin-top:10px">
                <input id="periodicScanEnabled" type="checkbox" ${cfg.periodicScanEnabled ? 'checked' : ''} />
                Scan the catalog automatically on a schedule
              </label>

              <div style="display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; margin-top:12px">
                <label style="display:flex; flex-direction:column; gap:6px; min-width:180px">
                  <div class="subtle">Minutes between scans</div>
                  <input id="periodicScanIntervalMinutes" class="input" type="number" min="1" max="1440" step="1" value="${escapeHtml(String(cfg.periodicScanIntervalMinutes || 30))}" />
                </label>
              </div>
              <div class="subtle" style="margin-top:8px">When enabled, Camero starts a new scan only if there is no other scan already running.</div>
            </div>
          </section>

          <section id="settingsPanelNetwork" class="settings-panel" role="tabpanel" aria-labelledby="settingsTabNetwork" data-settings-panel="network" hidden>
            <div class="settings-panel-summary">
              <div class="settings-panel-eyebrow">Network</div>
              <div class="settings-panel-title">Backend binding and remote access</div>
              <div class="settings-panel-copy">Choose the host or IP address and the port where Camero listens. Use this tab when you want local-only access or when you need to expose the UI to other devices on your network.</div>
            </div>

            <div class="panel settings-section-panel">
              <div class="subtle">Backend listener</div>

              <div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:12px; align-items:flex-end">
                <label style="display:flex; flex-direction:column; gap:6px; min-width:260px; flex:2">
                  <div class="subtle">Host / IP</div>
                  <input id="backendHost" class="input" placeholder="127.0.0.1" value="${escapeHtml(cfg.backendHost || '127.0.0.1')}" />
                </label>
                <label style="display:flex; flex-direction:column; gap:6px; min-width:140px; flex:1">
                  <div class="subtle">Port</div>
                  <input id="backendPort" class="input" type="number" min="1" max="65535" step="1" value="${escapeHtml(String(cfg.backendPort || 6969))}" />
                </label>
              </div>
              <div class="subtle" style="margin-top:8px">Use <b>127.0.0.1</b> for local-only access, <b>0.0.0.0</b> to listen on all interfaces, or a specific external/local IP to bind only that address. Changes apply after restarting Camero.</div>
            </div>

            <div class="panel settings-section-panel">
              <div class="subtle">Remote access authentication</div>

              <label class="checkbox" style="margin-top:10px">
                <input id="networkAuthRequired" type="checkbox" ${cfg.authRequired ? 'checked' : ''} />
                Require username and password to access the application
              </label>

              <div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:12px; align-items:flex-end">
                <label style="display:flex; flex-direction:column; gap:6px; min-width:220px; flex:1">
                  <div class="subtle">Username</div>
                  <input id="networkAuthUsername" class="input" autocomplete="username" value="${escapeHtml(cfg.authUsername || '')}" />
                </label>
                <label style="display:flex; flex-direction:column; gap:6px; min-width:220px; flex:1">
                  <div class="subtle">Password</div>
                  <input id="networkAuthPassword" class="input" type="password" autocomplete="current-password" value="${escapeHtml(cfg.authPassword || '')}" />
                </label>
              </div>
              <div class="subtle" style="margin-top:8px">Enable this when exposing Camero on a public or shared network. The username and password fields stay disabled until authentication is turned on.</div>
            </div>
          </section>
          </div>
        </div>

        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:12px">
          <button id="saveSettings" class="btn btn-primary">Save</button>
          <button id="resetSettings" class="btn">Reset</button>
        </div>
      </div>
    </div>
  `;

  function initSettingsTabs() {
    const tabs = Array.from(document.querySelectorAll('[data-settings-tab]'));
    const panels = Array.from(document.querySelectorAll('[data-settings-panel]'));
    if (!tabs.length || !panels.length) return;

    const activateTab = (name, focus = false) => {
      tabs.forEach((tab) => {
        const active = tab.dataset.settingsTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.setAttribute('tabindex', active ? '0' : '-1');
        if (active && focus) tab.focus();
      });
      panels.forEach((panel) => {
        const active = panel.dataset.settingsPanel === name;
        panel.classList.toggle('is-active', active);
        panel.hidden = !active;
      });
    };

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activateTab(tab.dataset.settingsTab || 'catalog'));
      tab.addEventListener('keydown', (ev) => {
        const currentIndex = tabs.indexOf(tab);
        if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
          ev.preventDefault();
          const delta = ev.key === 'ArrowRight' ? 1 : -1;
          const next = tabs[(currentIndex + delta + tabs.length) % tabs.length];
          activateTab(next.dataset.settingsTab || 'catalog', true);
          return;
        }
        if (ev.key === 'Home') {
          ev.preventDefault();
          activateTab(tabs[0].dataset.settingsTab || 'catalog', true);
          return;
        }
        if (ev.key === 'End') {
          ev.preventDefault();
          activateTab(tabs[tabs.length - 1].dataset.settingsTab || 'catalog', true);
          return;
        }
        if ((ev.key === 'Enter' || ev.key === ' ') && tabs[index]) {
          ev.preventDefault();
          activateTab(tab.dataset.settingsTab || 'catalog', true);
        }
      });
    });

    activateTab('catalog');
  }

  initSettingsTabs();

  function syncNetworkAuthInputs() {
    const enabled = !!document.getElementById('networkAuthRequired')?.checked;
    const usernameEl = document.getElementById('networkAuthUsername');
    const passwordEl = document.getElementById('networkAuthPassword');
    if (usernameEl) usernameEl.disabled = !enabled;
    if (passwordEl) passwordEl.disabled = !enabled;
  }

  const authRequiredEl = document.getElementById('networkAuthRequired');
  if (authRequiredEl) authRequiredEl.addEventListener('change', syncNetworkAuthInputs);
  syncNetworkAuthInputs();

  function syncAutoTagInputs() {
    const enabled = !!document.getElementById('autoTagAfterScan')?.checked;
    const confidenceEl = document.getElementById('autoTagConfidence');
    const sceneEl = document.getElementById('autoTagSceneThreshold');
    const intervalEl = document.getElementById('autoTagFrameInterval');
    if (confidenceEl) confidenceEl.disabled = !enabled;
    if (sceneEl) sceneEl.disabled = !enabled;
    if (intervalEl) intervalEl.disabled = !enabled;
  }

  const autoTagAfterScanEl = document.getElementById('autoTagAfterScan');
  if (autoTagAfterScanEl) autoTagAfterScanEl.addEventListener('change', syncAutoTagInputs);
  syncAutoTagInputs();

  function syncPeriodicScanInputs() {
    const enabled = !!document.getElementById('periodicScanEnabled')?.checked;
    const intervalEl = document.getElementById('periodicScanIntervalMinutes');
    if (intervalEl) intervalEl.disabled = !enabled;
  }

  const periodicScanEnabledEl = document.getElementById('periodicScanEnabled');
  if (periodicScanEnabledEl) periodicScanEnabledEl.addEventListener('change', syncPeriodicScanInputs);
  syncPeriodicScanInputs();

  function escapeRegexLiteral(s) {
    return String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function insertAtCursor(input, text) {
    if (!input) return;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    const before = input.value.slice(0, start);
    const after = input.value.slice(end);
    input.value = before + text + after;
    const next = start + text.length;
    try {
      input.focus();
      input.setSelectionRange(next, next);
    } catch { /* ignore */ }
  }

  function buildRegexFromTemplate(template) {
    const src = String(template || '');
    let out = '';
    const usedGroups = new Set();

    function escapeTokenSep(ch) {
      if (ch === '/' || ch === '\\') {
        // Match both separators at runtime.
        return '(?:/|\\\\)';
      }
      return escapeRegexLiteral(ch);
    }

    function parseDateToken(tokRaw) {
      // Accept common date pattern tokens even if not explicitly listed in the UI.
      // Examples: DDMMYYYY, DD-MM-YY, YYYY_MM_DD, YYMMDD
      const tok = String(tokRaw || '').trim();
      if (!tok) return null;

      // Quick filter: only allow Y/M/D (any case) plus separators.
      if (!/^[YyMmDd\-_/\\]+$/.test(tok)) return null;
      const hasY = /[Yy]/.test(tok);
      const hasM = /[Mm]/.test(tok);
      const hasD = /[Dd]/.test(tok);
      if (!hasY || !hasM || !hasD) return null;

      // Enforce typical widths: DD, MM, and YY or YYYY.
      const yCount = (tok.match(/[Yy]/g) || []).length;
      const mCount = (tok.match(/[Mm]/g) || []).length;
      const dCount = (tok.match(/[Dd]/g) || []).length;
      if (!((yCount === 2) || (yCount === 4))) return null;
      if (mCount !== 2) return null;
      if (dCount !== 2) return null;

      // Build regex mirroring separators.
      let r = '';
      for (let i = 0; i < tok.length; ) {
        const ch = tok[i];
        if (ch === 'Y' || ch === 'y') {
          let j = i;
          while (j < tok.length && (tok[j] === 'Y' || tok[j] === 'y')) j++;
          r += `\\d{${j - i}}`;
          i = j;
          continue;
        }
        if (ch === 'M' || ch === 'm') {
          let j = i;
          while (j < tok.length && (tok[j] === 'M' || tok[j] === 'm')) j++;
          r += `\\d{${j - i}}`;
          i = j;
          continue;
        }
        if (ch === 'D' || ch === 'd') {
          let j = i;
          while (j < tok.length && (tok[j] === 'D' || tok[j] === 'd')) j++;
          r += `\\d{${j - i}}`;
          i = j;
          continue;
        }
        r += escapeTokenSep(ch);
        i++;
      }
      return r;
    }

    function parseTimeToken(tokRaw) {
      // Accept common time patterns like HHmmss, HH-mm-ss, HH:MM:SS.
      // Also accept legacy uppercase MM/SS for back-compat (HHMMSS, HH-MM-SS).
      const tok = String(tokRaw || '').trim();
      if (!tok) return null;

      // Allow H + (m/M) + (s/S) with optional separators.
      // Include '_' since many filenames use underscores (e.g. 13_44).
      if (!/^[HhMmSs\-_:./\\_]+$/.test(tok)) return null;
      if (!/[Hh]/.test(tok)) return null;

      const hCount = (tok.match(/[Hh]/g) || []).length;
      if (hCount !== 2) return null;
      const mCount = (tok.match(/[Mm]/g) || []).length;
      const sCount = (tok.match(/[Ss]/g) || []).length;
      if (mCount !== 2) return null;
      // Seconds are optional: allow HHmm (no seconds) or HHmmss (with seconds).
      if (!(sCount === 0 || sCount === 2)) return null;

      let r = '';
      for (let i = 0; i < tok.length; ) {
        const ch = tok[i];
        if (ch === 'H' || ch === 'h') {
          let j = i;
          while (j < tok.length && (tok[j] === 'H' || tok[j] === 'h')) j++;
          r += `\\d{${j - i}}`;
          i = j;
          continue;
        }
        if (ch === 'M' || ch === 'm') {
          let j = i;
          while (j < tok.length && (tok[j] === 'M' || tok[j] === 'm')) j++;
          r += `\\d{${j - i}}`;
          i = j;
          continue;
        }
        if (ch === 'S' || ch === 's') {
          let j = i;
          while (j < tok.length && (tok[j] === 'S' || tok[j] === 's')) j++;
          r += `\\d{${j - i}}`;
          i = j;
          continue;
        }
        r += escapeTokenSep(ch);
        i++;
      }
      return r;
    }

    function addGroup(name, body) {
      if (usedGroups.has(name)) {
        return { ok: false, error: `Duplicate token {${name}} (each field can appear once)` };
      }
      usedGroups.add(name);
      out += body;
      return { ok: true };
    }

    for (let i = 0; i < src.length; ) {
      const ch = src[i];
      if (ch !== '{') {
        // Be a bit forgiving with common filename variations.
        // - Treat runs of spaces as a flexible separator (space/underscore).
        // - Treat straight apostrophe as either ' or ’.
        if (ch === ' ') {
          // Consume consecutive spaces.
          let j = i;
          while (j < src.length && src[j] === ' ') j++;
          // Note: NBSP (\u00A0) is common in text copied from web pages and
          // may end up inside filenames. Python's re \s does NOT match NBSP.
          out += '[ _\\u00A0]+';
          i = j;
          continue;
        }
        if (ch === "'") {
          out += "['’]";
          i++;
          continue;
        }
        if (ch === '/' || ch === '\\') {
          // Allow templates to use either slash; match both at runtime.
          // Note: output targets Python's regex engine, so a literal backslash is "\\".
          out += '(?:/|\\\\)';
        } else {
          out += escapeRegexLiteral(ch);
        }
        i++;
        continue;
      }

      const j = src.indexOf('}', i + 1);
      if (j === -1) return { ok: false, error: 'Unclosed { in template' };

      const token = src.slice(i + 1, j).trim();
      i = j + 1;

      if (token === '' || token === 'wildcard') {
        out += '.*?';
        continue;
      }

      if (token.toLowerCase() === 'digit') {
        out += '\\d';
        continue;
      }

      if (token === 'streamer') {
        const r = addGroup('streamer', '(?P<streamer>[^/\\\\]+?)');
        if (!r.ok) return r;
        continue;
      }
      if (token === 'site') {
        const r = addGroup('site', '(?P<site>[^/\\\\]+?)');
        if (!r.ok) return r;
        continue;
      }

      if (token === 'date' || token === 'YYYY-MM-DD') {
        const r = addGroup('date', '(?P<date>\\d{4}-\\d{2}-\\d{2})');
        if (!r.ok) return r;
        continue;
      }
      if (token === 'YYYYMMDD') {
        const r = addGroup('date', '(?P<date>\\d{8})');
        if (!r.ok) return r;
        continue;
      }
      if (token === 'YYMMDD') {
        const r = addGroup('date', '(?P<date>\\d{6})');
        if (!r.ok) return r;
        continue;
      }

      if (token === 'time' || token === 'HH-MM-SS') {
        const r = addGroup('time', '(?P<time>\\d{2}-\\d{2}-\\d{2})');
        if (!r.ok) return r;
        continue;
      }
      if (token === 'HHMMSS' || token === 'HHmmss') {
        const r = addGroup('time', '(?P<time>\\d{6})');
        if (!r.ok) return r;
        continue;
      }

      // Flexible date/time tokens: accept patterns like {DDMMYYYY}, {DD-MM-YY}, {HH-mm-ss}, etc.
      // These are treated as a single named group (date/time) so scan can extract it.
      {
        const dateBody = parseDateToken(token);
        if (dateBody) {
          const r = addGroup('date', `(?P<date>${dateBody})`);
          if (!r.ok) return r;
          continue;
        }
        const timeBody = parseTimeToken(token);
        if (timeBody) {
          const r = addGroup('time', `(?P<time>${timeBody})`);
          if (!r.ok) return r;
          continue;
        }
      }

      if (token === 'ext') {
        const r = addGroup('ext', '(?P<ext>[^.]+)');
        if (!r.ok) return r;
        continue;
      }

      return { ok: false, error: `Unknown token {${token}}` };
    }

    // Metadata groups are optional. When absent, the scan will still import recordings
    // but without streamer/site/recorded_at information.

    return { ok: true, regex: `^${out}$` };
  }

  function templateStorageKeyForRow(idx) {
    const pathEl = document.querySelector(`[data-folder-path="${idx}"]`);
    const folderKey = (pathEl?.dataset?.folderKey || '').trim();
    const path = (pathEl?.value || '').trim();
    const raw = folderKey || path || String(idx);
    return `camero.filenameTemplate.${encodeURIComponent(raw)}`;
  }

  function syncTemplateRow(idx) {
    const templateEl = document.querySelector(`[data-folder-template="${idx}"]`);
    const regexPreviewEl = document.querySelector(`[data-template-regex="${idx}"]`);
    const regexEl = document.querySelector(`[data-folder-regex="${idx}"]`);
    const manualHintEl = document.querySelector(`[data-regex-manual-hint="${idx}"]`);
    if (!regexPreviewEl && !regexEl) return;

    const template = (templateEl?.value || '').trim();
    if (!template) {
      // Show current regex in preview as a fallback.
      const current = (regexEl?.value || '').trim();
      if (regexPreviewEl) {
        regexPreviewEl.value = current || '';
        regexPreviewEl.dataset.templateValid = '1';
      }
      if (manualHintEl) {
        manualHintEl.textContent = '';
      }
      return;
    }

    const built = buildRegexFromTemplate(template);
    if (!built.ok) {
      if (regexPreviewEl) {
        regexPreviewEl.value = `Template error: ${built.error}`;
        regexPreviewEl.dataset.templateValid = '0';
      }
      if (manualHintEl) {
        manualHintEl.textContent = '';
      }
      return;
    }

    if (regexPreviewEl) {
      regexPreviewEl.value = built.regex;
      regexPreviewEl.dataset.templateValid = '1';
    }

    // Auto-fill the editable regex unless the user has manually overridden it.
    if (regexEl) {
      // First sync after load: if the server-provided regex differs from the auto regex,
      // preserve it and mark as manual so it doesn't get overwritten.
      if (regexEl.dataset.initialSyncDone !== '1') {
        regexEl.dataset.initialSyncDone = '1';
        const existing = (regexEl.value || '').trim();
        if (existing && existing !== built.regex) {
          regexEl.dataset.regexManual = '1';
        }
      }

      const manual = (regexEl.dataset.regexManual || '0') === '1';
      regexEl.dataset.autoRegex = built.regex;
      if (!manual) {
        regexEl.value = built.regex;
      }

      if (manualHintEl) {
        manualHintEl.textContent = manual
          ? 'Manual regex: this field will NOT update from the template. While a template is set, Save/Scan uses the auto-generated regex. Clear the template to use this manual regex.'
          : '';
      }
    }
  }

  function wireTemplateInputs() {
    const templateEls = Array.from(document.querySelectorAll('[data-folder-template]'));
    const idxs = templateEls.map(el => el.dataset.folderTemplate);

    idxs.forEach((idx) => {
      const templateEl = document.querySelector(`[data-folder-template="${idx}"]`);
      if (!templateEl) return;

      // Restore last-used template (local only).
      try {
        const key = templateStorageKeyForRow(idx);
        const saved = localStorage.getItem(key);
        if (saved !== null && !templateEl.value) templateEl.value = saved;
        // First run / no saved value: prefill with a sensible default only when
        // there is no server-provided regex yet (otherwise we risk overwriting it).
        const regexEl = document.querySelector(`[data-folder-regex="${idx}"]`);
        const hasServerRegex = !!((regexEl?.value || '').trim());
        const pathEl = document.querySelector(`[data-folder-path="${idx}"]`);
        const isNewRow = !((pathEl?.value || '').trim()) && !((pathEl?.dataset?.folderKey || '').trim());
        if (saved === null && !templateEl.value && (!hasServerRegex || isNewRow)) templateEl.value = DEFAULT_FILENAME_TEMPLATE;
      } catch { /* ignore */ }

      templateEl.addEventListener('input', () => {
        syncTemplateRow(idx);
        try {
          localStorage.setItem(templateStorageKeyForRow(idx), templateEl.value || '');
        } catch { /* ignore */ }
      });

      const pills = Array.from(document.querySelectorAll(`[data-template-insert="${idx}"]`));
      pills.forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          insertAtCursor(templateEl, btn.dataset.templateToken || '');
          templateEl.dispatchEvent(new Event('input'));
        });
      });

      const regexEl = document.querySelector(`[data-folder-regex="${idx}"]`);
      if (regexEl) {
        regexEl.addEventListener('input', () => {
          // Mark manual override. If there is no autoRegex yet (template empty),
          // treat edits as manual so future template changes won't clobber it.
          const auto = (regexEl.dataset.autoRegex || '').trim();
          const now = (regexEl.value || '').trim();
          if (!auto) {
            regexEl.dataset.regexManual = '1';
            const hintEl = document.querySelector(`[data-regex-manual-hint="${idx}"]`);
            const templateEl = document.querySelector(`[data-folder-template="${idx}"]`);
            const template = (templateEl?.value || '').trim();
            if (hintEl) {
              hintEl.textContent = template
                ? 'Manual regex: this field will NOT update from the template. While a template is set, Save/Scan uses the auto-generated regex. Clear the template to use this manual regex.'
                : 'Manual regex: this field will NOT update from the template.';
            }
            return;
          }
          regexEl.dataset.regexManual = (now !== auto) ? '1' : '0';
          const hintEl = document.querySelector(`[data-regex-manual-hint="${idx}"]`);
          const manual = (regexEl.dataset.regexManual || '0') === '1';
          if (hintEl) {
            const templateEl = document.querySelector(`[data-folder-template="${idx}"]`);
            const template = (templateEl?.value || '').trim();
            hintEl.textContent = manual
              ? (template
                ? 'Manual regex: this field will NOT update from the template. While a template is set, Save/Scan uses the auto-generated regex. Clear the template to use this manual regex.'
                : 'Manual regex: this field will NOT update from the template.')
              : '';
          }
        });
      }

      const testBtn = document.querySelector(`[data-regex-test-btn="${idx}"]`);
      if (testBtn) {
        testBtn.addEventListener('click', async (ev) => {
          ev.preventDefault();
          const outEl = document.querySelector(`[data-regex-test-out="${idx}"]`);
          const inputEl = document.querySelector(`[data-regex-test-input="${idx}"]`);
          const rEl = document.querySelector(`[data-folder-regex="${idx}"]`);
          const candidate = (inputEl?.value || '').trim();
          const pattern = (rEl?.value || '').trim();
          if (!candidate) {
            if (outEl) outEl.textContent = 'Paste a filename to test.';
            return;
          }
          if (!pattern) {
            if (outEl) outEl.textContent = 'Regex is empty.';
            return;
          }
          try {
            if (outEl) outEl.textContent = `Testing… (regex: ${pattern})`;
            const res = await gql(
              `mutation($r:String!,$c:String!){ testFilenameRegex(filenameRegex:$r, candidate:$c){ ok matched error groupsJson } }`,
              { r: pattern, c: candidate }
            );
            const t = res?.testFilenameRegex;
            if (!t?.ok) {
              if (outEl) outEl.textContent = `Error: ${t?.error || 'Unknown error'}`;
              return;
            }
            if (!t.matched) {
              if (outEl) outEl.textContent = 'No match.';
              return;
            }
            const groups = (t.groupsJson || '').trim();
            if (outEl) outEl.textContent = groups ? `Matched. Groups: ${groups}` : 'Matched.';
          } catch (e) {
            if (outEl) outEl.textContent = e.message || String(e);
          }
        });
      }

      syncTemplateRow(idx);
    });
  }

  function applyFolderKeysFromConfig(newCfg) {
    const serverFolders = Array.isArray(newCfg?.catalogFolders) ? newCfg.catalogFolders : [];
    const folderEls = Array.from(document.querySelectorAll('[data-folder-path]'));
    // Only consider rows that were included in the payload (non-empty path).
    const used = folderEls.filter(el => !!(el.value || '').trim());
    serverFolders.forEach((sf, i) => {
      const el = used[i];
      if (!el) return;
      if (sf?.key) el.dataset.folderKey = sf.key;
    });
  }

  document.getElementById('saveSettings').onclick = async () => {
    const saveBtn = document.getElementById('saveSettings');
    const wasNewCatalog = !((cfg?.catalogRoot || '').trim());
    const folderEls = Array.from(document.querySelectorAll('[data-folder-path]'));
    const folders = folderEls.map((el) => {
      const idx = el.dataset.folderPath;
      const key = (el.dataset.folderKey || '').trim() || null;
      const path = (el.value || '').trim();
      // Ensure template -> regex is synced before reading.
      syncTemplateRow(idx);
      const templateEl = document.querySelector(`[data-folder-template="${idx}"]`);
      const template = (templateEl?.value || '').trim();
      const templatePreviewEl = document.querySelector(`[data-template-regex="${idx}"]`);
      const templateValid = (templatePreviewEl?.dataset?.templateValid || '1') === '1';
      if (template && !templateValid) {
        throw new Error(`Folder ${Number(idx) + 1}: template is invalid`);
      }
      const regexEl = document.querySelector(`[data-folder-regex="${idx}"]`);
      let filenameRegex = (regexEl?.value || '').trim();
      // IMPORTANT: when a template is provided, scanning should follow the template.
      // The editable regex field is only used when template is empty.
      if (template) {
        const built = buildRegexFromTemplate(template);
        if (!built.ok) {
          throw new Error(`Folder ${Number(idx) + 1}: template is invalid`);
        }
        filenameRegex = built.regex;
      }
      if (!filenameRegex) {
        throw new Error(`Folder ${Number(idx) + 1}: regex is empty (use a template or enter a regex)`);
      }

      const matchPathEl = document.querySelector(`[data-folder-match-path="${idx}"]`);
      const matchPath = !!matchPathEl?.checked;

      // Persist the template locally for convenience.
      try {
        if (template) {
          localStorage.setItem(templateStorageKeyForRow(idx), template);
        }
      } catch { /* ignore */ }
      return { key, path, filenameRegex, filenameTemplate: (template || null), matchPath };
    }).filter(f => !!f.path);

    const generateLivePreviews = !!document.getElementById('generateLivePreviews')?.checked;
    const livePreviewSegments = Number(document.getElementById('livePreviewSegments')?.value || cfg.livePreviewSegments || 10);
    const periodicScanEnabled = !!document.getElementById('periodicScanEnabled')?.checked;
    const periodicScanIntervalMinutes = Number(document.getElementById('periodicScanIntervalMinutes')?.value || cfg.periodicScanIntervalMinutes || 30);
    const autoTagAfterScan = !!document.getElementById('autoTagAfterScan')?.checked;
    const autoTagConfidence = Number(document.getElementById('autoTagConfidence')?.value ?? cfg.autoTagConfidence ?? 0.6);
    const autoTagSceneThreshold = Number(document.getElementById('autoTagSceneThreshold')?.value ?? cfg.autoTagSceneThreshold ?? 0.35);
    const autoTagFrameInterval = String(document.getElementById('autoTagFrameInterval')?.value || cfg.autoTagFrameInterval || 'auto');
    const backendHost = String(document.getElementById('backendHost')?.value || cfg.backendHost || '127.0.0.1').trim();
    const backendPort = Number(document.getElementById('backendPort')?.value || cfg.backendPort || 6969);
    const authRequired = !!document.getElementById('networkAuthRequired')?.checked;
    const authUsername = String(document.getElementById('networkAuthUsername')?.value || '').trim();
    const authPassword = String(document.getElementById('networkAuthPassword')?.value || '').trim();
    if (!backendHost) {
      throw new Error('Backend host/IP is required');
    }
    if (!Number.isInteger(backendPort) || backendPort < 1 || backendPort > 65535) {
      throw new Error('Backend port must be between 1 and 65535');
    }
    if (!Number.isInteger(periodicScanIntervalMinutes) || periodicScanIntervalMinutes < 1 || periodicScanIntervalMinutes > 1440) {
      throw new Error('Minutes between scans must be between 1 and 1440');
    }
    if (authRequired && !authUsername) {
      throw new Error('Username is required when authentication is enabled');
    }
    if (authRequired && !authPassword) {
      throw new Error('Password is required when authentication is enabled');
    }
    const backendChanged = backendHost !== String(cfg.backendHost || '127.0.0.1') || backendPort !== Number(cfg.backendPort || 6969);
    const authChanged = authRequired !== !!cfg.authRequired
      || authUsername !== String(cfg.authUsername || '')
      || authPassword !== String(cfg.authPassword || '');
    try {
      if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving…';
      }
      showToast('Saving settings…');

      // If the user is configuring a different folder tree than the currently-active catalog,
      // switch the active catalog first so settings are persisted under the intended root.
      // Otherwise it looks like "Save" wiped patterns (they were written to a different catalog).
      const normPathForCompare = (p) => String(p || '').replace(/\\/g, '/').replace(/\/+$/g, '').toLowerCase();
      const desiredRoot = (folders[0]?.path || '').trim();
      const currentRoot = (cfg?.catalogRoot || '').trim();
      const hasCatalogSettingsToSave = !!folders.length;
      const hasExistingCatalog = !!currentRoot;

      if (hasCatalogSettingsToSave) {
        if (desiredRoot && currentRoot && normPathForCompare(desiredRoot) !== normPathForCompare(currentRoot)) {
          if (recordingsTotal > 0) {
            const ok = confirm(
              `You are about to switch the active catalog from:\n\n${currentRoot}\n\nTo:\n\n${desiredRoot}\n\nThis may show a different library (different database). Continue?`
            );
            if (!ok) return;
          }
          showToast('Switching active catalog…');
          await gql(
            `mutation($r:String!){ setCatalogRoot(catalogRoot:$r){ catalogRoot } }`,
            { r: desiredRoot }
          );
        }

        const saved = await gql(
          `mutation($folders:[CatalogFolderInput!]!){ setCatalogFolders(folders:$folders){ catalogRoot filenameRegex catalogFolders { key path filenameRegex filenameTemplate matchPath } generateLivePreviews livePreviewSegments } }`,
          { folders }
        );
        applyFolderKeysFromConfig(saved?.setCatalogFolders);

        await gql(
          `mutation($v:Boolean!){ setGenerateLivePreviews(generateLivePreviews:$v){ catalogRoot filenameRegex generateLivePreviews livePreviewSegments } }`,
          { v: generateLivePreviews }
        );
        await gql(
          `mutation($n:Int!){ setLivePreviewSegments(livePreviewSegments:$n){ catalogRoot filenameRegex generateLivePreviews livePreviewSegments } }`,
          { n: livePreviewSegments }
        );
        await gql(
          `mutation($v:Boolean!){ setPeriodicScanEnabled(periodicScanEnabled:$v){ periodicScanEnabled periodicScanIntervalMinutes } }`,
          { v: periodicScanEnabled }
        );
        await gql(
          `mutation($n:Int!){ setPeriodicScanIntervalMinutes(periodicScanIntervalMinutes:$n){ periodicScanEnabled periodicScanIntervalMinutes } }`,
          { n: periodicScanIntervalMinutes }
        );
        await gql(
          `mutation($v:Boolean!){ setAutoTagAfterScan(autoTagAfterScan:$v){ autoTagAfterScan } }`,
          { v: autoTagAfterScan }
        );
        await gql(
          `mutation($v:Float!){ setAutoTagConfidence(autoTagConfidence:$v){ autoTagConfidence } }`,
          { v: autoTagConfidence }
        );
        await gql(
          `mutation($v:Float!){ setAutoTagSceneThreshold(autoTagSceneThreshold:$v){ autoTagSceneThreshold } }`,
          { v: autoTagSceneThreshold }
        );
        await gql(
          `mutation($v:String!){ setAutoTagFrameInterval(autoTagFrameInterval:$v){ autoTagFrameInterval } }`,
          { v: autoTagFrameInterval }
        );
      } else if (hasExistingCatalog) {
        await gql(
          `mutation($v:Boolean!){ setGenerateLivePreviews(generateLivePreviews:$v){ catalogRoot filenameRegex generateLivePreviews livePreviewSegments } }`,
          { v: generateLivePreviews }
        );
        await gql(
          `mutation($n:Int!){ setLivePreviewSegments(livePreviewSegments:$n){ catalogRoot filenameRegex generateLivePreviews livePreviewSegments } }`,
          { n: livePreviewSegments }
        );
        await gql(
          `mutation($v:Boolean!){ setPeriodicScanEnabled(periodicScanEnabled:$v){ periodicScanEnabled periodicScanIntervalMinutes } }`,
          { v: periodicScanEnabled }
        );
        await gql(
          `mutation($n:Int!){ setPeriodicScanIntervalMinutes(periodicScanIntervalMinutes:$n){ periodicScanEnabled periodicScanIntervalMinutes } }`,
          { n: periodicScanIntervalMinutes }
        );
        await gql(
          `mutation($v:Boolean!){ setAutoTagAfterScan(autoTagAfterScan:$v){ autoTagAfterScan } }`,
          { v: autoTagAfterScan }
        );
        await gql(
          `mutation($v:Float!){ setAutoTagConfidence(autoTagConfidence:$v){ autoTagConfidence } }`,
          { v: autoTagConfidence }
        );
        await gql(
          `mutation($v:Float!){ setAutoTagSceneThreshold(autoTagSceneThreshold:$v){ autoTagSceneThreshold } }`,
          { v: autoTagSceneThreshold }
        );
        await gql(
          `mutation($v:String!){ setAutoTagFrameInterval(autoTagFrameInterval:$v){ autoTagFrameInterval } }`,
          { v: autoTagFrameInterval }
        );
      }

      await gql(
        `mutation($host:String!,$port:Int!){ setBackendBind(backendHost:$host, backendPort:$port){ backendHost backendPort } }`,
        { host: backendHost, port: backendPort }
      );
      if (authChanged) {
        await gql(
          `mutation($required:Boolean!,$username:String!,$password:String!){ setNetworkAuth(authRequired:$required, authUsername:$username, authPassword:$password){ authRequired authUsername authPassword } }`,
          { required: authRequired, username: authUsername, password: authPassword }
        );
      }
      showToast((backendChanged || authChanged) ? 'Settings saved. Restart Camero to apply network changes.' : 'Settings saved');
      refreshCatalogInfo().catch(() => {});

      // First-time catalog creation flow: go to Recordings and auto-start scan.
      if (wasNewCatalog && hasCatalogSettingsToSave) {
        try { sessionStorage.setItem('camero.forceAutoScan', '1'); } catch { /* ignore */ }
        location.hash = '#recordings';
      }
    } catch (e) {
      showToast(e.message);
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save';
      }
    }
  };

  document.getElementById('resetSettings').onclick = async () => {
    const resetBtn = document.getElementById('resetSettings');
    const saveBtn = document.getElementById('saveSettings');
    if (!confirm('Reset settings to defaults?')) return;
    try {
      if (resetBtn) {
        resetBtn.disabled = true;
        resetBtn.textContent = 'Resetting…';
      }
      if (saveBtn) saveBtn.disabled = true;

      await gql(`mutation { resetAppConfig { catalogRoot filenameRegex catalogFolders { key path filenameRegex } generateLivePreviews livePreviewSegments periodicScanEnabled periodicScanIntervalMinutes backendHost backendPort authRequired authUsername authPassword } }`);
      showToast('Settings reset');
      await renderSettings();
      refreshCatalogInfo().catch(() => {});
    } catch (e) {
      showToast(e.message);
    } finally {
      if (resetBtn) {
        resetBtn.disabled = false;
        resetBtn.textContent = 'Reset';
      }
      if (saveBtn) saveBtn.disabled = false;
    }
  };

  document.getElementById('addCatalogFolder').onclick = async () => {
    const wrap = document.getElementById('catalogFolders');
    if (!wrap) return;
    const nextIdx = wrap.querySelectorAll('[data-folder-path]').length;
    const defaultRegex = cfg.filenameRegex;
    const html = `
      <div class="panel" style="padding:10px; border:1px solid var(--border); border-radius:12px">
        <div class="subtle">Folder ${nextIdx + 1}</div>
        <div style="display:flex; gap:10px; margin-top:8px">
          <input class="input" data-folder-path="${nextIdx}" data-folder-key="" style="width:100%" placeholder="D:\\Videos\\Catalog" value="" />
          <button class="btn" data-folder-browse="${nextIdx}">Browse…</button>
          <button class="btn btn-danger" data-folder-remove="${nextIdx}">Remove</button>
        </div>
        <div style="display:flex; align-items:center; gap:12px; margin-top:10px; flex-wrap:wrap">
          <div class="subtle">Filename pattern template</div>
          <label class="checkbox" style="margin:0">
            <input type="checkbox" data-folder-match-path="${nextIdx}" />
            Match against relative path (instead of filename)
          </label>
        </div>

        <input class="input" data-folder-template="${nextIdx}" style="width:100%; margin-top:8px" placeholder="${escapeHtml(DEFAULT_FILENAME_TEMPLATE)}" />


        

        <div class="pattern-pill-row" style="margin-top:8px">
          ${[
            '{streamer}',
            '{site}',
            '{YYYY-MM-DD}',
            '{YYYYMMDD}',
            '{YYMMDD}',
            // Time formats: accept both conventional lowercase (mm/ss) and legacy uppercase.
            '{HH_mm}',
            '{HH-mm-ss}',
            '{HHmmss}',
            '{HH-MM-SS}',
            '{HHMMSS}',
            '{ext}',
            '{wildcard}',
            '{digit}',
          ].map(tok => `<button class="pattern-pill" type="button" data-template-insert="${nextIdx}" data-template-token="${escapeHtml(tok)}">${escapeHtml(tok)}</button>`).join('')}
        </div>

        <details style="margin-top:10px">
          <summary class="small-link">Advanced: edit regex directly</summary>
          <div style="margin-top:10px">
            <div class="subtle">Generated regex (auto)</div>
            <textarea class="input" data-template-regex="${nextIdx}" rows="2" readonly style="width:100%; margin-top:8px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace"></textarea>
            <div class="subtle" style="margin-top:8px">Regex (editable). Auto-fills from the template until you edit it.</div>
            <input class="input" data-folder-regex="${nextIdx}" data-regex-manual="0" style="width:100%; margin-top:8px" value="${escapeHtml(defaultRegex)}" />
            <div class="subtle" style="margin-top:8px">Leave template empty to use the regex as-is.</div>
          </div>
        </details>
      </div>
    `;
    wrap.insertAdjacentHTML('beforeend', html);
    wireCatalogFolderControls();
    wireTemplateInputs();
  };

  function wireCatalogFolderControls() {
    document.querySelectorAll('[data-folder-browse]').forEach(btn => {
      btn.onclick = async (ev) => {
        ev.preventDefault();
        if (btn.disabled) return;
        const idx = btn.dataset.folderBrowse;
        const input = document.querySelector(`[data-folder-path="${idx}"]`);
        if (!input) return;

        const prevText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Browsing…';
        try {
          const res = await fetch('/api/dialog/select-folder', { method: 'POST' });
          const json = await res.json();
          if (!res.ok) {
            if (res.status === 409) throw new Error('Folder dialog already open');
            throw new Error(json?.detail || 'Failed to open dialog');
          }
          if (json.path) input.value = json.path;
        } catch (e) {
          showToast(e.message);
        } finally {
          btn.disabled = false;
          btn.textContent = prevText;
        }
      };
    });

    document.querySelectorAll('[data-folder-remove]').forEach(btn => {
      btn.onclick = (ev) => {
        ev.preventDefault();
        if (btn.disabled) return;
        const idx = btn.dataset.folderRemove;
        const pathEl = document.querySelector(`[data-folder-path="${idx}"]`);
        if (!pathEl) return;
        const panel = pathEl.closest('.panel');
        if (panel) panel.remove();
      };
    });
  }

  wireCatalogFolderControls();
  wireTemplateInputs();

}

async function renderRecordings(options = {}) {
  const title = options.title || 'Recordings';
  const subtitle = options.subtitle || 'Browse your catalog. Click a card for details.';
  const extraFilter = options.filter || {};
  const includeHeader = options.includeHeader !== false;
  const idPrefix = options.idPrefix || 'rec';
  const streamersHref = extraFilter.siteName
    ? `#streamers:${encodeURIComponent(extraFilter.siteName)}`
    : '#streamers';

  // If the filter context changed (e.g. switching from all recordings to a site/model/tag),
  // reset to the first page to avoid landing on an out-of-range page.
  const filterKey = JSON.stringify({
    streamerName: extraFilter.streamerName || null,
    siteName: extraFilter.siteName || null,
    tagName: extraFilter.tagName || null,
    adv: {
      nameContains: state.recordingsFilter?.nameContains || '',
      negateNameContains: !!state.recordingsFilter?.negateNameContains,
      nameContainsRegex: !!state.recordingsFilter?.nameContainsRegex,
      minSizeBytes: state.recordingsFilter?.minSizeBytes ?? null,
      maxSizeBytes: state.recordingsFilter?.maxSizeBytes ?? null,
      minDurationSeconds: state.recordingsFilter?.minDurationSeconds ?? null,
      maxDurationSeconds: state.recordingsFilter?.maxDurationSeconds ?? null,
      minShortSide: state.recordingsFilter?.minShortSide ?? null,
      maxShortSide: state.recordingsFilter?.maxShortSide ?? null,
      videoCodecs: (state.recordingsFilter?.videoCodecs || []).slice().sort(),
      missingLivePreview: !!state.recordingsFilter?.missingLivePreview,
    },
  });
  if (state._recordingsFilterKey !== filterKey) {
    state._recordingsFilterKey = filterKey;
    state.offset = 0;
  }

  const rootEl = options.containerId ? document.getElementById(options.containerId) : viewEl;
  if (!rootEl) throw new Error('Invalid container');

  let config;
  try {
    config = (await gql(`query { appConfig { catalogRoot } }`)).appConfig;
  } catch {
    config = { catalogRoot: null };
  }

  const hasCatalog = !!(config && config.catalogRoot && String(config.catalogRoot).trim());

  rootEl.innerHTML = `
    ${includeHeader ? `
      <div class="page-title">
        <div>
          <div class="h1">${escapeHtml(title)}</div>
          <div class="subtle">${escapeHtml(subtitle)}</div>
        </div>
      </div>
    ` : ''}

    <div class="toolbar" style="position:relative">
      <label class="checkbox">
        Page size
        <select id="${idPrefix}-pageSize">
          ${PAGE_SIZES.map(n => `<option value="${n}" ${n===state.pageSize?'selected':''}>${n}</option>`).join('')}
        </select>
      </label>
      <label class="checkbox">
        Sort
        <select id="${idPrefix}-sortBy">
          <option value="RECORDED_AT" ${state.recordingsSort.by==='RECORDED_AT'?'selected':''}>Date</option>
          <option value="TITLE" ${state.recordingsSort.by==='TITLE'?'selected':''}>Name</option>
          <option value="STREAMER_NAME" ${state.recordingsSort.by==='STREAMER_NAME'?'selected':''}>Streamer</option>
          <option value="DURATION" ${state.recordingsSort.by==='DURATION'?'selected':''}>Duration</option>
          <option value="SIZE" ${state.recordingsSort.by==='SIZE'?'selected':''}>Size</option>
          <option value="RESOLUTION" ${state.recordingsSort.by==='RESOLUTION'?'selected':''}>Resolution</option>
        </select>
      </label>
      <label class="checkbox">
        Dir
        <select id="${idPrefix}-sortDir">
          <option value="ASC" ${state.recordingsSort.dir==='ASC'?'selected':''}>Asc</option>
          <option value="DESC" ${state.recordingsSort.dir==='DESC'?'selected':''}>Desc</option>
        </select>
      </label>
      <button id="${idPrefix}-selectAll" class="btn" title="Select all recordings matching current filters">Select all</button>
      <span class="subtle" id="${idPrefix}-selCount">0 selected</span>
      <button id="${idPrefix}-clearSelection" class="btn" title="Clear selection" hidden>Clear</button>
      <span style="flex:1"></span>
      ${hasCatalog ? `
        <button id="scan" class="btn btn-primary btn-scan">Scan catalog</button>
        <button id="generateMissingLivePreviews" class="btn" hidden>Generate animated previews</button>
        <button id="stopScan" class="btn btn-danger" hidden>Stop scan</button>
        <button id="stopLivePreviews" class="btn btn-danger" hidden>Stop generation of animated previews</button>
        <button id="autoTagUntagged" class="btn" hidden>Auto-tag untagged</button>
        <button id="stopAutoTag" class="btn btn-danger" hidden>Stop auto-tagging</button>
      ` : ''}
      <button id="${idPrefix}-autoPreview" class="toggle-round ${state.recordingsAutoPreview ? 'active' : ''}" title="Animated previews: ${state.recordingsAutoPreview ? 'ON' : 'OFF'}" aria-label="Toggle animated previews" type="button">🎞️</button>
      <button id="${idPrefix}-filtersBtn" class="toggle-round" title="Filters" aria-label="Filters" type="button">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <path d="M3 5h18l-7 8v5l-4 2v-7L3 5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
        </svg>
      </button>
    </div>

    ${hasCatalog ? `
      <div id="scanProgressWrap" class="progress-wrap" hidden>
        <div class="progress-row">
          <div class="progress"><div id="scanProgressBar" class="progress-bar" style="width:0%"></div></div>
          <div id="scanProgressPct" class="subtle">0%</div>
        </div>
        <div id="scanProgressText" class="subtle" style="margin-top:8px"></div>
      </div>
      <div id="autoTagProgressWrap" class="progress-wrap" hidden>
        <div class="progress-row">
          <div class="progress"><div id="autoTagProgressBar" class="progress-bar" style="width:0%"></div></div>
          <div id="autoTagProgressPct" class="subtle">0%</div>
        </div>
        <div id="autoTagProgressText" class="subtle" style="margin-top:8px"></div>
      </div>
    ` : ''}

    <div id="${idPrefix}-selActions" class="toolbar toolbar-actions" style="margin-top:10px" hidden>
      <span class="subtle">Selected actions</span>
      <span style="flex:1"></span>
      <button id="${idPrefix}-tagSelected" class="btn">Assign tag</button>
      <button id="${idPrefix}-autoTagSelected" class="btn">Auto-tag</button>
      <button id="${idPrefix}-siteSelected" class="btn">Assign site</button>
      <button id="${idPrefix}-moveSelected" class="btn">Move</button>
      <button id="${idPrefix}-copySelected" class="btn">Copy</button>
      <button id="${idPrefix}-rescanSelected" class="btn">Rescan metadata</button>
      <button id="${idPrefix}-reencodeSelected" class="btn">Transcode</button>
      <button id="${idPrefix}-deleteSelected" class="btn btn-danger">Delete</button>
    </div>

    <div class="pagination">
      <div class="subtle" id="${idPrefix}-pageInfoTop"></div>
      <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
        <label class="checkbox" style="gap:8px">
          Page
          <select id="${idPrefix}-pageSelectTop"></select>
        </label>
      </div>
      <div style="display:flex; gap:10px">
        <button id="${idPrefix}-prevTop" class="btn">Prev</button>
        <button id="${idPrefix}-nextTop" class="btn">Next</button>
      </div>
    </div>

    <div id="${idPrefix}-grid" class="grid"></div>
    <div class="pagination">
      <div class="subtle" id="${idPrefix}-pageInfo"></div>
      <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
        <label class="checkbox" style="gap:8px">
          Page
          <select id="${idPrefix}-pageSelect"></select>
        </label>
      </div>
      <div style="display:flex; gap:10px">
        <button id="${idPrefix}-prev" class="btn">Prev</button>
        <button id="${idPrefix}-next" class="btn">Next</button>
      </div>
    </div>
  `;

  const gridEl = document.getElementById(`${idPrefix}-grid`);
  const pageInfoEl = document.getElementById(`${idPrefix}-pageInfo`);
  const pageInfoTopEl = document.getElementById(`${idPrefix}-pageInfoTop`);
  const selCountEl = document.getElementById(`${idPrefix}-selCount`);
  const prevBtn = document.getElementById(`${idPrefix}-prev`);
  const nextBtn = document.getElementById(`${idPrefix}-next`);
  const pageSelectEl = document.getElementById(`${idPrefix}-pageSelect`);
  const prevTopBtn = document.getElementById(`${idPrefix}-prevTop`);
  const nextTopBtn = document.getElementById(`${idPrefix}-nextTop`);
  const pageSelectTopEl = document.getElementById(`${idPrefix}-pageSelectTop`);
  const selActionsEl = document.getElementById(`${idPrefix}-selActions`);
  const selectAllBtn = document.getElementById(`${idPrefix}-selectAll`);
  const clearSelectionBtn = document.getElementById(`${idPrefix}-clearSelection`);
  const autoPreviewBtn = document.getElementById(`${idPrefix}-autoPreview`);
  const filtersBtn = document.getElementById(`${idPrefix}-filtersBtn`);
  const toolbarEl = rootEl.querySelector('.toolbar');

  const scanBtn = document.getElementById('scan');
  const genMissingBtn = document.getElementById('generateMissingLivePreviews');
  const stopScanBtn = document.getElementById('stopScan');
  const stopLivePreviewsBtn = document.getElementById('stopLivePreviews');
  const autoTagUntaggedBtn = document.getElementById('autoTagUntagged');
  const stopAutoTagBtn = document.getElementById('stopAutoTag');

  async function startScanJobFromUI() {
    if (!hasCatalog) return false;
    if (state.scan.jobId) {
      showToast('A job is already running');
      return false;
    }
    try {
      if (scanBtn) {
        scanBtn.disabled = true;
        scanBtn.textContent = 'Scanning…';
      }
      showToast('Scanning catalog…');
      const res = await gql(`mutation { startScanCatalog { jobId status } }`);
      const jobId = res.startScanCatalog?.jobId;
      if (!jobId) throw new Error('Failed to start scan');
      startScanPolling(jobId);
      return true;
    } catch (err) {
      showToast(err.message || String(err));
      return false;
    } finally {
      if (scanBtn) {
        scanBtn.disabled = false;
        scanBtn.textContent = 'Scan catalog';
      }
    }
  }

  const RES_LEVELS = [
    { label: '360p', px: 360 },
    { label: '480p', px: 480 },
    { label: '540p', px: 540 },
    { label: '720p', px: 720 },
    { label: '960p', px: 960 },
    { label: '1080p', px: 1080 },
    { label: '2K', px: 1440 },
    { label: '4K', px: 2160 },
    { label: '8K', px: 4320 },
  ];

  function hasActiveAdvancedFilters() {
    const f = state.recordingsFilter || {};
    const hasNameContains = !!String(f.nameContains || '').trim();
    const hasNums = (
      f.minSizeBytes != null || f.maxSizeBytes != null ||
      f.minDurationSeconds != null || f.maxDurationSeconds != null ||
      f.minShortSide != null || f.maxShortSide != null
    );
    const hasCodecs = Array.isArray(f.videoCodecs) && f.videoCodecs.length > 0;
    const hasPreviewFilter = !!f.missingLivePreview;
    return hasNameContains || hasNums || hasCodecs || hasPreviewFilter;
  }

  if (filtersBtn) {
    filtersBtn.classList.toggle('active', hasActiveAdvancedFilters());
  }

  let filtersMenuEl = null;
  let filtersMenuOpen = false;
  let filtersMenuOnDocClick = null;
  let cachedFilterMeta = null;

  function clamp(n, lo, hi) {
    const x = Number(n);
    if (!Number.isFinite(x)) return lo;
    return Math.max(lo, Math.min(hi, x));
  }

  function nearestResIdx(px) {
    const target = Number(px);
    if (!Number.isFinite(target) || target <= 0) return 0;
    let bestI = 0;
    let bestD = Infinity;
    for (let i = 0; i < RES_LEVELS.length; i++) {
      const d = Math.abs(RES_LEVELS[i].px - target);
      if (d < bestD) {
        bestD = d;
        bestI = i;
      }
    }
    return bestI;
  }

  async function getFilterMeta() {
    // Meta depends on current context (streamer/site/tag/search) but ignores advanced filters.
    const vars = {
      search: state.search || null,
      streamerName: extraFilter.streamerName || null,
      siteName: extraFilter.siteName || null,
      tagName: extraFilter.tagName || null,
    };
    const key = JSON.stringify(vars);
    if (cachedFilterMeta && cachedFilterMeta._key === key) return cachedFilterMeta;

    const res = await gql(
      `query($search:String, $streamerName:String, $siteName:String, $tagName:String) {
        recordingsFilterMeta(search:$search, streamerName:$streamerName, siteName:$siteName, tagName:$tagName) {
          maxSizeBytes
          maxDurationSeconds
          maxShortSide
          videoCodecs
        }
      }`,
      vars
    );
    const m = res?.recordingsFilterMeta || null;
    cachedFilterMeta = {
      _key: key,
      maxSizeBytes: Number(m?.maxSizeBytes || 0),
      maxDurationSeconds: Number(m?.maxDurationSeconds || 0),
      maxShortSide: Number(m?.maxShortSide || 0),
      videoCodecs: Array.isArray(m?.videoCodecs) ? m.videoCodecs.filter(Boolean) : [],
    };
    return cachedFilterMeta;
  }

  function destroyFiltersMenu() {
    if (filtersMenuEl) {
      try { filtersMenuEl.remove(); } catch { /* ignore */ }
    }
    filtersMenuEl = null;
    filtersMenuOpen = false;
    if (filtersMenuOnDocClick) {
      document.removeEventListener('click', filtersMenuOnDocClick, true);
      filtersMenuOnDocClick = null;
    }
  }

  function positionMenuNearButton(btnEl, menuEl) {
    if (!btnEl || !menuEl || !toolbarEl) return;
    const btnRect = btnEl.getBoundingClientRect();
    const tbRect = toolbarEl.getBoundingClientRect();
    const margin = 8;

    // Position within the toolbar (which is position:relative).
    const desiredTop = (btnRect.bottom - tbRect.top) + margin;
    // Align right edge with the button.
    const desiredLeft = (btnRect.right - tbRect.left) - menuEl.offsetWidth;

    const maxLeft = toolbarEl.clientWidth - menuEl.offsetWidth - margin;
    const left = clamp(desiredLeft, margin, Math.max(margin, maxLeft));
    const top = clamp(desiredTop, margin, Math.max(margin, (toolbarEl.clientHeight + 600))); // allow overflow over grid

    menuEl.style.left = `${left}px`;
    menuEl.style.top = `${top}px`;
  }

  function buildCodecPills(codecs, selectedSet) {
    if (!codecs || !codecs.length) {
      return '<div class="subtle">No codecs found.</div>';
    }
    return `
      <div class="pattern-pill-row" style="margin-top:8px">
        ${codecs.map(c => {
          const cc = String(c || '').trim();
          if (!cc) return '';
          const active = selectedSet.has(cc) ? 'active' : '';
          return `<button type="button" class="pattern-pill filter-pill ${active}" data-filter-codec="${escapeHtml(cc)}">${escapeHtml(cc)}</button>`;
        }).join('')}
      </div>
    `;
  }

  async function openFiltersMenu() {
    if (!filtersBtn) return;
    if (filtersMenuOpen) {
      destroyFiltersMenu();
      return;
    }

    filtersMenuOpen = true;
    filtersMenuEl = document.createElement('div');
    filtersMenuEl.className = 'context-menu';
    filtersMenuEl.innerHTML = `<div class="subtle">Loading filters…</div>`;
    // Attach to the toolbar so it floats over the recordings grid.
    // (Toolbar is rendered with position:relative)
    (toolbarEl || document.body).appendChild(filtersMenuEl);
    // Position immediately (even while loading), then re-position after layout.
    positionMenuNearButton(filtersBtn, filtersMenuEl);
    requestAnimationFrame(() => positionMenuNearButton(filtersBtn, filtersMenuEl));

    // Close on outside click.
    filtersMenuOnDocClick = (ev) => {
      const t = ev.target;
      if (!filtersMenuOpen) return;
      if (t && (filtersBtn.contains(t) || (filtersMenuEl && filtersMenuEl.contains(t)))) return;
      destroyFiltersMenu();
    };
    document.addEventListener('click', filtersMenuOnDocClick, true);

    // Load meta and render UI.
    let meta;
    try {
      meta = await getFilterMeta();
      cachedFilterMeta = meta;
    } catch (e) {
      filtersMenuEl.innerHTML = `<div class="subtle">Failed to load filter options: ${escapeHtml(e.message || String(e))}</div>`;
      requestAnimationFrame(() => positionMenuNearButton(filtersBtn, filtersMenuEl));
      return;
    }

    const maxMB = Math.max(1, Math.ceil((meta.maxSizeBytes || 0) / (1024 * 1024)));
    const maxSec = Math.max(1, Math.ceil(meta.maxDurationSeconds || 0));

    // Defaults are full-range; only deviations become active filters.
    const applied = state.recordingsFilter || {};
    const initMinMB = applied.minSizeBytes != null ? Math.floor(Number(applied.minSizeBytes) / (1024 * 1024)) : 0;
    const initMaxMB = applied.maxSizeBytes != null ? Math.ceil(Number(applied.maxSizeBytes) / (1024 * 1024)) : maxMB;
    const initMinSec = applied.minDurationSeconds != null ? Math.floor(Number(applied.minDurationSeconds)) : 0;
    const initMaxSec = applied.maxDurationSeconds != null ? Math.ceil(Number(applied.maxDurationSeconds)) : maxSec;

    const resMinIdx = applied.minShortSide != null ? nearestResIdx(applied.minShortSide) : 0;
    const resMaxIdx = applied.maxShortSide != null ? nearestResIdx(applied.maxShortSide) : (RES_LEVELS.length - 1);

    const selectedCodecs = new Set(Array.isArray(applied.videoCodecs) ? applied.videoCodecs : []);
    const initNameContains = String(applied.nameContains || '').trim();
    const initNegateNameContains = !!applied.negateNameContains;
    const initNameContainsRegex = !!applied.nameContainsRegex;
    const initMissingPreview = !!applied.missingLivePreview;

    const sizeStep = maxMB <= 500 ? 5 : (maxMB <= 5000 ? 50 : 200);
    const durStep = maxSec <= 3600 ? 30 : 60;

    filtersMenuEl.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; gap:10px">
        <div class="subtle filter-section-title">Filters</div>
        <button type="button" class="icon-btn" data-filter-close title="Close">✕</button>
      </div>

      <div style="margin-top:10px">
        <div class="subtle filter-section-title" data-filter-name-title>Name contains</div>
        <div style="display:flex; align-items:center; gap:10px; margin-top:6px">
          <input
            type="text"
            class="input"
            data-filter-name-contains
            placeholder="Filter by recording name"
            value="${escapeHtml(initNameContains)}"
            style="flex:1; min-width:0"
          />
          <label class="subtle" style="display:flex; align-items:center; gap:6px; user-select:none; white-space:nowrap">
            <input type="checkbox" class="input" data-filter-name-regex ${initNameContainsRegex ? 'checked' : ''} />
            regex
          </label>
          <label class="subtle" style="display:flex; align-items:center; gap:6px; user-select:none; white-space:nowrap">
            <input type="checkbox" class="input" data-filter-name-negate ${initNegateNameContains ? 'checked' : ''} />
            negate
          </label>
        </div>
      </div>

      <div style="margin-top:10px">
        <div class="subtle filter-section-title">Animated preview</div>
        <label class="subtle" style="display:flex; align-items:center; gap:8px; margin-top:6px; user-select:none">
          <input type="checkbox" class="input" data-filter-missing-preview ${initMissingPreview ? 'checked' : ''} />
          Only show recordings without animated preview
        </label>
      </div>

      <div style="margin-top:10px">
        <div class="subtle filter-section-title">Size (MB)</div>
        <div class="subtle" style="display:flex; justify-content:space-between; gap:10px; margin-top:6px">
          <span>Min: <span data-filter-size-min-val></span></span>
          <span>Max: <span data-filter-size-max-val></span></span>
        </div>
        <div class="dual-range" style="margin-top:6px" data-filter-dual="size">
          <input type="range" class="input dual-range-input dual-min" data-filter-size-min min="0" max="${maxMB}" step="${sizeStep}" value="${clamp(initMinMB, 0, maxMB)}" />
          <input type="range" class="input dual-range-input dual-max" data-filter-size-max min="0" max="${maxMB}" step="${sizeStep}" value="${clamp(initMaxMB, 0, maxMB)}" />
        </div>
      </div>

      <div style="margin-top:12px">
        <div class="subtle filter-section-title">Duration (seconds)</div>
        <div class="subtle" style="display:flex; justify-content:space-between; gap:10px; margin-top:6px">
          <span>Min: <span data-filter-dur-min-val></span></span>
          <span>Max: <span data-filter-dur-max-val></span></span>
        </div>
        <div class="dual-range" style="margin-top:6px" data-filter-dual="dur">
          <input type="range" class="input dual-range-input dual-min" data-filter-dur-min min="0" max="${maxSec}" step="${durStep}" value="${clamp(initMinSec, 0, maxSec)}" />
          <input type="range" class="input dual-range-input dual-max" data-filter-dur-max min="0" max="${maxSec}" step="${durStep}" value="${clamp(initMaxSec, 0, maxSec)}" />
        </div>
      </div>

      <div style="margin-top:12px">
        <div class="subtle filter-section-title">Resolution (short side)</div>
        <div class="subtle" style="display:flex; justify-content:space-between; gap:10px; margin-top:6px">
          <span>Min: <span data-filter-res-min-val></span></span>
          <span>Max: <span data-filter-res-max-val></span></span>
        </div>
        <div class="dual-range" style="margin-top:6px" data-filter-dual="res">
          <input type="range" class="input dual-range-input dual-min" data-filter-res-min min="0" max="${RES_LEVELS.length - 1}" step="1" value="${clamp(resMinIdx, 0, RES_LEVELS.length - 1)}" />
          <input type="range" class="input dual-range-input dual-max" data-filter-res-max min="0" max="${RES_LEVELS.length - 1}" step="1" value="${clamp(resMaxIdx, 0, RES_LEVELS.length - 1)}" />
        </div>
      </div>

      <div style="margin-top:12px">
        <div class="subtle filter-section-title">Video codec</div>
        ${buildCodecPills(meta.videoCodecs || [], selectedCodecs)}
      </div>

      <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:14px">
        <button type="button" class="btn" data-filter-clear>Clear filters</button>
        <button type="button" class="btn btn-primary" data-filter-apply>Apply</button>
      </div>
    `;

    const closeBtn = filtersMenuEl.querySelector('[data-filter-close]');
    if (closeBtn) closeBtn.onclick = (ev) => { ev.preventDefault(); destroyFiltersMenu(); };

    const sizeMinEl = filtersMenuEl.querySelector('[data-filter-size-min]');
    const sizeMaxEl = filtersMenuEl.querySelector('[data-filter-size-max]');
    const sizeMinValEl = filtersMenuEl.querySelector('[data-filter-size-min-val]');
    const sizeMaxValEl = filtersMenuEl.querySelector('[data-filter-size-max-val]');

    const durMinEl = filtersMenuEl.querySelector('[data-filter-dur-min]');
    const durMaxEl = filtersMenuEl.querySelector('[data-filter-dur-max]');
    const durMinValEl = filtersMenuEl.querySelector('[data-filter-dur-min-val]');
    const durMaxValEl = filtersMenuEl.querySelector('[data-filter-dur-max-val]');

    const resMinEl = filtersMenuEl.querySelector('[data-filter-res-min]');
    const resMaxEl = filtersMenuEl.querySelector('[data-filter-res-max]');
    const resMinValEl = filtersMenuEl.querySelector('[data-filter-res-min-val]');
    const resMaxValEl = filtersMenuEl.querySelector('[data-filter-res-max-val]');

    const sizeDualEl = filtersMenuEl.querySelector('[data-filter-dual="size"]');
    const durDualEl = filtersMenuEl.querySelector('[data-filter-dual="dur"]');
    const resDualEl = filtersMenuEl.querySelector('[data-filter-dual="res"]');

    const nameContainsEl = filtersMenuEl.querySelector('[data-filter-name-contains]');
    const nameTitleEl = filtersMenuEl.querySelector('[data-filter-name-title]');
    const nameContainsRegexEl = filtersMenuEl.querySelector('[data-filter-name-regex]');
    const negateNameContainsEl = filtersMenuEl.querySelector('[data-filter-name-negate]');
    const missingPreviewEl = filtersMenuEl.querySelector('[data-filter-missing-preview]');

    function syncNameFilterUx() {
      const isRegex = !!(nameContainsRegexEl && nameContainsRegexEl.checked);
      const isNegated = !!(negateNameContainsEl && negateNameContainsEl.checked);
      if (nameTitleEl) {
        if (isNegated && isRegex) nameTitleEl.textContent = 'Name does not match regex';
        else if (isNegated) nameTitleEl.textContent = 'Name does not contain';
        else if (isRegex) nameTitleEl.textContent = 'Name matches regex';
        else nameTitleEl.textContent = 'Name contains';
      }
      if (nameContainsEl) {
        if (isRegex) nameContainsEl.placeholder = isNegated ? 'Exclude recordings matching regex' : 'Filter recordings matching regex';
        else nameContainsEl.placeholder = isNegated ? 'Exclude recordings containing text' : 'Filter by recording name';
      }
    }

    function updateDualBg(dualEl, minV, maxV, lo, hi) {
      if (!dualEl) return;
      const denom = Math.max(1, (hi - lo));
      const a = clamp(minV, lo, hi);
      const b = clamp(maxV, lo, hi);
      const leftPct = ((a - lo) / denom) * 100;
      const rightPct = ((b - lo) / denom) * 100;
      const left = Math.min(leftPct, rightPct);
      const right = Math.max(leftPct, rightPct);
      dualEl.style.setProperty('--dual-a', `${left}%`);
      dualEl.style.setProperty('--dual-b', `${right}%`);
    }

    function wireDualRange(dualEl, minEl, maxEl, lo, hi, step) {
      if (!dualEl || !minEl || !maxEl) return;

      const stepN = Math.max(1e-9, Number(step) || 1);
      const range = Math.max(1e-9, (hi - lo));
      const supportsPointer = (typeof window !== 'undefined') && ('PointerEvent' in window);

      function roundToStep(v) {
        const snapped = Math.round((v - lo) / stepN) * stepN + lo;
        // Keep it stable for integer-ish steps.
        return Number.isInteger(stepN) ? Math.round(snapped) : snapped;
      }

      function valueFromClientX(clientX) {
        const rect = dualEl.getBoundingClientRect();
        const x = clamp(clientX - rect.left, 0, rect.width);
        const t = rect.width > 0 ? (x / rect.width) : 0;
        const raw = lo + (t * range);
        return clamp(roundToStep(raw), lo, hi);
      }

      function chooseThumb(targetValue) {
        const curMin = Number(minEl.value);
        const curMax = Number(maxEl.value);
        const dMin = Math.abs(targetValue - curMin);
        const dMax = Math.abs(targetValue - curMax);
        if (dMin < dMax) return 'min';
        if (dMax < dMin) return 'max';
        // Tie-breaker: prefer whichever was last active, else prefer min.
        const last = dualEl.dataset.activeThumb;
        return (last === 'max') ? 'max' : 'min';
      }

      function setThumb(which, v) {
        const vv = clamp(v, lo, hi);
        if (which === 'min') {
          minEl.value = String(vv);
          if (Number(minEl.value) > Number(maxEl.value)) {
            maxEl.value = minEl.value;
          }
        } else {
          maxEl.value = String(vv);
          if (Number(maxEl.value) < Number(minEl.value)) {
            minEl.value = maxEl.value;
          }
        }
      }

      let active = null;
      let activePointerId = null;

      function beginDragAtClientX(clientX) {
        const v = valueFromClientX(clientX);
        active = chooseThumb(v);
        dualEl.dataset.activeThumb = active;
        setThumb(active, v);
        syncLabels();
      }

      function dragToClientX(clientX) {
        const v = valueFromClientX(clientX);
        setThumb(active || 'min', v);
        syncLabels();
      }

      function endDrag() {
        activePointerId = null;
        active = null;
      }

      if (supportsPointer) {
        dualEl.addEventListener('pointerdown', (ev) => {
          if (ev.button != null && ev.button !== 0) return;
          ev.preventDefault();
          activePointerId = ev.pointerId;
          try { dualEl.setPointerCapture(ev.pointerId); } catch { /* ignore */ }
          beginDragAtClientX(ev.clientX);
        });

        dualEl.addEventListener('pointermove', (ev) => {
          if (activePointerId == null || ev.pointerId !== activePointerId) return;
          ev.preventDefault();
          dragToClientX(ev.clientX);
        });

        const endPointer = (ev) => {
          if (activePointerId == null || ev.pointerId !== activePointerId) return;
          ev.preventDefault();
          endDrag();
        };

        dualEl.addEventListener('pointerup', endPointer);
        dualEl.addEventListener('pointercancel', endPointer);
      } else {
        // Fallback: mouse events.
        const onMouseMove = (ev) => {
          if (!activePointerId) return;
          ev.preventDefault();
          dragToClientX(ev.clientX);
        };
        const onMouseUp = (ev) => {
          if (!activePointerId) return;
          ev.preventDefault();
          activePointerId = null;
          endDrag();
          window.removeEventListener('mousemove', onMouseMove, true);
          window.removeEventListener('mouseup', onMouseUp, true);
        };

        dualEl.addEventListener('mousedown', (ev) => {
          if (ev.button != null && ev.button !== 0) return;
          ev.preventDefault();
          activePointerId = 1; // sentinel
          beginDragAtClientX(ev.clientX);
          window.addEventListener('mousemove', onMouseMove, true);
          window.addEventListener('mouseup', onMouseUp, true);
        });

        // Fallback: touch events.
        const onTouchMove = (ev) => {
          if (!activePointerId) return;
          if (!ev.touches || ev.touches.length === 0) return;
          ev.preventDefault();
          dragToClientX(ev.touches[0].clientX);
        };
        const onTouchEnd = (ev) => {
          if (!activePointerId) return;
          ev.preventDefault();
          activePointerId = null;
          endDrag();
          window.removeEventListener('touchmove', onTouchMove, true);
          window.removeEventListener('touchend', onTouchEnd, true);
          window.removeEventListener('touchcancel', onTouchEnd, true);
        };

        dualEl.addEventListener('touchstart', (ev) => {
          if (!ev.touches || ev.touches.length === 0) return;
          ev.preventDefault();
          activePointerId = 1; // sentinel
          beginDragAtClientX(ev.touches[0].clientX);
          window.addEventListener('touchmove', onTouchMove, { capture: true, passive: false });
          window.addEventListener('touchend', onTouchEnd, { capture: true, passive: false });
          window.addEventListener('touchcancel', onTouchEnd, { capture: true, passive: false });
        }, { passive: false });
      }

      // Keep in sync if values are changed programmatically.
      minEl.addEventListener('input', syncLabels);
      maxEl.addEventListener('input', syncLabels);
    }

    function syncLabels() {
      const minMB = clamp(sizeMinEl?.value ?? 0, 0, maxMB);
      const maxMBv = clamp(sizeMaxEl?.value ?? maxMB, 0, maxMB);
      if (sizeMinValEl) sizeMinValEl.textContent = fmtBytes(minMB * 1024 * 1024);
      if (sizeMaxValEl) sizeMaxValEl.textContent = fmtBytes(maxMBv * 1024 * 1024);
      updateDualBg(sizeDualEl, minMB, maxMBv, 0, maxMB);

      const minS = clamp(durMinEl?.value ?? 0, 0, maxSec);
      const maxS = clamp(durMaxEl?.value ?? maxSec, 0, maxSec);
      if (durMinValEl) durMinValEl.textContent = fmtDuration(minS);
      if (durMaxValEl) durMaxValEl.textContent = fmtDuration(maxS);
      updateDualBg(durDualEl, minS, maxS, 0, maxSec);

      const rmin = clamp(resMinEl?.value ?? 0, 0, RES_LEVELS.length - 1);
      const rmax = clamp(resMaxEl?.value ?? (RES_LEVELS.length - 1), 0, RES_LEVELS.length - 1);
      if (resMinValEl) resMinValEl.textContent = RES_LEVELS[rmin]?.label || '';
      if (resMaxValEl) resMaxValEl.textContent = RES_LEVELS[rmax]?.label || '';
      updateDualBg(resDualEl, rmin, rmax, 0, RES_LEVELS.length - 1);
    }

    wireDualRange(sizeDualEl, sizeMinEl, sizeMaxEl, 0, maxMB, sizeStep);
    wireDualRange(durDualEl, durMinEl, durMaxEl, 0, maxSec, durStep);
    wireDualRange(resDualEl, resMinEl, resMaxEl, 0, (RES_LEVELS.length - 1), 1);
    syncLabels();
    syncNameFilterUx();

    if (nameContainsRegexEl) nameContainsRegexEl.addEventListener('change', syncNameFilterUx);
    if (negateNameContainsEl) negateNameContainsEl.addEventListener('change', syncNameFilterUx);

    filtersMenuEl.querySelectorAll('[data-filter-codec]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        const codec = (btn.dataset.filterCodec || '').trim();
        if (!codec) return;
        if (selectedCodecs.has(codec)) selectedCodecs.delete(codec);
        else selectedCodecs.add(codec);
        btn.classList.toggle('active', selectedCodecs.has(codec));
      });
    });

    async function applyFiltersFromMenu() {
      // Normalize min/max.
      let minMB = clamp(sizeMinEl?.value ?? 0, 0, maxMB);
      let maxMBv = clamp(sizeMaxEl?.value ?? maxMB, 0, maxMB);
      if (minMB > maxMBv) [minMB, maxMBv] = [maxMBv, minMB];

      let minS = clamp(durMinEl?.value ?? 0, 0, maxSec);
      let maxS = clamp(durMaxEl?.value ?? maxSec, 0, maxSec);
      if (minS > maxS) [minS, maxS] = [maxS, minS];

      let rmin = clamp(resMinEl?.value ?? 0, 0, RES_LEVELS.length - 1);
      let rmax = clamp(resMaxEl?.value ?? (RES_LEVELS.length - 1), 0, RES_LEVELS.length - 1);
      if (rmin > rmax) [rmin, rmax] = [rmax, rmin];

      const nameContains = String(nameContainsEl?.value || '').trim();
      const next = {
        nameContains,
        negateNameContains: !!(negateNameContainsEl && negateNameContainsEl.checked),
        nameContainsRegex: !!(nameContainsRegexEl && nameContainsRegexEl.checked),
        minSizeBytes: (minMB > 0) ? (minMB * 1024 * 1024) : null,
        maxSizeBytes: (maxMBv < maxMB) ? (maxMBv * 1024 * 1024) : null,
        minDurationSeconds: (minS > 0) ? minS : null,
        maxDurationSeconds: (maxS < maxSec) ? maxS : null,
        minShortSide: (rmin > 0) ? RES_LEVELS[rmin].px : null,
        maxShortSide: (rmax < (RES_LEVELS.length - 1)) ? RES_LEVELS[rmax].px : null,
        videoCodecs: Array.from(selectedCodecs).sort(),
        missingLivePreview: !!(missingPreviewEl && missingPreviewEl.checked),
      };

      state.recordingsFilter = next;
      setStoredRecordingsFilter(next);
      state.offset = 0;

      if (filtersBtn) {
        filtersBtn.classList.toggle('active', hasActiveAdvancedFilters());
      }

      destroyFiltersMenu();
      await load();
    }

    const applyBtn = filtersMenuEl.querySelector('[data-filter-apply]');
    if (applyBtn) {
      applyBtn.onclick = async (ev) => {
        ev.preventDefault();
        await applyFiltersFromMenu();
      };
    }

    if (nameContainsEl) {
      nameContainsEl.addEventListener('keydown', async (ev) => {
        if (ev.key !== 'Enter') return;
        ev.preventDefault();
        await applyFiltersFromMenu();
      });
    }

    const clearBtn = filtersMenuEl.querySelector('[data-filter-clear]');
    if (clearBtn) {
      clearBtn.onclick = async (ev) => {
        ev.preventDefault();
        state.recordingsFilter = createDefaultRecordingsFilter();
        setStoredRecordingsFilter(state.recordingsFilter);
        state.offset = 0;
        if (filtersBtn) {
          filtersBtn.classList.toggle('active', hasActiveAdvancedFilters());
        }
        destroyFiltersMenu();
        await load();
      };
    }

    requestAnimationFrame(() => positionMenuNearButton(filtersBtn, filtersMenuEl));
  }

  if (filtersBtn) {
    filtersBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      await openFiltersMenu();
    };
  }

  function applyAutoPreviewMode(enabled) {
    if (!gridEl) return;
    gridEl.querySelectorAll('.card.has-preview .thumb-wrap').forEach(wrap => {
      const video = wrap.querySelector('video.thumb-video');
      const img = wrap.querySelector('img.thumb-img');
      if (!video) return;

      if (enabled) {
        try {
          if (!video.src) {
            const card = wrap.closest('.card');
            const id = card ? parseInt(card.dataset.id, 10) : null;
            if (id) video.src = `/preview/${id}.mp4`;
          }
          video.loop = true;
          video.muted = true;
          video.play().catch(() => {});
          video.classList.add('active');
          if (img) img.classList.add('hide');
        } catch { /* ignore */ }
      } else {
        try {
          video.pause();
          video.classList.remove('active');
          if (img) img.classList.remove('hide');
        } catch { /* ignore */ }
      }
    });
  }

  if (autoPreviewBtn) {
    autoPreviewBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      state.recordingsAutoPreview = !state.recordingsAutoPreview;
      localStorage.setItem('camero.recordingsAutoPreview', state.recordingsAutoPreview ? '1' : '0');
      autoPreviewBtn.classList.toggle('active', state.recordingsAutoPreview);
      autoPreviewBtn.title = `Animated previews: ${state.recordingsAutoPreview ? 'ON' : 'OFF'}`;
      applyAutoPreviewMode(state.recordingsAutoPreview);
    };
  }

  if (hasCatalog && scanBtn) {
    scanBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      await startScanJobFromUI();
    };
  }

  if (hasCatalog && genMissingBtn) {
    genMissingBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (state.scan.jobId) {
        showToast('A job is already running');
        return;
      }
      try {
        genMissingBtn.disabled = true;
        genMissingBtn.textContent = 'Starting…';
        showToast('Generating animated previews…');
        const res = await gql(`mutation { startGenerateMissingLivePreviews { jobId status } }`);
        const jobId = res.startGenerateMissingLivePreviews?.jobId;
        if (!jobId) throw new Error('Failed to start animated preview generation');
        startScanPolling(jobId);
      } catch (err) {
        showToast(err.message || String(err));
      } finally {
        genMissingBtn.disabled = false;
        genMissingBtn.textContent = 'Generate animated previews';
      }
    };
  }

  if (hasCatalog && stopLivePreviewsBtn) {
    stopLivePreviewsBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const jobId = state.scan.jobId;
      if (!jobId) return showToast('No scan running');

      try {
        stopLivePreviewsBtn.disabled = true;
        stopLivePreviewsBtn.textContent = 'Stopping…';
        const res = await gql(`mutation($jobId:String!){ cancelLivePreviews(jobId:$jobId){ ok message } }`, { jobId });
        const r = res.cancelLivePreviews;
        showToast(r?.message || 'Requested');
        await pollScanJobOnce(jobId);
      } catch (err) {
        showToast(err.message || String(err));
      } finally {
        stopLivePreviewsBtn.disabled = false;
        stopLivePreviewsBtn.textContent = 'Stop generation of animated previews';
      }
    };
  }

  if (hasCatalog && stopScanBtn) {
    stopScanBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const jobId = state.scan.jobId;
      if (!jobId) return showToast('No scan running');
      if (!confirm('Stop scanning files (recording discovery) for this scan?')) return;

      try {
        stopScanBtn.disabled = true;
        stopScanBtn.textContent = 'Stopping…';
        const res = await gql(`mutation($jobId:String!){ cancelScanCatalog(jobId:$jobId){ ok message } }`, { jobId });
        const r = res.cancelScanCatalog;
        showToast(r?.message || 'Requested');
        await pollScanJobOnce(jobId);
      } catch (err) {
        showToast(err.message || String(err));
      } finally {
        stopScanBtn.disabled = false;
        stopScanBtn.textContent = 'Stop scan';
      }
    };
  }

  if (hasCatalog && autoTagUntaggedBtn) {
    autoTagUntaggedBtn.hidden = false;
    if (isAutoTagBusy()) {
      autoTagUntaggedBtn.hidden = true;
      if (stopAutoTagBtn) stopAutoTagBtn.hidden = false;
    }
    autoTagUntaggedBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (isAutoTagBusy()) { showToast('Auto-tagging already running'); return; }
      try {
        autoTagUntaggedBtn.disabled = true;
        autoTagUntaggedBtn.textContent = 'Starting…';
        showToast('Starting auto-tagging…');
        const res = await gql(`mutation { startAutoTagUntagged { jobId status } }`);
        const jobId = res.startAutoTagUntagged?.jobId;
        if (!jobId) throw new Error('Failed to start auto-tag job');
        startAutoTagPolling(jobId);
      } catch (err) {
        showToast(err.message || String(err));
        autoTagUntaggedBtn.disabled = false;
        autoTagUntaggedBtn.textContent = 'Auto-tag untagged';
      }
    };
  }

  if (hasCatalog && stopAutoTagBtn) {
    stopAutoTagBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const jobId = state.autoTag.jobId;
      if (!jobId) return showToast('No auto-tag job running');
      if (!confirm('Stop the auto-tagging job?')) return;
      try {
        stopAutoTagBtn.disabled = true;
        stopAutoTagBtn.textContent = 'Stopping…';
        await gql(`mutation($jobId:String!){ cancelAutoTagJob(jobId:$jobId){ ok message } }`, { jobId });
        if (isExternalScanAutoTagJobId(jobId)) {
          const scanJobId = String(jobId).split(':', 2)[1] || state.scan.jobId;
          if (scanJobId) await pollScanJobOnce(scanJobId);
        } else {
          await pollAutoTagJobOnce(jobId);
        }
      } catch (err) {
        showToast(err.message || String(err));
      } finally {
        stopAutoTagBtn.disabled = false;
        stopAutoTagBtn.textContent = 'Stop auto-tagging';
      }
    };
  }

  // If a scan is already running, show progress immediately.
  if (hasCatalog && state.scan.jobId) {
    pollScanJobOnce(state.scan.jobId).catch(err => showToast(err.message || String(err)));
  }

  function updateSelectionUI() {
    if (selCountEl) selCountEl.textContent = `${state.selected.size} selected`;
    if (selActionsEl) selActionsEl.hidden = state.selected.size === 0;
    if (selectAllBtn) selectAllBtn.hidden = state.selected.size !== 0;
    if (clearSelectionBtn) clearSelectionBtn.hidden = state.selected.size === 0;

    // When at least one card is selected, clicks on cards toggle selection instead of opening details.
    if (gridEl) gridEl.classList.toggle('selection-mode', state.selected.size > 0);
  }

  function toggleCardSelection(cardEl) {
    if (!cardEl) return;
    const id = parseInt(cardEl.dataset.id, 10);
    if (!id) return;

    const next = !state.selected.has(id);
    if (next) state.selected.add(id);
    else state.selected.delete(id);

    const cb = cardEl.querySelector('input[type=checkbox][data-select]');
    if (cb) cb.checked = next;
    cardEl.classList.toggle('is-selected', next);
    updateSelectionUI();
  }

  async function fetchAllFilteredRecordingIds() {
    const chunk = 500;
    let offset = 0;
    let total = null;
    const ids = [];

    while (total == null || offset < total) {
      const vars = {
        limit: chunk,
        offset,
        search: state.search || null,
        nameContains: (state.recordingsFilter?.nameContains || '').trim() || null,
        negateNameContains: state.recordingsFilter?.negateNameContains ? true : null,
        nameContainsRegex: state.recordingsFilter?.nameContainsRegex ? true : null,
        streamerName: extraFilter.streamerName || null,
        siteName: extraFilter.siteName || null,
        tagName: extraFilter.tagName || null,
        missingLivePreview: state.recordingsFilter?.missingLivePreview ? true : null,
        minSizeBytes: state.recordingsFilter?.minSizeBytes ?? null,
        maxSizeBytes: state.recordingsFilter?.maxSizeBytes ?? null,
        minDurationSeconds: state.recordingsFilter?.minDurationSeconds ?? null,
        maxDurationSeconds: state.recordingsFilter?.maxDurationSeconds ?? null,
        minShortSide: state.recordingsFilter?.minShortSide ?? null,
        maxShortSide: state.recordingsFilter?.maxShortSide ?? null,
        videoCodecs: (state.recordingsFilter?.videoCodecs || []).length ? state.recordingsFilter.videoCodecs : null,
        orderBy: state.recordingsSort.by,
        orderDir: state.recordingsSort.dir,
      };

      const data = await gql(
        `query($limit:Int!, $offset:Int!, $search:String, $nameContains:String, $negateNameContains:Boolean, $nameContainsRegex:Boolean, $streamerName:String, $siteName:String, $tagName:String, $missingLivePreview:Boolean, $minSizeBytes:Float, $maxSizeBytes:Float, $minDurationSeconds:Float, $maxDurationSeconds:Float, $minShortSide:Int, $maxShortSide:Int, $videoCodecs:[String!], $orderBy:RecordingOrderBy, $orderDir:SortDirection) {
          recordings(limit:$limit, offset:$offset, search:$search, nameContains:$nameContains, negateNameContains:$negateNameContains, nameContainsRegex:$nameContainsRegex, streamerName:$streamerName, siteName:$siteName, tagName:$tagName, missingLivePreview:$missingLivePreview, minSizeBytes:$minSizeBytes, maxSizeBytes:$maxSizeBytes, minDurationSeconds:$minDurationSeconds, maxDurationSeconds:$maxDurationSeconds, minShortSide:$minShortSide, maxShortSide:$maxShortSide, videoCodecs:$videoCodecs, orderBy:$orderBy, orderDir:$orderDir) {
            total
            items { id }
          }
        }`,
        vars
      );

      const conn = data.recordings;
      const t = conn?.total;
      if (typeof t === 'number') total = t;
      const batch = (conn?.items || []).map(x => Number(x?.id)).filter(Boolean);
      batch.forEach(id => ids.push(id));

      offset += chunk;
      if (!batch.length) break;
    }

    return ids;
  }

  if (selectAllBtn) {
    selectAllBtn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        selectAllBtn.disabled = true;
        const ids = await fetchAllFilteredRecordingIds();
        if (!ids.length) {
          showToast('No recordings to select');
          return;
        }
        if (ids.length > 500 && !confirm(`Select ${ids.length} recording(s)?`)) {
          return;
        }
        ids.forEach(id => state.selected.add(id));
        await load();
      } catch (err) {
        showToast(err.message || String(err));
      } finally {
        selectAllBtn.disabled = false;
      }
    };
  }

  if (clearSelectionBtn) {
    clearSelectionBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      state.selected.clear();
      try {
        gridEl.querySelectorAll('input[type=checkbox][data-select]').forEach(cb => { cb.checked = false; });
        gridEl.querySelectorAll('.card.is-selected').forEach(card => card.classList.remove('is-selected'));
      } catch { /* ignore */ }
      updateSelectionUI();
    };
  }

  async function load() {
    if (!config.catalogRoot) {
      gridEl.innerHTML = `
        <div class="list-item" style="grid-column: 1 / -1">
          <div class="h1" style="font-size:18px; margin-bottom:8px">Catalog root is not configured</div>
          <div class="subtle">Go to Settings and set the catalog folder path, then scan.</div>
          <div style="margin-top:10px"><a class="small-link" href="#settings">Open Settings</a></div>
        </div>
      `;
      pageInfoEl.textContent = '';

      if (pageInfoTopEl) pageInfoTopEl.textContent = '';
      if (pageSelectEl) pageSelectEl.innerHTML = '';
      if (pageSelectTopEl) pageSelectTopEl.innerHTML = '';
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
      if (prevTopBtn) prevTopBtn.disabled = true;
      if (nextTopBtn) nextTopBtn.disabled = true;

      return;
    }

    const vars = {
      limit: state.pageSize,
      offset: state.offset,
      search: state.search || null,
      nameContains: (state.recordingsFilter?.nameContains || '').trim() || null,
      negateNameContains: state.recordingsFilter?.negateNameContains ? true : null,
      nameContainsRegex: state.recordingsFilter?.nameContainsRegex ? true : null,
      streamerName: extraFilter.streamerName || null,
      siteName: extraFilter.siteName || null,
      tagName: extraFilter.tagName || null,
      missingLivePreview: state.recordingsFilter?.missingLivePreview ? true : null,
      minSizeBytes: state.recordingsFilter?.minSizeBytes ?? null,
      maxSizeBytes: state.recordingsFilter?.maxSizeBytes ?? null,
      minDurationSeconds: state.recordingsFilter?.minDurationSeconds ?? null,
      maxDurationSeconds: state.recordingsFilter?.maxDurationSeconds ?? null,
      minShortSide: state.recordingsFilter?.minShortSide ?? null,
      maxShortSide: state.recordingsFilter?.maxShortSide ?? null,
      videoCodecs: (state.recordingsFilter?.videoCodecs || []).length ? state.recordingsFilter.videoCodecs : null,
      orderBy: state.recordingsSort.by,
      orderDir: state.recordingsSort.dir,
    };

    const data = await gql(`
      query($limit:Int!, $offset:Int!, $search:String, $nameContains:String, $negateNameContains:Boolean, $nameContainsRegex:Boolean, $streamerName:String, $siteName:String, $tagName:String, $missingLivePreview:Boolean, $minSizeBytes:Float, $maxSizeBytes:Float, $minDurationSeconds:Float, $maxDurationSeconds:Float, $minShortSide:Int, $maxShortSide:Int, $videoCodecs:[String!], $orderBy:RecordingOrderBy, $orderDir:SortDirection) {
        recordings(limit:$limit, offset:$offset, search:$search, nameContains:$nameContains, negateNameContains:$negateNameContains, nameContainsRegex:$nameContainsRegex, streamerName:$streamerName, siteName:$siteName, tagName:$tagName, missingLivePreview:$missingLivePreview, minSizeBytes:$minSizeBytes, maxSizeBytes:$maxSizeBytes, minDurationSeconds:$minDurationSeconds, maxDurationSeconds:$maxDurationSeconds, minShortSide:$minShortSide, maxShortSide:$maxShortSide, videoCodecs:$videoCodecs, orderBy:$orderBy, orderDir:$orderDir) {
          total
          totalSizeBytes
          items { id title fileName relPath recordedAt durationSeconds width height sizeBytes videoCodec audioCodec thumbnailUrl livePreviewUrl contactSheetUrl streamer { name site { name } } site { name } tags { name } }
        }
      }
    `, vars);

    const conn = data.recordings;
    const total = conn.total || 0;
    const totalSizeBytes = (typeof conn.totalSizeBytes === 'number') ? conn.totalSizeBytes : 0;

    // Auto-start scan if the catalog is empty (app startup experience).
    // Also used after first-time catalog creation from Settings.
    try {
      const force = sessionStorage.getItem('camero.forceAutoScan') === '1';
      const key = `camero.autoScanDone:${String(config.catalogRoot || '').replace(/\s+/g, ' ')}`;
      const already = sessionStorage.getItem(key) === '1';
      const isRootView = !extraFilter.streamerName && !extraFilter.siteName && !extraFilter.tagName;
      const canAuto = isRootView && !state.scan.jobId && Number(total || 0) === 0;
      if (canAuto && (force || !already)) {
        sessionStorage.setItem(key, '1');
        sessionStorage.removeItem('camero.forceAutoScan');
        await startScanJobFromUI();
      }
    } catch { /* ignore */ }

    // Only show "Generate animated previews" when there are recordings.
    // Do not override job-driven visibility while a scan/preview job is running.
    try {
      if (genMissingBtn) {
        const hasAny = Number(total || 0) > 0;
        if (!state.scan.jobId) {
          genMissingBtn.hidden = !hasAny;
        }
      }
    } catch { /* ignore */ }

    const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
    const currentPage = total ? Math.floor(state.offset / state.pageSize) + 1 : 1;
    const startIdx = total ? Math.min(state.offset + 1, total) : 0;
    const endIdx = total ? Math.min(state.offset + state.pageSize, total) : 0;
    const sizeHint = total ? ` (${fmtBytes(totalSizeBytes)})` : '';
    const pageInfoText = `${total} total${sizeHint} • ${totalPages} pages • showing ${startIdx}-${endIdx}`;
    pageInfoEl.textContent = pageInfoText;
    if (pageInfoTopEl) pageInfoTopEl.textContent = pageInfoText;

    const opts = buildPageOptions(totalPages, currentPage);
    const optsHtml = opts
      .map(o => `<option value="${o.value}" ${o.disabled ? 'disabled' : ''} ${o.value === currentPage ? 'selected' : ''}>${o.label}</option>`)
      .join('');

    if (pageSelectEl) pageSelectEl.innerHTML = optsHtml;
    if (pageSelectTopEl) pageSelectTopEl.innerHTML = optsHtml;

    const isFirst = state.offset <= 0;
    const isLast = (state.offset + state.pageSize) >= total;
    if (prevBtn) prevBtn.disabled = isFirst;
    if (nextBtn) nextBtn.disabled = isLast;
    if (prevTopBtn) prevTopBtn.disabled = isFirst;
    if (nextTopBtn) nextTopBtn.disabled = isLast;

    // Expire optimistic deletions.
    try {
      const now = Date.now();
      for (const [rid, exp] of state._recentlyDeleted.entries()) {
        if (!exp || exp <= now) state._recentlyDeleted.delete(rid);
      }
    } catch { /* ignore */ }

    const items = (conn.items || []).filter((r) => {
      const rid = Number(r?.id);
      return !(rid && state._recentlyDeleted.has(rid));
    });

    gridEl.innerHTML = items.map(r => {
      const res = r.width && r.height ? `${r.width}×${r.height}` : null;
      const streamerName = r.streamer?.name ?? null;
      const siteName = r.site?.name ?? r.streamer?.site?.name ?? null;
      const dateText = r.recordedAt ? new Date(r.recordedAt).toLocaleString() : null;
      const tags = (r.tags || []).map(t => `<span class="badge tag">${escapeHtml(t.name)}</span>`).join('');
      const hasPreview = !!r.livePreviewUrl;
      const isPortrait = !!(r.width && r.height && Number(r.height) > Number(r.width));
      const vCodec = (r.videoCodec || '').trim();
      const rawName = (r.fileName || r.relPath || '').trim();
      const ext = rawName.includes('.') ? rawName.split('.').pop() : '';
      const container = String(ext || '').trim().toLowerCase();
      const pills = [
        r.durationSeconds != null ? `<span class="badge">${fmtDuration(r.durationSeconds)}</span>` : '',
        r.sizeBytes != null ? `<span class="badge">${fmtBytes(r.sizeBytes)}</span>` : '',
        res ? `<span class="badge">${escapeHtml(res)}</span>` : '',
        vCodec ? `<span class="badge pill-codec" title="${escapeHtml(vCodec)}">${escapeHtml(vCodec)}</span>` : '',
        container ? `<span class="badge">${escapeHtml(container)}</span>` : '',
      ].filter(Boolean).join('');

      const streamerLink = streamerName
        ? `<a class="small-link" href="#streamer:${encodeURIComponent(streamerName)}" onclick="event.stopPropagation()">${escapeHtml(streamerName)}</a>`
        : '';
      const siteLink = siteName
        ? `<a class="small-link" href="#site:${encodeURIComponent(siteName)}" onclick="event.stopPropagation()">${escapeHtml(siteName)}</a>`
        : '';
      const streamerSiteLine = streamerLink || siteLink
        ? `<div style="grid-column: 1 / -1">${streamerLink ? `Streamer: ${streamerLink}` : ''}${streamerLink && siteLink ? ' <span class="subtle">•</span> ' : ''}${siteLink ? `Site: ${siteLink}` : ''}</div>`
        : '';

      return `
        <div class="card ${hasPreview ? 'has-preview' : ''} ${isPortrait ? 'is-portrait' : ''} ${state.selected.has(r.id) ? 'is-selected' : ''}" data-id="${r.id}">
          <div class="thumb-wrap">
            <img class="thumb thumb-img" src="/thumb/${r.id}" alt="thumbnail" onerror="this.style.display='none'" />
            ${hasPreview ? `<video class="thumb thumb-video" muted loop playsinline preload="none"></video>` : ''}
            <label class="select-overlay" onclick="event.stopPropagation()">
              <input type="checkbox" data-select="${r.id}" ${state.selected.has(r.id) ? 'checked' : ''} aria-label="Select recording" />
            </label>
            ${hasPreview ? '' : `<button class="thumb-action-btn" data-gen-preview="${r.id}" onclick="event.stopPropagation()">Create preview</button>`}
          </div>
          <div class="card-body">
            <div class="title recording-title">${escapeHtml(r.title)}</div>
            <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px; flex-wrap:wrap">
              <div class="card-pills" style="display:flex; gap:5px; align-items:center; flex-wrap:wrap">${pills}</div>
            </div>
            <div class="meta meta-recording">
              ${streamerSiteLine}
              ${dateText ? `<div style="grid-column: 1 / -1">Date: ${escapeHtml(dateText)}</div>` : ''}
              <div style="grid-column: 1 / -1" class="tags-scroll-wrap" data-tags-wrap="${r.id}" onclick="event.stopPropagation()"><button class="tags-scroll-btn" data-scroll-prev hidden>&#8249;</button><div class="tags-scroll-inner" data-tags-line="${r.id}">${tags}</div><button class="tags-scroll-btn" data-scroll-next>&#8250;</button></div>
            </div>
            <div style="display:flex; justify-content:flex-start; margin-top:10px; gap:8px" onclick="event.stopPropagation()">
              <button class="icon-btn" data-open-folder="${r.id}" title="Open containing folder">📁</button>
              <button class="icon-btn" data-open-video="${r.id}" title="Open video in default player">▶️</button>
              ${r.contactSheetUrl ? `<button class="icon-btn" data-open-contact-sheet="${r.id}" data-contact-sheet-url="${escapeHtml(r.contactSheetUrl)}" data-contact-sheet-title="${escapeHtml(r.title || r.fileName || 'Contact sheet')}" title="Show contact sheet">🖼️</button>` : ''}
              <button class="icon-btn" data-open-tags="${r.id}" title="Assign tags">🏷️</button>
            </div>
          </div>
        </div>
      `;
    }).join('');

    // Card click handlers
    gridEl.querySelectorAll('.card').forEach(card => {
      card.addEventListener('click', async (e) => {
        const id = parseInt(card.dataset.id, 10);
        if (!id) return;

        // Selection mode: clicking a card toggles its selection.
        if (state.selected.size > 0) {
          e.preventDefault();
          toggleCardSelection(card);
          return;
        }

        await openRecording(id);
      });
    });

    // Hover animated preview handlers
    gridEl.querySelectorAll('.card.has-preview .thumb-wrap').forEach(wrap => {
      const card = wrap.closest('.card');
      const id = card ? parseInt(card.dataset.id, 10) : null;
      if (!id) return;

      const video = wrap.querySelector('video.thumb-video');
      const img = wrap.querySelector('img.thumb-img');
      if (!video) return;

      // Lazy-attach src on first hover.
      let attached = false;

      wrap.addEventListener('mouseenter', () => {
        if (state.recordingsAutoPreview) return;
        try {
          if (!attached) {
            video.src = `/preview/${id}.mp4`;
            attached = true;
          }
          video.currentTime = 0;
          video.play().catch(() => {});
          video.classList.add('active');
          if (img) img.classList.add('hide');
        } catch { /* ignore */ }
      });

      wrap.addEventListener('mouseleave', () => {
        if (state.recordingsAutoPreview) return;
        try {
          video.pause();
          video.classList.remove('active');
          if (img) img.classList.remove('hide');
        } catch { /* ignore */ }
      });
    });

    // Generate preview from card
    gridEl.querySelectorAll('button[data-gen-preview]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        const rid = parseInt(btn.dataset.genPreview, 10);
        if (!rid) return;
        await startLivePreviewFromCard(rid, btn);
      });
    });

    // Open containing folder from card
    gridEl.querySelectorAll('button[data-open-folder]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        const rid = parseInt(btn.dataset.openFolder, 10);
        if (!rid) return;
        await openRecordingFolder(rid, btn);
      });
    });

    // Open video from card (default OS player)
    gridEl.querySelectorAll('button[data-open-video]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        const rid = parseInt(btn.dataset.openVideo, 10);
        if (!rid) return;
        await openRecordingVideo(rid, btn);
      });
    });

    gridEl.querySelectorAll('button[data-open-contact-sheet]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        openContactSheetModal(btn.dataset.contactSheetTitle, btn.dataset.contactSheetUrl);
      });
    });

    // Assign tags from card
    gridEl.querySelectorAll('button[data-open-tags]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        const rid = parseInt(btn.dataset.openTags, 10);
        if (!rid) return;
        await openTagPicker([rid]);
      });
    });

    // Selection handlers
    gridEl.querySelectorAll('input[type=checkbox][data-select]').forEach(cb => {
      cb.addEventListener('change', () => {
        const id = parseInt(cb.dataset.select, 10);
        if (cb.checked) state.selected.add(id); else state.selected.delete(id);
        const card = cb.closest('.card');
        if (card) card.classList.toggle('is-selected', cb.checked);
        updateSelectionUI();
      });
    });

    updateSelectionUI();

    // Tag scroll buttons on cards
    gridEl.querySelectorAll('[data-tags-wrap]').forEach(wrap => {
      const inner = wrap.querySelector('.tags-scroll-inner');
      const prevBtn = wrap.querySelector('[data-scroll-prev]');
      const nextBtn = wrap.querySelector('[data-scroll-next]');
      if (!inner || !prevBtn || !nextBtn) return;
      function updateTagScrollBtns() {
        prevBtn.hidden = inner.scrollLeft <= 0;
        nextBtn.hidden = inner.scrollWidth <= inner.clientWidth + 1 ||
          inner.scrollLeft >= inner.scrollWidth - inner.clientWidth - 1;
      }
      updateTagScrollBtns();
      inner.addEventListener('scroll', updateTagScrollBtns, { passive: true });
      prevBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        inner.scrollBy({ left: -120, behavior: 'smooth' });
      });
      nextBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        inner.scrollBy({ left: 120, behavior: 'smooth' });
      });
    });

    // Apply global auto-preview mode after rendering cards.
    applyAutoPreviewMode(state.recordingsAutoPreview);
  }

  state.scan.refreshList = async () => {
    if (!rootEl || !rootEl.isConnected) return;
    const r = route();
    if (!['recordings', 'streamer', 'site', 'tag'].includes(r.name)) return;
    await load();
  };

  state.autoTag.refreshList = async () => {
    if (!rootEl || !rootEl.isConnected) return;
    const r = route();
    if (!['recordings', 'streamer', 'site', 'tag'].includes(r.name)) return;
    await load();
  };

  document.getElementById(`${idPrefix}-pageSize`).onchange = (e) => {
    state.pageSize = parseInt(e.target.value, 10);
    state.offset = 0;
    load().catch(e => showToast(e.message));
  };

  document.getElementById(`${idPrefix}-sortBy`).onchange = (e) => {
    state.recordingsSort.by = e.target.value;
    state.offset = 0;
    load().catch(e => showToast(e.message));
  };

  document.getElementById(`${idPrefix}-sortDir`).onchange = (e) => {
    state.recordingsSort.dir = e.target.value;
    state.offset = 0;
    load().catch(e => showToast(e.message));
  };

  if (pageSelectEl) {
    pageSelectEl.onchange = (e) => {
      const page = parseInt(e.target.value, 10);
      if (!page || page < 1) return;
      state.offset = (page - 1) * state.pageSize;
      load().catch(err => showToast(err.message));
    };
  }

  if (pageSelectTopEl) {
    pageSelectTopEl.onchange = (e) => {
      const page = parseInt(e.target.value, 10);
      if (!page || page < 1) return;
      state.offset = (page - 1) * state.pageSize;
      load().catch(err => showToast(err.message));
    };
  }

  document.getElementById(`${idPrefix}-prev`).onclick = () => {
    state.offset = Math.max(0, state.offset - state.pageSize);
    load().catch(e => showToast(e.message));
  };

  if (prevTopBtn) {
    prevTopBtn.onclick = () => {
      state.offset = Math.max(0, state.offset - state.pageSize);
      load().catch(e => showToast(e.message));
    };
  }

  document.getElementById(`${idPrefix}-next`).onclick = async () => {
    // We don't know total without re-query; just try.
    state.offset = state.offset + state.pageSize;
    await load().catch(e => showToast(e.message));
  };

  if (nextTopBtn) {
    nextTopBtn.onclick = async () => {
      state.offset = state.offset + state.pageSize;
      await load().catch(e => showToast(e.message));
    };
  }

  document.getElementById(`${idPrefix}-deleteSelected`).onclick = async () => {
    if (!state.selected.size) return showToast('No recordings selected');
    if (!confirm(`Delete ${state.selected.size} recording(s) from disk?`)) return;
    try {
      const ids = Array.from(state.selected).map(Number).filter(Boolean);
      const res = await gql(`mutation($ids:[Int!]!){ deleteRecordings(ids:$ids){ ok message } }`, { ids });
      const r = res.deleteRecordings;
      showToast(r?.message || 'Deleted');
      if (r && r.ok === false) return;

      // Optimistic: hide deleted cards immediately.
      const expiresAt = Date.now() + 10_000;
      ids.forEach((id) => state._recentlyDeleted.set(id, expiresAt));
      try {
        ids.forEach((id) => {
          const el = gridEl.querySelector(`.card[data-id="${CSS.escape(String(id))}"]`);
          if (el) el.remove();
        });
      } catch { /* ignore */ }

      state.selected.clear();
      updateSelectionUI();
      await load();
    } catch (e) {
      showToast(e.message);
    }
  };

  document.getElementById(`${idPrefix}-tagSelected`).onclick = async () => {
    await openTagPicker(Array.from(state.selected));
  };

  document.getElementById(`${idPrefix}-autoTagSelected`).onclick = async () => {
    if (!state.selected.size) return showToast('No recordings selected');
    if (isAutoTagBusy()) return showToast('Auto-tagging already running');
    const ids = Array.from(state.selected).map(Number).filter(Boolean);
    const btn = document.getElementById(`${idPrefix}-autoTagSelected`);
    if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
    try {
      const res = await gql(
        `mutation($ids:[Int!]!){ startAutoTagRecordings(recordingIds:$ids){ jobId status } }`,
        { ids }
      );
      const jobId = res.startAutoTagRecordings?.jobId;
      if (!jobId) throw new Error('Failed to start auto-tag job');
      showToast(`Auto-tagging ${ids.length} recording${ids.length !== 1 ? 's' : ''}…`);
      startAutoTagPolling(jobId);
    } catch (e) {
      showToast(e.message || String(e));
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Auto-tag'; }
    }
  };

  document.getElementById(`${idPrefix}-siteSelected`).onclick = async () => {
    await openSitePicker(Array.from(state.selected));
  };

  document.getElementById(`${idPrefix}-moveSelected`).onclick = async () => {
    if (!state.selected.size) return showToast('No recordings selected');
    const ids = Array.from(state.selected).map(Number).filter(Boolean);
    await openMoveCopyDialog({
      mode: 'move',
      ids,
      onSuccess: async () => {
        state.selected.clear();
        await load();
      },
    });
  };

  document.getElementById(`${idPrefix}-copySelected`).onclick = async () => {
    if (!state.selected.size) return showToast('No recordings selected');
    const ids = Array.from(state.selected).map(Number).filter(Boolean);
    await openMoveCopyDialog({
      mode: 'copy',
      ids,
      onSuccess: async () => {
        state.selected.clear();
        await load();
      },
    });
  };

  document.getElementById(`${idPrefix}-rescanSelected`).onclick = async () => {
    if (!state.selected.size) return showToast('No recordings selected');
    try {
      const ids = Array.from(state.selected);
      const res = await gql(
        `mutation($ids:[Int!]!){ rescanRecordingsMetadata(ids:$ids){ ok message } }`,
        { ids }
      );
      showToast(res.rescanRecordingsMetadata?.message || 'Rescan complete');
      await load();
    } catch (e) {
      showToast(e.message);
    }
  };

  document.getElementById(`${idPrefix}-reencodeSelected`).onclick = async () => {
    await openReencodeModal(Array.from(state.selected));
  };

  await load().catch(e => showToast(e.message));
}

async function openRecording(id) {
  const data = await gql(`query($id:Int!){ recording(id:$id){ id title recordedAt durationSeconds width height sizeBytes videoCodec audioCodec relPath fileName livePreviewUrl streamer{ name site{ name } } site{ name } tags{ name } } }`, { id });
  const r = data.recording;
  if (!r) return showToast('Recording not found');

  const res = r.width && r.height ? `${r.width}×${r.height}` : 'Unknown';
  const streamerName = r.streamer?.name ?? null;
  const siteName = r.site?.name ?? r.streamer?.site?.name ?? null;
  const dateText = r.recordedAt ? new Date(r.recordedAt).toLocaleString() : null;
  let tagNames = (r.tags || []).map(t => String(t?.name || '').trim()).filter(Boolean);
  function tagsHtml() {
    if (!tagNames.length) return '<span class="subtle">No tags</span>';
    return tagNames
      .map((n) => {
        const safe = escapeHtml(n);
        const encoded = encodeURIComponent(n);
        return `<span class="badge tag tag-pill"><a class="tag-pill-link" href="#tag:${encoded}" onclick="closeModal()" title="Browse recordings with this tag">${safe}</a><button class="tag-x" type="button" data-remove-tag="${safe}" title="Remove tag" aria-label="Remove tag">×</button></span>`;
      })
      .join(' ');
  }
  const vCodec = (r.videoCodec || '').trim();
  const aCodec = (r.audioCodec || '').trim();

  const ext = fileExt(r.fileName);
  const mime = guessVideoMimeFromExt(ext);
  const fileNameSafe = r.fileName || `recording.${ext || 'bin'}`;
  const mediaUrl = `/media/${r.id}/${encodeURIComponent(fileNameSafe)}`;
  const shouldTranscode = needsTranscodeForPlayback(ext);
  const shouldRemux = !shouldTranscode && needsRemux(ext);

  openModal(r.title, `
    <div class="two-col">
      <div>
        <video
          id="player-${r.id}"
          class="video-js vjs-fluid vjs-big-play-centered"
          controls
          preload="auto"
          data-setup='{}'
        >
          <source src="${mediaUrl}" ${mime ? `type="${mime}"` : ''} />
        </video>

        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px" onclick="event.stopPropagation()">
          <button class="btn" id="setStreamerAvatarFromFrameBtn">Set current frame as streamer avatar</button>
          <button class="btn" id="setRecordingThumbFromFrameBtn">Set current frame as recording thumbnail</button>
        </div>

        <div id="remuxWrap" class="progress-wrap" style="margin-top:12px" ${shouldRemux ? '' : 'hidden'}>
          <div class="progress-row">
            <div class="progress"><div id="remuxBar" class="progress-bar" style="width:0%"></div></div>
            <div id="remuxPct" class="subtle">0%</div>
          </div>
          <div id="remuxText" class="subtle" style="margin-top:8px"></div>
        </div>

        <div id="transcodeWrap" class="progress-wrap" style="margin-top:12px" ${shouldTranscode ? '' : 'hidden'}>
          <div class="progress-row">
            <div class="progress"><div id="transcodeBar" class="progress-bar" style="width:0%"></div></div>
            <div id="transcodePct" class="subtle">0%</div>
          </div>
          <div id="transcodeText" class="subtle" style="margin-top:8px"></div>
        </div>

        <div id="previewWrap" class="progress-wrap" style="margin-top:12px" hidden>
          <div class="progress-row">
            <div class="progress"><div id="previewBar" class="progress-bar" style="width:0%"></div></div>
            <div id="previewPct" class="subtle">0%</div>
          </div>
          <div id="previewText" class="subtle" style="margin-top:8px"></div>
        </div>
      </div>
      <div>
        <div class="list">
          <div class="list-item">
            <div class="h1" style="font-size:16px; margin-bottom:10px">Details</div>
            <div class="kv">
              <b>Streamer</b><div>
                ${streamerName
                  ? `<a class="small-link" href="#streamer:${encodeURIComponent(streamerName)}" onclick="closeModal()">${escapeHtml(streamerName)}</a>`
                  : `<span class="subtle">—</span> <button class="icon-btn" data-edit-meta="streamer" title="Set streamer">✎</button>`
                }
              </div>
              <b>Site</b><div>
                ${siteName
                  ? `<a class="small-link" href="#site:${encodeURIComponent(siteName)}" onclick="closeModal()">${escapeHtml(siteName)}</a>`
                  : `<span class="subtle">—</span> <button class="icon-btn" data-edit-meta="site" title="Set site">✎</button>`
                }
              </div>
              <b>Date</b><div>
                ${dateText
                  ? `${escapeHtml(dateText)}`
                  : `<span class="subtle">—</span> <button class="icon-btn" data-edit-meta="recordedAt" title="Set date/time">✎</button>`
                }
              </div>
              <b>Duration</b><div>${fmtDuration(r.durationSeconds)}</div>
              <b>Size</b><div>${fmtBytes(r.sizeBytes)}</div>
              <b>Resolution</b><div>${escapeHtml(res)}</div>
              ${vCodec ? `<b>Video codec</b><div>${escapeHtml(vCodec)}</div>` : ''}
              ${aCodec ? `<b>Audio codec</b><div>${escapeHtml(aCodec)}</div>` : ''}
              <b>File</b><div class="subtle">${escapeHtml(r.relPath)}</div>
            </div>
          </div>
          <div class="list-item">
            <div class="subtle" style="margin-bottom:8px">Tags</div>
            <div id="recordingTags" style="display:flex; flex-wrap:wrap; gap:8px">${tagsHtml()}</div>
            <div style="display:flex; gap:10px; margin-top:12px">
              <button class="btn" id="addTagBtn">Assign tag</button>
              <button class="btn" id="autoTagBtn">Auto-tag</button>
              ${r.livePreviewUrl ? '' : `<button class="btn" id="previewBtn">Create animated preview</button>`}
              <button class="btn" id="remuxBtn" ${shouldRemux ? '' : 'hidden'}>Make playable (remux MP4)</button>
              <button class="btn" id="transcodeBtn" ${shouldTranscode ? '' : 'hidden'}>Make playable (transcode MP4)</button>
              <button class="btn btn-danger" id="deleteBtn">Delete</button>
            </div>
            ${shouldRemux ? `<div class="subtle" style="margin-top:10px">This file is <b>.${escapeHtml(ext)}</b>. Browsers usually can't play it directly; Camero will remux it to MP4 (no transcoding).</div>` : ''}
            ${shouldTranscode ? `<div class="subtle" style="margin-top:10px">This file is <b>.${escapeHtml(ext)}</b>. Most browsers can't play FLV; Camero will transcode it to a browser-friendly MP4 (H.264/AAC).</div>` : ''}
            ${r.livePreviewUrl ? `<div class="subtle" style="margin-top:10px">Animated preview is available (hover on the card thumbnail).</div>` : `<div class="subtle" style="margin-top:10px">An animated preview is a 15s MP4 built from 15×1s fragments across the video.</div>`}
          </div>
        </div>
      </div>
    </div>
  `);

  try {
    window.videojs?.(`player-${r.id}`);
  } catch { /* ignore */ }

  function getCurrentPlayerTimeSeconds() {
    try {
      const player = window.videojs?.(`player-${r.id}`);
      if (player && typeof player.currentTime === 'function') {
        const t = Number(player.currentTime() || 0);
        return Number.isFinite(t) ? t : 0;
      }
    } catch { /* ignore */ }

    try {
      const el = document.getElementById(`player-${r.id}`);
      if (el && typeof el.currentTime === 'number') {
        const t = Number(el.currentTime || 0);
        return Number.isFinite(t) ? t : 0;
      }
    } catch { /* ignore */ }

    return 0;
  }

  function getPlayerDurationSeconds() {
    try {
      const player = window.videojs?.(`player-${r.id}`);
      if (player && typeof player.duration === 'function') {
        const d = Number(player.duration() || 0);
        return Number.isFinite(d) ? d : 0;
      }
    } catch { /* ignore */ }

    try {
      const el = document.getElementById(`player-${r.id}`);
      if (el && typeof el.duration === 'number') {
        const d = Number(el.duration || 0);
        return Number.isFinite(d) ? d : 0;
      }
    } catch { /* ignore */ }

    return 0;
  }

  function setPlayerTimeSeconds(t) {
    const target = Number(t || 0);
    const tNorm = Number.isFinite(target) ? target : 0;

    try {
      const player = window.videojs?.(`player-${r.id}`);
      if (player && typeof player.currentTime === 'function') {
        player.currentTime(tNorm);
        return true;
      }
    } catch { /* ignore */ }

    try {
      const el = document.getElementById(`player-${r.id}`);
      if (el && typeof el.currentTime === 'number') {
        el.currentTime = tNorm;
        return true;
      }
    } catch { /* ignore */ }

    return false;
  }

  function seekPlayerBySeconds(deltaSeconds) {
    const cur = getCurrentPlayerTimeSeconds();
    const dur = getPlayerDurationSeconds();
    const delta = Number(deltaSeconds || 0);
    const d = Number.isFinite(delta) ? delta : 0;
    let next = cur + d;
    if (!Number.isFinite(next)) next = 0;
    if (next < 0) next = 0;

    // Clamp to duration if known.
    if (dur && Number.isFinite(dur) && dur > 0) {
      const maxT = Math.max(0, dur - 0.05);
      if (next > maxT) next = maxT;
    }

    setPlayerTimeSeconds(next);
  }

  // Modal hotkeys: skip ±5s with left/right arrows.
  setModalKeydownHandler((e) => {
    try {
      if (!e || modalEl.hidden) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;

      const active = document.activeElement;
      const tag = active && active.tagName ? String(active.tagName).toUpperCase() : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (active && active.isContentEditable)) return;

      // Only handle when the recording player exists in the current modal.
      const el = document.getElementById(`player-${r.id}`);
      if (!el) return;

      e.preventDefault();
      e.stopPropagation();
      seekPlayerBySeconds(e.key === 'ArrowLeft' ? -5 : 5);
    } catch { /* ignore */ }
  });

  const setThumbBtn = document.getElementById('setRecordingThumbFromFrameBtn');
  if (setThumbBtn) {
    setThumbBtn.onclick = async () => {
      setThumbBtn.disabled = true;
      try {
        const t = getCurrentPlayerTimeSeconds();
        const res = await gql(
          `mutation($id:Int!, $t:Float!){ setRecordingThumbnailFromFrame(recordingId:$id, timeSeconds:$t){ ok message } }`,
          { id: r.id, t }
        );
        showToast(res.setRecordingThumbnailFromFrame.message);
        // Refresh list thumbnails.
        render().catch(() => {});
      } catch (e) {
        showToast(e.message);
      } finally {
        setThumbBtn.disabled = false;
      }
    };
  }

  const setAvatarBtn = document.getElementById('setStreamerAvatarFromFrameBtn');
  if (setAvatarBtn) {
    setAvatarBtn.onclick = async () => {
      setAvatarBtn.disabled = true;
      try {
        const t = getCurrentPlayerTimeSeconds();
        const res = await gql(
          `mutation($id:Int!, $t:Float!){ setStreamerAvatarFromFrame(recordingId:$id, timeSeconds:$t){ ok message } }`,
          { id: r.id, t }
        );
        showToast(res.setStreamerAvatarFromFrame.message);
        // Refresh streamers view thumbnails if visible.
        render().catch(() => {});
      } catch (e) {
        showToast(e.message);
      } finally {
        setAvatarBtn.disabled = false;
      }
    };
  }

  document.getElementById('addTagBtn').onclick = async () => {
    closeModal();
    await openTagPicker([r.id]);
  };

  const autoTagBtn = document.getElementById('autoTagBtn');
  if (autoTagBtn) {
    autoTagBtn.onclick = async () => {
      autoTagBtn.disabled = true;
      const origText = autoTagBtn.textContent;
      try {
        autoTagBtn.textContent = 'Tagging…';
        const res = await gql(
          `mutation($ids:[Int!]!){ startAutoTagRecordings(recordingIds:$ids){ jobId status } }`,
          { ids: [r.id] }
        );
        const jobId = res.startAutoTagRecordings?.jobId;
        if (!jobId) throw new Error('Failed to start auto-tag job');
        showToast('Auto-tagging started…');

        // Poll until done.
        let done = false;
        while (!done) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          try {
            const d = await gql(
              `query($jobId:String!){ autoTagJob(jobId:$jobId){ jobId status error progress { total completed } } }`,
              { jobId }
            );
            const j = d.autoTagJob;
            if (!j) break;
            const p = j.progress || {};
            if (j.status === 'running' || j.status === 'queued') {
              autoTagBtn.textContent = `Tagging…`;
            } else if (j.status === 'done') {
              done = true;
              showToast(`Auto-tagging complete for this recording.`);
            } else if (j.status === 'cancelled') {
              done = true;
              showToast('Auto-tagging cancelled.');
            } else if (j.status === 'error') {
              done = true;
              showToast(j.error || 'Auto-tagging failed');
            }
          } catch { break; }
        }
        // Refresh tags in modal if still open.
        try {
          const updated = await gql(
            `query($id:Int!){ recording(id:$id){ tags { name } } }`,
            { id: r.id }
          );
          if (updated?.recording?.tags) {
            tagNames = updated.recording.tags.map(t => t.name);
            refreshRecordingTagsUI();
          }
        } catch { /* ignore */ }
        render().catch(() => {});
      } catch (e) {
        showToast(e.message || String(e));
      } finally {
        autoTagBtn.disabled = false;
        autoTagBtn.textContent = origText;
      }
    };
  }

  async function refreshRecordingTagsUI() {
    const wrap = document.getElementById('recordingTags');
    if (!wrap) return;
    wrap.innerHTML = tagsHtml();

    wrap.querySelectorAll('button[data-remove-tag]').forEach((btn) => {
      btn.onclick = async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();

        const name = String(btn.dataset.removeTag || '').trim();
        if (!name) return;
        if (!tagNames.includes(name)) return;

        const next = tagNames.filter(t => t !== name);
        btn.disabled = true;
        try {
          const res = await gql(
            `mutation($recordingIds:[Int!]!, $tagNames:[String!]!){ setTags(recordingIds:$recordingIds, tagNames:$tagNames){ ok message } }`,
            { recordingIds: [r.id], tagNames: next }
          );
          showToast(res.setTags?.message || 'Updated');
          tagNames = next;
          refreshRecordingTagsUI();
          // Refresh lists/filters best-effort.
          render().catch(() => {});
        } catch (e) {
          showToast(e.message);
          btn.disabled = false;
        }
      };
    });
  }

  refreshRecordingTagsUI();

  document.getElementById('deleteBtn').onclick = async () => {
    if (!confirm('Delete this recording from disk?')) return;
    try {
      const res = await gql(`mutation($ids:[Int!]!){ deleteRecordings(ids:$ids){ ok message } }`, { ids: [r.id] });
      const dr = res.deleteRecordings;
      showToast(dr?.message || 'Deleted');
      if (dr && dr.ok === false) return;

      // Optimistic hide for list view.
      try { state._recentlyDeleted.set(Number(r.id), Date.now() + 10_000); } catch { /* ignore */ }
      closeModal();
      await render();
    } catch (e) {
      showToast(e.message);
    }
  };

  document.querySelectorAll('[data-edit-meta]').forEach((btn) => {
    btn.onclick = async (ev) => {
      ev.preventDefault();
      const field = btn.dataset.editMeta;
      try {
        let vars = { id: r.id };
        if (field === 'streamer') {
          const v = prompt('Streamer name (leave blank to clear):', '');
          if (v === null) return;
          vars.streamer = v;
        } else if (field === 'site') {
          const v = prompt('Site name (leave blank to clear):', '');
          if (v === null) return;
          vars.site = v;
        } else if (field === 'recordedAt') {
          const v = prompt('Recorded at (ISO, e.g. 2026-01-12 21:30 or 2026-01-12T21:30:00):', '');
          if (v === null) return;
          vars.recordedAt = v;
        } else {
          return;
        }

        await gql(
          `mutation($id:Int!, $streamer:String, $site:String, $recordedAt:String){ setRecordingMetadata(recordingId:$id, streamerName:$streamer, siteName:$site, recordedAt:$recordedAt){ id } }`,
          vars
        );
        showToast('Saved');
        closeModal();
        await openRecording(r.id);
        // Refresh lists so cards/filters reflect new metadata.
        render().catch(() => {});
      } catch (e) {
        showToast(e.message);
      }
    };
  });

  let remuxTimer = null;
  let transcodeTimer = null;
  let previewTimer = null;

  async function pollPreview(jobId) {
    const wrap = document.getElementById('previewWrap');
    const bar = document.getElementById('previewBar');
    const pctEl = document.getElementById('previewPct');
    const txt = document.getElementById('previewText');
    if (wrap) wrap.hidden = false;

    const data = await gql(
      `query($jobId:String!){ livePreviewJob(jobId:$jobId){ jobId status percent outputUrl error } }`,
      { jobId }
    );
    const j = data.livePreviewJob;
    if (!j) throw new Error('Animated preview job not found');

    const p = Math.max(0, Math.min(100, j.percent || 0));
    if (bar) bar.style.width = `${p.toFixed(1)}%`;
    if (pctEl) pctEl.textContent = `${Math.round(p)}%`;
    if (txt) {
      if (j.status === 'queued') txt.textContent = 'Queued…';
      else if (j.status === 'running') txt.textContent = 'Generating animated preview…';
      else if (j.status === 'done') txt.textContent = 'Animated preview ready';
      else txt.textContent = j.error || 'Animated preview failed';
    }

    if (j.status === 'done') return true;
    if (j.status === 'error') throw new Error(j.error || 'Animated preview failed');
    return false;
  }

  async function startPreviewFlow() {
    const btn = document.getElementById('previewBtn');
    if (btn) btn.disabled = true;
    try {
      showToast('Starting animated preview…');
      const res = await gql(
        `mutation($id:Int!){ startLivePreviewRecording(recordingId:$id){ jobId status } }`,
        { id: r.id }
      );
      const jobId = res.startLivePreviewRecording?.jobId;
      if (!jobId) throw new Error('Failed to start animated preview');

      if (previewTimer) clearInterval(previewTimer);
      previewTimer = setInterval(() => {
        pollPreview(jobId)
          .then(done => {
            if (done) {
              clearInterval(previewTimer);
              previewTimer = null;
              showToast('Animated preview ready');
              // Refresh the recordings list so hover previews appear.
              render().catch(() => {});
            }
          })
          .catch(e => {
            clearInterval(previewTimer);
            previewTimer = null;
            showToast(e.message);
          });
      }, 800);

      await pollPreview(jobId).catch(() => {});
    } catch (e) {
      showToast(e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function pollRemux(jobId) {
    const wrap = document.getElementById('remuxWrap');
    const bar = document.getElementById('remuxBar');
    const pctEl = document.getElementById('remuxPct');
    const txt = document.getElementById('remuxText');
    if (wrap) wrap.hidden = false;

    const data = await gql(
      `query($jobId:String!){ remuxJob(jobId:$jobId){ jobId status percent outputUrl error } }`,
      { jobId }
    );

    const j = data.remuxJob;
    if (!j) throw new Error('Remux job not found');

    const p = Math.max(0, Math.min(100, j.percent || 0));
    if (bar) bar.style.width = `${p.toFixed(1)}%`;
    if (pctEl) pctEl.textContent = `${Math.round(p)}%`;
    if (txt) {
      if (j.status === 'queued') txt.textContent = 'Queued…';
      else if (j.status === 'running') txt.textContent = 'Remuxing…';
      else if (j.status === 'done') txt.textContent = 'Ready to play (MP4)';
      else txt.textContent = j.error || 'Remux failed';
    }

    if (j.status === 'done' && j.outputUrl) {
      // Switch player source to remuxed MP4.
      try {
        const player = window.videojs?.(`player-${r.id}`);
        if (player) {
          player.src({ src: j.outputUrl, type: 'video/mp4' });
          player.load();
          player.play().catch(() => {});
        } else {
          const el = document.getElementById(`player-${r.id}`);
          if (el) {
            el.src = j.outputUrl;
            el.load();
            el.play().catch(() => {});
          }
        }
      } catch { /* ignore */ }

      return true;
    }

    if (j.status === 'error') {
      throw new Error(j.error || 'Remux failed');
    }

    return false;
  }

  async function pollTranscode(jobId) {
    const wrap = document.getElementById('transcodeWrap');
    const bar = document.getElementById('transcodeBar');
    const pctEl = document.getElementById('transcodePct');
    const txt = document.getElementById('transcodeText');
    if (wrap) wrap.hidden = false;

    const data = await gql(
      `query($jobId:String!){ transcodeJob(jobId:$jobId){ jobId status percent outputUrl error } }`,
      { jobId }
    );

    const j = data.transcodeJob;
    if (!j) throw new Error('Transcode job not found');

    const p = Math.max(0, Math.min(100, j.percent || 0));
    if (bar) bar.style.width = `${p.toFixed(1)}%`;
    if (pctEl) pctEl.textContent = `${Math.round(p)}%`;
    if (txt) {
      if (j.status === 'queued') txt.textContent = 'Queued…';
      else if (j.status === 'running') txt.textContent = 'Transcoding…';
      else if (j.status === 'done') txt.textContent = 'Ready to play (MP4)';
      else txt.textContent = j.error || 'Transcode failed';
    }

    if (j.status === 'done' && j.outputUrl) {
      // Switch player source to transcoded MP4.
      try {
        const player = window.videojs?.(`player-${r.id}`);
        if (player) {
          player.src({ src: j.outputUrl, type: 'video/mp4' });
          player.load();
          player.play().catch(() => {});
        } else {
          const el = document.getElementById(`player-${r.id}`);
          if (el) {
            el.src = j.outputUrl;
            el.load();
            el.play().catch(() => {});
          }
        }
      } catch { /* ignore */ }

      return true;
    }

    if (j.status === 'error') {
      throw new Error(j.error || 'Transcode failed');
    }

    return false;
  }

  async function startTranscodeFlow() {
    const btn = document.getElementById('transcodeBtn');
    if (btn) btn.disabled = true;
    try {
      showToast('Starting transcode…');
      const res = await gql(
        `mutation($id:Int!){ startTranscodeRecording(recordingId:$id){ jobId status } }`,
        { id: r.id }
      );

      const jobId = res.startTranscodeRecording?.jobId;
      if (!jobId) throw new Error('Failed to start transcode');

      if (transcodeTimer) clearInterval(transcodeTimer);
      transcodeTimer = setInterval(() => {
        pollTranscode(jobId)
          .then(done => {
            if (done) {
              clearInterval(transcodeTimer);
              transcodeTimer = null;
              showToast('Ready to play (MP4)');
            }
          })
          .catch(e => {
            clearInterval(transcodeTimer);
            transcodeTimer = null;
            showToast(e.message);
          });
      }, 800);

      await pollTranscode(jobId).catch(() => {});
    } catch (e) {
      showToast(e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function startRemuxFlow() {
    const btn = document.getElementById('remuxBtn');
    if (btn) btn.disabled = true;
    try {
      showToast('Starting remux…');
      const res = await gql(
        `mutation($id:Int!){ startRemuxRecording(recordingId:$id){ jobId status } }`,
        { id: r.id }
      );
      const jobId = res.startRemuxRecording?.jobId;
      if (!jobId) throw new Error('Failed to start remux');

      if (remuxTimer) clearInterval(remuxTimer);
      remuxTimer = setInterval(() => {
        pollRemux(jobId)
          .then(done => {
            if (done) {
              clearInterval(remuxTimer);
              remuxTimer = null;
              showToast('Ready to play (MP4)');
            }
          })
          .catch(e => {
            clearInterval(remuxTimer);
            remuxTimer = null;
            showToast(e.message);
          });
      }, 800);

      await pollRemux(jobId).catch(() => {});
    } catch (e) {
      showToast(e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  const remuxBtn = document.getElementById('remuxBtn');
  if (remuxBtn) remuxBtn.onclick = () => startRemuxFlow();

  const transcodeBtn = document.getElementById('transcodeBtn');
  if (transcodeBtn) transcodeBtn.onclick = () => startTranscodeFlow();

  const previewBtn = document.getElementById('previewBtn');
  if (previewBtn) previewBtn.onclick = () => startPreviewFlow();

  // Auto-start remux for common non-playable containers.
  if (shouldRemux) {
    startRemuxFlow();
  }

  // FLV is usually not browser-playable even after container changes.
  // Auto-start transcode so the integrated player gets a compatible MP4.
  if (shouldTranscode) {
    startTranscodeFlow();
  }
}

async function renderSites() {
  const data = await gql(`query { siteOverviews { id name logoUrl recordingsCount streamersCount } }`);
  const items = (data.siteOverviews || []);

  viewEl.innerHTML = `
    <div class="page-title">
      <div>
        <div class="h1">Sites</div>
        <div class="subtle">Browse sites, recordings, and associated models.</div>
      </div>
    </div>
    <div class="toolbar">
      <input id="siteSearch" class="input" style="min-width:260px" placeholder="Search sites..." />
      <span class="subtle">${items.length} total</span>
    </div>

    <div id="siteGrid" class="grid"></div>
  `;

  const gridEl = document.getElementById('siteGrid');
  const inputEl = document.getElementById('siteSearch');

  function paint(filterText) {
    const q = (filterText || '').trim().toLowerCase();
    const filtered = q ? items.filter(s => (s.name || '').toLowerCase().includes(q)) : items;

    gridEl.innerHTML = filtered.map(s => {
      const name = s.name || '';
      const recCount = (typeof s.recordingsCount === 'number') ? s.recordingsCount : 0;
      const modelCount = (typeof s.streamersCount === 'number') ? s.streamersCount : 0;
      const logoUrl = s.logoUrl || '';

      return `
        <div class="card" onclick="location.hash='#site:${encodeURIComponent(name)}'">
          <img class="thumb" src="${escapeHtml(logoUrl)}" alt="${escapeHtml(name)} logo" onerror="this.style.display='none'" />
          <div class="card-body">
            <div class="title">${escapeHtml(name)}</div>
            <div class="meta">
              <div>Recordings: <strong>${recCount}</strong></div>
              <div></div>
              <div>Models: <strong>${modelCount}</strong></div>
              <div></div>
              <div style="grid-column:1/-1; display:flex; gap:10px; flex-wrap:wrap">
                <button class="btn" onclick="event.stopPropagation(); location.hash='#site:${encodeURIComponent(name)}'">Recordings</button>
                <button class="btn" onclick="event.stopPropagation(); location.hash='#streamers:${encodeURIComponent(name)}'">Models</button>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  inputEl.oninput = (e) => paint(e.target.value);
  paint('');
}

async function renderSite(siteName) {
  const name = decodeURIComponent(siteName || '');
  await renderRecordings({
    title: `Site: ${name}`,
    subtitle: 'Recordings filtered by site.',
    filter: { siteName: name },
    idPrefix: 'rec-site',
  });
}

async function renderStreamerDetail(streamerName) {
  const name = decodeURIComponent(streamerName || '');
  const data = await gql(
    `query($name:String!){ streamer(name:$name){ id name avatarUrl url gender age ethnicity country hairColor about site{ name } } }`,
    { name }
  );
  const s = data.streamer;
  const site = (s?.site?.name || '').trim() || 'Unknown';
  const canFetch = site.toLowerCase() !== 'unknown';
  const profileUrl = (s?.url || '').trim();

  const infoRows = [];
  const addRow = (label, value) => {
    const v = String(value || '').trim();
    if (!v) return;
    infoRows.push(`<div>${escapeHtml(label)}</div><div><b>${escapeHtml(v)}</b></div>`);
  };
  addRow('Gender', s?.gender);
  addRow('Age', s?.age);
  addRow('Country', s?.country);
  addRow('Ethnicity', s?.ethnicity);
  addRow('Hair', s?.hairColor);
  const aboutHtml = renderAboutHtml(s?.about);
  const infoHtml = (infoRows.length || aboutHtml)
    ? `
      <div style="margin-top:10px">
        ${infoRows.length ? `<div class="kv">${infoRows.join('')}</div>` : ''}
        ${aboutHtml ? `<div class="subtle streamer-about" style="margin-top:8px">${aboutHtml}</div>` : ''}
      </div>
    `
    : '';

  viewEl.innerHTML = `
    <div class="page-title">
      <div>
        <div class="h1">Streamer</div>
        <div class="subtle">Manage this streamer and their recordings.</div>
      </div>
      <a class="btn" href="#streamers">Back</a>
    </div>

    <div class="list">
      <div class="list-item streamer-detail-header">
        <div class="streamer-detail-header-left">
          <div class="avatar streamer-detail-avatar" style="width:112px; height:112px">
            ${s?.avatarUrl ? `<img src="${escapeHtml(s.avatarUrl)}" alt="avatar" onerror="this.remove();" ${profileUrl ? `onclick="event.stopPropagation(); openExternal('${escapeJs(profileUrl)}')" style="cursor:pointer"` : ''} />` : ''}
            <span>${escapeHtml((name || '?').trim().slice(0,1).toUpperCase())}</span>
          </div>
          <div class="streamer-detail-header-main">
            <div class="h1" style="font-size:18px; margin:0">${escapeHtml(name)}</div>
            <div class="subtle" style="display:flex; align-items:center; gap:8px; flex-wrap:wrap">
              <span>Site: <a class="small-link" href="#site:${encodeURIComponent(site)}">${escapeHtml(site)}</a></span>
              <button class="btn btn-ghost" title="Change site" style="padding:4px 8px; min-width:auto" onclick="event.stopPropagation(); openStreamerSitePickerById(${Number(s?.id || 0)}, '${escapeJs(name)}', '${escapeJs(site)}')">✎</button>
            </div>
            ${infoHtml}
          </div>
        </div>
        <div class="streamer-detail-header-actions">
          <button class="btn" id="streamerFetch">Fetch info</button>
          <button class="btn btn-danger" id="streamerDelete">Delete streamer</button>
        </div>
      </div>
    </div>

    <div id="streamerDeleteStatus" class="list" style="margin-top:10px" hidden>
      <div class="list-item" style="display:flex; align-items:center; gap:8px">
        <span class="spinner" aria-hidden="true"></span>
        <span class="subtle">Deleting streamer... This may take a while for large files.</span>
      </div>
    </div>

    <div id="streamerRecordings" style="margin-top:14px"></div>
  `;

  const fetchBtn = document.getElementById('streamerFetch');
  const deleteBtn = document.getElementById('streamerDelete');
  const deleteStatusEl = document.getElementById('streamerDeleteStatus');
  if (fetchBtn) {
    if (!canFetch) {
      fetchBtn.disabled = true;
      fetchBtn.title = 'Site is unknown';
    }
    fetchBtn.onclick = async () => {
      await fetchStreamerInfo(name, site);
    };
  }
  if (deleteBtn) {
    deleteBtn.onclick = async () => {
      if (!confirm(`Delete streamer "${name}"? This will permanently delete all their recordings.`)) return;
      try {
        deleteBtn.disabled = true;
        if (deleteStatusEl) deleteStatusEl.hidden = false;
        const res = await gql(
          `mutation($id:Int!){ deleteStreamer(streamerId:$id){ ok message } }`,
          { id: Number(s?.id || 0) }
        );
        const r = res.deleteStreamer;
        showToast(r?.message || 'Delete completed');
        if (r?.ok) location.hash = '#streamers';
      } catch (e) {
        showToast(e.message || String(e));
      } finally {
        if (deleteStatusEl) deleteStatusEl.hidden = true;
        deleteBtn.disabled = false;
      }
    };
  }

  await renderRecordings({
    containerId: 'streamerRecordings',
    includeHeader: true,
    title: 'Recordings',
    subtitle: 'Recordings for this streamer.',
    filter: { streamerName: name },
    idPrefix: 'rec-streamer',
  });
}

async function renderTags() {
  const data = await gql(`query { tags { id name } }`);
  const tags = (data.tags || []).slice().sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  viewEl.innerHTML = `
    <div class="page-title">
      <div>
        <div class="h1">Tags</div>
        <div class="subtle">Browse tags and their recordings.</div>
      </div>
    </div>

    <div class="toolbar">
      <input id="tagName" class="input" style="min-width:260px" placeholder="New tag name..." />
      <button id="addTag" class="btn btn-primary">Add tag</button>
    </div>

    ${tags.length ? `
      <div class="tag-chip-wrap">
        ${tags.map(t => `
          <div class="tag-chip" data-id="${t.id}" data-name="${encodeURIComponent(t.name)}">
            <button class="tag-chip-button" type="button">${escapeHtml(t.name)}</button>
            <div class="tag-chip-actions" hidden>
              <button class="btn" data-action="open-tag" data-id="${t.id}" data-name="${encodeURIComponent(t.name)}">Open</button>
              <button class="btn" data-action="rename-tag" data-id="${t.id}" data-name="${encodeURIComponent(t.name)}">Rename</button>
              <button class="btn btn-danger" data-action="delete-tag" data-id="${t.id}" data-name="${encodeURIComponent(t.name)}">Delete</button>
            </div>
          </div>
        `).join('')}
      </div>
    ` : `
      <div class="list" style="margin-top:14px">
        <div class="list-item"><div class="subtle">No tags yet. Assign tags from the Recordings view or create one above.</div></div>
      </div>
    `}
  `;

  document.getElementById('addTag').onclick = async () => {
    const name = document.getElementById('tagName').value.trim();
    if (!name) return showToast('Tag name is required');
    try {
      await gql(`mutation($name:String!){ createTag(name:$name){ id name } }`, { name });
      showToast('Tag created');
      await renderTags();
    } catch (e) {
      showToast(e.message);
    }
  };

  function closeAllTagPopovers(exceptChip = null) {
    viewEl.querySelectorAll('.tag-chip').forEach(el => {
      if (exceptChip && el === exceptChip) return;
      el.classList.remove('active');
      const actions = el.querySelector('.tag-chip-actions');
      if (actions) {
        actions.hidden = true;
        actions.style.left = '';
        actions.style.right = '';
        actions.style.top = '';
        actions.style.bottom = '';
      }
    });
  }

  function positionPopover(chipEl, actionsEl) {
    // Reset to default placement, then clamp.
    actionsEl.style.left = '0';
    actionsEl.style.right = 'auto';
    actionsEl.style.top = 'calc(100% + 8px)';
    actionsEl.style.bottom = 'auto';

    const rect = actionsEl.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;

    if (rect.right > vw - 8) {
      actionsEl.style.left = 'auto';
      actionsEl.style.right = '0';
    }

    const rect2 = actionsEl.getBoundingClientRect();
    if (rect2.bottom > vh - 8) {
      actionsEl.style.top = 'auto';
      actionsEl.style.bottom = 'calc(100% + 8px)';
    }
  }

  // Tag chip expand/collapse + actions (event delegation).
  viewEl.onclick = async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;

    // Click outside chips closes any open popover.
    if (!target.closest('.tag-chip')) {
      closeAllTagPopovers(null);
      return;
    }

    const chipBtn = target.closest('.tag-chip-button');
    if (chipBtn) {
      const chip = chipBtn.closest('.tag-chip');
      if (!chip) return;

      // Close others
      closeAllTagPopovers(chip);

      const actions = chip.querySelector('.tag-chip-actions');
      const willOpen = actions ? actions.hidden : false;
      chip.classList.toggle('active', willOpen);
      if (actions) {
        actions.hidden = !willOpen;
        if (willOpen) positionPopover(chip, actions);
      }
      return;
    }

    const actionBtn = target.closest('button[data-action]');
    if (!actionBtn) return;

    const action = actionBtn.dataset.action;
    const tagId = parseInt(actionBtn.dataset.id, 10);
    const tagName = decodeURIComponent(actionBtn.dataset.name || '');

    if (action === 'open-tag') {
      location.hash = `#tag:${encodeURIComponent(tagName)}`;
      return;
    }

    if (action === 'rename-tag') {
      const newName = prompt('Rename tag:', tagName);
      if (newName == null) return;
      const trimmed = newName.trim();
      if (!trimmed) return showToast('New tag name is required');
      try {
        await gql(`mutation($tagId:Int!, $newName:String!){ renameTag(tagId:$tagId, newName:$newName){ id name } }`, { tagId, newName: trimmed });
        showToast('Tag renamed');
        await renderTags();
      } catch (err) {
        showToast(err.message);
      }
      return;
    }

    if (action === 'delete-tag') {
      if (!confirm(`Delete tag "${tagName}"? This will remove it from all recordings.`)) return;
      try {
        const res = await gql(`mutation($tagId:Int!){ deleteTag(tagId:$tagId){ ok message } }`, { tagId });
        showToast(res.deleteTag.message);
        await renderTags();
      } catch (err) {
        showToast(err.message);
      }
    }
  };
}

async function renderTag(tagName) {
  const name = decodeURIComponent(tagName || '');
  await renderRecordings({
    title: `Tag: ${name}`,
    subtitle: 'Recordings filtered by tag.',
    filter: { tagName: name },
    idPrefix: 'rec-tag',
  });
}

async function renderStreamers(siteName) {
  const decodedSite = siteName ? decodeURIComponent(siteName || '') : '';
  const siteKey = decodedSite || null;
  if (state.streamers._siteKey !== siteKey) {
    state.streamers._siteKey = siteKey;
    state.streamers.offset = 0;
    // Keep other filters (search/startsWith) compatible when changing site.
  }

  // Load sites list for the filter pills.
  let sites = [];
  try {
    const sitesData = await gql(`query { siteOverviews { name } }`);
    sites = (sitesData.siteOverviews || []).map(s => (s?.name || '').trim()).filter(Boolean);
    sites.sort((a, b) => a.localeCompare(b));
  } catch {
    sites = [];
  }
  const vars = {
    limit: state.streamers.pageSize,
    offset: state.streamers.offset,
    siteName: decodedSite || null,
    search: state.streamers.search || null,
    orderBy: state.streamers.sort.by,
    orderDir: state.streamers.sort.dir,
    startsWith: state.streamers.startsWith,
  };
  const data = await gql(
    `query($limit:Int!, $offset:Int!, $siteName:String, $search:String, $orderBy:StreamerOrderBy, $orderDir:SortDirection, $startsWith:String){ streamersConnection(limit:$limit, offset:$offset, siteName:$siteName, search:$search, orderBy:$orderBy, orderDir:$orderDir, startsWith:$startsWith){ total items { id name site { name } avatarUrl url recordingsCount totalSizeBytes } } }`,
    vars
  );
  const conn = data.streamersConnection;
  const items = conn.items || [];
  const total = conn.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / state.streamers.pageSize));
  const currentPage = total ? Math.floor(state.streamers.offset / state.streamers.pageSize) + 1 : 1;

  const headerTitle = decodedSite ? `Models: ${escapeHtml(decodedSite)}` : 'Streamers';
  const headerSubtitle = decodedSite
    ? 'Models associated with this site.'
    : `${total} Streamers found in the catalog. Click one to open details.`;
  const backHref = decodedSite ? '#sites' : '#recordings';

  viewEl.innerHTML = `
    <div class="page-title">
      <div>
        <div class="h1">${headerTitle}</div>
        <div class="subtle">${headerSubtitle}</div>
      </div>
      <a class="btn" href="${backHref}">Back</a>
    </div>

    <div class="toolbar">
      <div class="input-clear-wrap" style="min-width:156px; width:156px">
        <input id="streamerSearch" class="input" placeholder="Search streamers..." value="${escapeHtml(state.streamers.search)}" />
        <button id="streamerSearchClear" class="input-clear-btn" type="button" title="Clear" aria-label="Clear search" hidden>✕</button>
      </div>
      <label class="checkbox">
        Page size
        <select id="streamerPageSize">
          ${PAGE_SIZES.map(n => `<option value="${n}" ${n===state.streamers.pageSize?'selected':''}>${n}</option>`).join('')}
        </select>
      </label>
      <label class="checkbox">
        Sort
        <select id="streamerSortBy">
          <option value="NAME" ${state.streamers.sort.by==='NAME'?'selected':''}>Name</option>
          <option value="RECORDINGS_COUNT" ${state.streamers.sort.by==='RECORDINGS_COUNT'?'selected':''}>Recordings</option>
          <option value="TOTAL_SIZE" ${state.streamers.sort.by==='TOTAL_SIZE'?'selected':''}>Total size</option>
          <option value="RECENT_ADDED" ${state.streamers.sort.by==='RECENT_ADDED'?'selected':''}>Recently added</option>
        </select>
      </label>
      <label class="checkbox">
        Dir
        <select id="streamerSortDir">
          <option value="ASC" ${state.streamers.sort.dir==='ASC'?'selected':''}>Asc</option>
          <option value="DESC" ${state.streamers.sort.dir==='DESC'?'selected':''}>Desc</option>
        </select>
      </label>
      <span class="subtle">${total} total • ${totalPages} pages</span>
      <span style="flex:1"></span>
      <button id="bulkFetchStreamerInfoBtn" class="btn btn-primary btn-scan" title="Fetch info for all streamers missing info${decodedSite ? ' (current site only)' : ''}">Fetch streamers info</button>
      <label class="checkbox" style="gap:8px">
        Page
        <select id="streamerPageSelect"></select>
      </label>
      <button id="streamerPrev" class="btn">Prev</button>
      <button id="streamerNext" class="btn">Next</button>
    </div>

    <div id="bulkFetchStreamerInfoWrap" class="progress-wrap" hidden>
      <div class="progress-row">
        <div class="progress"><div id="bulkFetchStreamerInfoBar" class="progress-bar" style="width:0%"></div></div>
        <div id="bulkFetchStreamerInfoPct" class="subtle">0%</div>
      </div>
      <div id="bulkFetchStreamerInfoText" class="subtle" style="margin-top:8px"></div>
    </div>

    <div id="streamerSites" class="alpha-bar" style="margin-top:10px"></div>

    <div id="streamerAlpha" class="alpha-bar"></div>

    <div id="streamerGrid" class="grid"></div>
  `;

  const gridEl = document.getElementById('streamerGrid');
  const inputEl = document.getElementById('streamerSearch');
  const inputClearEl = document.getElementById('streamerSearchClear');
  const alphaEl = document.getElementById('streamerAlpha');
  const sitesEl = document.getElementById('streamerSites');

  const pageSelectEl = document.getElementById('streamerPageSelect');
  const prevBtn = document.getElementById('streamerPrev');
  const nextBtn = document.getElementById('streamerNext');

  const bulkBtn = document.getElementById('bulkFetchStreamerInfoBtn');
  if (bulkBtn) {
    // Disable if a job is already running.
    const last = state.bulkFetchStreamerInfo.lastJob;
    const running = last && (normStatus(last.status) === 'running' || normStatus(last.status) === 'queued');
    bulkBtn.disabled = !!running;
    bulkBtn.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      startBulkFetchMissingStreamerInfo(decodedSite || null);
    };
  }

  // If a job exists, refresh its status once and continue polling.
  if (state.bulkFetchStreamerInfo.jobId) {
    startBulkFetchStreamerInfoPolling(state.bulkFetchStreamerInfo.jobId);
  } else if (state.bulkFetchStreamerInfo.lastJob) {
    updateBulkFetchStreamerInfoProgressUI(state.bulkFetchStreamerInfo.lastJob);
  }

  if (pageSelectEl) {
    const opts = buildPageOptions(totalPages, currentPage);
    pageSelectEl.innerHTML = opts
      .map(o => `<option value="${o.value}" ${o.disabled ? 'disabled' : ''} ${o.value === currentPage ? 'selected' : ''}>${o.label}</option>`)
      .join('');
    pageSelectEl.onchange = (e) => {
      const page = parseInt(e.target.value, 10);
      if (!page || page < 1) return;
      state.streamers.offset = (page - 1) * state.streamers.pageSize;
      renderStreamers(siteName).catch(err => showToast(err.message));
    };
  }

  if (prevBtn) {
    prevBtn.disabled = state.streamers.offset <= 0;
    prevBtn.onclick = () => {
      state.streamers.offset = Math.max(0, state.streamers.offset - state.streamers.pageSize);
      renderStreamers(siteName).catch(err => showToast(err.message));
    };
  }

  if (nextBtn) {
    nextBtn.disabled = (state.streamers.offset + state.streamers.pageSize) >= total;
    nextBtn.onclick = () => {
      state.streamers.offset = state.streamers.offset + state.streamers.pageSize;
      renderStreamers(siteName).catch(err => showToast(err.message));
    };
  }

  if (inputEl) {
    const syncClear = () => {
      if (inputClearEl) inputClearEl.hidden = !(inputEl.value || '').trim();
    };
    syncClear();
    inputEl.oninput = (e) => {
      state.streamers.search = e.target.value;
      state.streamers.offset = 0;
      syncClear();
      renderStreamers(siteName).catch(err => showToast(err.message));
    };
  }

  if (inputClearEl && inputEl) {
    inputClearEl.onclick = () => {
      inputEl.value = '';
      state.streamers.search = '';
      state.streamers.offset = 0;
      inputEl.focus();
      inputClearEl.hidden = true;
      renderStreamers(siteName).catch(err => showToast(err.message));
    };
  }

  document.getElementById('streamerPageSize').onchange = (e) => {
    state.streamers.pageSize = parseInt(e.target.value, 10);
    state.streamers.offset = 0;
    renderStreamers(siteName).catch(err => showToast(err.message));
  };

  document.getElementById('streamerSortBy').onchange = (e) => {
    state.streamers.sort.by = e.target.value;
    state.streamers.offset = 0;
    renderStreamers(siteName).catch(err => showToast(err.message));
  };

  document.getElementById('streamerSortDir').onchange = (e) => {
    state.streamers.sort.dir = e.target.value;
    state.streamers.offset = 0;
    renderStreamers(siteName).catch(err => showToast(err.message));
  };

  function paintAlpha() {
    if (!alphaEl) return;
    const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
    const selected = state.streamers.startsWith;
    const btn = (label, val) => {
      const active = (selected === val) || (!selected && val === null);
      return `<button class="alpha-btn ${active ? 'active' : ''}" data-alpha="${val === null ? '' : escapeHtml(val)}">${escapeHtml(label)}</button>`;
    };
    alphaEl.innerHTML = [
      btn('All', null),
      btn('#', '#'),
      ...letters.map(l => btn(l, l)),
    ].join('');

    alphaEl.querySelectorAll('button[data-alpha]').forEach(b => {
      b.onclick = (ev) => {
        ev.preventDefault();
        const v = (b.dataset.alpha || '').trim();
        state.streamers.startsWith = v ? v : null;
        state.streamers.offset = 0;
        renderStreamers(siteName).catch(err => showToast(err.message));
      };
    });
  }

  paintAlpha();

  function paintSites() {
    if (!sitesEl) return;
    const active = decodedSite;

    const btn = (label, val) => {
      const isActive = (val || '') === (active || '');
      const data = val ? escapeHtml(val) : '';
      return `<button class="alpha-btn ${isActive ? 'active' : ''}" style="min-width:auto" data-site="${data}">${escapeHtml(label)}</button>`;
    };

    const pills = [btn('All', '')].concat(sites.map(n => btn(n, n)));
    sitesEl.innerHTML = pills.join('');

    sitesEl.querySelectorAll('button[data-site]').forEach(b => {
      b.onclick = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const v = (b.dataset.site || '').trim();
        state.streamers.offset = 0;
        // Keep search/startsWith; only change the site filter.
        location.hash = v ? `#streamers:${encodeURIComponent(v)}` : '#streamers';
      };
    });
  }

  paintSites();

  function paint() {
    gridEl.innerHTML = items.map(s => {
      const rawSite = (s.site?.name || '').trim();
      const site = rawSite || 'Unknown';
      const siteForFetch = (decodedSite || site || '').trim();
      const canFetch = !!siteForFetch && siteForFetch.toLowerCase() !== 'unknown';
      const canSetSite = !siteForFetch || siteForFetch.toLowerCase() === 'unknown';
      const recordingsCount = (typeof s.recordingsCount === 'number') ? s.recordingsCount : 0;
      const totalSize = (typeof s.totalSizeBytes === 'number') ? s.totalSizeBytes : null;
      const totalSizeLabel = totalSize != null ? ` (${fmtBytes(totalSize)})` : '';
      const initial = (s.name || '?').trim().slice(0,1).toUpperCase();
      const avatar = `
        <div class="thumb-wrap">
          <div class="thumb" style="display:grid; place-items:center; font-size:46px; color:#9fb0c0;">${escapeHtml(initial)}</div>
          ${s.avatarUrl
            ? `<img class="thumb thumb-streamer" src="${escapeHtml(s.avatarUrl)}" alt="avatar" style="position:absolute; inset:0" onerror="this.remove()" />`
            : ''
          }
        </div>
      `;

      const webBtn = s.url
        ? `
          <button class="btn btn-ghost" title="Open profile" style="padding:6px 8px; min-width:auto" onclick="event.stopPropagation(); openExternal('${escapeJs(s.url)}')">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
              <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M2 12h20" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M12 2c2.5 2.74 4 6.23 4 10s-1.5 7.26-4 10c-2.5-2.74-4-6.23-4-10s1.5-7.26 4-10Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        `
        : '';

      return `
        <div class="card" onclick="location.hash='#streamer:${encodeURIComponent(s.name)}'">
          ${avatar}
          <div class="card-body">
            <div class="title">${escapeHtml(s.name)}</div>
            <div class="meta meta-streamer">
              <div class="meta-site">Site: <a class="small-link" href="#site:${encodeURIComponent(site)}" onclick="event.stopPropagation()">${escapeHtml(site)}</a></div>
              <div class="meta-recordings">Recordings: <span class="meta-value">${recordingsCount}</span>${totalSizeLabel}</div>
              <div></div>
              <div style="grid-column:1/-1; display:flex; gap:10px; flex-wrap:wrap">
                <button class="btn" onclick="event.stopPropagation(); location.hash='#streamer:${encodeURIComponent(s.name)}'">Recordings</button>
                ${webBtn}
                ${canSetSite
                  ? `<button class="btn" onclick="event.stopPropagation(); openStreamerSitePickerById(${Number(s.id || 0)}, '${escapeJs(s.name)}', '${escapeJs(rawSite)}')">Set site</button>`
                  : ''
                }
                ${canFetch
                  ? `<button class="btn" onclick="event.stopPropagation(); fetchStreamerInfo('${escapeJs(s.name)}', '${escapeJs(siteForFetch)}')">Fetch info</button>`
                  : `<button class="btn" disabled title="Site is unknown">Fetch info</button>`
                }
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  inputEl.oninput = (e) => {
    state.streamers.search = e.target.value;
    state.streamers.offset = 0;
    renderStreamers(siteName).catch(err => showToast(err.message));
  };

  paint();
}

async function fetchStreamerInfo(streamerName, siteName) {
  const site = (siteName || '').trim();
  if (!site || site.toLowerCase() === 'unknown') {
    showToast('Cannot fetch streamer info: site is unknown');
    return;
  }
  try {
    const res = await gql(
      `mutation($streamerName:String!, $siteName:String!){ fetchStreamerInfo(streamerName:$streamerName, siteName:$siteName){ ok message } }`,
      { streamerName, siteName: site }
    );
    showToast(res.fetchStreamerInfo.message);
    if (res.fetchStreamerInfo.ok) {
      // Refresh current view so avatar/url updates show immediately.
      try { await render(); } catch { /* ignore */ }
    }
  } catch (e) {
    showToast(e.message);
  }
}

function openExternal(url) {
  const u = String(url || '').trim();
  if (!u) return;
  try {
    const w = window.open(u, '_blank', 'noopener');
    if (w) w.opener = null;
  } catch {
    // ignore
  }
}

function escapeJs(s) {
  return String(s).replaceAll('\\', '\\\\').replaceAll("'", "\\'");
}

function renderAboutHtml(about) {
  const raw = String(about || '').trim();
  if (!raw) return '';

  // If it looks like HTML, sanitize and render as HTML.
  if (raw.includes('<') && raw.includes('>')) {
    return sanitizeHtml(raw);
  }

  // Plain text: escape, preserve newlines.
  return escapeHtml(raw).replaceAll('\n', '<br>');
}

function sanitizeHtml(html) {
  const raw = String(html || '').trim();
  if (!raw) return '';

  // Basic allowlist sanitizer (keeps formatting, removes scripts/handlers).
  const allowedTags = new Set([
    'A', 'B', 'STRONG', 'I', 'EM', 'U',
    'BR', 'P', 'DIV', 'SPAN',
    'UL', 'OL', 'LI',
  ]);

  const parser = new DOMParser();
  const doc = parser.parseFromString(raw, 'text/html');
  const body = doc.body;
  if (!body) return '';

  const nodes = Array.from(body.querySelectorAll('*'));
  for (const el of nodes) {
    const tag = el.tagName;

    // Drop dangerous blocks entirely.
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'IFRAME' || tag === 'OBJECT' || tag === 'EMBED') {
      el.remove();
      continue;
    }

    if (!allowedTags.has(tag)) {
      // Replace unknown tags with their text content.
      const txt = doc.createTextNode(el.textContent || '');
      el.replaceWith(txt);
      continue;
    }

    // Strip risky attributes.
    for (const attr of Array.from(el.attributes)) {
      const n = attr.name.toLowerCase();
      if (n.startsWith('on') || n === 'style') {
        el.removeAttribute(attr.name);
        continue;
      }
      if (tag !== 'A') {
        // Keep no attributes for non-links (simple + safe).
        el.removeAttribute(attr.name);
        continue;
      }
      // A tag: only allow href/title.
      if (n !== 'href' && n !== 'title') {
        el.removeAttribute(attr.name);
      }
    }

    if (tag === 'A') {
      const href = (el.getAttribute('href') || '').trim();
      if (!href) {
        el.removeAttribute('href');
      } else {
        try {
          const u = new URL(href, window.location.origin);
          const ok = (u.protocol === 'http:' || u.protocol === 'https:');
          if (!ok) {
            el.removeAttribute('href');
          }
        } catch {
          el.removeAttribute('href');
        }
      }

      // Always open external links safely.
      el.setAttribute('target', '_blank');
      el.setAttribute('rel', 'noopener');
    }
  }

  return body.innerHTML;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function fileExt(name) {
  const s = String(name || '');
  const i = s.lastIndexOf('.');
  if (i < 0) return '';
  return s.slice(i + 1).toLowerCase();
}

function guessVideoMimeFromExt(ext) {
  const e = (ext || '').toLowerCase();
  if (e === 'mp4' || e === 'm4v') return 'video/mp4';
  if (e === 'webm') return 'video/webm';
  if (e === 'mov') return 'video/quicktime';
  // mkv/avi/ts are generally not playable by browsers as-is.
  return '';
}

function needsRemux(ext) {
  const e = (ext || '').toLowerCase();
  return ['mkv', 'avi', 'ts'].includes(e);
}

function needsTranscodeForPlayback(ext) {
  const e = (ext || '').toLowerCase();
  return ['flv'].includes(e);
}

async function openRecordingFolder(recordingId, btn = null) {
  if (!recordingId) return;

  try {
    if (btn) btn.disabled = true;

    const res = await fetch('/api/open-recording-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recordingId }),
    });

    let payload = null;
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }

    if (!res.ok) {
      const msg = payload?.detail || payload?.message || `Failed to open folder (${res.status})`;
      throw new Error(msg);
    }

    showToast(payload?.message || 'Opened folder');
  } catch (e) {
    showToast(e.message || String(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openRecordingVideo(recordingId, btn = null) {
  if (!recordingId) return;

  try {
    if (btn) btn.disabled = true;

    const res = await fetch('/api/open-recording-video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recordingId }),
    });

    let payload = null;
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }

    if (!res.ok) {
      const msg = payload?.detail || payload?.message || `Failed to open video (${res.status})`;
      throw new Error(msg);
    }

    showToast(payload?.message || 'Opened video');
  } catch (e) {
    showToast(e.message || String(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

let _activeJobsRestoredOnce = false;
let _activeJobsWatcherTimer = null;

function hasTerminalJobStatus(status) {
  const s = normStatus(status);
  return s === 'done' || s === 'error' || s === 'cancelled' || s === 'canceled';
}

async function syncActiveJobs() {
  try {
    const data = await gql(`
      query {
        activeScanJob {
          jobId status error
          progress {
            phase totalFiles scannedFiles added updated skipped errors
            currentFile currentRecording
            totalPreviews generatedPreviews failedPreviews currentPreview percent
          }
          result { scannedFiles added updated skipped errors }
        }
        activeBulkFetchStreamerInfoJob {
          jobId status error
          progress { total done ok failed skipped current percent }
        }
        activeReencodeJob {
          jobId status currentRecordingId percent error
          items { recordingId fileName outputFileName status percent outputUrl error }
        }
        activeAutoTagJob {
          jobId status error
          progress { total completed failed currentFile percent framesUsed candidateCount }
        }
      }
    `);

    try {
      const j = data?.activeScanJob;
      if (j?.jobId && !state.scan.jobId && !hasTerminalJobStatus(j.status)) {
        updateScanProgressUI(j);
        syncAutoTagUIFromScanJob(j);
        startScanPolling(j.jobId);
      }
    } catch { /* ignore */ }

    try {
      const j = data?.activeBulkFetchStreamerInfoJob;
      if (j?.jobId && !state.bulkFetchStreamerInfo.jobId && !hasTerminalBulkFetchStatus(j.status)) {
        state.bulkFetchStreamerInfo.lastJob = j;
        updateBulkFetchStreamerInfoProgressUI(j);
        startBulkFetchStreamerInfoPolling(j.jobId);
      }
    } catch { /* ignore */ }

    try {
      const j = data?.activeReencodeJob;
      if (j?.jobId && !state.reencode?.jobId && !isTerminalReencodeStatus(j.status)) {
        state.reencode.jobId = j.jobId;
        state.reencode.wasCanceled = false;
        state.reencode.active = true;
        state.reencode.lastJob = j;
        state.reencode.recordingIds = Array.isArray(j.items) ? j.items.map(it => it.recordingId).filter(Boolean) : [];
        updateReencodeProgressUI(j);
        ensureReencodePolling();
      }
    } catch { /* ignore */ }

    try {
      const j = data?.activeAutoTagJob;
      if (j?.jobId && !state.autoTag.jobId && !hasTerminalJobStatus(j.status)) {
        updateAutoTagProgressUI(j);
        startAutoTagPolling(j.jobId);
      }
    } catch { /* ignore */ }
  } catch {
    // Silent: older builds won't have these fields; the app should still work.
  }
}

function ensureActiveJobsWatcher() {
  if (_activeJobsWatcherTimer) return;
  _activeJobsWatcherTimer = setInterval(() => {
    const needsSync = !state.scan.jobId || !state.bulkFetchStreamerInfo.jobId || !state.reencode?.jobId || !state.autoTag.jobId;
    if (!needsSync) return;
    syncActiveJobs().catch(() => { /* ignore */ });
  }, 5000);
}

async function restoreActiveJobsOnce() {
  if (_activeJobsRestoredOnce) return;

  // Only attempt once per page lifecycle to avoid repeated GraphQL calls.
  _activeJobsRestoredOnce = true;
  await syncActiveJobs();
}

async function renderAndRestore() {
  await restoreActiveJobsOnce();
  await render();
}

window.addEventListener('hashchange', () => {
  renderAndRestore().catch(() => { /* ignore */ });
});
window.addEventListener('resize', updateTopbarHeightVar);

if (ffmpegBannerRetryEl) {
  ffmpegBannerRetryEl.addEventListener('click', () => {
    refreshFfmpegBanner({ force: true }).catch(() => { /* ignore */ });
  });
}

refreshFfmpegBanner().catch(() => { /* ignore */ });
refreshCatalogInfo().catch(() => { /* ignore */ });
ensureActiveJobsWatcher();
renderAndRestore().catch(e => showToast(e.message));
