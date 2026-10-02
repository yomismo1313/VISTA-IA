// ═══════════════════════════════════════════════════════════════
// VISTA · gateway/web/app.js
// ═══════════════════════════════════════════════════════════════

const logEl = document.getElementById('log');

function logMsg(m) {
  const d = document.createElement('div');
  d.textContent = new Date().toLocaleTimeString() + '  ' + m;
  logEl.insertBefore(d, logEl.firstChild);
  while (logEl.childElementCount > 200) logEl.removeChild(logEl.lastChild);
}

// ── Helper: fetch con timeout ──
function fetchT(url, opts = {}, timeoutMs = 30000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  return fetch(url, { ...opts, signal: ctrl.signal }).finally(() => clearTimeout(t));
}

// ── /cmd (beep, say rápido, stop, radio) ──
async function api(body, timeoutMs = 30000) {
  try {
    const r = await fetchT('/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, timeoutMs);
    const data = await r.json();
    if (!data.ok) logMsg('❌ ' + (data.err || 'error'));
    return data;
  } catch (e) {
    logMsg('❌ red: ' + e.message);
    return { ok: false, err: e.message };
  }
}

// ── Beeps ──
function beep(f, m) {
  logMsg('beep ' + f + 'Hz');
  api({ cmd: 'beep', freq: f, ms: m });
}
function beepSeq() {
  beep(523, 150);
  setTimeout(() => beep(659, 200), 200);
  setTimeout(() => beep(784, 250), 400);
}

// ── TTS rápido (espeak, en el gateway) ──
function say() {
  const t = document.getElementById('say-text').value.trim();
  if (!t) return;
  logMsg('TTS rápido: "' + t + '"');
  api({ cmd: 'say', text: t });
}

// ── TTS voz clonada (XTTS, en voz/ a través del gateway) ──
async function sayClonada(textoForzado) {
  const t = (textoForzado !== undefined ? textoForzado : document.getElementById('say-text').value).trim();
  if (!t) return;

  logMsg('TTS voz clonada: "' + (t.length > 60 ? t.substring(0, 60) + '…' : t) + '"');

  try {
    const r = await fetchT('/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: t }),
    }, 600000);   // 10 min

    let data;
    try { data = await r.json(); } catch { data = {}; }

    if (!r.ok || data.ok === false) {
      logMsg('❌ voz/: ' + (data.err || ('HTTP ' + r.status)));
      return null;
    }
    const kb = data.bytes ? (data.bytes / 1024).toFixed(1) + ' KB' : '?';
    logMsg('✅ voz clonada [' + (data.motor || '?') + '] ' + kb);
    return data;
  } catch (e) {
    logMsg('❌ voz/: ' + (e.name === 'AbortError' ? 'timeout' : e.message));
    return null;
  }
}

// ── Stop audio ──
function stopAudio() {
  logMsg('⏹ Parar sonido');
  api({ cmd: 'stop' });
}

// ── Radio ──
function radioOn() {
  const u = document.getElementById('radio-url').value.trim();
  logMsg('radio ON');
  api({ cmd: 'radio_on', url: u }, 15000).then(r => {
    if (r.ok === false) logMsg('❌ radio: ' + (r.err || 'error'));
    else logMsg('✅ radio ON');
  });
}
function radioOff() {
  logMsg('radio OFF');
  api({ cmd: 'radio_off' }, 15000).then(r => {
    if (r.ok === false) logMsg('❌ radio OFF: ' + (r.err || 'error'));
  });
}

// ── Cámara ──
let cameraActive = false;
let camInterval = null;
let camFailures = 0;

function toggleCamera() {
  cameraActive = !cameraActive;
  const container = document.getElementById('videoContainer');

  if (cameraActive) {
    container.style.display = 'block';
    logMsg('📷 Cámara activada');
    camFailures = 0;
    refreshCamera();
    camInterval = setInterval(refreshCamera, 3000);
  } else {
    container.style.display = 'none';
    if (camInterval) { clearInterval(camInterval); camInterval = null; }
    logMsg('📷 Cámara desactivada');
  }
}

function refreshCamera() {
  const img = document.getElementById('camImg');
  if (!img) return;

  const newImg = new Image();
  newImg.onload = () => {
    img.src = newImg.src;
    camFailures = 0;
  };
  newImg.onerror = () => {
    camFailures++;
    if (camFailures === 1) logMsg('⚠️ frame de cámara falló, reintentando...');
    if (camFailures === 5) logMsg('❌ cámara no responde (¿ESP32 apagada?)');
  };
  newImg.src = '/camera_proxy?' + Date.now();
}

// ── Pausar/reanudar cámara (para no competir con vision) ──
function pausarCamara() {
  if (camInterval) {
    clearInterval(camInterval);
    camInterval = null;
    return true;
  }
  return false;
}
function reanudarCamara(estabaActiva) {
  if (estabaActiva && cameraActive && !camInterval) {
    camInterval = setInterval(refreshCamera, 3000);
  }
}

// ── Describir (LLaVA vía vision/) + hablar la descripción ──
async function describe() {
  const box = document.getElementById('desc-box');
  const hablar = document.getElementById('desc-hablar');
  const quiereHablar = hablar ? hablar.checked : true;

  box.textContent = '⏳ Generando descripción...';

  // Pausar cámara para liberar la ESP32 mientras vision captura
  const pausada = pausarCamara();
  if (pausada) logMsg('⏸️ cámara en pausa mientras describe');

  try {
    const r = await fetchT('/describe', { method: 'POST' }, 120000);   // 2 min
    const data = await r.json();

    if (!data.descripcion) {
      box.textContent = '❌ Error: ' + (data.error || data.err || ('HTTP ' + r.status));
      logMsg('❌ describe: ' + (data.error || data.err || 'error'));
      return;
    }

    box.textContent = '🧠 ' + data.descripcion;
    logMsg('🧠 ' + data.descripcion);

    // Hablar la descripción si el usuario quiere
    if (quiereHablar) {
      logMsg('🗣️  hablando descripción...');
      await sayClonada(data.descripcion);
    }
  } catch (e) {
    box.textContent = '❌ Error de red: ' + (e.name === 'AbortError' ? 'timeout' : e.message);
    logMsg('❌ describe: ' + (e.name === 'AbortError' ? 'timeout' : e.message));
  } finally {
    reanudarCamara(pausada);
    if (pausada) logMsg('▶️ cámara reanudada');
  }
}

// ── Poll de estado ──
let consecutiveFails = 0;
async function poll() {
  try {
    const r = await fetchT('/stats', {}, 5000);
    const d = await r.json();
    consecutiveFails = 0;

    document.getElementById('ips').textContent =
      'servidor: ' + (d.server_ip || '?') +
      '   ·   esp32: ' + (d.esp32_ip || '— esperando —');

    const p = (id, v) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.className = 'pill' + (v === true ? ' on' : (v === false ? ' off' : ''));
    };
    p('pill-tcp', d.tcp_spk);
    p('pill-radio', d.radio);
  } catch (e) {
    consecutiveFails++;
    if (consecutiveFails === 3) {
      const el = document.getElementById('ips');
      if (el) el.textContent = 'servidor: ❌ sin conexión';
    }
  }
  setTimeout(poll, 1500);
}
poll();
