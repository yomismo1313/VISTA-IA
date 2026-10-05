// ══════════════════════════════════════════════════════════════
//  AÑADIDO: WAKE WORD + ENVÍO DE FRASE AL SERVIDOR STT :5010
// ══════════════════════════════════════════════════════════════
const char* STT_SERVER_IP  = "192.168.1.147";   // IP del PC con stt_server.py
const uint16_t STT_PORT    = 5010;

// Duración de la frase que enviamos al servidor tras detectar voz
const uint32_t FRASE_MS       = 5000;   // 5 s
const uint32_t PRE_ROLL_MS    = 400;    // audio previo que incluimos

// VAD por energía: umbral y tiempo mínimo de voz para disparar
const float    VAD_RMS_THRESH = 0.020f; // sobre señal ya convertida a float
const uint32_t VAD_MIN_MS     = 250;    // al menos 250 ms de voz sostenida

// Buffer circular de pre-roll (INMP441 → int16)
#define PREROLL_SAMPLES  (SAMPLE_RATE * PRE_ROLL_MS / 1000)
static int16_t preroll_buf[PREROLL_SAMPLES];
static volatile size_t preroll_idx = 0;
static volatile size_t preroll_len = 0;

// Cola de "frases a enviar" (protegida por mutex)
static portMUX_TYPE frase_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool frase_pendiente = false;

// Convierte int32 crudo del INMP441 a int16 ya desplazado
static inline int16_t mic_raw_a_i16(int32_t s) {
  s >>= MIC_SHIFT;
  if (s > 32767)  s = 32767;
  if (s < -32768) s = -32768;
  return (int16_t)s;
}

// Lee del I2S1 (mic), devuelve el nº de muestras int16 en 'out'
static size_t mic_leer(int16_t* out, size_t max_n) {
  static int32_t raw[256];
  size_t total = 0;
  while (total < max_n) {
    size_t want = min((size_t)256, max_n - total);
    size_t bytes = 0;
    i2s_read(I2S_NUM_1, raw, want * 4, &bytes, portMAX_DELAY);
    size_t n = bytes / 4;
    if (n == 0) break;
    for (size_t i = 0; i < n; i++) out[total + i] = mic_raw_a_i16(raw[i]);
    total += n;
  }
  return total;
}

// Tarea: VAD continuo, guarda pre-roll, dispara envío al detectar voz
void mic_wake_task(void *param) {
  static int16_t tmp[256];
  uint32_t voz_inicio_ms = 0;
  bool     voz_activa    = false;

  for (;;) {
    size_t n = mic_leer(tmp, sizeof(tmp) / sizeof(tmp[0]));
    if (n == 0) { vTaskDelay(pdMS_TO_TICKS(5)); continue; }

    // Actualiza pre-roll circular
    for (size_t i = 0; i < n; i++) {
      preroll_buf[preroll_idx] = tmp[i];
      preroll_idx = (preroll_idx + 1) % PREROLL_SAMPLES;
      if (preroll_len < PREROLL_SAMPLES) preroll_len++;
    }

    // RMS del bloque (float)
    float acc = 0.0f;
    for (size_t i = 0; i < n; i++) {
      float f = tmp[i] / 32768.0f;
      acc += f * f;
    }
    float rms = sqrtf(acc / n);

    uint32_t now = millis();

    if (rms > VAD_RMS_THRESH) {
      if (!voz_activa) {
        voz_activa = true;
        voz_inicio_ms = now;
      } else if (now - voz_inicio_ms >= VAD_MIN_MS) {
        // Voz sostenida → dispara una frase
        if (!frase_pendiente) {
          portENTER_CRITICAL(&frase_mux);
          frase_pendiente = true;
          portEXIT_CRITICAL(&frase_mux);
          Serial.printf("🎤 Voz detectada (rms=%.3f) → capturando %u ms\n",
                        rms, FRASE_MS);
        }
        voz_activa = false;   // evita disparos repetidos dentro de la misma frase
      }
    } else {
      voz_activa = false;
    }

    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// Tarea: cuando hay frase pendiente, abre TCP y manda pre-roll + 5 s de audio
void frase_sender_task(void *param) {
  static int16_t cap[512];
  for (;;) {
    if (!frase_pendiente) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }

    // Copia pre-roll
    int16_t pre[PREROLL_SAMPLES];
    size_t pre_n = 0;
    noInterrupts();
    size_t idx = preroll_idx, len = preroll_len;
    interrupts();
    if (len > 0) {
      size_t start = (idx + PREROLL_SAMPLES - len) % PREROLL_SAMPLES;
      for (size_t i = 0; i < len; i++) {
        pre[i] = preroll_buf[(start + i) % PREROLL_SAMPLES];
      }
      pre_n = len;
    }

    WiFiClient cli;
    Serial.printf("📡 Conectando STT %s:%u ...\n", STT_SERVER_IP, STT_PORT);
    if (!cli.connect(STT_SERVER_IP, STT_PORT, 3000)) {
      Serial.println("   ✗ STT no disponible");
      portENTER_CRITICAL(&frase_mux);
      frase_pendiente = false;
      portEXIT_CRITICAL(&frase_mux);
      vTaskDelay(pdMS_TO_TICKS(2000));
      continue;
    }
    cli.setNoDelay(true);

    // 1) Envía pre-roll
    if (pre_n > 0) cli.write((const uint8_t*)pre, pre_n * 2);

    // 2) Captura FRASE_MS de audio en directo y lo envía
    uint32_t t0 = millis();
    while (millis() - t0 < FRASE_MS) {
      size_t n = mic_leer(cap, sizeof(cap) / sizeof(cap[0]));
      if (n > 0) cli.write((const uint8_t*)cap, n * 2);
    }

    cli.stop();
    Serial.println("📤 Frase enviada al STT");

    portENTER_CRITICAL(&frase_mux);
    frase_pendiente = false;
    portEXIT_CRITICAL(&frase_mux);

    vTaskDelay(pdMS_TO_TICKS(300));
  }
}
