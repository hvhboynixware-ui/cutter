(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    tab: 'studio',
    sourceMode: 'file',
    selectedFile: null,
    mode: 'moments',
    framing: 'auto',
    style: 'classic',
    effect: 'none',
    partSec: 45,
    health: null,
    settings: null,
    looks: { effects: [], music: [], voices: [], encoder: 'cpu' },
    providers: [],
    accountProviders: [],
    accounts: [],
    integrations: [],
    jobs: [],
    activeJobId: '',
    activeAuthJobId: '',
    activeUploadJobId: '',
    currentJobClips: [],
    currentClipsJobId: '',
    selectedClipIds: new Set(),
    playingClipId: '',
    editingClip: null,
    pollTimer: null,
  };

  function esc(val) {
    return String(val ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function toast(msg, kind = 'info') {
    const stack = $('#toast-stack');
    const el = document.createElement('div');
    el.className = `toast ${kind === 'err' ? 'is-err' : kind === 'ok' ? 'is-ok' : ''}`;
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!res.ok) throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    return data;
  }

  function fmtSec(sec) {
    const s = Math.max(0, Math.round(Number(sec || 0)));
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  function fmtBytes(bytes) {
    if (!bytes) return '';
    if (bytes > 1e9) return `${(bytes / 1e9).toFixed(2)} ГБ`;
    if (bytes > 1e6) return `${(bytes / 1e6).toFixed(1)} МБ`;
    return `${Math.round(bytes / 1e3)} КБ`;
  }

  // ------------------------------------------------------------------ navigation

  function switchTab(tab) {
    state.tab = tab;
    $$('.tb-tab').forEach((b) => b.classList.toggle('is-active', b.dataset.tab === tab));
    $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `tab-${tab}`));
    if (tab === 'history') loadJobs();
    if (tab === 'upload') syncUploadPanel();
    if (tab === 'settings') loadSettingsBundle();
  }

  function switchInsp(pane) {
    $$('.insp-tab').forEach((b) => b.classList.toggle('is-active', b.dataset.insp === pane));
    $$('.insp-pane').forEach((p) => p.classList.toggle('is-active', p.id === `insp-${pane}`));
  }

  function switchBin(pane) {
    $$('.bin-tab').forEach((b) => b.classList.toggle('is-active', b.dataset.bin === pane));
    $$('.bin-pane').forEach((p) => p.classList.toggle('is-active', p.id === `bin-${pane}`));
  }

  function setStageView(view) {
    const isPlayer = view === 'player';
    $$('[data-stage-view]').forEach((b) => b.classList.toggle('is-active', b.dataset.stageView === view));
    $('#preview-phone').hidden = isPlayer;
    $('#stage-player-wrap').hidden = !isPlayer;
    $('#clip-editor-strip').hidden = !isPlayer || !state.editingClip;
    if (!isPlayer) $('#stage-video').pause();
  }

  // ------------------------------------------------------------------ stage preview

  const SAMPLE_SUBS = {
    moments: 'ОКАЗАЛОСЬ ЧТО ЭТО БЫЛА ЛОВУШКА',
    parts: 'ЧАСТЬ 2 · ПОВОРОТНЫЙ МОМЕНТ',
    brainrot: 'ОН СРАЗУ ПРЕДУПРЕДИЛ ВСЮ ДЕРЕВНЮ',
  };

  function updateStagePreview() {
    const phone = $('#preview-phone');
    if (!phone) return;
    phone.dataset.framing = state.framing;
    phone.dataset.effect = state.effect;
    phone.dataset.style = state.style;

    $('#preview-sub-box').hidden = !$('#opt-subtitles').checked;
    $('#preview-sub-text').textContent = SAMPLE_SUBS[state.mode] || SAMPLE_SUBS.moments;

    const badgeMap = { moments: 'МОМЕНТ #1', parts: `ЧАСТЬ 1 · ${state.partSec}с`, brainrot: 'ДОРАМА · TTS' };
    $('#preview-mode-badge').textContent = badgeMap[state.mode] || 'КЛИП';

    const fxObj = (state.looks.effects || []).find((e) => e.id === state.effect);
    $('#preview-fx-label').textContent = `FX: ${fxObj ? fxObj.label : state.effect}`;

    const musicId = $('#opt-music').value;
    const musicObj = (state.looks.music || []).find((m) => m.id === musicId);
    $('#preview-music-label').textContent = musicId && musicId !== 'none' ? `♪ ${musicObj ? musicObj.title : musicId}` : '♪ без музыки';

    const intId = $('#opt-integration').value;
    const intObj = state.integrations.find((x) => x.id === intId);
    const isBanner = intObj && intObj.mode === 'overlay';
    $('#preview-ad-pill').hidden = !intObj || isBanner;
    $('#preview-ad-bottom').hidden = !intObj || !isBanner;
    if (intObj) {
      $('#preview-ad-pill').textContent = `ВРЕЗКА · ${intObj.name}`;
      $('#preview-ad-bottom').textContent = `БАННЕР · ${intObj.name}`;
    }

    const modeNames = { moments: 'Моменты', parts: `По частям (${state.partSec}с)`, brainrot: 'Озвучка дорамы' };
    const frameNames = { auto: 'Авто', blur: 'Размытие', crop: 'Кроп', black: 'Чёрный', original: 'Оригинал' };
    const styleNames = { classic: 'Классика', bold: 'Жирный', clean: 'Чистый', neon: 'Неон', drama: 'Дорама' };
    $('#action-summary').textContent = `${modeNames[state.mode]} · ${frameNames[state.framing]} 9:16 · ${styleNames[state.style]}`;
    $('#run-btn-label').textContent = state.sourceMode === 'youtube' ? 'Скачать и собрать' : 'Собрать клипы';
  }

  function setMode(mode) {
    state.mode = mode;
    $$('#mode-stack .mode-item').forEach((c) => c.classList.toggle('is-active', c.dataset.mode === mode));
    $('#opt-moments').hidden = mode !== 'moments';
    $('#opt-parts').hidden = mode !== 'parts';
    $('#opt-brainrot').hidden = mode !== 'brainrot';
    updateStagePreview();
  }

  function setFraming(framing) {
    state.framing = framing;
    $$('#framing-pills .chip').forEach((b) => b.classList.toggle('is-active', b.dataset.framing === framing));
    updateStagePreview();
  }

  function setStyle(style) {
    state.style = style;
    $$('#style-pills .chip').forEach((b) => b.classList.toggle('is-active', b.dataset.style === style));
    updateStagePreview();
  }

  function setEffect(effect) {
    state.effect = effect;
    $$('#effect-pills .chip').forEach((b) => b.classList.toggle('is-active', b.dataset.effect === effect));
    updateStagePreview();
  }

  function setPartSeconds(sec) {
    state.partSec = Math.max(10, Math.min(180, Number(sec) || 45));
    $('#part-sec-label').textContent = `${state.partSec}с`;
    $('#part-sec-range').value = String(state.partSec);
    $$('#part-presets .chip').forEach((b) => b.classList.toggle('is-active', Number(b.dataset.sec) === state.partSec));
    updateStagePreview();
  }

  // ------------------------------------------------------------------ initial data

  async function loadHealth() {
    try {
      const h = await api('/api/health');
      state.health = h;
      $('#brand-ver').textContent = `v${h.version || '1.1.0'}`;
      $('#tel-whisper').textContent = `Речь: ${h.whisper_size || 'base'}`;
      $('#tel-whisper').className = `st-pill ${(h.models || []).length ? 'is-ok' : ''}`;
      $('#tel-encoder').textContent = `Рендер: ${(h.encoder || 'cpu').toUpperCase()}`;
      $('#tel-encoder').className = `st-pill ${h.encoder && h.encoder !== 'cpu' ? 'is-ok' : ''}`;
      $('#tel-browser').textContent = h.browser ? 'Браузер: ОК' : 'Браузер: нет';
      $('#tel-browser').className = `st-pill ${h.browser ? 'is-ok' : 'is-warn'}`;
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  function updateLlmTelemetry() {
    const s = state.settings;
    if (!s) return;
    const llmCfg = s.llm || {};
    const acc = state.accounts.find((a) => a.id === llmCfg.account_id);
    const chip = $('#tel-llm');
    if (acc) {
      chip.textContent = `ИИ: ${acc.label}`;
      chip.className = 'st-pill is-ok';
    } else if (llmCfg.api_key_masked || llmCfg.api_key) {
      chip.textContent = `ИИ: ${llmCfg.provider || 'api'}`;
      chip.className = 'st-pill is-ok';
    } else {
      chip.textContent = 'ИИ: нужен вход';
      chip.className = 'st-pill is-warn';
    }
  }

  async function loadLooksAndSettings() {
    const [looks, settings, providersData, accData, intData] = await Promise.all([
      api('/api/looks'),
      api('/api/settings'),
      api('/api/providers'),
      api('/api/accounts'),
      api('/api/integrations'),
    ]);

    state.looks = looks;
    state.settings = settings;
    state.providers = providersData.providers || [];
    state.accountProviders = providersData.account_providers || [];
    state.accounts = accData.accounts || [];
    state.integrations = intData.integrations || [];

    const fxWrap = $('#effect-pills');
    fxWrap.innerHTML = '';
    (looks.effects || []).forEach((fx) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.dataset.effect = fx.id;
      b.title = fx.hint || '';
      b.textContent = fx.label;
      b.addEventListener('click', () => setEffect(fx.id));
      fxWrap.appendChild(b);
    });

    const musSel = $('#opt-music');
    musSel.innerHTML = '<option value="none">Без музыки</option>';
    (looks.music || []).forEach((m) => musSel.appendChild(new Option(`${m.title}${m.mood ? ` — ${m.mood}` : ''}`, m.id)));

    const voiceSel = $('#opt-voice');
    voiceSel.innerHTML = '';
    (looks.voices || []).forEach((v) => voiceSel.appendChild(new Option(v.label || v.id, v.id)));
    updateVoiceNote();

    populateIntegrations();
    hydrateStudioFromSettings(settings);
    hydrateSettingsForm();
    renderAccounts();
    updateLlmTelemetry();
    updateStagePreview();
  }

  function updateVoiceNote() {
    const v = (state.looks.voices || []).find((x) => x.id === $('#opt-voice').value);
    if (v && v.note) $('#voice-note-hint').textContent = v.note;
  }

  function populateIntegrations() {
    const items = state.integrations || [];
    const sSel = $('#opt-integration');
    const dSel = $('#deck-int-select');
    const list = $('#saved-int-list');
    const prev = sSel.value;

    sSel.innerHTML = '<option value="">Без интеграции</option>';
    dSel.innerHTML = '<option value="">Реклама…</option>';
    list.innerHTML = items.length ? '' : '<div class="muted-xs">Нет сохранённых материалов</div>';

    items.forEach((it) => {
      const lbl = `${it.name} (${it.mode === 'overlay' ? 'баннер' : 'врезка'})`;
      sSel.appendChild(new Option(lbl, it.id));
      dSel.appendChild(new Option(lbl, it.id));

      const row = document.createElement('div');
      row.className = 'model-row';
      row.innerHTML = `<div><strong>${esc(it.name)}</strong><div class="muted-xs">${esc(it.mode)} · ${esc(it.position)}</div></div><button class="btn-danger" type="button">✕</button>`;
      row.querySelector('button').addEventListener('click', async () => {
        await api(`/api/integrations/${encodeURIComponent(it.id)}`, { method: 'DELETE' });
        state.integrations = (await api('/api/integrations')).integrations || [];
        populateIntegrations();
        updateStagePreview();
        toast('Интеграция удалена', 'ok');
      });
      list.appendChild(row);
    });
    if (prev && items.some((x) => x.id === prev)) sSel.value = prev;
  }

  function hydrateStudioFromSettings(s) {
    const r = s.render || {};
    setMode(r.mode || 'moments');
    setFraming(r.framing || 'auto');
    setStyle(r.style || 'classic');
    setEffect(r.effect || 'none');
    $('#opt-clips').value = String(r.clips ?? 5);
    $('#parts-max-clips').value = String(r.clips ?? 8);
    $('#opt-min-dur').value = String(r.min_dur ?? 18);
    $('#opt-max-dur').value = String(r.max_dur ?? 60);
    setPartSeconds(r.min_dur || 45);
    $('#opt-subtitles').checked = r.subtitles !== false;
    $('#opt-protection').value = r.protection || 'off';
    if (r.music) $('#opt-music').value = r.music;
    const mv = Math.round(Number(r.music_volume ?? 0.18) * 100);
    $('#opt-music-vol').value = String(mv);
    $('#music-vol-label').textContent = `${mv}%`;
    if (r.voice && $$('#opt-voice option').some((o) => o.value === r.voice)) $('#opt-voice').value = r.voice;
    updateVoiceNote();
    const ov = Math.round(Number(r.orig_audio_vol ?? 0.08) * 100);
    $('#opt-orig-vol').value = String(ov);
    $('#orig-vol-label').textContent = `${ov}%`;
    $('#opt-brainrot-rewrite').checked = r.brainrot_rewrite !== false;
    if (r.integration_id) $('#opt-integration').value = r.integration_id;
  }

  // ------------------------------------------------------------------ run job

  function collectRenderPayload() {
    const isParts = state.mode === 'parts';
    return {
      mode: state.mode,
      clips: isParts ? Number($('#parts-max-clips').value || 8) : Number($('#opt-clips').value || 5),
      style: state.style,
      framing: state.framing,
      subtitles: $('#opt-subtitles').checked,
      encoder: $('#set-encoder').value || 'auto',
      effect: state.effect,
      protection: $('#opt-protection').value || 'off',
      music: $('#opt-music').value || 'none',
      music_volume: Number($('#opt-music-vol').value || 18) / 100,
      voice: $('#opt-voice').value || 'canon_bright',
      orig_audio_vol: Number($('#opt-orig-vol').value || 8) / 100,
      brainrot_rewrite: $('#opt-brainrot-rewrite').checked,
      integration_id: $('#opt-integration').value || '',
      min_dur: isParts ? state.partSec : Number($('#opt-min-dur').value || 18),
      max_dur: isParts ? state.partSec : Number($('#opt-max-dur').value || 60),
    };
  }

  async function startStudioJob() {
    const p = collectRenderPayload();
    try {
      $('#run-btn').disabled = true;
      let res;
      if (state.sourceMode === 'youtube') {
        const url = $('#yt-url').value.trim();
        if (!url) throw new Error('Вставьте ссылку на YouTube');
        res = await api('/api/youtube', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, ...p }),
        });
      } else {
        if (!state.selectedFile) throw new Error('Выберите видеофайл слева');
        const fd = new FormData();
        fd.append('file', state.selectedFile);
        Object.entries(p).forEach(([k, v]) => fd.append(k, String(v)));
        res = await api('/api/analyze', { method: 'POST', body: fd });
      }
      state.activeJobId = res.job_id;
      $('#cancel-job-btn').hidden = false;
      $('#open-folder-btn').hidden = true;
      switchBin('logs');
      toast('Задача запущена', 'ok');
      await pollAll();
    } catch (e) {
      toast(e.message, 'err');
      $('#run-btn').disabled = false;
    }
  }

  function stageIndexFromJob(job) {
    const st = `${job.step || ''} ${job.message || ''}`.toLowerCase();
    const pct = Number(job.progress || 0);
    if (job.status === 'done' || pct >= 100) return 4;
    if (st.includes('рендер') || st.includes('клип') || st.includes('субтитр') || pct >= 55) return 3;
    if (st.includes('подбор') || st.includes('сценари') || st.includes('озвуч') || st.includes('част') || pct >= 35) return 2;
    if (st.includes('речь') || st.includes('whisper') || pct >= 18) return 1;
    return 0;
  }

  function renderStudioJob(job) {
    if (!job) return;
    const pct = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0))));
    $('#job-pct-badge').textContent = `${pct}%`;
    $('#job-step-name').textContent = job.step || job.kind || 'Задача';
    $('#job-step-msg').textContent = job.message || job.error || 'Выполняется…';
    $('#job-progress-fill').style.width = `${pct}%`;
    const elapsed = job.finished_at ? (job.finished_at - job.created_at) : (Date.now() / 1000 - (job.created_at || Date.now() / 1000));
    $('#job-elapsed').textContent = fmtSec(elapsed);

    const activeIdx = stageIndexFromJob(job);
    $$('#pipeline-stepper span').forEach((sp, i) => {
      sp.classList.toggle('is-done', job.status === 'done' ? true : i < activeIdx);
      sp.classList.toggle('is-active', job.status === 'running' && i === activeIdx);
    });

    const term = $('#job-terminal');
    term.textContent = (job.logs || []).join('\n') || job.error || job.message || '';
    term.scrollTop = term.scrollHeight;

    const busy = job.status === 'running' || job.status === 'queued';
    $('#run-btn').disabled = busy;
    $('#cancel-job-btn').hidden = !busy;
    $('#open-folder-btn').hidden = job.status !== 'done';

    if (job.status === 'done' && job.result && Array.isArray(job.result.clips)) {
      if (state.currentClipsJobId !== job.id || state.currentJobClips.length !== job.result.clips.length) {
        setClipDeck(job.id, job.result.clips, job.result.source || '');
        switchBin('clips');
        if (job.result.clips[0]) loadClipIntoStage(job.result.clips[0]);
      }
    }
  }

  function renderAuthJob(job) {
    const box = $('#auth-console');
    if (!job) { box.hidden = true; return; }
    box.hidden = false;
    $('#auth-progress-fill').style.width = `${Math.max(5, Math.min(100, Math.round(Number(job.progress || 0))))}%`;
    $('#auth-console-title').textContent = job.status === 'done' ? 'Аккаунт подключён' : job.status === 'error' ? 'Ошибка входа' : 'Авторизация…';
    $('#auth-console-msg').textContent = job.error || job.message || job.step || '';
    const logs = job.logs || [];
    const term = $('#auth-terminal');
    term.textContent = logs.join('\n') || (job.error || '');
    term.scrollTop = term.scrollHeight;
    $('#auth-cancel-btn').hidden = !(job.status === 'running' || job.status === 'queued');

    const urlMatch = logs.join('\n').match(/https:\/\/[^\s]+/);
    if (urlMatch) {
      $('#auth-url-open').href = urlMatch[0];
      $('#auth-url-open').hidden = false;
      $('#auth-url-copy').hidden = false;
      $('#auth-url-copy').dataset.url = urlMatch[0];
    } else {
      $('#auth-url-open').hidden = true;
      $('#auth-url-copy').hidden = true;
    }

    if (job.status === 'done' && !job._doneNotified) {
      job._doneNotified = true;
      loadSettingsBundle();
      loadHealth();
      toast('Аккаунт подключён', 'ok');
    } else if (job.status === 'error' && !job._errNotified) {
      job._errNotified = true;
      toast(`Ошибка входа: ${job.error || 'см. лог'}`, 'err');
    }
  }

  function renderUploadJob(job) {
    if (!job) return;
    const pct = Math.round(Number(job.progress || 0));
    $('#up-status-badge').textContent = job.status === 'done' ? 'Готово' : job.status === 'error' ? 'Ошибка' : `${pct}%`;
    $('#up-progress-label').textContent = `${pct}%`;
    const term = $('#up-terminal');
    term.textContent = (job.logs || []).join('\n') || job.error || job.message || '';
    term.scrollTop = term.scrollHeight;
  }

  async function pollAll() {
    try {
      const { jobs } = await api('/api/jobs?limit=30');
      state.jobs = jobs || [];

      const running = state.jobs.find((j) => j.status === 'running' || j.status === 'queued');
      const gpill = $('#global-job-pill');
      if (running) {
        gpill.hidden = false;
        $('#global-job-text').textContent = running.step || running.kind;
        $('#global-job-pct').textContent = `${Math.round(Number(running.progress || 0))}%`;
        gpill.onclick = () => switchTab(running.kind === 'login' || running.kind === 'download_model' ? 'settings' : running.kind === 'upload' ? 'upload' : 'studio');
      } else {
        gpill.hidden = true;
      }

      if (state.activeJobId) {
        renderStudioJob(await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}`));
      } else {
        const latest = state.jobs.find((j) => (j.kind === 'analyze' || j.kind === 'youtube') && j.status === 'done');
        if (latest && !state.currentClipsJobId) {
          renderStudioJob(await api(`/api/jobs/${encodeURIComponent(latest.id)}`));
        }
      }

      if (state.activeAuthJobId) {
        const ad = await api(`/api/jobs/${encodeURIComponent(state.activeAuthJobId)}`);
        renderAuthJob(ad);
        if (ad.status === 'done') await refreshAccountsOnly();
      }

      if (state.activeUploadJobId) {
        renderUploadJob(await api(`/api/jobs/${encodeURIComponent(state.activeUploadJobId)}`));
      }

      if (state.tab === 'settings' && state.jobs.some((j) => j.kind === 'download_model' && j.status === 'running')) {
        await loadModels();
      }
      if (state.tab === 'history') renderHistory();
    } catch {
      // ignore transient poll error
    }
  }

  // ------------------------------------------------------------------ clip bin & stage player

  function setClipDeck(jobId, clips, source = '') {
    state.currentClipsJobId = jobId;
    state.currentJobClips = clips || [];
    state.selectedClipIds = new Set(state.currentJobClips.map((c) => String(c.id)));
    $('#deck-count').textContent = String(state.currentJobClips.length);
    $('#deck-source').textContent = source ? `Источник: ${source}` : `Задача #${jobId}`;
    renderClipDeck();
    syncUploadPanel();
  }

  function loadClipIntoStage(clip) {
    state.editingClip = clip;
    state.playingClipId = String(clip.id);
    $('#stage-video').src = `${clip.video_url}?t=${Date.now()}`;
    $('#ce-title').value = clip.title || '';
    $('#ce-hook').value = clip.hook || '';
    $('#ce-tags').value = clip.hashtags || '';
    $('#ce-start').value = String(clip.start ?? 0);
    $('#ce-end').value = String(clip.end ?? clip.duration ?? 30);
    $('#stage-telemetry').textContent = `${clip.title || `Клип #${clip.id}`} · ${fmtSec(clip.duration)}`;
    setStageView('player');
    renderClipDeck();
  }

  function renderClipDeck() {
    const grid = $('#clips-grid');
    $('#deck-selected-count').textContent = String(state.selectedClipIds.size);
    $('#nav-upload-count').textContent = String(state.selectedClipIds.size);
    $('#nav-upload-count').hidden = state.selectedClipIds.size === 0;

    if (!state.currentJobClips.length) {
      grid.innerHTML = '<div class="empty-box">Запустите нарезку слева — клипы сразу загрузятся сюда.</div>';
      return;
    }

    grid.innerHTML = '';
    state.currentJobClips.forEach((clip) => {
      const cid = String(clip.id);
      const checked = state.selectedClipIds.has(cid);
      const playing = state.playingClipId === cid;
      const card = document.createElement('div');
      card.className = `bin-card ${checked ? 'is-selected' : ''} ${playing ? 'is-playing' : ''}`;
      card.innerHTML = `
        <div class="bin-thumb">
          <img src="${esc(clip.preview_url)}?t=${Date.now()}" alt="">
          <input class="bin-check" type="checkbox" ${checked ? 'checked' : ''}>
        </div>
        <div class="bin-meta">
          <div class="bin-title">${esc(clip.title || `Клип #${cid}`)}</div>
          <div class="bin-sub">${esc(clip.hook || '')}</div>
          <div class="bin-foot">
            <span class="mono muted-xs">${esc(fmtSec(clip.duration))}</span>
            <button class="btn-ghost-xs bin-pub" type="button">Загрузить</button>
          </div>
        </div>
      `;
      card.querySelector('.bin-check').addEventListener('click', (e) => {
        e.stopPropagation();
        if (e.target.checked) state.selectedClipIds.add(cid);
        else state.selectedClipIds.delete(cid);
        card.classList.toggle('is-selected', e.target.checked);
        $('#deck-selected-count').textContent = String(state.selectedClipIds.size);
        $('#nav-upload-count').textContent = String(state.selectedClipIds.size);
        $('#nav-upload-count').hidden = state.selectedClipIds.size === 0;
      });
      card.querySelector('.bin-pub').addEventListener('click', (e) => {
        e.stopPropagation();
        state.selectedClipIds = new Set([cid]);
        renderClipDeck();
        switchTab('upload');
      });
      card.addEventListener('click', () => loadClipIntoStage(clip));
      grid.appendChild(card);
    });
  }

  async function saveClipMetadata() {
    if (!state.editingClip || !state.currentClipsJobId) return;
    const cid = state.editingClip.id;
    try {
      await api(`/api/clips/${encodeURIComponent(state.currentClipsJobId)}/${encodeURIComponent(cid)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: $('#ce-title').value.trim(),
          hook: $('#ce-hook').value.trim(),
          hashtags: $('#ce-tags').value.trim(),
        }),
      });
      const fresh = await api(`/api/clips?job_id=${encodeURIComponent(state.currentClipsJobId)}`);
      setClipDeck(state.currentClipsJobId, fresh.clips || [], fresh.source || '');
      toast('Сохранено', 'ok');
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  async function recutClip() {
    if (!state.editingClip || !state.currentClipsJobId) return;
    const cid = state.editingClip.id;
    try {
      $('#ce-recut-btn').disabled = true;
      const res = await api(`/api/clips/${encodeURIComponent(state.currentClipsJobId)}/${encodeURIComponent(cid)}/recut`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: Number($('#ce-start').value), end: Number($('#ce-end').value) }),
      });
      if (res.clip) loadClipIntoStage(res.clip);
      const fresh = await api(`/api/clips?job_id=${encodeURIComponent(state.currentClipsJobId)}`);
      setClipDeck(state.currentClipsJobId, fresh.clips || [], fresh.source || '');
      toast('Клип перерезан', 'ok');
    } catch (e) {
      toast(e.message, 'err');
    } finally {
      $('#ce-recut-btn').disabled = false;
    }
  }

  // ------------------------------------------------------------------ upload & history

  async function syncUploadPanel() {
    const { jobs } = await api('/api/jobs?limit=30');
    const doneJobs = (jobs || []).filter((j) => (j.kind === 'analyze' || j.kind === 'youtube') && j.status === 'done');
    const sel = $('#up-job-select');
    const curVal = state.currentClipsJobId || sel.value;
    sel.innerHTML = '';
    if (!doneJobs.length) {
      sel.innerHTML = '<option value="">Нет завершённых нарезок</option>';
      $('#up-clip-list').innerHTML = '<div class="empty-box">Сначала соберите клипы в Студии.</div>';
      $('#up-sel-count').textContent = '0';
      return;
    }
    doneJobs.forEach((j) => {
      const cnt = j.result && j.result.clips ? j.result.clips.length : '?';
      sel.appendChild(new Option(`${(j.meta && (j.meta.source || j.meta.url)) || j.id} (${cnt} кл.)`, j.id));
    });
    if (curVal && doneJobs.some((j) => j.id === curVal)) sel.value = curVal;
    await loadUploadJobClips(sel.value);
  }

  async function loadUploadJobClips(jobId) {
    if (!jobId) return;
    const data = await api(`/api/clips?job_id=${encodeURIComponent(jobId)}`);
    const clips = data.clips || [];
    const list = $('#up-clip-list');
    list.innerHTML = clips.length ? '' : '<div class="empty-box">Нет клипов</div>';
    clips.forEach((c) => {
      const cid = String(c.id);
      const checked = state.currentClipsJobId === jobId ? state.selectedClipIds.has(cid) : true;
      const row = document.createElement('label');
      row.className = 'check-tile';
      row.innerHTML = `<input type="checkbox" value="${esc(cid)}" ${checked ? 'checked' : ''}><span><strong>${esc(c.title || `Клип #${cid}`)}</strong> <span class="muted-xs">(${esc(fmtSec(c.duration))})</span></span>`;
      row.querySelector('input').addEventListener('change', updateUploadCount);
      list.appendChild(row);
    });
    updateUploadCount();
  }

  function updateUploadCount() {
    $('#up-sel-count').textContent = String($$('#up-clip-list input:checked').length);
  }

  async function refreshBrowserProfiles() {
    const engine = $('#up-engine').value;
    const row = $('#up-profile-row');
    if (engine === 'plain') { row.hidden = true; return; }
    row.hidden = false;
    const sel = $('#up-profile-id');
    sel.innerHTML = '<option value="">Загрузка…</option>';
    const data = await api(`/api/browsers?engine=${encodeURIComponent(engine)}`);
    sel.innerHTML = '';
    if (!data.running) { sel.innerHTML = `<option value="">${esc(data.error || 'Не запущен')}</option>`; return; }
    (data.profiles || []).forEach((p) => sel.appendChild(new Option(`${p.name} (${p.status})`, p.id)));
  }

  async function runBatchUpload(dryRun) {
    const jobId = $('#up-job-select').value;
    const clipIds = $$('#up-clip-list input:checked').map((i) => i.value);
    const platforms = $$('#up-platforms input:checked').map((i) => i.value);
    if (!jobId || !clipIds.length) return toast('Выберите задачу и клипы', 'err');
    if (!platforms.length) return toast('Выберите площадки', 'err');
    const res = await api('/api/upload/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: jobId,
        clip_ids: clipIds,
        platforms,
        dry_run: dryRun,
        visibility: $('#up-visibility').value,
        browser: { engine: $('#up-engine').value, profile_id: $('#up-profile-id').value || '' },
      }),
    });
    state.activeUploadJobId = res.job_id;
    toast(dryRun ? 'Тестовый прогон запущен' : 'Публикация запущена', 'ok');
    await pollAll();
  }

  async function loadJobs() {
    state.jobs = (await api('/api/jobs?limit=50')).jobs || [];
    renderHistory();
  }

  function renderHistory() {
    const wrap = $('#history-list');
    const q = ($('#hist-search').value || '').trim().toLowerCase();
    const filt = $('#hist-filter').value || 'all';
    const items = (state.jobs || []).filter((j) => {
      if (filt === 'clipping' && j.kind !== 'analyze' && j.kind !== 'youtube') return false;
      if (filt === 'login' && j.kind !== 'login') return false;
      if (filt === 'upload' && j.kind !== 'upload') return false;
      if (!q) return true;
      return `${j.id} ${j.kind} ${j.step} ${j.message} ${JSON.stringify(j.meta || {})}`.toLowerCase().includes(q);
    });
    wrap.innerHTML = items.length ? '' : '<div class="empty-box">Нет задач по фильтру</div>';
    items.forEach((j) => {
      const row = document.createElement('div');
      row.className = 'hist-row';
      const cls = j.status === 'done' ? 'is-done' : j.status === 'error' ? 'is-err' : '';
      row.innerHTML = `
        <div class="row-gap"><span class="kind-pill ${cls}">${esc(j.kind)} · ${esc(j.status)}</span><strong>${esc((j.meta && (j.meta.source || j.meta.url || j.meta.provider)) || j.kind)}</strong></div>
        <div class="muted-xs">${esc(j.message || j.error || j.step || '')}</div>
        <div class="row-gap"><span class="mono muted-xs">${Math.round(Number(j.progress || 0))}%</span></div>
      `;
      if ((j.kind === 'analyze' || j.kind === 'youtube') && j.status === 'done') {
        const b = document.createElement('button');
        b.className = 'btn-ghost-xs';
        b.type = 'button';
        b.textContent = 'Открыть';
        b.addEventListener('click', async () => {
          state.activeJobId = j.id;
          renderStudioJob(await api(`/api/jobs/${encodeURIComponent(j.id)}`));
          switchTab('studio');
        });
        row.lastElementChild.appendChild(b);
      }
      wrap.appendChild(row);
    });
  }

  // ------------------------------------------------------------------ settings & accounts

  async function loadSettingsBundle() {
    await Promise.all([refreshAccountsOnly(), loadModels()]);
  }

  async function refreshAccountsOnly() {
    const [acc, cfg] = await Promise.all([api('/api/accounts'), api('/api/settings')]);
    state.accounts = acc.accounts || [];
    state.settings = cfg;
    renderAccounts();
    updateLlmTelemetry();
  }

  function renderAccounts() {
    const grid = $('#accounts-grid');
    const activeAccId = (state.settings && state.settings.llm && state.settings.llm.account_id) || '';
    grid.innerHTML = state.accounts.length ? '' : '<div class="empty-box">Аккаунты не подключены. Нажмите кнопку входа справа сверху.</div>';
    state.accounts.forEach((acc) => {
      const isAct = acc.id === activeAccId;
      const card = document.createElement('div');
      card.className = `acc-card ${isAct ? 'is-active' : ''}`;
      card.innerHTML = `
        <div class="lbl-row">
          <div><strong>${esc(acc.label || acc.email || acc.provider)}</strong><div class="muted-xs">${esc(acc.provider)} · ${esc(acc.auth_method)}</div></div>
          <span class="kind-pill ${isAct ? 'is-done' : ''}">${isAct ? 'Активен' : esc(acc.status || 'готов')}</span>
        </div>
        <div class="row-gap">
          <button class="btn-indigo-xs acc-use" type="button">${isAct ? 'Выбран' : 'Выбрать'}</button>
          <button class="btn-ghost-xs acc-test" type="button">Тест</button>
          <button class="btn-ghost-xs acc-del" type="button">Удалить</button>
        </div>
      `;
      card.querySelector('.acc-use').addEventListener('click', async () => {
        await api('/api/accounts/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account_id: acc.id, activate_provider: true }) });
        await refreshAccountsOnly();
        hydrateSettingsForm();
        toast('Аккаунт выбран', 'ok');
      });
      card.querySelector('.acc-test').addEventListener('click', async () => {
        const r = await api('/api/accounts/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account_id: acc.id }) });
        toast(r.message || (r.ok ? 'Работает' : 'Ошибка'), r.ok ? 'ok' : 'err');
      });
      card.querySelector('.acc-del').addEventListener('click', async () => {
        await api(`/api/accounts/${encodeURIComponent(acc.id)}`, { method: 'DELETE' });
        await refreshAccountsOnly();
        toast('Аккаунт удалён', 'ok');
      });
      grid.appendChild(card);
    });
  }

  async function startAccountLogin(provider, authMethod) {
    try {
      const res = await api('/api/accounts/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, auth_method: authMethod }),
      });
      state.activeAuthJobId = res.job_id;
      toast('Окно авторизации запускается…', 'ok');
      await pollAll();
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  function hydrateSettingsForm() {
    const s = state.settings;
    if (!s) return;
    const llm = s.llm || {};
    const stt = s.stt || {};
    const ren = s.render || {};
    const net = s.network || {};
    const paths = s.paths || {};

    const provSel = $('#set-provider');
    provSel.innerHTML = '';
    [...(state.accountProviders || []), ...(state.providers || [])].forEach((p) => provSel.appendChild(new Option(p.label || p.id, p.id)));
    if (llm.provider) provSel.value = llm.provider;
    syncModelOptions();
    if (llm.model) $('#set-model').value = llm.model;
    $('#set-base-url').value = llm.base_url || '';
    $('#set-fallback').checked = llm.fallback !== false;
    $('#set-outdir').value = paths.outdir || '';
    $('#set-lang').value = stt.language || 'ru';
    $('#set-encoder').value = ren.encoder || 'auto';
    $('#set-proxy').value = net.proxy || '';
    $('#set-yt-cookies').value = net.youtube_cookies || 'auto';
  }

  function syncModelOptions() {
    const provId = $('#set-provider').value;
    const prov = [...(state.accountProviders || []), ...(state.providers || [])].find((p) => p.id === provId);
    const modSel = $('#set-model');
    modSel.innerHTML = '';
    ((prov && prov.models) || []).forEach((m) => {
      const id = typeof m === 'string' ? m : m.id;
      modSel.appendChild(new Option(typeof m === 'string' ? m : (m.label || m.id), id));
    });
    $('#set-key-wrap').hidden = provId === 'chatgpt_sub' || provId === 'google_sub';
    $('#set-baseurl-wrap').hidden = provId !== 'ollama' && provId !== 'custom';
  }

  async function loadModels() {
    const data = await api('/api/models');
    const wrap = $('#models-list');
    const sizes = Array.isArray(data.sizes) ? data.sizes : [];
    const installedList = Array.isArray(data.installed) ? data.installed : [];
    const curSize = (state.settings && state.settings.stt && state.settings.stt.whisper_size) || 'base';
    wrap.innerHTML = '';
    sizes.forEach((info) => {
      const sz = info.id;
      const inst = installedList.find((x) => x.size === sz);
      const isInst = Boolean(inst && inst.complete);
      const row = document.createElement('div');
      row.className = 'model-row';
      row.innerHTML = `
        <div><strong>${esc(info.label || sz)}</strong> <span class="muted-xs">${esc(fmtBytes(info.bytes))}</span> ${sz === curSize ? '<span class="kind-pill is-done">активна</span>' : ''}<div class="muted-xs">${esc(info.note || '')}</div></div>
        <div class="row-gap"></div>
      `;
      const btns = row.lastElementChild;
      if (isInst && sz !== curSize) {
        const u = document.createElement('button');
        u.className = 'btn-indigo-xs';
        u.type = 'button';
        u.textContent = 'Выбрать';
        u.addEventListener('click', async () => {
          state.settings = await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stt: { whisper_size: sz } }) });
          await loadModels();
          await loadHealth();
        });
        btns.appendChild(u);
      }
      const b = document.createElement('button');
      b.className = isInst ? 'btn-danger' : 'btn-ghost-xs';
      b.type = 'button';
      b.textContent = isInst ? 'Удалить' : 'Скачать';
      b.addEventListener('click', async () => {
        await api(isInst ? '/api/models/delete' : '/api/models/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ size: sz }),
        });
        toast(isInst ? `Удалена ${sz}` : `Скачивание ${sz} запущено`, 'ok');
        await loadModels();
        await loadHealth();
      });
      btns.appendChild(b);
      wrap.appendChild(row);
    });
  }

  async function saveAllSettings() {
    const prov = $('#set-provider').value;
    const keyVal = $('#set-api-key').value.trim();
    const elKey = $('#set-eleven-key').value.trim();
    const patch = {
      llm: {
        provider: prov,
        model: $('#set-model').value,
        base_url: $('#set-base-url').value.trim(),
        fallback: $('#set-fallback').checked,
      },
      stt: { language: $('#set-lang').value },
      render: { encoder: $('#set-encoder').value },
      paths: { outdir: $('#set-outdir').value.trim() },
      network: { proxy: $('#set-proxy').value.trim(), youtube_cookies: $('#set-yt-cookies').value },
    };
    if (keyVal) {
      patch.llm.api_key = keyVal;
      patch.llm.keys = { [prov]: keyVal };
    }
    if (elKey) patch.tts = { elevenlabs_key: elKey };

    state.settings = await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) });
    $('#set-api-key').value = '';
    $('#set-eleven-key').value = '';
    updateLlmTelemetry();
    await loadHealth();
    toast('Настройки сохранены', 'ok');
  }

  function playVoiceAudio(voiceId) {
    const audio = $('#voice-sample-audio');
    audio.src = `/api/voices/${encodeURIComponent(voiceId)}/sample?t=${Date.now()}`;
    audio.play().catch((e) => toast(e.message, 'err'));
  }

  // ------------------------------------------------------------------ events

  function bindEvents() {
    $$('.tb-tab').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
    $$('[data-jump-settings]').forEach((b) => b.addEventListener('click', () => switchTab('settings')));
    $$('.insp-tab').forEach((b) => b.addEventListener('click', () => switchInsp(b.dataset.insp)));
    $$('.bin-tab').forEach((b) => b.addEventListener('click', () => switchBin(b.dataset.bin)));
    $$('[data-stage-view]').forEach((b) => b.addEventListener('click', () => setStageView(b.dataset.stageView)));

    $$('[data-src-mode]').forEach((b) => {
      b.addEventListener('click', () => {
        state.sourceMode = b.dataset.srcMode;
        $$('[data-src-mode]').forEach((x) => x.classList.toggle('is-active', x === b));
        $('#src-file-pane').hidden = state.sourceMode !== 'file';
        $('#src-yt-pane').hidden = state.sourceMode !== 'youtube';
        updateStagePreview();
      });
    });

    const dz = $('#dropzone');
    const fi = $('#file-input');
    const applyFile = (f) => {
      if (!f) return;
      state.selectedFile = f;
      dz.classList.add('has-file');
      $('#drop-title').textContent = f.name;
      $('#drop-sub').textContent = fmtBytes(f.size);
      $('#clear-file-btn').hidden = false;
    };
    dz.addEventListener('click', (e) => { if (e.target.id !== 'clear-file-btn') fi.click(); });
    dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('is-drag'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('is-drag'));
    dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('is-drag'); if (e.dataTransfer.files[0]) applyFile(e.dataTransfer.files[0]); });
    fi.addEventListener('change', () => applyFile(fi.files[0]));
    $('#clear-file-btn').addEventListener('click', () => {
      state.selectedFile = null;
      fi.value = '';
      dz.classList.remove('has-file');
      $('#drop-title').textContent = 'Выбрать или перетащить видео';
      $('#drop-sub').textContent = 'MP4, MOV, MKV, WebM';
      $('#clear-file-btn').hidden = true;
    });

    $('#yt-paste-btn').addEventListener('click', async () => {
      try { $('#yt-url').value = await navigator.clipboard.readText(); } catch { toast('Нажмите Ctrl+V', 'info'); }
    });

    $$('[data-preset="brainrot"]').forEach((b) => b.addEventListener('click', () => {
      setMode('brainrot');
      setFraming('blur');
      setStyle('drama');
      setEffect('aura');
      if ($$('#opt-voice option').some((o) => o.value === 'canon_bright')) {
        $('#opt-voice').value = 'canon_bright';
        updateVoiceNote();
      }
      if ($$('#opt-music option').some((o) => o.value === 'drama-guzheng')) $('#opt-music').value = 'drama-guzheng';
      updateStagePreview();
      toast('Пресет китайской дорамы включён', 'ok');
    }));

    $$('#mode-stack .mode-item').forEach((c) => c.addEventListener('click', () => setMode(c.dataset.mode)));
    $$('#framing-pills .chip').forEach((b) => b.addEventListener('click', () => setFraming(b.dataset.framing)));
    $$('#style-pills .chip').forEach((b) => b.addEventListener('click', () => setStyle(b.dataset.style)));
    $$('#part-presets .chip').forEach((b) => b.addEventListener('click', () => setPartSeconds(b.dataset.sec)));
    $('#part-sec-range').addEventListener('input', (e) => setPartSeconds(e.target.value));

    $('#opt-dur-preset').addEventListener('change', (e) => {
      $('#custom-dur-row').hidden = e.target.value !== 'custom';
      if (e.target.value !== 'custom') {
        const [a, b] = e.target.value.split('-').map(Number);
        $('#opt-min-dur').value = String(a);
        $('#opt-max-dur').value = String(b);
      }
    });

    $('#opt-subtitles').addEventListener('change', updateStagePreview);
    $('#opt-music').addEventListener('change', updateStagePreview);
    $('#opt-integration').addEventListener('change', updateStagePreview);
    $('#opt-voice').addEventListener('change', updateVoiceNote);
    $('#opt-music-vol').addEventListener('input', (e) => { $('#music-vol-label').textContent = `${e.target.value}%`; });
    $('#opt-orig-vol').addEventListener('input', (e) => { $('#orig-vol-label').textContent = `${e.target.value}%`; });

    $('#preview-voice-btn').addEventListener('click', () => playVoiceAudio($('#opt-voice').value || 'canon_bright'));
    $('#play-canon-1').addEventListener('click', () => playVoiceAudio('canon_bright'));
    $('#play-canon-2').addEventListener('click', () => playVoiceAudio('canon_deep'));

    $('#run-btn').addEventListener('click', startStudioJob);
    $('#cancel-job-btn').addEventListener('click', async () => {
      if (state.activeJobId) await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}/cancel`, { method: 'POST' });
    });
    $('#copy-log-btn').addEventListener('click', async () => {
      await navigator.clipboard.writeText($('#job-terminal').textContent);
      toast('Лог скопирован', 'ok');
    });
    $('#open-folder-btn').addEventListener('click', async () => {
      if (state.currentClipsJobId || state.activeJobId) {
        await api('/api/open-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: state.currentClipsJobId || state.activeJobId }) });
      }
    });

    $('#deck-select-all').addEventListener('click', () => { state.selectedClipIds = new Set(state.currentJobClips.map((c) => String(c.id))); renderClipDeck(); });
    $('#deck-select-none').addEventListener('click', () => { state.selectedClipIds.clear(); renderClipDeck(); });
    $('#deck-to-upload-btn').addEventListener('click', () => switchTab('upload'));
    $('#deck-apply-int-btn').addEventListener('click', async () => {
      const intId = $('#deck-int-select').value;
      const clipIds = Array.from(state.selectedClipIds);
      if (!state.currentClipsJobId || !intId || !clipIds.length) return toast('Выберите рекламу и клипы', 'err');
      const res = await api('/api/integrations/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: state.currentClipsJobId, integration_id: intId, clip_ids: clipIds }),
      });
      state.activeJobId = res.job_id;
      toast('Вшивание рекламы запущено', 'ok');
    });

    $('#ce-save-btn').addEventListener('click', saveClipMetadata);
    $('#ce-recut-btn').addEventListener('click', recutClip);

    $('#int-upload-btn').addEventListener('click', async () => {
      const file = $('#int-file').files[0];
      if (!file) return toast('Выберите файл рекламы', 'err');
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', $('#int-name').value.trim() || file.name);
      fd.append('mode', $('#int-mode').value);
      fd.append('position', $('#int-pos').value);
      fd.append('hook_sec', $('#int-hook-sec').value || '3');
      fd.append('scale_pct', $('#int-scale').value || '75');
      const res = await api('/api/integrations', { method: 'POST', body: fd });
      state.integrations = (await api('/api/integrations')).integrations || [];
      populateIntegrations();
      if (res.integration) $('#opt-integration').value = res.integration.id;
      updateStagePreview();
      $('#int-file').value = '';
      $('#int-name').value = '';
      toast('Интеграция сохранена', 'ok');
    });

    $('#up-job-select').addEventListener('change', (e) => loadUploadJobClips(e.target.value));
    $('#up-toggle-all').addEventListener('click', () => {
      const bs = $$('#up-clip-list input');
      const all = bs.every((b) => b.checked);
      bs.forEach((b) => { b.checked = !all; });
      updateUploadCount();
    });
    $('#up-engine').addEventListener('change', refreshBrowserProfiles);
    $('#up-refresh-profiles').addEventListener('click', refreshBrowserProfiles);
    $('#up-dry-btn').addEventListener('click', () => runBatchUpload(true));
    $('#up-publish-btn').addEventListener('click', () => runBatchUpload(false));

    $('#hist-search').addEventListener('input', renderHistory);
    $('#hist-filter').addEventListener('change', renderHistory);
    $('#hist-refresh-btn').addEventListener('click', loadJobs);

    $('#login-chatgpt-oauth').addEventListener('click', () => startAccountLogin('chatgpt', 'oauth'));
    $('#login-chatgpt-browser').addEventListener('click', () => startAccountLogin('chatgpt', 'browser'));
    $('#login-google-browser').addEventListener('click', () => startAccountLogin('google', 'browser'));
    $('#auth-cancel-btn').addEventListener('click', async () => {
      if (state.activeAuthJobId) await api(`/api/jobs/${encodeURIComponent(state.activeAuthJobId)}/cancel`, { method: 'POST' });
    });
    $('#auth-url-copy').addEventListener('click', async () => {
      if ($('#auth-url-copy').dataset.url) {
        await navigator.clipboard.writeText($('#auth-url-copy').dataset.url);
        toast('Ссылка скопирована', 'ok');
      }
    });

    $('#set-provider').addEventListener('change', syncModelOptions);
    $('#test-provider-btn').addEventListener('click', async () => {
      const st = $('#provider-test-status');
      st.textContent = 'Проверка…';
      st.className = 'test-msg';
      const r = await api('/api/providers/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: $('#set-provider').value,
          model: $('#set-model').value,
          api_key: $('#set-api-key').value.trim(),
          base_url: $('#set-base-url').value.trim(),
        }),
      });
      st.textContent = r.message || (r.ok ? 'ОК' : 'Ошибка');
      st.className = `test-msg ${r.ok ? 'is-ok' : 'is-err'}`;
    });
    $('#save-settings-btn').addEventListener('click', saveAllSettings);
  }

  async function init() {
    bindEvents();
    await Promise.all([loadHealth(), loadLooksAndSettings(), loadJobs()]);
    state.pollTimer = setInterval(pollAll, 1200);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
