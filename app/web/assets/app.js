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
    models: { installed: {}, sizes: {} },
    integrations: [],
    jobs: [],
    activeJobId: '',
    activeAuthJobId: '',
    activeUploadJobId: '',
    currentJobClips: [],
    currentClipsJobId: '',
    selectedClipIds: new Set(),
    editingClip: null,
    pollTimer: null,
  };

  // ------------------------------------------------------------------ utils

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
    if (!res.ok) {
      throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    }
    return data;
  }

  function fmtSec(sec) {
    const s = Math.max(0, Math.round(Number(sec || 0)));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
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
    $$('.nav-pill').forEach((b) => b.classList.toggle('is-active', b.dataset.tab === tab));
    $$('.tab-view').forEach((s) => s.classList.toggle('is-active', s.id === `tab-${tab}`));
    if (tab === 'history') loadJobs();
    if (tab === 'upload') syncUploadPanel();
    if (tab === 'settings') loadSettingsBundle();
  }

  // ------------------------------------------------------------------ studio preview & summary

  const SAMPLE_SUBS = {
    moments: 'ОН НЕ ЗНАЛ ЧТО ЭТО ЛОВУШКА',
    parts: 'ЧАСТЬ 2 · САМЫЙ ПОВОРОТНЫЙ МОМЕНТ',
    brainrot: 'И ТОГДА ИМПЕРАТОР ПРИКАЗАЛ МОЛЧАТЬ!',
  };

  function updateStagePreview() {
    const phone = $('#preview-phone');
    if (!phone) return;
    phone.dataset.framing = state.framing;
    phone.dataset.effect = state.effect;
    phone.dataset.style = state.style;

    const subsEnabled = $('#opt-subtitles').checked;
    $('#preview-sub-box').hidden = !subsEnabled;
    $('#preview-sub-text').textContent = SAMPLE_SUBS[state.mode] || SAMPLE_SUBS.moments;

    const badgeMap = {
      moments: 'МОМЕНТ #1',
      parts: `ЧАСТЬ 1 · ${state.partSec}с`,
      brainrot: 'ДОРАМА · TTS',
    };
    $('#preview-mode-badge').textContent = badgeMap[state.mode] || 'КЛИП';

    const fxObj = (state.looks.effects || []).find((e) => e.id === state.effect);
    $('#preview-fx-label').textContent = `FX: ${fxObj ? fxObj.label : state.effect}`;

    const musicId = $('#opt-music').value;
    const musicObj = (state.looks.music || []).find((m) => m.id === musicId);
    $('#preview-music-label').textContent = musicId && musicId !== 'none'
      ? `♪ ${musicObj ? musicObj.title : musicId}`
      : '♪ без музыки';

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
    $('#action-summary').textContent = `${modeNames[state.mode] || state.mode} · ${frameNames[state.framing] || state.framing} 9:16 · ${styleNames[state.style] || state.style}`;
    $('#run-btn-label').textContent = state.sourceMode === 'youtube' ? 'Скачать и собрать' : 'Собрать клипы';
  }

  function setMode(mode) {
    state.mode = mode;
    $$('#mode-stack .mode-card').forEach((c) => c.classList.toggle('is-active', c.dataset.mode === mode));
    $('#opt-moments').hidden = mode !== 'moments';
    $('#opt-parts').hidden = mode !== 'parts';
    $('#opt-brainrot').hidden = mode !== 'brainrot';
    updateStagePreview();
  }

  function setFraming(framing) {
    state.framing = framing;
    $$('#framing-pills .mini-pill').forEach((b) => b.classList.toggle('is-active', b.dataset.framing === framing));
    updateStagePreview();
  }

  function setStyle(style) {
    state.style = style;
    $$('#style-pills .mini-pill').forEach((b) => b.classList.toggle('is-active', b.dataset.style === style));
    updateStagePreview();
  }

  function setEffect(effect) {
    state.effect = effect;
    $$('#effect-pills .mini-pill').forEach((b) => b.classList.toggle('is-active', b.dataset.effect === effect));
    updateStagePreview();
  }

  function setPartSeconds(sec) {
    state.partSec = Math.max(10, Math.min(180, Number(sec) || 45));
    $('#part-sec-label').textContent = `${state.partSec}с`;
    $('#part-sec-range').value = String(state.partSec);
    $$('#part-presets .mini-pill').forEach((b) => b.classList.toggle('is-active', Number(b.dataset.sec) === state.partSec));
    updateStagePreview();
  }

  function applyPreset(preset) {
    if (preset === 'moments') {
      setMode('moments');
      setFraming('auto');
      setStyle('classic');
      setEffect('none');
      $('#opt-music').value = 'none';
    } else if (preset === 'parts') {
      setMode('parts');
      setPartSeconds(45);
      setFraming('blur');
      setStyle('bold');
      setEffect('cinema');
    } else if (preset === 'brainrot') {
      setMode('brainrot');
      setFraming('blur');
      setStyle('drama');
      setEffect('aura');
      if ($$('#opt-music option').some((o) => o.value === 'drama-guzheng')) {
        $('#opt-music').value = 'drama-guzheng';
      }
      $('#opt-music-vol').value = '20';
      $('#music-vol-label').textContent = '20%';
    }
    updateStagePreview();
    toast('Пресет применён', 'ok');
  }

  // ------------------------------------------------------------------ initial data loading

  async function loadHealth() {
    try {
      const h = await api('/api/health');
      state.health = h;
      $('#brand-ver').textContent = `v${h.version || '1.1.0'}`;
      $('#tel-whisper').textContent = `Речь: ${h.whisper_size || 'base'}`;
      $('#tel-whisper').className = `tel-chip ${(h.models || []).length ? 'is-ok' : ''}`;
      $('#tel-encoder').textContent = `Рендер: ${(h.encoder || 'cpu').toUpperCase()}`;
      $('#tel-encoder').className = `tel-chip ${h.encoder && h.encoder !== 'cpu' ? 'is-ok' : ''}`;
      $('#tel-browser').textContent = h.browser ? 'Браузер: готов' : 'Браузер: нет';
      $('#tel-browser').className = `tel-chip ${h.browser ? 'is-ok' : 'is-warn'}`;
      $('#tel-browser').title = h.browser_error || 'Chrome / Edge готов';
    } catch (e) {
      toast(`Не удалось получить статус: ${e.message}`, 'err');
    }
  }

  function updateLlmTelemetry() {
    const s = state.settings;
    if (!s) return;
    const llmCfg = s.llm || {};
    const prov = llmCfg.provider || '—';
    const acc = state.accounts.find((a) => a.id === llmCfg.account_id);
    const chip = $('#tel-llm');
    if (acc) {
      chip.textContent = `ИИ: ${acc.label}`;
      chip.className = 'tel-chip is-ok';
    } else if (llmCfg.api_key_masked || llmCfg.api_key) {
      chip.textContent = `ИИ: ${prov}`;
      chip.className = 'tel-chip is-ok';
    } else {
      chip.textContent = 'ИИ: ключ / вход';
      chip.className = 'tel-chip is-warn';
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

    // Effects
    const fxWrap = $('#effect-pills');
    fxWrap.innerHTML = '';
    (looks.effects || []).forEach((fx) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'mini-pill';
      btn.dataset.effect = fx.id;
      btn.title = fx.hint || '';
      btn.textContent = fx.label;
      btn.addEventListener('click', () => setEffect(fx.id));
      fxWrap.appendChild(btn);
    });

    // Music
    const musSel = $('#opt-music');
    musSel.innerHTML = '<option value="none">Без музыки</option>';
    (looks.music || []).forEach((m) => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.title}${m.mood ? ` — ${m.mood}` : ''}`;
      musSel.appendChild(opt);
    });

    // Voices
    const voiceSel = $('#opt-voice');
    voiceSel.innerHTML = '';
    (looks.voices || []).forEach((v) => {
      const opt = document.createElement('option');
      opt.value = v.id;
      opt.textContent = v.label || v.id;
      voiceSel.appendChild(opt);
    });

    populateIntegrationSelects();
    hydrateStudioFromSettings(settings);
    hydrateSettingsForm();
    renderAccounts();
    updateLlmTelemetry();
    updateStagePreview();
  }

  function populateIntegrationSelects() {
    const items = state.integrations || [];
    const studioSel = $('#opt-integration');
    const deckSel = $('#deck-int-select');
    const savedList = $('#saved-int-list');

    const prevStudio = studioSel.value;
    studioSel.innerHTML = '<option value="">Без интеграции</option>';
    deckSel.innerHTML = '<option value="">Выбрать рекламу…</option>';
    savedList.innerHTML = '';

    if (!items.length) {
      savedList.innerHTML = '<div class="muted-xs">Пока нет сохранённых рекламных материалов.</div>';
    }

    items.forEach((item) => {
      const label = `${item.name} (${item.mode === 'overlay' ? 'баннер' : 'врезка'})`;
      const o1 = new Option(label, item.id);
      const o2 = new Option(label, item.id);
      studioSel.appendChild(o1);
      deckSel.appendChild(o2);

      const row = document.createElement('div');
      row.className = 'model-row';
      row.innerHTML = `
        <div>
          <strong>${esc(item.name)}</strong>
          <div class="muted-xs">${esc(item.mode)} · ${esc(item.position)}</div>
        </div>
        <button class="btn-danger" type="button">Удалить</button>
      `;
      row.querySelector('button').addEventListener('click', async () => {
        try {
          await api(`/api/integrations/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
          state.integrations = (await api('/api/integrations')).integrations || [];
          populateIntegrationSelects();
          updateStagePreview();
          toast('Интеграция удалена', 'ok');
        } catch (e) {
          toast(e.message, 'err');
        }
      });
      savedList.appendChild(row);
    });

    if (prevStudio && items.some((x) => x.id === prevStudio)) {
      studioSel.value = prevStudio;
    }
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
    const mvol = Math.round(Number(r.music_volume ?? 0.18) * 100);
    $('#opt-music-vol').value = String(mvol);
    $('#music-vol-label').textContent = `${mvol}%`;
    if (r.voice) $('#opt-voice').value = r.voice;
    const ovol = Math.round(Number(r.orig_audio_vol ?? 0.08) * 100);
    $('#opt-orig-vol').value = String(ovol);
    $('#orig-vol-label').textContent = `${ovol}%`;
    $('#opt-brainrot-rewrite').checked = r.brainrot_rewrite !== false;
    if (r.integration_id) $('#opt-integration').value = r.integration_id;
  }

  // ------------------------------------------------------------------ run studio job

  function collectRenderPayload() {
    const isParts = state.mode === 'parts';
    let minDur = Number($('#opt-min-dur').value || 18);
    let maxDur = Number($('#opt-max-dur').value || 60);
    let clips = Number($('#opt-clips').value || 5);
    if (isParts) {
      minDur = state.partSec;
      maxDur = state.partSec;
      clips = Number($('#parts-max-clips').value || 8);
    }
    return {
      mode: state.mode,
      clips,
      style: state.style,
      framing: state.framing,
      subtitles: $('#opt-subtitles').checked,
      encoder: $('#set-encoder').value || 'auto',
      effect: state.effect,
      protection: $('#opt-protection').value || 'off',
      music: $('#opt-music').value || 'none',
      music_volume: Number($('#opt-music-vol').value || 18) / 100,
      voice: $('#opt-voice').value || 'dmitry_drama',
      orig_audio_vol: Number($('#opt-orig-vol').value || 8) / 100,
      brainrot_rewrite: $('#opt-brainrot-rewrite').checked,
      integration_id: $('#opt-integration').value || '',
      min_dur: minDur,
      max_dur: maxDur,
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
      toast('Задача запущена', 'ok');
      await pollAll();
    } catch (e) {
      toast(e.message, 'err');
      $('#run-btn').disabled = false;
    }
  }

  // ------------------------------------------------------------------ job polling & pipeline stepper

  function stageIndexFromJob(job) {
    const st = `${job.step || ''} ${job.message || ''}`.toLowerCase();
    const pct = Number(job.progress || 0);
    if (job.status === 'done' || pct >= 100) return 4;
    if (st.includes('рендер') || st.includes('клип') || st.includes('субтитр') || pct >= 55) return 3;
    if (st.includes('подбор') || st.includes('сценари') || st.includes('озвуч') || st.includes('част') || pct >= 35) return 2;
    if (st.includes('речь') || st.includes('распозна') || st.includes('whisper') || pct >= 18) return 1;
    return 0;
  }

  function renderStudioJob(job) {
    if (!job) return;
    const pct = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0))));
    $('#job-pct-badge').textContent = job.status === 'error' ? 'Ошибка' : job.status === 'done' ? 'Готово' : `${pct}%`;
    $('#job-step-name').textContent = job.step || job.kind || 'Задача';
    $('#job-step-msg').textContent = job.message || job.error || 'Выполняется…';
    $('#job-progress-fill').style.width = `${pct}%`;

    const elapsed = job.finished_at
      ? (job.finished_at - job.created_at)
      : (Date.now() / 1000 - (job.created_at || Date.now() / 1000));
    $('#job-elapsed').textContent = fmtSec(elapsed);

    const activeIdx = stageIndexFromJob(job);
    $$('#pipeline-stepper li').forEach((li, idx) => {
      li.classList.toggle('is-done', job.status === 'done' ? true : idx < activeIdx);
      li.classList.toggle('is-active', job.status === 'running' && idx === activeIdx);
    });

    const term = $('#job-terminal');
    const lines = job.logs || [];
    term.textContent = lines.length ? lines.join('\n') : (job.error || job.message || '');
    term.scrollTop = term.scrollHeight;

    const busy = job.status === 'running' || job.status === 'queued';
    $('#run-btn').disabled = busy;
    $('#cancel-job-btn').hidden = !busy;
    $('#open-folder-btn').hidden = job.status !== 'done';

    if (job.status === 'done' && job.result && Array.isArray(job.result.clips)) {
      if (state.currentClipsJobId !== job.id || state.currentJobClips.length !== job.result.clips.length) {
        setClipDeck(job.id, job.result.clips, job.result.source || '');
      }
    }
  }

  function renderAuthJob(job) {
    const box = $('#auth-console');
    if (!job) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const pct = Math.max(5, Math.min(100, Math.round(Number(job.progress || 0))));
    $('#auth-progress-fill').style.width = `${pct}%`;
    $('#auth-console-title').textContent =
      job.status === 'done' ? 'Аккаунт подключён' :
      job.status === 'error' ? 'Ошибка входа' :
      'Авторизация аккаунта…';
    $('#auth-console-msg').textContent = job.error || job.message || job.step || '';
    const logs = job.logs || [];
    const term = $('#auth-terminal');
    term.textContent = logs.join('\n') || (job.error || '');
    term.scrollTop = term.scrollHeight;

    const busy = job.status === 'running' || job.status === 'queued';
    $('#auth-cancel-btn').hidden = !busy;

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

    if (job.status === 'done' && !job._handledDone) {
      job._handledDone = true;
      loadSettingsBundle();
      loadHealth();
      toast('Аккаунт успешно подключён', 'ok');
    } else if (job.status === 'error' && !job._handledErr) {
      job._handledErr = true;
      toast(`Ошибка входа: ${job.error || 'смотри лог'}`, 'err');
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
        const detail = await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}`);
        renderStudioJob(detail);
      } else {
        const latestStudio = state.jobs.find((j) => j.kind === 'analyze' || j.kind === 'youtube');
        if (latestStudio && !state.currentClipsJobId && latestStudio.status === 'done') {
          const detail = await api(`/api/jobs/${encodeURIComponent(latestStudio.id)}`);
          renderStudioJob(detail);
        }
      }

      if (state.activeAuthJobId) {
        const authDetail = await api(`/api/jobs/${encodeURIComponent(state.activeAuthJobId)}`);
        renderAuthJob(authDetail);
        if (authDetail.status === 'done' || authDetail.status === 'error' || authDetail.status === 'cancelled') {
          if (authDetail.status === 'done') {
            await refreshAccountsOnly();
          }
        }
      }

      if (state.activeUploadJobId) {
        const upDetail = await api(`/api/jobs/${encodeURIComponent(state.activeUploadJobId)}`);
        renderUploadJob(upDetail);
      }

      // Check active model download jobs
      const dlRunning = state.jobs.some((j) => j.kind === 'download_model' && j.status === 'running');
      if (dlRunning && state.tab === 'settings') {
        await loadModels();
      }
      if (state.tab === 'history') {
        renderHistory();
      }
    } catch {
      // silent poll retry
    }
  }

  // ------------------------------------------------------------------ clip deck & modals

  function setClipDeck(jobId, clips, source = '') {
    state.currentClipsJobId = jobId;
    state.currentJobClips = clips || [];
    state.selectedClipIds = new Set(state.currentJobClips.map((c) => String(c.id)));
    $('#deck-count').textContent = String(state.currentJobClips.length);
    $('#deck-source').textContent = source ? `Источник: ${source}` : `Задача #${jobId}`;
    renderClipDeck();
    syncUploadPanel();
  }

  function renderClipDeck() {
    const grid = $('#clips-grid');
    $('#deck-selected-count').textContent = String(state.selectedClipIds.size);
    $('#nav-upload-count').textContent = String(state.selectedClipIds.size);
    $('#nav-upload-count').hidden = state.selectedClipIds.size === 0;

    if (!state.currentJobClips.length) {
      grid.innerHTML = '<div class="empty-state">Пока нет собранных клипов в текущей задаче.</div>';
      return;
    }

    grid.innerHTML = '';
    state.currentJobClips.forEach((clip) => {
      const cid = String(clip.id);
      const checked = state.selectedClipIds.has(cid);
      const card = document.createElement('article');
      card.className = `clip-card ${checked ? 'is-selected' : ''}`;
      card.innerHTML = `
        <div class="clip-thumb-wrap">
          <img src="${esc(clip.preview_url)}?t=${Date.now()}" alt="${esc(clip.title || cid)}" loading="lazy">
          <div class="clip-overlay-top">
            <input class="clip-check" type="checkbox" ${checked ? 'checked' : ''}>
            <span class="clip-tag">${esc(fmtSec(clip.duration))} · #${esc(clip.index ?? cid)}</span>
          </div>
          <span class="clip-play-chip">▶ Смотреть</span>
        </div>
        <div class="clip-body">
          <div class="clip-title">${esc(clip.title || `Клип #${cid}`)}</div>
          <div class="clip-hook">${esc(clip.hook || '')}</div>
          <div class="clip-tags">${esc(clip.hashtags || '')}</div>
        </div>
        <div class="clip-actions">
          <button class="btn-soft btn-edit" type="button">Править / Тайминг</button>
          <button class="btn-accent btn-pub" type="button">В публикацию</button>
        </div>
      `;

      const cb = card.querySelector('.clip-check');
      cb.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (cb.checked) state.selectedClipIds.add(cid);
        else state.selectedClipIds.delete(cid);
        card.classList.toggle('is-selected', cb.checked);
        $('#deck-selected-count').textContent = String(state.selectedClipIds.size);
        $('#nav-upload-count').textContent = String(state.selectedClipIds.size);
        $('#nav-upload-count').hidden = state.selectedClipIds.size === 0;
      });

      card.querySelector('.clip-thumb-wrap').addEventListener('click', () => openClipModal(clip));
      card.querySelector('.btn-edit').addEventListener('click', () => openClipModal(clip));
      card.querySelector('.btn-pub').addEventListener('click', () => {
        state.selectedClipIds = new Set([cid]);
        renderClipDeck();
        switchTab('upload');
      });

      grid.appendChild(card);
    });
  }

  function openClipModal(clip) {
    state.editingClip = clip;
    $('#cm-heading').textContent = `${clip.title || 'Клип'} (${fmtSec(clip.duration)})`;
    const v = $('#cm-video');
    v.src = `${clip.video_url}?t=${Date.now()}`;
    $('#cm-title').value = clip.title || '';
    $('#cm-hook').value = clip.hook || '';
    $('#cm-tags').value = clip.hashtags || '';
    $('#cm-start').value = String(clip.start ?? 0);
    $('#cm-end').value = String(clip.end ?? clip.duration ?? 30);
    $('#clip-modal').hidden = false;
  }

  async function saveClipMetadata() {
    if (!state.editingClip || !state.currentClipsJobId) return;
    const cid = state.editingClip.id;
    try {
      await api(`/api/clips/${encodeURIComponent(state.currentClipsJobId)}/${encodeURIComponent(cid)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: $('#cm-title').value.trim(),
          hook: $('#cm-hook').value.trim(),
          hashtags: $('#cm-tags').value.trim(),
        }),
      });
      const fresh = await api(`/api/clips?job_id=${encodeURIComponent(state.currentClipsJobId)}`);
      setClipDeck(state.currentClipsJobId, fresh.clips || [], fresh.source || '');
      toast('Метаданные клипа сохранены', 'ok');
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  async function recutClip() {
    if (!state.editingClip || !state.currentClipsJobId) return;
    const cid = state.editingClip.id;
    try {
      $('#cm-recut-btn').disabled = true;
      const res = await api(`/api/clips/${encodeURIComponent(state.currentClipsJobId)}/${encodeURIComponent(cid)}/recut`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start: Number($('#cm-start').value),
          end: Number($('#cm-end').value),
        }),
      });
      if (res.clip) {
        $('#cm-video').src = `${res.clip.video_url}?t=${Date.now()}`;
      }
      const fresh = await api(`/api/clips?job_id=${encodeURIComponent(state.currentClipsJobId)}`);
      setClipDeck(state.currentClipsJobId, fresh.clips || [], fresh.source || '');
      toast('Клип перерезан', 'ok');
    } catch (e) {
      toast(e.message, 'err');
    } finally {
      $('#cm-recut-btn').disabled = false;
    }
  }

  // ------------------------------------------------------------------ upload tab

  async function syncUploadPanel() {
    const { jobs } = await api('/api/jobs?limit=30');
    const doneJobs = (jobs || []).filter((j) => (j.kind === 'analyze' || j.kind === 'youtube') && j.status === 'done');
    const sel = $('#up-job-select');
    const curVal = state.currentClipsJobId || sel.value;
    sel.innerHTML = '';
    if (!doneJobs.length) {
      sel.innerHTML = '<option value="">Нет завершённых нарезок</option>';
      $('#up-clip-list').innerHTML = '<div class="empty-state">Сначала соберите клипы в разделе «Студия».</div>';
      $('#up-sel-count').textContent = '0';
      return;
    }
    doneJobs.forEach((j) => {
      const count = j.result && j.result.clips ? j.result.clips.length : '?';
      const label = `${(j.meta && (j.meta.source || j.meta.url)) || j.id} (${count} кл.)`;
      sel.appendChild(new Option(label, j.id));
    });
    if (curVal && doneJobs.some((j) => j.id === curVal)) sel.value = curVal;
    await loadUploadJobClips(sel.value);
  }

  async function loadUploadJobClips(jobId) {
    if (!jobId) return;
    const data = await api(`/api/clips?job_id=${encodeURIComponent(jobId)}`);
    const clips = data.clips || [];
    const list = $('#up-clip-list');
    list.innerHTML = '';
    if (!clips.length) {
      list.innerHTML = '<div class="empty-state">В этой задаче нет клипов</div>';
      $('#up-sel-count').textContent = '0';
      return;
    }
    clips.forEach((c) => {
      const cid = String(c.id);
      const checked = state.currentClipsJobId === jobId ? state.selectedClipIds.has(cid) : true;
      const row = document.createElement('label');
      row.className = 'check-row';
      row.innerHTML = `
        <input type="checkbox" value="${esc(cid)}" ${checked ? 'checked' : ''}>
        <span><strong>${esc(c.title || `Клип #${cid}`)}</strong> <span class="muted-xs">(${esc(fmtSec(c.duration))})</span></span>
      `;
      row.querySelector('input').addEventListener('change', updateUploadCount);
      list.appendChild(row);
    });
    updateUploadCount();
  }

  function updateUploadCount() {
    const count = $$('#up-clip-list input:checked').length;
    $('#up-sel-count').textContent = String(count);
  }

  async function refreshBrowserProfiles() {
    const engine = $('#up-engine').value;
    const row = $('#up-profile-row');
    if (engine === 'plain') {
      row.hidden = true;
      return;
    }
    row.hidden = false;
    const sel = $('#up-profile-id');
    sel.innerHTML = '<option value="">Загрузка…</option>';
    try {
      const data = await api(`/api/browsers?engine=${encodeURIComponent(engine)}`);
      sel.innerHTML = '';
      if (!data.running) {
        sel.innerHTML = `<option value="">${esc(data.error || 'Не запущен')}</option>`;
        return;
      }
      (data.profiles || []).forEach((p) => {
        sel.appendChild(new Option(`${p.name} (${p.status})`, p.id));
      });
    } catch (e) {
      sel.innerHTML = `<option value="">${esc(e.message)}</option>`;
    }
  }

  async function runBatchUpload(dryRun) {
    const jobId = $('#up-job-select').value;
    const clipIds = $$('#up-clip-list input:checked').map((i) => i.value);
    const platforms = $$('#up-platforms input:checked').map((i) => i.value);
    if (!jobId || !clipIds.length) return toast('Выберите задачу и хотя бы один клип', 'err');
    if (!platforms.length) return toast('Выберите хотя бы одну платформу', 'err');

    try {
      const res = await api('/api/upload/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: jobId,
          clip_ids: clipIds,
          platforms,
          dry_run: dryRun,
          visibility: $('#up-visibility').value,
          browser: {
            engine: $('#up-engine').value,
            profile_id: $('#up-profile-id').value || '',
          },
        }),
      });
      state.activeUploadJobId = res.job_id;
      toast(dryRun ? 'Тестовый прогон запущен' : 'Публикация запущена', 'ok');
      await pollAll();
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  // ------------------------------------------------------------------ history tab

  async function loadJobs() {
    const { jobs } = await api('/api/jobs?limit=50');
    state.jobs = jobs || [];
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
      const hay = `${j.id} ${j.kind} ${j.step} ${j.message} ${JSON.stringify(j.meta || {})}`.toLowerCase();
      return hay.includes(q);
    });

    if (!items.length) {
      wrap.innerHTML = '<div class="empty-state">Задачи по фильтру не найдены.</div>';
      return;
    }

    wrap.innerHTML = '';
    items.forEach((j) => {
      const row = document.createElement('div');
      row.className = 'hist-row';
      const stCls = j.status === 'done' ? 'is-done' : j.status === 'error' ? 'is-err' : 'is-run';
      const title = (j.meta && (j.meta.source || j.meta.url || j.meta.provider)) || j.kind;
      const dt = new Date((j.created_at || 0) * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      row.innerHTML = `
        <span class="kind-badge ${stCls}">${esc(j.kind)} · ${esc(j.status)}</span>
        <div>
          <strong>${esc(title)}</strong>
          <div class="muted-xs">${esc(j.message || j.error || j.step || '')}</div>
        </div>
        <span class="mono muted-xs">${esc(dt)} · ${Math.round(Number(j.progress || 0))}%</span>
        <div class="action-buttons"></div>
      `;
      const btns = row.querySelector('.action-buttons');
      if ((j.kind === 'analyze' || j.kind === 'youtube') && j.status === 'done') {
        const loadBtn = document.createElement('button');
        loadBtn.className = 'btn-soft';
        loadBtn.type = 'button';
        loadBtn.textContent = 'Открыть клипы';
        loadBtn.addEventListener('click', async () => {
          const d = await api(`/api/jobs/${encodeURIComponent(j.id)}`);
          state.activeJobId = j.id;
          renderStudioJob(d);
          switchTab('studio');
        });
        btns.appendChild(loadBtn);
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
    if (!state.accounts.length) {
      grid.innerHTML = '<div class="empty-state">Аккаунты не подключены. Нажмите любую кнопку входа выше.</div>';
      return;
    }
    grid.innerHTML = '';
    state.accounts.forEach((acc) => {
      const isAct = acc.id === activeAccId;
      const card = document.createElement('div');
      card.className = `acc-card ${isAct ? 'is-active' : ''}`;
      card.innerHTML = `
        <div class="acc-top">
          <div>
            <div class="acc-title">${esc(acc.label || acc.email || acc.provider)}</div>
            <div class="acc-meta">${esc(acc.provider)} · ${esc(acc.auth_method)}${acc.plan ? ` · ${esc(acc.plan)}` : ''}</div>
          </div>
          <span class="kind-badge ${isAct ? 'is-done' : ''}">${isAct ? 'Активен' : esc(acc.status || 'готов')}</span>
        </div>
        <div class="acc-actions">
          <button class="btn-accent acc-use" type="button">${isAct ? 'Выбран' : 'Сделать активным'}</button>
          <button class="btn-soft acc-test" type="button">Проверить</button>
          <button class="btn-danger acc-del" type="button">Удалить</button>
        </div>
      `;
      card.querySelector('.acc-use').addEventListener('click', async () => {
        try {
          await api('/api/accounts/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: acc.id, activate_provider: true }),
          });
          await refreshAccountsOnly();
          hydrateSettingsForm();
          toast('Аккаунт активирован', 'ok');
        } catch (e) {
          toast(e.message, 'err');
        }
      });
      card.querySelector('.acc-test').addEventListener('click', async () => {
        try {
          const r = await api('/api/accounts/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: acc.id }),
          });
          toast(r.message || (r.ok ? 'Работает' : 'Не отвечает'), r.ok ? 'ok' : 'err');
        } catch (e) {
          toast(e.message, 'err');
        }
      });
      card.querySelector('.acc-del').addEventListener('click', async () => {
        try {
          await api(`/api/accounts/${encodeURIComponent(acc.id)}`, { method: 'DELETE' });
          await refreshAccountsOnly();
          toast('Аккаунт удалён', 'ok');
        } catch (e) {
          toast(e.message, 'err');
        }
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
      toast('Авторизация запущена…', 'ok');
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
    const allProvs = [...(state.accountProviders || []), ...(state.providers || [])];
    allProvs.forEach((p) => {
      provSel.appendChild(new Option(p.label || p.id, p.id));
    });
    if (llm.provider) provSel.value = llm.provider;
    syncModelOptions();
    if (llm.model) $('#set-model').value = llm.model;
    $('#set-base-url').value = llm.base_url || '';
    $('#set-fallback').checked = llm.auto_fallback !== false;
    $('#set-outdir').value = paths.output_dir || '';
    $('#set-lang').value = stt.language || 'auto';
    $('#set-encoder').value = ren.encoder || 'auto';
    $('#set-proxy').value = net.proxy || '';
    $('#set-yt-cookies').checked = net.yt_browser_cookies !== false;
  }

  function syncModelOptions() {
    const provId = $('#set-provider').value;
    const allProvs = [...(state.accountProviders || []), ...(state.providers || [])];
    const prov = allProvs.find((p) => p.id === provId);
    const modSel = $('#set-model');
    modSel.innerHTML = '';
    const models = (prov && prov.models) || [];
    models.forEach((m) => {
      const id = typeof m === 'string' ? m : m.id;
      const label = typeof m === 'string' ? m : (m.label || m.id);
      modSel.appendChild(new Option(label, id));
    });
    const isSub = provId === 'chatgpt_sub' || provId === 'google_sub';
    $('#set-key-wrap').hidden = isSub;
    $('#set-baseurl-wrap').hidden = provId !== 'ollama' && provId !== 'custom';
  }

  async function loadModels() {
    const data = await api('/api/models');
    state.models = data;
    const wrap = $('#models-list');
    const sizes = Array.isArray(data.sizes) ? data.sizes : [];
    const installedList = Array.isArray(data.installed) ? data.installed : [];
    const curSize = (state.settings && state.settings.stt && state.settings.stt.whisper_size) || 'base';
    wrap.innerHTML = '';
    sizes.forEach((info) => {
      const sz = info.id;
      const inst = installedList.find((x) => x.size === sz);
      const isInst = Boolean(inst && inst.complete);
      const sizeStr = info.bytes ? fmtBytes(info.bytes) : (info.size || '');
      const row = document.createElement('div');
      row.className = 'model-row';
      row.innerHTML = `
        <div>
          <strong>${esc(info.label || sz)}</strong> <span class="muted-xs">${esc(sizeStr)}</span>
          ${sz === curSize ? '<span class="kind-badge is-done">активна</span>' : ''}
          <div class="muted-xs">${esc(info.note || '')}</div>
        </div>
        <div class="action-buttons"></div>
      `;
      const btns = row.querySelector('.action-buttons');
      if (isInst && sz !== curSize) {
        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.className = 'btn-accent';
        useBtn.textContent = 'Выбрать';
        useBtn.addEventListener('click', async () => {
          try {
            state.settings = await api('/api/settings', {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ stt: { whisper_size: sz } }),
            });
            await loadModels();
            await loadHealth();
            toast(`Модель ${sz} выбрана`, 'ok');
          } catch (e) {
            toast(e.message, 'err');
          }
        });
        btns.appendChild(useBtn);
      }
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = isInst ? 'btn-danger' : 'btn-soft';
      btn.textContent = isInst ? 'Удалить' : 'Скачать';
      btn.addEventListener('click', async () => {
        try {
          if (isInst) {
            await api('/api/models/delete', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ size: sz }),
            });
            toast(`Модель ${sz} удалена`, 'ok');
          } else {
            await api('/api/models/download', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ size: sz }),
            });
            toast(`Скачивание ${sz} запущено`, 'ok');
          }
          await loadModels();
          await loadHealth();
        } catch (e) {
          toast(e.message, 'err');
        }
      });
      btns.appendChild(btn);
      wrap.appendChild(row);
    });
  }

  async function saveAllSettings() {
    const prov = $('#set-provider').value;
    const keyVal = $('#set-api-key').value.trim();
    const patch = {
      llm: {
        provider: prov,
        model: $('#set-model').value,
        base_url: $('#set-base-url').value.trim(),
        auto_fallback: $('#set-fallback').checked,
      },
      stt: { language: $('#set-lang').value },
      render: { encoder: $('#set-encoder').value },
      paths: { output_dir: $('#set-outdir').value.trim() },
      network: {
        proxy: $('#set-proxy').value.trim(),
        yt_browser_cookies: $('#set-yt-cookies').checked,
      },
    };
    if (keyVal) patch.llm[`${prov}_key`] = keyVal;

    try {
      state.settings = await api('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      $('#set-api-key').value = '';
      updateLlmTelemetry();
      await loadHealth();
      toast('Настройки сохранены', 'ok');
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  // ------------------------------------------------------------------ events wiring

  function bindEvents() {
    $$('.nav-pill').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));

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
    const applyFile = (file) => {
      if (!file) return;
      state.selectedFile = file;
      dz.classList.add('has-file');
      $('#drop-title').textContent = file.name;
      $('#drop-sub').textContent = `${fmtBytes(file.size)} · готово к сборке`;
      $('#clear-file-btn').hidden = false;
    };
    dz.addEventListener('click', (e) => {
      if (e.target.id === 'clear-file-btn') return;
      fi.click();
    });
    dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('is-drag'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('is-drag'));
    dz.addEventListener('drop', (e) => {
      e.preventDefault();
      dz.classList.remove('is-drag');
      if (e.dataTransfer.files[0]) applyFile(e.dataTransfer.files[0]);
    });
    fi.addEventListener('change', () => applyFile(fi.files[0]));
    $('#clear-file-btn').addEventListener('click', () => {
      state.selectedFile = null;
      fi.value = '';
      dz.classList.remove('has-file');
      $('#drop-title').textContent = 'Перетащите видео или нажмите';
      $('#drop-sub').textContent = 'MP4, MOV, MKV, WebM · до 4K';
      $('#clear-file-btn').hidden = true;
    });

    $('#yt-paste-btn').addEventListener('click', async () => {
      try {
        $('#yt-url').value = await navigator.clipboard.readText();
      } catch {
        toast('Вставьте ссылку через Ctrl+V', 'info');
      }
    });

    $$('[data-preset]').forEach((b) => b.addEventListener('click', () => applyPreset(b.dataset.preset)));
    $$('#mode-stack .mode-card').forEach((c) => c.addEventListener('click', () => setMode(c.dataset.mode)));
    $$('#framing-pills .mini-pill').forEach((b) => b.addEventListener('click', () => setFraming(b.dataset.framing)));
    $$('#style-pills .mini-pill').forEach((b) => b.addEventListener('click', () => setStyle(b.dataset.style)));
    $$('#part-presets .mini-pill').forEach((b) => b.addEventListener('click', () => setPartSeconds(b.dataset.sec)));
    $('#part-sec-range').addEventListener('input', (e) => setPartSeconds(e.target.value));

    $('#opt-dur-preset').addEventListener('change', (e) => {
      const val = e.target.value;
      $('#custom-dur-row').hidden = val !== 'custom';
      if (val !== 'custom') {
        const [a, b] = val.split('-').map(Number);
        $('#opt-min-dur').value = String(a);
        $('#opt-max-dur').value = String(b);
      }
    });

    $('#opt-subtitles').addEventListener('change', updateStagePreview);
    $('#opt-music').addEventListener('change', updateStagePreview);
    $('#opt-integration').addEventListener('change', updateStagePreview);
    $('#opt-music-vol').addEventListener('input', (e) => {
      $('#music-vol-label').textContent = `${e.target.value}%`;
    });
    $('#opt-orig-vol').addEventListener('input', (e) => {
      $('#orig-vol-label').textContent = `${e.target.value}%`;
    });

    $('#run-btn').addEventListener('click', startStudioJob);
    $('#cancel-job-btn').addEventListener('click', async () => {
      if (state.activeJobId) await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}/cancel`, { method: 'POST' });
    });
    $('#copy-log-btn').addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText($('#job-terminal').textContent);
        toast('Лог скопирован', 'ok');
      } catch {
        toast('Не удалось скопировать', 'err');
      }
    });
    $('#open-folder-btn').addEventListener('click', async () => {
      if (state.currentClipsJobId || state.activeJobId) {
        await api('/api/open-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: state.currentClipsJobId || state.activeJobId }),
        });
      }
    });

    // Clip deck batch actions
    $('#deck-select-all').addEventListener('click', () => {
      state.selectedClipIds = new Set(state.currentJobClips.map((c) => String(c.id)));
      renderClipDeck();
    });
    $('#deck-select-none').addEventListener('click', () => {
      state.selectedClipIds.clear();
      renderClipDeck();
    });
    $('#deck-to-upload-btn').addEventListener('click', () => switchTab('upload'));
    $('#deck-apply-int-btn').addEventListener('click', async () => {
      const intId = $('#deck-int-select').value;
      const jobId = state.currentClipsJobId;
      const clipIds = Array.from(state.selectedClipIds);
      if (!jobId || !intId || !clipIds.length) return toast('Выберите рекламу и клипы', 'err');
      try {
        const res = await api('/api/integrations/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: jobId, integration_id: intId, clip_ids: clipIds }),
        });
        state.activeJobId = res.job_id;
        toast('Наложение интеграции запущено', 'ok');
      } catch (e) {
        toast(e.message, 'err');
      }
    });

    // Modals
    $$('[data-close-modal]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.closeModal;
        $(`#${id}`).hidden = true;
        if (id === 'clip-modal') $('#cm-video').pause();
      });
    });
    $('#cm-save-btn').addEventListener('click', saveClipMetadata);
    $('#cm-recut-btn').addEventListener('click', recutClip);
    $('#open-int-modal-btn').addEventListener('click', () => { $('#int-modal').hidden = false; });
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
      try {
        const res = await api('/api/integrations', { method: 'POST', body: fd });
        state.integrations = (await api('/api/integrations')).integrations || [];
        populateIntegrationSelects();
        if (res.integration) $('#opt-integration').value = res.integration.id;
        updateStagePreview();
        $('#int-file').value = '';
        $('#int-name').value = '';
        toast('Интеграция сохранена', 'ok');
      } catch (e) {
        toast(e.message, 'err');
      }
    });

    // Upload tab
    $('#up-job-select').addEventListener('change', (e) => loadUploadJobClips(e.target.value));
    $('#up-toggle-all').addEventListener('click', () => {
      const boxes = $$('#up-clip-list input');
      const allChecked = boxes.every((b) => b.checked);
      boxes.forEach((b) => { b.checked = !allChecked; });
      updateUploadCount();
    });
    $('#up-engine').addEventListener('change', refreshBrowserProfiles);
    $('#up-refresh-profiles').addEventListener('click', refreshBrowserProfiles);
    $('#up-dry-btn').addEventListener('click', () => runBatchUpload(true));
    $('#up-publish-btn').addEventListener('click', () => runBatchUpload(false));

    // History tab
    $('#hist-search').addEventListener('input', renderHistory);
    $('#hist-filter').addEventListener('change', renderHistory);
    $('#hist-refresh-btn').addEventListener('click', loadJobs);

    // Settings & Accounts
    $('#login-chatgpt-oauth').addEventListener('click', () => startAccountLogin('chatgpt', 'oauth'));
    $('#login-chatgpt-browser').addEventListener('click', () => startAccountLogin('chatgpt', 'browser'));
    $('#login-google-browser').addEventListener('click', () => startAccountLogin('google', 'browser'));
    $('#auth-cancel-btn').addEventListener('click', async () => {
      if (state.activeAuthJobId) await api(`/api/jobs/${encodeURIComponent(state.activeAuthJobId)}/cancel`, { method: 'POST' });
    });
    $('#auth-url-copy').addEventListener('click', async () => {
      const url = $('#auth-url-copy').dataset.url;
      if (url) {
        await navigator.clipboard.writeText(url);
        toast('Ссылка входа скопирована', 'ok');
      }
    });

    $('#set-provider').addEventListener('change', syncModelOptions);
    $('#test-provider-btn').addEventListener('click', async () => {
      const st = $('#provider-test-status');
      st.textContent = 'Проверка…';
      st.className = 'test-status';
      try {
        const res = await api('/api/providers/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider: $('#set-provider').value,
            model: $('#set-model').value,
            api_key: $('#set-api-key').value.trim(),
            base_url: $('#set-base-url').value.trim(),
          }),
        });
        st.textContent = res.message || (res.ok ? 'Работает' : 'Ошибка');
        st.className = `test-status ${res.ok ? 'is-ok' : 'is-err'}`;
      } catch (e) {
        st.textContent = e.message;
        st.className = 'test-status is-err';
      }
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
