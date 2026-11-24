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
    encoder: $('#chip-encoder'),
    browser: $('#chip-browser'),
  },
  drop: $('#dropzone'),
  fileInput: $('#file-input'),
  ytUrl: $('#yt-url'),
  ytBtn: $('#yt-btn'),
  modeSeg: $('#mode-seg'),
  modeHint: $('#mode-hint'),
  brainrotPanel: $('#brainrot-panel'),
  voiceSelect: $('#voice-select'),
  origVol: $('#orig-vol'),
  origVolLabel: $('#orig-vol-label'),
  brainrotRewrite: $('#brainrot-rewrite'),
  dramaMusicPills: $('#drama-music-pills'),
  clipsSeg: $('#clips-seg'),
  clipsAllBtn: $('#clips-all-btn'),
  clipsCustom: $('#clips-custom'),
  durSeg: $('#dur-seg'),
  durMin: $('#dur-min'),
  durMax: $('#dur-max'),
  subtitlesToggle: $('#subtitles-toggle'),
  styleSeg: $('#style-seg'),
  framingSeg: $('#framing-seg'),
  fxSeg: $('#fx-seg'),
  musicSelect: $('#music-select'),
  musicHint: $('#music-hint'),
  volRow: $('#vol-row'),
  musicVolume: $('#music-volume'),
  volLabel: $('#vol-label'),
  integrationSelect: $('#integration-select'),
  openAdModal: $('#open-ad-modal'),
  protectSeg: $('#protect-seg'),
  stats: $('#stats'),
  statCount: $('#stat-count'),
  statDuration: $('#stat-duration'),
  statScore: $('#stat-score'),
  statWpm: $('#stat-wpm'),
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
  applyAdResults: $('#apply-ad-results'),
  uploadAllResults: $('#upload-all-results'),
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
  outdirLink: $('#outdir-link'),
  loginChatgptOauth: $('#login-chatgpt-oauth'),
  loginChatgptBrowser: $('#login-chatgpt-browser'),
  loginGoogleBrowser: $('#login-google-browser'),
  accountsList: $('#accounts-list'),
  llmProvider: $('#llm-provider'),
  llmAccountRow: $('#llm-account-row'),
  llmAccount: $('#llm-account'),
  llmModel: $('#llm-model'),
  llmModelCustom: $('#llm-model-custom'),
  llmBaseRow: $('#llm-base-row'),
  llmBaseUrl: $('#llm-base-url'),
  llmKeyRow: $('#llm-key-row'),
  llmKey: $('#llm-key'),
  llmHint: $('#llm-hint'),
  llmTestAccountRow: $('#llm-test-account-row'),
  testAccountBtn: $('#test-account-btn'),
  llmFallback: $('#llm-fallback'),
  testKey: $('#test-key'),
  models: $('#models'),
  outdir: $('#outdir'),
  language: $('#language'),
  encoderSelect: $('#encoder-select'),
  netProxy: $('#net-proxy'),
  netIpv4: $('#net-ipv4'),
  netCookies: $('#net-cookies'),
  saveSettings: $('#save-settings'),
  aboutText: $('#about-text'),
  aboutStack: $('#about-stack'),
  modal: $('#modal'),
  modalVideo: $('#modal-video'),
  modalTitle: $('#modal-title'),
  modalHook: $('#modal-hook'),
  modalClose: $('#modal-close'),
  adModal: $('#ad-modal'),
  adModalClose: $('#ad-modal-close'),
  adFile: $('#ad-file'),
  adName: $('#ad-name'),
  adMode: $('#ad-mode'),
  adPosition: $('#ad-position'),
  adHookRow: $('#ad-hook-row'),
  adHookSec: $('#ad-hook-sec'),
  adScaleRow: $('#ad-scale-row'),
  adScale: $('#ad-scale'),
  adTimeRow: $('#ad-time-row'),
  adStartSec: $('#ad-start-sec'),
  adDurSec: $('#ad-dur-sec'),
  adSaveBtn: $('#ad-save-btn'),
  adApplyNowBtn: $('#ad-apply-now-btn'),
  adLibrary: $('#ad-library'),
  editModal: $('#edit-modal'),
  editModalClose: $('#edit-modal-close'),
  editTitle: $('#edit-title'),
  editHook: $('#edit-hook'),
  editTags: $('#edit-tags'),
  editStart: $('#edit-start'),
  editEnd: $('#edit-end'),
  editSaveMeta: $('#edit-save-meta'),
  editRecutBtn: $('#edit-recut-btn'),
  toasts: $('#toasts'),
};

const STYLE_LABELS = { classic: 'Классика', bold: 'Жирный', clean: 'Чистый', neon: 'Неон', drama: 'Дорама' };
const MODE_HINTS = {
  moments: 'ИИ выберет самые цепляющие моменты с хуком в начале.',
  parts: 'Умная последовательная нарезка на части по паузам и границам фраз (без ИИ-отбора).',
  brainrot: 'Китайская история: пересказ + полная нейро-озвучка в тайминг + каноничный фон дорамы.',
};

const state = {
  screen: 'cut',
  file: null,
  mode: 'moments',
  clipCount: 5,
  dur: [18, 60],
  style: 'classic',
  framing: 'auto',
  subtitles: true,
  encoder: 'auto',
  effect: 'none',
  protection: 'off',
  music: 'none',
  musicVolume: 18,
  voice: 'dmitry_drama',
  origAudioVol: 8,
  brainrotRewrite: true,
  integrationId: '',
  integrations: [],
  looks: { effects: [], music: [], voices: [], encoder: 'cpu' },
  jobId: null,
  job: null,
  pollTimer: null,
  clipList: [],
  editingClipId: null,
  selected: new Set(),
  platforms: new Set(['tiktok']),
  engine: 'plain',
  providers: [],
  accountProviders: [],
  accounts: [],
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
  settings: ['Настройки', 'Аккаунты с подпиской, модели, сеть и ускорение'],
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
  if (screen === 'settings') refreshAccounts();
}

$$('.rail__btn').forEach((btn) => btn.addEventListener('click', () => go(btn.dataset.nav)));
document.addEventListener('click', (event) => {
  const goto = event.target.closest('[data-goto]');
  if (goto) go(goto.dataset.goto);
});

/* ---------------------------------------------------------------- статус и чипы */

function setStatus(kind, text) {
  el.dots.status.className = `dot ${kind ? 'is-' + kind : ''}`;
  el.dots.text.textContent = text;
}

function isAccountProvider(id) {
  return id === 'chatgpt_sub' || id === 'google_sub';
}

function hasActiveAuth() {
  const provider = state.settings?.llm?.provider || '';
  if (isAccountProvider(provider)) {
    const want = provider === 'chatgpt_sub' ? 'chatgpt' : 'google';
    return state.accounts.some((acc) => acc.provider === want && acc.status === 'ready');
  }
  return Boolean(state.keyDirty ? el.llmKey.value.trim() : state.settings?.llm?.api_key);
}

function renderChips(health) {
  if (health) state.health = health;
  const current = state.health;
  const settings = state.settings;
  const provider = settings?.llm?.provider || '—';
  const model = settings?.llm?.model || '—';
  el.chips.model.textContent = `${provider} / ${model}`;
  el.chips.model.className = 'chip chip--ghost';

  const authOk = hasActiveAuth();
  const keyChip = el.chips.key;
  if (isAccountProvider(provider)) {
    keyChip.textContent = authOk ? 'подписка активна' : 'нужен вход в аккаунт';
  } else {
    keyChip.textContent = authOk ? 'ключ есть' : 'ключ или аккаунт';
  }
  keyChip.className = `chip chip--btn ${authOk ? 'is-ok' : 'is-bad'}`;

  if (current) {
    const enc = (current.encoder || state.looks.encoder || 'cpu').toUpperCase();
    el.chips.encoder.textContent = enc === 'CPU' ? 'рендер: CPU' : `GPU: ${enc}`;
    el.chips.encoder.className = `chip ${enc === 'CPU' ? 'chip--ghost' : 'is-ok'}`;

    el.chips.browser.textContent = current.browser ? 'браузер есть' : 'нужен Chrome/Edge';
    el.chips.browser.className = `chip ${current.browser ? 'is-ok' : 'is-bad'}`;
  }

  renderSetup();
}

function renderSetup() {
  const setup = $('#setup');
  if (!setup) return;
  const hasModel = Boolean(state.health && state.health.models && state.health.models.length);
  const authOk = hasActiveAuth() || state.mode === 'parts';
  const modelItem = setup.querySelector('[data-setup="model"]');
  const keyItem = setup.querySelector('[data-setup="key"]');
  if (modelItem) modelItem.hidden = hasModel;
  if (keyItem) keyItem.hidden = authOk;
  setup.hidden = hasModel && authOk;
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
    const vUrl = `${clip.video_url}?t=${Date.now()}`;
    const pUrl = `${clip.preview_url}?t=${Date.now()}`;
    card.innerHTML = `
      <div class="clip__thumb">
        <img src="${esc(pUrl)}" alt="" loading="lazy">
        <div class="clip__badges">
          <span class="badge badge--score${scoreClass}">${score ? esc(score) + '%' : '—'}</span>
          <span class="badge">${esc(fmtDuration(clip.duration))}</span>
        </div>
        <button class="clip__play" data-play="${esc(vUrl)}" data-title="${esc(clip.title)}" data-hook="${esc(clip.hook)}" aria-label="Смотреть клип">
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
        <div class="clip__meta clip__meta--dense">${esc(clipMetrics(clip))}</div>
        <div class="clip__actions clip__actions--3">
          <button class="clip__btn" data-download="${esc(clip.video_url)}" title="Скачать mp4">Файл</button>
          <button class="clip__btn" data-edit="${esc(clip.id)}" title="Править текст и границы">Править</button>
          <button class="clip__btn clip__btn--accent" data-upload="${esc(clip.id)}">Грузить</button>
        </div>
      </div>`;
    el.clips.appendChild(card);
  });

  renderStrip(clips);
  renderStats(clips);
}

function renderStats(clips) {
  if (!clips.length) {
    el.stats.hidden = true;
    return;
  }
  el.stats.hidden = false;
  const totalDuration = clips.reduce((sum, clip) => sum + (Number(clip.duration) || 0), 0);
  const avgScore = Math.round(clips.reduce((sum, clip) => sum + (Number(clip.score) || 0), 0) / clips.length);
  const wpms = clips.map((clip) => Number(clip.wpm) || 0).filter((value) => value > 0);
  const avgWpm = wpms.length ? Math.round(wpms.reduce((a, b) => a + b, 0) / wpms.length) : 0;
  el.statCount.textContent = String(clips.length);
  el.statDuration.textContent = fmtDuration(totalDuration);
  el.statScore.textContent = `${avgScore}%`;
  el.statWpm.textContent = String(avgWpm);
}

function clipMetrics(clip) {
  const parts = [`${Number(clip.words) || 0} сл.`, `${Number(clip.wpm) || 0}/мин`];
  if (clip.effect && clip.effect !== 'none') {
    const fx = (state.looks.effects || []).find((item) => item.id === clip.effect);
    parts.push(fx ? fx.label : clip.effect);
  }
  if (clip.music && clip.music !== 'none') {
    const track = (state.looks.music || []).find((item) => item.id === clip.music);
    parts.push(track ? `♫ ${track.title}` : '♫');
  }
  if (clip.integration) parts.push(`+ ${clip.integration}`);
  return parts.join(' · ');
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
  const edit = event.target.closest('[data-edit]');
  if (edit) {
    openEditModal(edit.dataset.edit);
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
  if (event.key === 'Escape') {
    if (!el.modal.hidden) closeModal();
    if (!el.adModal.hidden) closeAdModal();
    if (!el.editModal.hidden) closeEditModal();
  }
  if (event.ctrlKey && event.key === 'Enter' && state.screen === 'cut' && !el.runBtn.disabled) {
    event.preventDefault();
    el.runBtn.click();
  }
});

/* ---------------------------------------------------------------- параметры и режимы */

function syncModeUI() {
  const isBrainrot = state.mode === 'brainrot';
  const allowAll = state.mode === 'parts' || isBrainrot;
  el.brainrotPanel.hidden = !isBrainrot;
  el.clipsAllBtn.hidden = !allowAll;
  el.modeHint.textContent = MODE_HINTS[state.mode] || '';
  if (!allowAll && state.clipCount === 0) {
    state.clipCount = 5;
    syncClipButtons();
  }
  $$('button[data-mode]', el.modeSeg).forEach((b) => b.classList.toggle('is-active', b.dataset.mode === state.mode));
  renderSetup();
  updateRunHint();
}

el.modeSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-mode]');
  if (!button) return;
  state.mode = button.dataset.mode;
  if (state.mode === 'brainrot') {
    if (state.music === 'none' && (state.looks.music || []).some((m) => m.id === 'drama-guzheng')) {
      state.music = 'drama-guzheng';
      el.musicSelect.value = state.music;
      updateVolumeVisibility();
    }
    if (state.style === 'classic') {
      state.style = 'drama';
      $$('.style', el.styleSeg).forEach((b) => b.classList.toggle('is-active', b.dataset.style === state.style));
    }
  }
  syncModeUI();
});

function syncClipButtons() {
  const matched = $$('button[data-clips]', el.clipsSeg).some((b) => {
    const active = Number(b.dataset.clips) === state.clipCount;
    b.classList.toggle('is-active', active);
    return active;
  });
  el.clipsCustom.value = matched || state.clipCount <= 0 ? '' : String(state.clipCount);
}

el.clipsSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-clips]');
  if (!button) return;
  state.clipCount = Number(button.dataset.clips);
  syncClipButtons();
  updateRunHint();
});

el.clipsCustom.addEventListener('input', () => {
  const val = parseInt(el.clipsCustom.value, 10);
  if (Number.isFinite(val) && val >= 1) {
    state.clipCount = Math.min(100, val);
    $$('button[data-clips]', el.clipsSeg).forEach((b) => b.classList.remove('is-active'));
    updateRunHint();
  }
});

function syncDurationUI() {
  el.durMin.value = String(state.dur[0]);
  el.durMax.value = String(state.dur[1]);
  $$('button[data-dur]', el.durSeg).forEach((b) => {
    const [a, c] = b.dataset.dur.split(',').map(Number);
    b.classList.toggle('is-active', a === state.dur[0] && c === state.dur[1]);
  });
  updateRunHint();
}

el.durSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-dur]');
  if (!button) return;
  state.dur = button.dataset.dur.split(',').map(Number);
  syncDurationUI();
});

function onCustomDur() {
  const lo = Math.max(4, parseInt(el.durMin.value, 10) || 18);
  const hi = Math.max(lo, parseInt(el.durMax.value, 10) || lo);
  state.dur = [lo, hi];
  $$('button[data-dur]', el.durSeg).forEach((b) => b.classList.remove('is-active'));
  updateRunHint();
}
el.durMin.addEventListener('change', onCustomDur);
el.durMax.addEventListener('change', onCustomDur);

el.subtitlesToggle.addEventListener('change', () => {
  state.subtitles = el.subtitlesToggle.checked;
  updateRunHint();
});

el.styleSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-style]');
  if (!button) return;
  state.style = button.dataset.style;
  $$('.style', el.styleSeg).forEach((b) => b.classList.toggle('is-active', b === button));
  updateRunHint();
});

el.framingSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-framing]');
  if (!button) return;
  state.framing = button.dataset.framing;
  $$('button[data-framing]', el.framingSeg).forEach((b) => b.classList.toggle('is-active', b === button));
});

el.voiceSelect.addEventListener('change', () => { state.voice = el.voiceSelect.value; });
el.origVol.addEventListener('input', () => {
  state.origAudioVol = Number(el.origVol.value);
  el.origVolLabel.textContent = `${state.origAudioVol}%`;
});
el.brainrotRewrite.addEventListener('change', () => {
  state.brainrotRewrite = el.brainrotRewrite.checked;
});
el.dramaMusicPills.addEventListener('click', (event) => {
  const btn = event.target.closest('[data-pick-music]');
  if (!btn) return;
  const trackId = btn.dataset.pickMusic;
  if ((state.looks.music || []).some((m) => m.id === trackId)) {
    state.music = trackId;
    el.musicSelect.value = trackId;
    updateVolumeVisibility();
    updateRunHint();
  }
});

/* ---------------------------------------------------------------- стиль кадра, музыка, защита */

function renderLooks() {
  const effects = state.looks.effects || [];
  el.fxSeg.innerHTML = effects.map((fx) => `
    <button class="fx${fx.id === state.effect ? ' is-active' : ''}" data-fx="${esc(fx.id)}" title="${esc(fx.hint || '')}">
      <span class="fx__dot" style="--tone:${esc(fx.tone || '#6b675e')}"></span>
      <span class="fx__label">${esc(fx.label)}</span>
    </button>`).join('');

  const music = state.looks.music || [];
  const options = ['<option value="none">Без музыки</option>']
    .concat(music.map((track) => `<option value="${esc(track.id)}">${esc(track.title)}${track.mood ? ' · ' + esc(track.mood) : ''}</option>`));
  el.musicSelect.innerHTML = options.join('');
  el.musicSelect.value = music.some((track) => track.id === state.music) ? state.music : 'none';
  el.musicHint.textContent = music.length
    ? 'Треки сгенерированы для Cutter — claim\'ов не будет.'
    : 'Пак музыки не установлен. Можно рендерить без подложки.';
  updateVolumeVisibility();

  const voices = state.looks.voices || [];
  el.voiceSelect.innerHTML = voices
    .map((v) => `<option value="${esc(v.id)}">${esc(v.label)}${v.note ? ' — ' + esc(v.note) : ''}</option>`)
    .join('');
  if (voices.some((v) => v.id === state.voice)) el.voiceSelect.value = state.voice;
}

function updateVolumeVisibility() {
  const hasMusic = state.music && state.music !== 'none';
  el.volRow.hidden = !hasMusic;
  el.volLabel.textContent = `${state.musicVolume}%`;
  el.musicVolume.value = String(state.musicVolume);
}

el.fxSeg.addEventListener('click', (event) => {
  const button = event.target.closest('[data-fx]');
  if (!button) return;
  state.effect = button.dataset.fx;
  $$('.fx', el.fxSeg).forEach((b) => b.classList.toggle('is-active', b === button));
  updateRunHint();
});

el.musicSelect.addEventListener('change', () => {
  state.music = el.musicSelect.value;
  updateVolumeVisibility();
  updateRunHint();
});

el.musicVolume.addEventListener('input', () => {
  state.musicVolume = Number(el.musicVolume.value);
  el.volLabel.textContent = `${state.musicVolume}%`;
});

el.integrationSelect.addEventListener('change', () => {
  state.integrationId = el.integrationSelect.value;
  updateRunHint();
});

el.protectSeg.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-protect]');
  if (!button) return;
  state.protection = button.dataset.protect;
  $$('button[data-protect]', el.protectSeg).forEach((b) => b.classList.toggle('is-active', b === button));
});

function updateRunHint() {
  const hasSource = Boolean(state.file) || Boolean(el.ytUrl.value.trim());
  el.runBtn.disabled = !hasSource;
  const fx = (state.looks.effects || []).find((item) => item.id === state.effect);
  const countLabel = state.clipCount === 0 ? 'все части' : `${state.clipCount} клипов`;
  const modeLabel = state.mode === 'parts' ? 'на части' : state.mode === 'brainrot' ? 'дорама' : 'моменты';
  const parts = [modeLabel, countLabel, `${state.dur[0]}–${state.dur[1]} с`];
  if (state.subtitles) parts.push(STYLE_LABELS[state.style] || 'субтитры');
  else parts.push('без сабов');
  if (fx && fx.id !== 'none') parts.push(fx.label);
  if (state.music !== 'none') parts.push('музыка');
  if (state.integrationId) parts.push('+реклама');
  el.runHint.textContent = hasSource ? parts.join(' · ') : 'Выбери файл или вставь ссылку';
  el.estimate.textContent = `${countLabel} · ${state.dur[0]}–${state.dur[1]} с`;
}

el.ytUrl.addEventListener('input', updateRunHint);

el.ytBtn.addEventListener('click', () => {
  const url = el.ytUrl.value.trim();
  if (!url) {
    toast('Вставь ссылку: YouTube, Rutube или VK видео', 'err');
    el.ytUrl.focus();
    return;
  }
  if (el.runBtn.disabled) el.runBtn.disabled = false;
  el.runBtn.click();
});

el.ytUrl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    el.ytBtn.click();
  }
});

/* ---------------------------------------------------------------- папка результатов */

function updateOutdir() {
  const custom = (state.settings?.paths?.outdir || '').trim();
  el.outdirLink.textContent = custom || 'папка приложения (по умолчанию)';
  el.outdirLink.title = custom || 'Нажми, чтобы выбрать папку';
}

el.outdirLink.addEventListener('click', () => {
  go('settings');
  setTimeout(() => {
    const input = $('#outdir');
    if (input) {
      input.focus();
      input.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, 80);
});

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
  if (stage.includes('скач') || stage.includes('звук')) return 'scan';
  if (stage.includes('распозн')) return 'stt';
  if (stage.includes('момент')) return 'llm';
  if (stage.includes('рендер') || stage.includes('интегр')) return 'render';
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
    if (clips.length) {
      renderClips(clips, job.result.source || state.sourceName);
      if (job.kind === 'analyze' || job.kind === 'youtube') {
        state.selected = new Set(clips.map((c) => String(c.id)));
        localStorage.setItem('cutter.jobId', job.id);
      }
    }
    if (job.kind === 'upload' && job.result) renderUploadResult(job.result);
    if (job.kind === 'download_model') {
      toast('Модель скачана', 'ok');
      refreshModels();
    }
    if (job.kind === 'login') {
      toast('Аккаунт подключён', 'ok');
      refreshAccounts().then(() => renderChips(null));
    }
    if (job.kind === 'integration') {
      toast('Интеграция применена к клипам', 'ok');
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
      el.uploadBtn.disabled = state.selected.size === 0;
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
        mode: state.mode,
        clips: state.clipCount,
        style: state.style,
        framing: state.framing,
        subtitles: state.subtitles,
        encoder: state.encoder,
        effect: state.effect,
        protection: state.protection,
        music: state.music,
        music_volume: state.musicVolume / 100,
        voice: state.voice,
        orig_audio_vol: state.origAudioVol / 100,
        brainrot_rewrite: state.brainrotRewrite,
        integration_id: state.integrationId,
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
    form.append('mode', state.mode);
    form.append('clips', state.clipCount);
    form.append('style', state.style);
    form.append('framing', state.framing);
    form.append('subtitles', String(state.subtitles));
    form.append('encoder', state.encoder);
    form.append('effect', state.effect);
    form.append('protection', state.protection);
    form.append('music', state.music);
    form.append('music_volume', state.musicVolume / 100);
    form.append('voice', state.voice);
    form.append('orig_audio_vol', state.origAudioVol / 100);
    form.append('brainrot_rewrite', String(state.brainrotRewrite));
    form.append('integration_id', state.integrationId);
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

el.uploadAllResults.addEventListener('click', () => {
  if (!state.clipList.length) return;
  state.selected = new Set(state.clipList.map((c) => String(c.id)));
  go('upload');
  renderPickList();
  updateUploadButton();
});

/* ---------------------------------------------------------------- редактирование клипов */

function openEditModal(clipId) {
  const clip = state.clipList.find((c) => String(c.id) === String(clipId));
  if (!clip) return;
  state.editingClipId = String(clip.id);
  el.editTitle.value = clip.title || '';
  el.editHook.value = clip.hook || '';
  el.editTags.value = (clip.hashtags || []).join(' ');
  el.editStart.value = String(clip.start ?? 0);
  el.editEnd.value = String(clip.end ?? 10);
  el.editModal.hidden = false;
}

function closeEditModal() {
  el.editModal.hidden = true;
  state.editingClipId = null;
}

el.editModalClose.addEventListener('click', closeEditModal);
el.editModal.addEventListener('click', (e) => {
  if (e.target.dataset.closeEdit) closeEditModal();
});

el.editSaveMeta.addEventListener('click', async () => {
  if (!state.jobId || !state.editingClipId) return;
  el.editSaveMeta.disabled = true;
  try {
    const data = await apiJson(`/api/clips/${state.jobId}/${state.editingClipId}`, {
      title: el.editTitle.value.trim(),
      hook: el.editHook.value.trim(),
      hashtags: el.editTags.value.trim(),
    }, 'PATCH');
    const idx = state.clipList.findIndex((c) => String(c.id) === state.editingClipId);
    if (idx >= 0 && data.clip) state.clipList[idx] = data.clip;
    renderClips(state.clipList, state.sourceName);
    toast('Метаданные клипа сохранены', 'ok');
    closeEditModal();
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    el.editSaveMeta.disabled = false;
  }
});

el.editRecutBtn.addEventListener('click', async () => {
  if (!state.jobId || !state.editingClipId) return;
  el.editRecutBtn.disabled = true;
  el.editRecutBtn.textContent = 'Перерезаю…';
  try {
    const data = await apiJson(`/api/clips/${state.jobId}/${state.editingClipId}/recut`, {
      start: Number(el.editStart.value),
      end: Number(el.editEnd.value),
    });
    const idx = state.clipList.findIndex((c) => String(c.id) === state.editingClipId);
    if (idx >= 0 && data.clip) state.clipList[idx] = data.clip;
    renderClips(state.clipList, state.sourceName);
    toast('Границы клипа обновлены', 'ok');
    closeEditModal();
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    el.editRecutBtn.disabled = false;
    el.editRecutBtn.textContent = 'Перерезать границы';
  }
});

/* ---------------------------------------------------------------- интеграции */

async function refreshIntegrations() {
  try {
    const data = await api('/api/integrations');
    state.integrations = data.integrations || [];
  } catch {
    state.integrations = [];
  }
  const opts = ['<option value="">Без интеграции</option>']
    .concat(state.integrations.map((ad) => `<option value="${esc(ad.id)}">${esc(ad.name)} (${ad.mode === 'overlay' ? 'баннер' : 'вставка'})</option>`));
  el.integrationSelect.innerHTML = opts.join('');
  if (state.integrations.some((ad) => ad.id === state.integrationId)) {
    el.integrationSelect.value = state.integrationId;
  } else {
    state.integrationId = '';
  }
  renderAdLibrary();
}

function renderAdLibrary() {
  if (!state.integrations.length) {
    el.adLibrary.innerHTML = '<p class="hint">Сохранённых интеграций пока нет.</p>';
    return;
  }
  el.adLibrary.innerHTML = state.integrations.map((ad) => `
    <div class="ad-item">
      <div class="ad-item__info">
        <strong>${esc(ad.name)}</strong>
        <span>${ad.mode === 'overlay' ? 'Баннер' : 'Видео'} · ${esc(ad.position)} · ${(Number(ad.bytes || 0) / 1024).toFixed(0)} КБ</span>
      </div>
      <button class="btn btn--soft btn--tiny" data-use-ad="${esc(ad.id)}" type="button">Выбрать</button>
      ${state.clipList.length ? `<button class="btn btn--soft btn--tiny" data-apply-ad="${esc(ad.id)}" type="button">В клипы</button>` : ''}
      <button class="btn btn--ghost btn--tiny" data-del-ad="${esc(ad.id)}" type="button">✕</button>
    </div>`).join('');
}

function syncAdModeFields() {
  const isOverlay = el.adMode.value === 'overlay';
  el.adHookRow.hidden = isOverlay || el.adPosition.value !== 'after_hook';
  el.adScaleRow.hidden = !isOverlay;
  el.adTimeRow.hidden = !isOverlay;
  if (isOverlay) {
    el.adPosition.innerHTML = `
      <option value="bottom">Снизу (над краем)</option>
      <option value="top">Сверху</option>
      <option value="center">По центру</option>`;
  } else {
    el.adPosition.innerHTML = `
      <option value="after_hook">После хука (через N сек)</option>
      <option value="start">В самом начале (преролл)</option>
      <option value="end">В конце (постролл)</option>`;
  }
}

el.adMode.addEventListener('change', syncAdModeFields);
el.adPosition.addEventListener('change', () => {
  el.adHookRow.hidden = el.adMode.value === 'overlay' || el.adPosition.value !== 'after_hook';
});

function openAdModal(forResults = false) {
  syncAdModeFields();
  el.adApplyNowBtn.hidden = !forResults && !state.clipList.length;
  renderAdLibrary();
  el.adModal.hidden = false;
}

function closeAdModal() {
  el.adModal.hidden = true;
}

el.openAdModal.addEventListener('click', () => openAdModal(false));
el.applyAdResults.addEventListener('click', () => openAdModal(true));
el.adModalClose.addEventListener('click', closeAdModal);
el.adModal.addEventListener('click', (e) => {
  if (e.target.dataset.closeAd) closeAdModal();
});

async function uploadSelectedAdFile() {
  const file = el.adFile.files && el.adFile.files[0];
  if (!file) throw new Error('Выбери файл интеграции (MP4 или PNG/GIF)');
  const form = new FormData();
  form.append('file', file);
  form.append('name', el.adName.value.trim() || file.name);
  form.append('mode', el.adMode.value);
  form.append('position', el.adPosition.value);
  form.append('hook_sec', el.adHookSec.value || '3');
  form.append('start_sec', el.adStartSec.value || '0');
  form.append('duration_sec', el.adDurSec.value || '0');
  form.append('scale_pct', el.adScale.value || '75');
  const data = await api('/api/integrations', { method: 'POST', body: form });
  await refreshIntegrations();
  state.integrationId = data.integration.id;
  el.integrationSelect.value = state.integrationId;
  el.adFile.value = '';
  updateRunHint();
  return data.integration;
}

el.adSaveBtn.addEventListener('click', async () => {
  el.adSaveBtn.disabled = true;
  try {
    const ad = await uploadSelectedAdFile();
    toast(`Интеграция «${ad.name}» сохранена и выбрана`, 'ok');
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    el.adSaveBtn.disabled = false;
  }
});

async function applyIntegrationJob(adId) {
  if (!state.jobId || !state.clipList.length) return;
  const clipIds = state.selected.size
    ? Array.from(state.selected)
    : state.clipList.map((c) => String(c.id));
  const data = await apiJson('/api/integrations/apply', {
    job_id: state.jobId,
    integration_id: adId,
    clip_ids: clipIds,
  });
  closeAdModal();
  watchJob(data.job_id, 'интеграция');
  toast('Вшиваю интеграцию в готовые клипы…');
}

el.adApplyNowBtn.addEventListener('click', async () => {
  el.adApplyNowBtn.disabled = true;
  try {
    let adId = state.integrationId;
    if (el.adFile.files && el.adFile.files[0]) {
      const ad = await uploadSelectedAdFile();
      adId = ad.id;
    }
    if (!adId) throw new Error('Выбери файл или интеграцию из списка ниже');
    await applyIntegrationJob(adId);
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    el.adApplyNowBtn.disabled = false;
  }
});

el.adLibrary.addEventListener('click', async (e) => {
  const useBtn = e.target.closest('[data-use-ad]');
  if (useBtn) {
    state.integrationId = useBtn.dataset.useAd;
    el.integrationSelect.value = state.integrationId;
    updateRunHint();
    toast('Интеграция выбрана для нарезки', 'ok');
    closeAdModal();
    return;
  }
  const applyBtn = e.target.closest('[data-apply-ad]');
  if (applyBtn) {
    try {
      await applyIntegrationJob(applyBtn.dataset.applyAd);
    } catch (err) {
      toast(err.message, 'err');
    }
    return;
  }
  const delBtn = e.target.closest('[data-del-ad]');
  if (delBtn) {
    try {
      await api(`/api/integrations/${encodeURIComponent(delBtn.dataset.delAd)}`, { method: 'DELETE' });
      await refreshIntegrations();
      updateRunHint();
      toast('Удалено');
    } catch (err) {
      toast(err.message, 'err');
    }
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
    const data = await apiJson('/api/upload/batch', {
      job_id: state.jobId,
      clip_ids: Array.from(state.selected),
      platforms,
      dry_run: el.dryRun.checked,
      visibility: el.visibility.value,
      browser: { engine: state.engine, profile_id: el.profileSelect.value },
    });
    watchJob(data.job_id, 'загрузка');
    toast('Запустил пакетную загрузку — браузер откроется сам');
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
    const clipTag = item.clip_id ? `#${item.clip_id} · ` : '';
    return `<div class="upload-item ${ok ? 'is-ok' : 'is-err'}">
      <span class="upload-item__icon"><svg class="ico"><use href="#${ok ? 'i-check' : 'i-close'}"/></svg></span>
      <div class="upload-item__text">
        <strong>${esc(clipTag + item.platform)}</strong>
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
    const rows = (data.jobs || []).filter((job) => job.kind !== 'download_model' && job.kind !== 'login');
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

/* ---------------------------------------------------------------- аккаунты и настройки */

async function refreshAccounts() {
  try {
    const data = await api('/api/accounts');
    state.accounts = data.accounts || [];
  } catch {
    state.accounts = [];
  }
  renderAccountsList();
  renderAccountDropdown();
  renderChips(null);
}

function renderAccountsList() {
  if (!state.accounts.length) {
    el.accountsList.innerHTML = '<div class="empty"><p>Аккаунты не подключены. Войди в ChatGPT Plus/Pro или Google Gemini кнопками выше.</p></div>';
    return;
  }
  const activeId = state.settings?.llm?.account_id || '';
  el.accountsList.innerHTML = state.accounts.map((acc) => {
    const isActive = acc.id === activeId;
    const isOk = acc.status === 'ready';
    const provLabel = acc.provider === 'chatgpt' ? 'ChatGPT' : 'Google Gemini';
    const methodLabel = acc.auth_method === 'oauth' ? 'OAuth' : 'Браузер';
    const planLabel = acc.plan ? ` · ${acc.plan}` : '';
    return `<div class="acc-row${isActive ? ' is-active' : ''}">
      <div class="acc-row__info">
        <div class="acc-row__title">
          <span>${esc(acc.label)}</span>
          <span class="tag">${esc(provLabel)} · ${esc(methodLabel)}${esc(planLabel)}</span>
          <span class="tag ${isOk ? 'tag--ok' : ''}">${esc(acc.status)}</span>
        </div>
        <div class="acc-row__meta">${esc(acc.email || acc.profile_dir || acc.id)}${acc.error ? ' · ' + esc(acc.error) : ''}</div>
      </div>
      <div class="acc-row__btns">
        <button class="btn btn--soft btn--tiny" data-acc-use="${esc(acc.id)}" type="button">${isActive ? 'Активен' : 'Выбрать'}</button>
        <button class="btn btn--soft btn--tiny" data-acc-test="${esc(acc.id)}" type="button">Проверить</button>
        <button class="btn btn--ghost btn--tiny" data-acc-relogin="${esc(acc.id)}" data-acc-prov="${esc(acc.provider)}" data-acc-method="${esc(acc.auth_method)}" type="button">Перевойти</button>
        <button class="btn btn--ghost btn--tiny" data-acc-del="${esc(acc.id)}" type="button">Удалить</button>
      </div>
    </div>`;
  }).join('');
}

function renderAccountDropdown() {
  const prov = el.llmProvider.value;
  if (!isAccountProvider(prov)) {
    el.llmAccountRow.hidden = true;
    return;
  }
  el.llmAccountRow.hidden = false;
  const want = prov === 'chatgpt_sub' ? 'chatgpt' : 'google';
  const filtered = state.accounts.filter((a) => a.provider === want);
  if (!filtered.length) {
    el.llmAccount.innerHTML = '<option value="">Нет подключённых аккаунтов — войди выше</option>';
    return;
  }
  el.llmAccount.innerHTML = filtered
    .map((a) => `<option value="${esc(a.id)}">${esc(a.label)} (${a.auth_method})</option>`)
    .join('');
  const saved = state.settings?.llm?.account_id || '';
  if (filtered.some((a) => a.id === saved)) el.llmAccount.value = saved;
}

async function startAccountLogin(provider, authMethod, accountId = '') {
  try {
    const data = await apiJson('/api/accounts/login', {
      provider,
      auth_method: authMethod,
      account_id: accountId,
    });
    watchJob(data.job_id, 'вход');
    toast(authMethod === 'oauth'
      ? 'Открыл окно авторизации ChatGPT в браузере…'
      : 'Открыл окно браузера — войди в аккаунт, окно закроется само');
  } catch (err) {
    toast(err.message, 'err');
  }
}

el.loginChatgptOauth.addEventListener('click', () => startAccountLogin('chatgpt', 'oauth'));
el.loginChatgptBrowser.addEventListener('click', () => startAccountLogin('chatgpt', 'browser'));
el.loginGoogleBrowser.addEventListener('click', () => startAccountLogin('google', 'browser'));

el.accountsList.addEventListener('click', async (event) => {
  const useBtn = event.target.closest('[data-acc-use]');
  if (useBtn) {
    try {
      const data = await apiJson('/api/accounts/select', { account_id: useBtn.dataset.accUse, activate_provider: true });
      state.settings = data.settings;
      renderProviderSelect(state.settings.llm.provider);
      await refreshAccounts();
      toast(`Выбран аккаунт ${data.account.label}`, 'ok');
    } catch (err) {
      toast(err.message, 'err');
    }
    return;
  }
  const testBtn = event.target.closest('[data-acc-test]');
  if (testBtn) {
    testBtn.disabled = true;
    try {
      const data = await apiJson('/api/accounts/test', { account_id: testBtn.dataset.accTest, model: currentModelId() });
      toast(data.message, data.ok ? 'ok' : 'err');
    } catch (err) {
      toast(err.message, 'err');
    } finally {
      testBtn.disabled = false;
    }
    return;
  }
  const reloginBtn = event.target.closest('[data-acc-relogin]');
  if (reloginBtn) {
    startAccountLogin(reloginBtn.dataset.accProv, reloginBtn.dataset.accMethod, reloginBtn.dataset.accRelogin);
    return;
  }
  const delBtn = event.target.closest('[data-acc-del]');
  if (delBtn) {
    if (!confirm('Удалить аккаунт и локальный профиль?')) return;
    try {
      await api(`/api/accounts/${encodeURIComponent(delBtn.dataset.accDel)}`, { method: 'DELETE' });
      await refreshAccounts();
      toast('Аккаунт удалён');
    } catch (err) {
      toast(err.message, 'err');
    }
  }
});

function allProviders() {
  return [...state.accountProviders, ...state.providers];
}

function providerById(id) {
  return allProviders().find((provider) => provider.id === id) || null;
}

function renderProviderSelect(selectedId) {
  const accGroup = state.accountProviders.length
    ? `<optgroup label="Аккаунты с подпиской (без API-ключа)">${state.accountProviders.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('')}</optgroup>`
    : '';
  const apiGroup = `<optgroup label="API-провайдеры">${state.providers.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('')}</optgroup>`;
  el.llmProvider.innerHTML = accGroup + apiGroup;
  const fallback = selectedId || state.settings?.llm?.provider || 'deepseek';
  el.llmProvider.value = providerById(fallback) ? fallback : (state.providers[0]?.id || 'deepseek');
  renderModelSelect(state.settings?.llm?.model);
}

function renderModelSelect(selectedModel) {
  const provider = providerById(el.llmProvider.value);
  const isCustom = Boolean(provider && provider.id === 'custom');
  const isAcc = isAccountProvider(el.llmProvider.value);
  const models = provider ? provider.models : [];

  el.llmModel.hidden = isCustom;
  el.llmModelCustom.hidden = !isCustom;
  el.llmBaseRow.hidden = !isCustom;
  el.llmKeyRow.hidden = isAcc;
  el.llmTestAccountRow.hidden = !isAcc;
  renderAccountDropdown();

  el.llmModel.innerHTML = models
    .map((model) => {
      const free = model.free ? ' · бесплатно' : '';
      return `<option value="${esc(model.id)}">${esc(model.label || model.id)}${esc(free)}</option>`;
    })
    .join('');

  const saved = state.settings?.llm || {};
  if (isCustom) {
    const prevCustom = saved.provider === 'custom';
    el.llmModelCustom.value = (prevCustom && saved.model) || selectedModel || 'local-model';
    el.llmBaseUrl.value = (prevCustom && saved.base_url) || 'http://127.0.0.1:1234/v1';
    el.llmHint.textContent = 'Свой сервер: модель пиши вручную, ключ не обязателен. Сервер должен отвечать на /chat/completions.';
    return;
  }

  const wanted = selectedModel && models.some((model) => model.id === selectedModel)
    ? selectedModel
    : (models[0]?.id || '');
  if (wanted) el.llmModel.value = wanted;
  el.llmHint.textContent = provider && provider.key_url
    ? `Ключ: ${provider.key_url}. Хранится локально в app/data/settings.json`
    : 'Ключ хранится локально в app/data/settings.json';
}

function currentProviderIsCustom() {
  const provider = providerById(el.llmProvider.value);
  return Boolean(provider && provider.id === 'custom');
}

function currentModelId() {
  return currentProviderIsCustom()
    ? (el.llmModelCustom.value.trim() || 'local-model')
    : el.llmModel.value;
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
      model: currentModelId(),
      api_key: key,
      base_url: currentProviderIsCustom() ? el.llmBaseUrl.value.trim() : '',
    });
    toast(data.message || (data.ok ? 'работает' : 'не работает'), data.ok ? 'ok' : 'err');
    setStatus(data.ok ? 'ok' : 'err', data.ok ? 'ключ рабочий' : 'ключ не работает');
  } catch (error) {
    toast(error.message, 'err');
  } finally {
    el.testKey.disabled = false;
  }
});

el.testAccountBtn.addEventListener('click', async () => {
  const accId = el.llmAccount.value;
  if (!accId) {
    toast('Сначала войди в аккаунт кнопками выше', 'err');
    return;
  }
  el.testAccountBtn.disabled = true;
  try {
    const data = await apiJson('/api/accounts/test', { account_id: accId, model: currentModelId() });
    toast(data.message, data.ok ? 'ok' : 'err');
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    el.testAccountBtn.disabled = false;
  }
});

async function refreshModels() {
  try {
    const data = await api('/api/models');
    const installed = new Map((data.installed || []).map((item) => [item.size, item]));
    if (state.health) {
      state.health.models = (data.installed || []).filter((item) => item.complete);
    }
    renderSetup();

    const human = (bytes) => {
      const mb = Number(bytes || 0) / (1024 * 1024);
      return mb >= 1024 ? `${(mb / 1024).toFixed(2)} ГБ` : `${mb.toFixed(0)} МБ`;
    };

    el.models.innerHTML = (data.sizes || []).map((size) => {
      const item = installed.get(size.id);
      let stateHtml = `<button class="btn btn--soft btn--tiny" data-model="${esc(size.id)}">Скачать</button>`;
      if (item && item.complete) {
        stateHtml = `<span class="tag tag--ok">на диске · ${human(item.bytes)}</span>
          <button class="btn btn--ghost btn--tiny" data-delete-model="${esc(size.id)}" title="Удалить модель">Удалить</button>`;
      } else if (item) {
        stateHtml = `<span class="tag">недокачана · ${human(item.bytes)}</span>
          <button class="btn btn--soft btn--tiny" data-model="${esc(size.id)}">Докачать</button>`;
      }
      return `<div class="model-row">
        <div>
          <div class="model-row__name">${esc(size.label)}</div>
          <div class="model-row__note">${human(size.bytes)} · ${esc(size.note)}</div>
        </div>
        <div class="model-row__state">${stateHtml}</div>
      </div>`;
    }).join('');
  } catch (error) {
    el.models.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
  }
}

el.models.addEventListener('click', async (event) => {
  const removeButton = event.target.closest('[data-delete-model]');
  if (removeButton) {
    const size = removeButton.dataset.deleteModel;
    if (!confirm(`Удалить модель ${size}? Придётся качать заново.`)) return;
    removeButton.disabled = true;
    try {
      const data = await apiJson('/api/models/delete', { size });
      if (data.ok) {
        toast(`Модель ${size} удалена`);
        await refreshModels();
      } else {
        toast('не нашёл модель на диске', 'err');
      }
    } catch (error) {
      toast(error.message, 'err');
    }
    return;
  }

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
    const isCustom = currentProviderIsCustom();
    const llmPatch = {
      provider: el.llmProvider.value,
      model: currentModelId(),
      fallback: el.llmFallback.checked,
    };
    if (isAccountProvider(el.llmProvider.value)) {
      llmPatch.account_id = el.llmAccount.value || '';
    }
    if (isCustom) {
      llmPatch.base_url = el.llmBaseUrl.value.trim();
    }
    if (state.keyDirty && el.llmKey.value.trim()) {
      llmPatch.api_key = el.llmKey.value.trim();
    }
    state.encoder = el.encoderSelect.value;
    const patch = {
      llm: llmPatch,
      stt: { language: el.language.value },
      render: { encoder: state.encoder },
      network: {
        proxy: el.netProxy.value.trim(),
        force_ipv4: el.netIpv4.checked,
        youtube_cookies: el.netCookies.checked ? 'auto' : 'none',
      },
      paths: { outdir: el.outdir.value.trim() },
    };
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
    updateOutdir();
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
    state.health = health;
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
    state.accountProviders = providers.account_providers || [];
  } catch {
    state.providers = [];
    state.accountProviders = [];
  }

  try {
    const accData = await api('/api/accounts');
    state.accounts = accData.accounts || [];
  } catch {
    state.accounts = [];
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

    const net = state.settings.network || {};
    el.netProxy.value = net.proxy || '';
    el.netIpv4.checked = net.force_ipv4 !== false;
    el.netCookies.checked = net.youtube_cookies !== 'none';

    const render = state.settings.render || {};
    state.mode = render.mode || 'moments';
    state.clipCount = Number(render.clips ?? 5);
    state.style = render.style || 'classic';
    state.framing = render.framing || 'auto';
    state.subtitles = render.subtitles !== false;
    state.encoder = render.encoder || 'auto';
    state.effect = render.effect || 'none';
    state.protection = render.protection || 'off';
    state.music = render.music || 'none';
    state.musicVolume = Math.round((Number(render.music_volume) || 0.18) * 100);
    state.voice = render.voice || 'dmitry_drama';
    state.origAudioVol = Math.round((Number(render.orig_audio_vol) ?? 0.08) * 100);
    state.brainrotRewrite = render.brainrot_rewrite !== false;
    state.integrationId = render.integration_id || '';
    state.dur = [Number(render.min_dur) || 18, Number(render.max_dur) || 60];

    el.subtitlesToggle.checked = state.subtitles;
    el.encoderSelect.value = state.encoder;
    el.origVol.value = String(state.origAudioVol);
    el.origVolLabel.textContent = `${state.origAudioVol}%`;
    el.brainrotRewrite.checked = state.brainrotRewrite;

    syncClipButtons();
    syncDurationUI();
    $$('button[data-style]', el.styleSeg).forEach((b) => b.classList.toggle('is-active', b.dataset.style === state.style));
    $$('button[data-framing]', el.framingSeg).forEach((b) => b.classList.toggle('is-active', b.dataset.framing === state.framing));
    $$('button[data-protect]', el.protectSeg).forEach((b) => b.classList.toggle('is-active', b.dataset.protect === state.protection));

    renderAccountsList();
    renderAccountDropdown();
    renderChips(null);
  } catch (error) {
    toast(error.message, 'err');
  }

  try {
    state.looks = await api('/api/looks');
  } catch {
    state.looks = { effects: [], music: [], voices: [], encoder: 'cpu' };
  }
  renderLooks();
  syncModeUI();
  await refreshIntegrations();
  updateRunHint();
  updateOutdir();

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
