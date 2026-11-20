/* Cutter — клиентская логика. Без фреймворков: маленькое приложение, зато быстрое. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const el = {
  screens: $('#screens'),
  title: $('#screen-title'),
  sub: $('#screen-sub'),
  dots: {
    status: $('#status-dot'),
    text: $('#status-text'),
  },
  chips: {
    model: $('#chip-model'),
    key: $('#chip-key'),
    browser: $('#chip-browser'),
  },
  drop: $('#dropzone'),
  fileInput: $('#file-input'),
  ytUrl: $('#yt-url'),
  ytBtn: $('#yt-btn'),
  clipsSeg: $('#clips-seg'),
  durSeg: $('#dur-seg'),
  styleSeg: $('#style-seg'),
  runBtn: $('#run-btn'),
  runHint: $('#run-hint'),
  estimate: $('#estimate'),
  jobEmpty: $('#job-empty'),
  jobRun: $('#job-run'),
  progressFill: $('#progress-fill'),
  progressPct: $('#progress-pct'),
  progressStage: $('#progress-stage'),
  steps: $('#steps'),
  log: $('#log'),
  cancelBtn: $('#cancel-btn'),
  results: $('#results'),
  clips: $('#clips'),
  clipsCount: $('#clips-count'),
  resultsSub: $('#results-sub'),
  strip: $('#strip'),
  stripMarks: $('#strip-marks'),
  stripEnd: $('#strip-end'),
  openFolder: $('#open-folder'),
  pickList: $('#pick-list'),
  uploadSource: $('#upload-source'),
  platforms: $('#platforms'),
  engineSeg: $('#engine-seg'),
  profileSelect: $('#profile-select'),
  refreshProfiles: $('#refresh-profiles'),
  engineHint: $('#engine-hint'),
  visibility: $('#visibility'),
  dryRun: $('#dry-run'),
  uploadBtn: $('#upload-btn'),
  uploadBtnText: $('#upload-btn-text'),
  uploadResult: $('#upload-result'),
  uploadResultList: $('#upload-result-list'),
  history: $('#history'),
  historyRefresh: $('#history-refresh'),
  llmProvider: $('#llm-provider'),
  llmModel: $('#llm-model'),
  llmKey: $('#llm-key'),
  llmHint: $('#llm-hint'),
  llmFallback: $('#llm-fallback'),
  testKey: $('#test-key'),
  models: $('#models'),
  outdir: $('#outdir'),
  language: $('#language'),
  saveSettings: $('#save-settings'),
  aboutText: $('#about-text'),
  aboutStack: $('#about-stack'),
  modal: $('#modal'),
  modalVideo: $('#modal-video'),
  modalTitle: $('#modal-title'),
  modalHook: $('#modal-hook'),
  modalClose: $('#modal-close'),
  toasts: $('#toasts'),
};

const STYLE_LABELS = { classic: 'Классика', bold: 'Жирный', clean: 'Чистый', neon: 'Неон' };

const state = {
  screen: 'cut',
  file: null,
  clipCount: 5,
  dur: [18, 60],
  style: 'classic',
  jobId: null,
  job: null,
  pollTimer: null,
  clipList: [],
  selected: new Set(),
  platforms: new Set(['tiktok']),
  engine: 'plain',
  providers: [],
  settings: null,
  health: null,
  keyDirty: false,
  sourceName: '',
};

/* ---------------------------------------------------------------- утилиты */

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function toast(message, kind = '') {
  const node = document.createElement('div');
  node.className = `toast${kind ? ' is-' + kind : ''}`;
  node.innerHTML = `<span class="toast__mark"></span><span>${esc(message)}</span>`;
  el.toasts.appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .2s, transform .2s';
    node.style.opacity = '0';
    node.style.transform = 'translateY(6px)';
    setTimeout(() => node.remove(), 220);
  }, kind === 'err' ? 7000 : 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data && (data.detail || data.message || data.error);
    throw new Error(typeof detail === 'string' ? detail : `ошибка ${response.status}`);
  }
  return data;
}

async function apiJson(path, body, method = 'POST') {
  return api(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function fmtDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function fmtWhen(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return 'только что';
  if (diff < 3600) return `${Math.round(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.round(diff / 3600)} ч назад`;
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

/* ---------------------------------------------------------------- навигация */

const SCREEN_META = {
  cut: ['Нарезка', 'Длинное видео → вертикальные клипы с субтитрами'],
  upload: ['Загрузка', 'Публикация клипов через браузер с сохранёнными сессиями'],
  history: ['История', 'Что уже нарезано и загружено'],
  settings: ['Настройки', 'Модели, ключи, папки'],
};

function go(screen) {
  state.screen = screen;
  $$('.rail__btn').forEach((btn) => {
    const active = btn.dataset.nav === screen;
    btn.classList.toggle('is-active', active);
    if (active) btn.setAttribute('aria-current', 'page');
    else btn.removeAttribute('aria-current');
  });
  $$('.screen').forEach((section) => section.classList.toggle('is-active', section.dataset.screen === screen));
  const [title, sub] = SCREEN_META[screen];
  el.title.textContent = title;
  el.sub.textContent = sub;
  el.screens.scrollTop = 0;
  if (screen === 'history') loadHistory();
  if (screen === 'upload') ensureUploadScreen();
}

$$('.rail__btn').forEach((btn) => btn.addEventListener('click', () => go(btn.dataset.nav)));
document.addEventListener('click', (event) => {
  const goto = event.target.closest('[data-goto]');
  if (goto) go(goto.dataset.goto);
});

/* ---------------------------------------------------------------- статус */

function setStatus(kind, text) {
  el.dots.status.className = `dot ${kind ? 'is-' + kind : ''}`;
  el.dots.text.textContent = text;
}

function renderChips(health) {
  if (health) state.health = health;
  const current = state.health;
  const settings = state.settings;
  const provider = settings?.llm?.provider || '—';
  const model = settings?.llm?.model || '—';
  el.chips.model.textContent = `${provider} / ${model}`;
  el.chips.model.className = 'chip chip--ghost';

  const hasKey = Boolean(state.keyDirty ? el.llmKey.value.trim() : settings?.llm?.api_key);
  const keyChip = el.chips.key;
  keyChip.textContent = hasKey ? 'ключ есть' : 'ключ не задан';
  keyChip.className = `chip ${hasKey ? 'is-ok' : 'is-bad'}`;

  if (current) {
    el.chips.browser.textContent = current.browser ? 'браузер есть' : 'нужен Chrome/Edge';
    el.chips.browser.className = `chip ${current.browser ? 'is-ok' : 'is-bad'}`;
  }

  renderSetup();
}

/** Подсказки первого запуска: модель и ключ. */
function renderSetup() {
  const setup = document.querySelector('#setup');
  if (!setup) return;
  const hasModel = Boolean(state.health && state.health.models && state.health.models.length);
  const hasKey = Boolean(state.settings && state.settings.llm && state.settings.llm.api_key);
  const modelItem = setup.querySelector('[data-setup="model"]');
  const keyItem = setup.querySelector('[data-setup="key"]');
  if (modelItem) modelItem.hidden = hasModel;
  if (keyItem) keyItem.hidden = hasKey;
  setup.hidden = hasModel && hasKey;
}

/* ---------------------------------------------------------------- превью клипов */

function renderClips(clips, source) {
  state.clipList = clips || [];
  el.clips.innerHTML = '';
  if (!state.clipList.length) {
    el.results.hidden = true;
    return;
  }
  el.results.hidden = false;
  el.clipsCount.textContent = state.clipList.length;
  el.resultsSub.textContent = source ? `из «${source}»` : '';

  state.clipList.forEach((clip) => {
    const card = document.createElement('article');
    card.className = 'clip';
    const score = Number(clip.score) || 0;
    const scoreClass = score >= 75 ? '' : score >= 50 ? ' is-mid' : ' is-low';
    card.innerHTML = `
      <div class="clip__thumb">
        <img src="${esc(clip.preview_url)}" alt="" loading="lazy">
        <div class="clip__badges">
          <span class="badge badge--score${scoreClass}">${score ? esc(score) + '%' : '—'}</span>
          <span class="badge">${esc(fmtDuration(clip.duration))}</span>
        </div>
        <button class="clip__play" data-play="${esc(clip.video_url)}" data-title="${esc(clip.title)}" data-hook="${esc(clip.hook)}" aria-label="Смотреть клип">
          <svg class="ico"><use href="#i-play"/></svg>
        </button>
      </div>
      <div class="clip__body">
        <p class="clip__title">${esc(clip.title || 'Без заголовка')}</p>
        <div class="clip__meta">
          <span>${esc(fmtDuration(clip.start))}→${esc(fmtDuration(clip.end))}</span>
          <span>·</span>
          <span>${(clip.hashtags || []).length ? esc(clip.hashtags.slice(0, 2).join(' ')) : 'без тегов'}</span>
        </div>
        <div class="clip__actions">
          <button class="clip__btn" data-download="${esc(clip.video_url)}">Скачать</button>
          <button class="clip__btn clip__btn--accent" data-upload="${esc(clip.id)}">Грузить</button>
        </div>
      </div>`;
    el.clips.appendChild(card);
  });

  renderStrip(clips);
}

function renderStrip(clips) {
  const end = Math.max(...clips.map((clip) => Number(clip.end) || 0), 1);
  el.stripEnd.textContent = fmtDuration(end);
  el.stripMarks.innerHTML = clips
    .map((clip) => {
      const left = Math.max(0, Math.min(100, (Number(clip.start) / end) * 100));
      return `<span class="strip__mark" style="left:${left.toFixed(2)}%"></span>`;
    })
    .join('');
}

document.addEventListener('click', (event) => {
  const play = event.target.closest('[data-play]');
  if (play) {
    openModal(play.dataset.play, play.dataset.title, play.dataset.hook);
    return;
  }
  const download = event.target.closest('[data-download]');
  if (download) {
    const link = document.createElement('a');
    link.href = download.dataset.download;
    link.download = '';
    document.body.appendChild(link);
    link.click();
    link.remove();
    return;
  }
  const upload = event.target.closest('[data-upload]');
  if (upload) {
    state.selected = new Set([upload.dataset.upload]);
    go('upload');
    renderPickList();
  }
});

function openModal(url, title, hook) {
  el.modalVideo.src = url;
  el.modalTitle.textContent = title || 'Клип';
  el.modalHook.textContent = hook || '';
  el.modal.hidden = false;
  document.body.style.overflow = 'hidden';
  el.modalVideo.play().catch(() => {});
}

function closeModal() {
  el.modal.hidden = true;
  el.modalVideo.pause();
  el.modalVideo.removeAttribute('src');
  el.modalVideo.load();
  document.body.style.overflow = '';
}

el.modalClose.addEventListener('click', closeModal);
el.modal.addEventListener('click', (event) => {
  if (event.target.dataset.close) closeModal();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !el.modal.hidden) closeModal();
  if (event.ctrlKey && event.key === 'Enter' && state.screen === 'cut' && !el.runBtn.disabled) {
    event.preventDefault();
    el.runBtn.click();
  }
});

/* ---------------------------------------------------------------- параметры */

el.clipsSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-clips]');
  if (!button) return;
  state.clipCount = Number(button.dataset.clips);
  $$('button[data-clips]', el.clipsSeg).forEach((b) => b.classList.toggle('is-active', b === button));
  updateRunHint();
});

el.durSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-dur]');
  if (!button) return;
  state.dur = button.dataset.dur.split(',').map(Number);
  $$('button[data-dur]', el.durSeg).forEach((b) => b.classList.toggle('is-active', b === button));
  updateRunHint();
});

el.styleSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-style]');
  if (!button) return;
  state.style = button.dataset.style;
  $$('.style', el.styleSeg).forEach((b) => b.classList.toggle('is-active', b === button));
  updateRunHint();
});

function updateRunHint() {
  const hasSource = Boolean(state.file) || Boolean(el.ytUrl.value.trim());
  el.runBtn.disabled = !hasSource;
  el.runHint.textContent = hasSource
    ? `${state.clipCount} клипов · ${state.dur[0]}–${state.dur[1]} с · ${STYLE_LABELS[state.style]}`
    : 'Выбери файл или вставь ссылку';
  el.estimate.textContent = `${state.clipCount} клипов · ${state.dur[0]}–${state.dur[1]} с`;
}

el.ytUrl.addEventListener('input', updateRunHint);

/* ---------------------------------------------------------------- источник */

el.drop.addEventListener('click', () => el.fileInput.click());
el.drop.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    el.fileInput.click();
  }
});

el.fileInput.addEventListener('change', () => {
  const file = el.fileInput.files && el.fileInput.files[0];
  if (file) setFile(file);
});

['dragenter', 'dragover'].forEach((type) => {
  el.drop.addEventListener(type, (event) => {
    event.preventDefault();
    el.drop.classList.add('is-over');
  });
});
['dragleave', 'drop'].forEach((type) => {
  el.drop.addEventListener(type, (event) => {
    event.preventDefault();
    el.drop.classList.remove('is-over');
  });
});
el.drop.addEventListener('drop', (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (file) setFile(file);
});

function setFile(file) {
  state.file = file;
  el.drop.classList.add('has-file');
  $('.drop__title', el.drop).textContent = file.name;
  $('.drop__hint', el.drop).textContent = `${(file.size / 1024 / 1024).toFixed(1)} МБ · готов к нарезке`;
  el.ytUrl.value = '';
  updateRunHint();
}

/* ---------------------------------------------------------------- запуск */

function resetJobPanel() {
  el.jobEmpty.hidden = false;
  el.jobRun.hidden = true;
  el.cancelBtn.hidden = true;
  el.log.textContent = '';
  el.progressFill.style.width = '0%';
  el.progressPct.textContent = '0%';
  el.progressStage.textContent = '';
  $$('#steps li').forEach((li) => li.classList.remove('is-done', 'is-active'));
}

function startJobUI(stageText) {
  el.jobEmpty.hidden = true;
  el.jobRun.hidden = false;
  el.cancelBtn.hidden = false;
  el.log.textContent = '';
  el.progressFill.style.width = '0%';
  el.progressPct.textContent = '0%';
  el.progressStage.textContent = stageText || 'старт';
  $$('#steps li').forEach((li) => li.classList.remove('is-done', 'is-active'));
}

function stepForStage(stage) {
  if (!stage) return null;
  if (stage.includes('скач')) return 'scan';
  if (stage.includes('распозн')) return 'stt';
  if (stage.includes('момент')) return 'llm';
  if (stage.includes('рендер')) return 'render';
  if (stage.includes('готов')) return 'render';
  return null;
}

function renderJob(job) {
  if (!job) return;
  state.job = job;
  const pct = Math.max(0, Math.min(100, Number(job.progress) || 0));
  el.progressFill.style.width = `${pct}%`;
  el.progressPct.textContent = `${Math.round(pct)}%`;
  el.progressStage.textContent = job.message || job.stage || '';

  const active = stepForStage(job.stage);
  const order = ['scan', 'stt', 'llm', 'render'];
  const activeIndex = order.indexOf(active);
  $$('#steps li').forEach((li, index) => {
    li.classList.toggle('is-active', order[index] === active && job.status === 'running');
    li.classList.toggle('is-done', activeIndex >= 0 && index < activeIndex || job.status === 'done');
  });

  el.log.textContent = (job.logs || []).join('\n');
  el.log.scrollTop = el.log.scrollHeight;

  if (job.status === 'done') {
    el.cancelBtn.hidden = true;
    setStatus('ok', 'готово');
    const clips = (job.result && job.result.clips) || [];
    if (clips.length) renderClips(clips, job.result.source || state.sourceName);
    if (job.kind === 'upload' && job.result) renderUploadResult(job.result);
    if (job.kind === 'download_model') {
      toast('Модель скачана', 'ok');
      refreshModels();
    }
    if (job.kind === 'analyze' || job.kind === 'youtube') toast(`Готово: ${clips.length} клипов`, 'ok');
  } else if (job.status === 'error') {
    el.cancelBtn.hidden = true;
    setStatus('err', 'ошибка');
    toast(job.error || 'Задача упала', 'err');
  } else if (job.status === 'cancelled') {
    el.cancelBtn.hidden = true;
    setStatus('warn', 'отменено');
  } else {
    setStatus('warn', job.kind === 'upload' ? 'загружаю' : 'работаю');
  }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`/api/jobs/${state.jobId}`);
    renderJob(job);
    if (['done', 'error', 'cancelled'].includes(job.status)) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      el.runBtn.disabled = false;
      if (job.status === 'done' && job.kind === 'upload') loadClipsForJob(state.jobId);
    }
  } catch (error) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    toast(error.message, 'err');
  }
}

function watchJob(jobId, stageText) {
  state.jobId = jobId;
  startJobUI(stageText);
  setStatus('warn', 'работаю');
  el.runBtn.disabled = true;
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(pollJob, 900);
  pollJob();
}

el.runBtn.addEventListener('click', async () => {
  const url = el.ytUrl.value.trim();
  el.runBtn.disabled = true;
  try {
    if (url) {
      const data = await apiJson('/api/youtube', {
        url,
        clips: state.clipCount,
        style: state.style,
        min_dur: state.dur[0],
        max_dur: state.dur[1],
      });
      state.sourceName = url;
      watchJob(data.job_id, 'скачивание');
      return;
    }
    if (!state.file) {
      toast('Сначала выбери файл или ссылку', 'err');
      return;
    }
    const form = new FormData();
    form.append('file', state.file);
    form.append('clips', state.clipCount);
    form.append('style', state.style);
    form.append('min_dur', state.dur[0]);
    form.append('max_dur', state.dur[1]);
    const data = await api('/api/analyze', { method: 'POST', body: form });
    state.sourceName = state.file.name;
    watchJob(data.job_id, 'скан');
  } catch (error) {
    toast(error.message, 'err');
    el.runBtn.disabled = false;
  }
});

el.cancelBtn.addEventListener('click', async () => {
  if (!state.jobId) return;
  try {
    await apiJson(`/api/jobs/${state.jobId}/cancel`, {});
    toast('Отменяю…');
  } catch (error) {
    toast(error.message, 'err');
  }
});

el.openFolder.addEventListener('click', async () => {
  if (!state.jobId) return;
  try {
    await apiJson('/api/open-folder', { job_id: state.jobId });
  } catch (error) {
    toast(error.message, 'err');
  }
});

/* ---------------------------------------------------------------- загрузка на площадки */

async function loadClipsForJob(jobId) {
  try {
    const data = await api(`/api/clips?job_id=${encodeURIComponent(jobId)}`);
    if (data.clips && data.clips.length) {
      state.clipList = data.clips;
      state.jobId = jobId;
      state.sourceName = data.source || '';
      state.selected = new Set(data.clips.map((clip) => String(clip.id)));
      renderClips(data.clips, state.sourceName);
      localStorage.setItem('cutter.jobId', jobId);
    }
  } catch {
    /* тихо: клипов просто нет */
  }
}

async function ensureUploadScreen() {
  if (state.jobId) {
    if (!state.clipList.length) await loadClipsForJob(state.jobId);
  }
  renderPickList();
  updateUploadButton();
  await loadProfiles();
}

function renderPickList() {
  if (!state.clipList.length) {
    el.pickList.innerHTML = `<div class="empty"><p>Сначала сделай нарезку — клипы появятся здесь.</p>
      <button class="btn btn--soft" data-goto="cut">К нарезке</button></div>`;
    el.uploadSource.textContent = '—';
    return;
  }
  el.uploadSource.textContent = state.sourceName ? `«${state.sourceName}»` : '—';
  el.pickList.innerHTML = state.clipList.map((clip) => {
    const id = String(clip.id);
    const checked = state.selected.has(id);
    return `<label class="pick${checked ? ' is-checked' : ''}" data-pick="${esc(id)}">
      <input type="checkbox" ${checked ? 'checked' : ''} data-pick-input="${esc(id)}">
      <span class="pick__box"><svg class="ico"><use href="#i-check"/></svg></span>
      <img class="pick__thumb" src="${esc(clip.preview_url)}" alt="" loading="lazy">
      <span class="pick__info">
        <span class="pick__name">${esc(clip.title || 'Без заголовка')}</span>
        <span class="pick__sub">${esc(fmtDuration(clip.duration))} · ${esc((clip.hashtags || [])[0] || 'без тегов')}</span>
      </span>
    </label>`;
  }).join('');
}

el.pickList.addEventListener('change', (event) => {
  const input = event.target.closest('[data-pick-input]');
  if (!input) return;
  const id = input.dataset.pickInput;
  if (input.checked) state.selected.add(id);
  else state.selected.delete(id);
  const label = input.closest('.pick');
  if (label) label.classList.toggle('is-checked', input.checked);
  updateUploadButton();
});

el.platforms.addEventListener('click', (event) => {
  const button = event.target.closest('[data-platform]');
  if (!button) return;
  const id = button.dataset.platform;
  if (state.platforms.has(id)) {
    if (state.platforms.size === 1) {
      toast('Хотя бы одна платформа должна остаться');
      return;
    }
    state.platforms.delete(id);
  } else {
    state.platforms.add(id);
  }
  button.classList.toggle('is-active', state.platforms.has(id));
  updateUploadButton();
});

el.engineSeg.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-engine]');
  if (!button) return;
  state.engine = button.dataset.engine;
  $$('button[data-engine]', el.engineSeg).forEach((b) => b.classList.toggle('is-active', b === button));
  el.engineHint.textContent = state.engine === 'plain'
    ? 'Сохранённые логины живут в локальном профиле Cutter.'
    : 'Нужен запущенный клиент антидетекта на этой машине.';
  await loadProfiles();
});

el.refreshProfiles.addEventListener('click', loadProfiles);

async function loadProfiles() {
  el.profileSelect.innerHTML = '<option value="">по умолчанию</option>';
  if (state.engine === 'plain') return;
  try {
    const data = await api(`/api/browsers?engine=${encodeURIComponent(state.engine)}`);
    if (!data.running) {
      toast(data.error || 'клиент антидетекта не запущен', 'err');
      return;
    }
    (data.profiles || []).forEach((profile) => {
      const option = document.createElement('option');
      option.value = profile.id;
      option.textContent = `${profile.name}${profile.status === 'running' ? ' · запущен' : ''}`;
      el.profileSelect.appendChild(option);
    });
  } catch (error) {
    toast(error.message, 'err');
  }
}

function updateUploadButton() {
  const count = state.selected.size;
  el.uploadBtn.disabled = count === 0;
  el.uploadBtnText.textContent = `Загрузить ${count} ${plural(count, 'клип', 'клипа', 'клипов')}`;
}

function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

el.uploadBtn.addEventListener('click', async () => {
  if (!state.jobId || !state.selected.size) return;
  const platforms = Array.from(state.platforms);
  el.uploadBtn.disabled = true;
  el.uploadResult.hidden = true;
  try {
    let lastJobId = null;
    for (const clipId of state.selected) {
      const data = await apiJson('/api/upload', {
        job_id: state.jobId,
        clip_id: clipId,
        platforms,
        dry_run: el.dryRun.checked,
        visibility: el.visibility.value,
        browser: { engine: state.engine, profile_id: el.profileSelect.value },
      });
      lastJobId = data.job_id;
    }
    if (lastJobId) {
      watchJob(lastJobId, 'загрузка');
      toast('Запустил загрузку — браузер откроется сам');
    }
  } catch (error) {
    toast(error.message, 'err');
    el.uploadBtn.disabled = false;
  }
});

function renderUploadResult(result) {
  el.uploadResult.hidden = false;
  const items = result.uploads || [];
  el.uploadResultList.innerHTML = items.map((item) => {
    const ok = Boolean(item.ok);
    return `<div class="upload-item ${ok ? 'is-ok' : 'is-err'}">
      <span class="upload-item__icon"><svg class="ico"><use href="#${ok ? 'i-check' : 'i-close'}"/></svg></span>
      <div class="upload-item__text">
        <strong>${esc(item.platform)}</strong>
        <p>${esc(item.message || (ok ? 'готово' : 'не вышло'))}</p>
      </div>
      ${item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noopener">открыть</a>` : ''}
    </div>`;
  }).join('') || '<div class="empty"><p>Пусто</p></div>';
}

/* ---------------------------------------------------------------- история */

async function loadHistory() {
  el.history.innerHTML = '<div class="skeleton" style="height:64px"></div>'.repeat(3);
  try {
    const data = await api('/api/jobs?limit=30');
    const rows = (data.jobs || []).filter((job) => job.kind !== 'download_model');
    if (!rows.length) {
      el.history.innerHTML = '<div class="empty"><p>Пока пусто. Запусти первую нарезку.</p></div>';
      return;
    }
    el.history.innerHTML = rows.map((job) => {
      const kindIcon = job.kind === 'upload' ? 'i-upload' : job.kind === 'youtube' ? 'i-link' : 'i-cut';
      const metaName = job.meta?.source || job.meta?.url || job.meta?.platforms?.join(', ') || '';
      return `<div class="hist" data-hist="${esc(job.id)}">
        <span class="hist__kind"><svg class="ico"><use href="#${kindIcon}"/></svg></span>
        <div class="hist__main">
          <div class="hist__title">${esc(job.kind === 'upload' ? 'Загрузка' : job.kind === 'youtube' ? 'YouTube' : 'Нарезка')} · ${esc(metaName)}</div>
          <div class="hist__sub">${esc(fmtWhen(job.created_at))} · ${Math.round(job.progress || 0)}%</div>
        </div>
        <span class="status status--${esc(job.status)}">${esc(job.status)}</span>
      </div>`;
    }).join('');
  } catch (error) {
    el.history.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
  }
}

el.historyRefresh.addEventListener('click', loadHistory);

el.history.addEventListener('click', async (event) => {
  const row = event.target.closest('[data-hist]');
  if (!row) return;
  const jobId = row.dataset.hist;
  await loadClipsForJob(jobId);
  if (state.clipList.length) {
    state.jobId = jobId;
    renderClips(state.clipList, state.sourceName);
    go('upload');
    toast(`Загружено клипов: ${state.clipList.length}`);
  } else {
    toast('У этой задачи нет готовых клипов', 'err');
  }
});

/* ---------------------------------------------------------------- настройки */

function providerById(id) {
  return state.providers.find((provider) => provider.id === id) || null;
}

function renderProviderSelect(selectedId) {
  el.llmProvider.innerHTML = state.providers
    .map((provider) => `<option value="${esc(provider.id)}">${esc(provider.label)}</option>`)
    .join('');
  const fallback = selectedId || state.settings?.llm?.provider || 'deepseek';
  el.llmProvider.value = providerById(fallback) ? fallback : (state.providers[0]?.id || 'deepseek');
  renderModelSelect(state.settings?.llm?.model);
}

function renderModelSelect(selectedModel) {
  const provider = providerById(el.llmProvider.value);
  const models = provider ? provider.models : [];
  el.llmModel.innerHTML = models
    .map((model) => {
      const free = model.free ? ' · бесплатно' : '';
      return `<option value="${esc(model.id)}">${esc(model.label || model.id)}${esc(free)}</option>`;
    })
    .join('');
  const wanted = selectedModel && models.some((model) => model.id === selectedModel)
    ? selectedModel
    : (models[0]?.id || '');
  if (wanted) el.llmModel.value = wanted;
  const current = models.find((model) => model.id === el.llmModel.value);
  el.llmHint.textContent = provider && provider.key_url
    ? `Ключ: ${provider.key_url}. Хранится локально в app/data/settings.json`
    : 'Ключ хранится локально в app/data/settings.json';
  if (current && current.note) el.llmHint.textContent += ` · ${current.note}`;
}

el.llmProvider.addEventListener('change', () => {
  renderModelSelect(null);
  el.llmKey.value = '';
  state.keyDirty = false;
  renderChips(null);
});

el.llmKey.addEventListener('input', () => {
  state.keyDirty = true;
  renderChips(null);
});

el.testKey.addEventListener('click', async () => {
  el.testKey.disabled = true;
  try {
    const key = state.keyDirty ? el.llmKey.value.trim() : '';
    const data = await apiJson('/api/providers/test', {
      provider: el.llmProvider.value,
      model: el.llmModel.value,
      api_key: key,
    });
    toast(data.message || (data.ok ? 'работает' : 'не работает'), data.ok ? 'ok' : 'err');
    setStatus(data.ok ? 'ok' : 'err', data.ok ? 'ключ рабочий' : 'ключ не работает');
  } catch (error) {
    toast(error.message, 'err');
  } finally {
    el.testKey.disabled = false;
  }
});

async function refreshModels() {
  try {
    const data = await api('/api/models');
    const installed = new Set((data.installed || []).map((item) => item.size));
    if (state.health) {
      state.health.models = data.installed || [];
    }
    renderSetup();
    el.models.innerHTML = (data.sizes || []).map((size) => {
      const isInstalled = installed.has(size.id);
      return `<div class="model-row">
        <div>
          <div class="model-row__name">${esc(size.label)}</div>
          <div class="model-row__note">${esc(size.mb)} МБ · ${esc(size.note)}</div>
        </div>
        <div class="model-row__state">
          ${isInstalled
            ? '<span class="tag tag--ok">на диске</span>'
            : `<button class="btn btn--soft btn--tiny" data-model="${esc(size.id)}">Скачать</button>`}
        </div>
      </div>`;
    }).join('');
  } catch (error) {
    el.models.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
  }
}

el.models.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-model]');
  if (!button) return;
  button.disabled = true;
  button.textContent = 'качаю…';
  try {
    const data = await apiJson('/api/models/download', { size: button.dataset.model });
    state.jobId = data.job_id;
    toast('Качаю модель — прогресс в разделе «Нарезка»');
    watchJob(data.job_id, 'скачивание');
    go('cut');
  } catch (error) {
    toast(error.message, 'err');
    button.disabled = false;
    button.textContent = 'Скачать';
  }
});

el.saveSettings.addEventListener('click', async () => {
  el.saveSettings.disabled = true;
  try {
    const patch = {
      llm: {
        provider: el.llmProvider.value,
        model: el.llmModel.value,
        fallback: el.llmFallback.checked,
      },
      stt: { language: el.language.value },
      paths: { outdir: el.outdir.value.trim() },
    };
    if (state.keyDirty && el.llmKey.value.trim()) {
      patch.llm.api_key = el.llmKey.value.trim();
    }
    state.settings = await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    state.keyDirty = false;
    el.llmKey.value = '';
    toast('Сохранил', 'ok');
    renderChips(null);
    updateRunHint();
  } catch (error) {
    toast(error.message, 'err');
  } finally {
    el.saveSettings.disabled = false;
  }
});

/* ---------------------------------------------------------------- старт */

async function boot() {
  resetJobPanel();
  setStatus('', 'загружаю');

  try {
    const health = await api('/api/health');
    setStatus(health.ffmpeg ? 'ok' : 'err', health.ffmpeg ? 'ffmpeg на месте' : 'нет ffmpeg');
    renderChips(health);
    el.aboutText.textContent = `Cutter ${health.version} · Python ${health.python}`;
    el.aboutStack.textContent = health.browser
      ? 'ffmpeg · faster-whisper · Playwright'
      : 'ffmpeg · faster-whisper · браузер не найден';
    if (!health.browser && health.browser_error) {
      toast(`Загрузка на площадки недоступна: ${health.browser_error}`, 'err');
    }
  } catch (error) {
    setStatus('err', 'сервер не отвечает');
    toast(error.message, 'err');
  }

  try {
    const providers = await api('/api/providers');
    state.providers = providers.providers || [];
  } catch {
    state.providers = [];
  }

  try {
    state.settings = await api('/api/settings');
    renderProviderSelect();
    el.llmFallback.checked = state.settings.llm.fallback !== false;
    el.language.value = state.settings.stt.language || 'ru';
    el.outdir.value = state.settings.paths.outdir || '';
    el.dryRun.checked = state.settings.upload.dry_run !== false;
    el.visibility.value = state.settings.upload.visibility || 'public';
    state.engine = state.settings.upload.engine || 'plain';
    $$('button[data-engine]', el.engineSeg).forEach((b) => b.classList.toggle('is-active', b.dataset.engine === state.engine));
    if (state.settings.llm.api_key) {
      el.llmHint.dataset.hasKey = '1';
    }
    renderChips(null);
  } catch (error) {
    toast(error.message, 'err');
  }

  await refreshModels();
  updateRunHint();

  const savedJob = localStorage.getItem('cutter.jobId');
  if (savedJob) {
    await loadClipsForJob(savedJob);
    if (state.clipList.length) {
      state.jobId = savedJob;
      renderClips(state.clipList, state.sourceName);
    }
  }

  go('cut');
}

boot();
