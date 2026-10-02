/*
  ══════════════════════════════════════════════════════════════
  VISTA-IA · ESP32-S3 CAM  (local: cámara + micrófono + altavoz)

  Placa Arduino IDE:
    Board:        ESP32S3 Dev Module
    PSRAM:        OPI PSRAM
    Flash Size:   16MB
    USB CDC On Boot: Enabled
    Partition:    Default 4MB with spiffs  (o "16M Flash (3MB APP/9.9MB FATFS)")

  Servicios (IP fija 192.168.1.10):
    :80 /capture y /stream  -> contrato original (gateway/vision)
    TCP cliente -> SERVER_IP:5002 (TTS/beeps) · UDP escucha :5003 (radio)
    http://192.168.1.10/          -> página de control
    http://192.168.1.10/capture   -> foto JPEG
    http://192.168.1.10:81/stream -> vídeo MJPEG
    http://192.168.1.10:82/mic    -> audio del micrófono (WAV en directo)
    http://192.168.1.10/beep      -> pitido de prueba por el altavoz
    POST http://192.168.1.10/play -> PCM s16le mono 16 kHz al altavoz

  Cableado (pines libres en las placas S3 CAM habituales):
    Altavoz MAX98357A:  BCLK=GPIO1  LRC=GPIO2  DIN=GPIO14
    Micrófono INMP441:  SCK=GPIO21  WS=GPIO47  SD=GPIO42  (L/R a GND)
  ══════════════════════════════════════════════════════════════
*/

#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiUdp.h>
#include <ArduinoOTA.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "driver/i2s.h"

// ══════════════════════════════════════════════════════════════
//  CONFIG
// ══════════════════════════════════════════════════════════════
const char* WIFI_SSID     = "Livebox6-BED3";
const char* WIFI_PASSWORD = "6VcbcXS54GPd";

#define USE_STATIC_IP true
IPAddress local_IP(192, 168, 1, 10);
IPAddress gateway_IP(192, 168, 1, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress dns1(8, 8, 8, 8);

const int SAMPLE_RATE = 16000;

// ── Módulos activables (pon a false uno para aislar un cuelgue) ──
#define ENABLE_CAMERA  true
#define ENABLE_SPK     true
#define ENABLE_MIC     true
#define ENABLE_OTA     true
#define ENABLE_GATEWAY true   // cliente TCP (TTS/beeps) + radio UDP, como el sketch original

// ── Gateway (debe coincidir con services.env) ──
const char* SERVER_IP         = "192.168.1.135";
const uint16_t TCP_SPK_PORT   = 5002;
const uint16_t UDP_RADIO_PORT = 5003;
WiFiUDP udpRadio;

// ── Altavoz (MAX98357A) ──
#define SPK_BCLK_PIN  1
#define SPK_LRC_PIN   2
#define SPK_DOUT_PIN  14

// ── Micrófono (INMP441) ──
#define MIC_SCK_PIN   21
#define MIC_WS_PIN    47
#define MIC_SD_PIN    42
#define MIC_SHIFT     14   // 16 = volumen normal, 14 = x4, 12 = x16

// ── Cámara: pinout estándar ESP32-S3 CAM (Freenove / GOOUUU / S3-EYE) ──
#define PWDN_GPIO_NUM    -1
#define RESET_GPIO_NUM   -1
#define XCLK_GPIO_NUM    15
#define SIOD_GPIO_NUM     4
#define SIOC_GPIO_NUM     5
#define Y9_GPIO_NUM      16
#define Y8_GPIO_NUM      17
#define Y7_GPIO_NUM      18
#define Y6_GPIO_NUM      12
#define Y5_GPIO_NUM      10
#define Y4_GPIO_NUM       8
#define Y3_GPIO_NUM       9
#define Y2_GPIO_NUM      11
#define VSYNC_GPIO_NUM    6
#define HREF_GPIO_NUM     7
#define PCLK_GPIO_NUM    13

httpd_handle_t srv_main   = NULL;   // :80
httpd_handle_t srv_stream = NULL;   // :81
httpd_handle_t srv_mic    = NULL;   // :82

bool camera_ok = false;

// ══════════════════════════════════════════════════════════════
//  I2S  (altavoz = I2S0 TX, micrófono = I2S1 RX)
// ══════════════════════════════════════════════════════════════
void spk_setup() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = true;

  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = SPK_BCLK_PIN;
  pins.ws_io_num = SPK_LRC_PIN;
  pins.data_out_num = SPK_DOUT_PIN;
  pins.data_in_num = I2S_PIN_NO_CHANGE;

  i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &pins);
  i2s_zero_dma_buffer(I2S_NUM_0);
}

void mic_setup() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 6;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;

  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = MIC_SCK_PIN;
  pins.ws_io_num = MIC_WS_PIN;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = MIC_SD_PIN;

  i2s_driver_install(I2S_NUM_1, &cfg, 0, NULL);
  i2s_set_pin(I2S_NUM_1, &pins);
  i2s_zero_dma_buffer(I2S_NUM_1);
}

// Reproduce n muestras mono 16-bit duplicándolas a estéreo
void spk_play_mono(const int16_t* mono, size_t n) {
  int16_t st[512];
  while (n > 0) {
    size_t k = n > 256 ? 256 : n;
    for (size_t i = 0; i < k; i++) {
      st[2 * i] = mono[i];
      st[2 * i + 1] = mono[i];
    }
    size_t written = 0;
    i2s_write(I2S_NUM_0, st, k * 4, &written, portMAX_DELAY);
    mono += k;
    n -= k;
  }
}

void spk_beep(int freq, int ms) {
  int16_t buf[256];
  size_t total = (size_t)SAMPLE_RATE * ms / 1000;
  size_t done = 0;
  while (done < total) {
    size_t k = min((size_t)256, total - done);
    for (size_t i = 0; i < k; i++) {
      float t = (float)(done + i) / SAMPLE_RATE;
      buf[i] = (int16_t)(8000.0f * sinf(2.0f * PI * freq * t));
    }
    spk_play_mono(buf, k);
    done += k;
  }
  i2s_zero_dma_buffer(I2S_NUM_0);
}

// ══════════════════════════════════════════════════════════════
//  CÁMARA
// ══════════════════════════════════════════════════════════════
bool camera_setup() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("❌ Error iniciando cámara: 0x%x\n", err);
    return false;
  }
  Serial.println("✅ Cámara lista");
  return true;
}

// ══════════════════════════════════════════════════════════════
//  HANDLERS HTTP
// ══════════════════════════════════════════════════════════════
static const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VISTA-IA</title>
<style>
body{font-family:sans-serif;background:#111;color:#eee;text-align:center;margin:0;padding:12px}
img{max-width:100%;border-radius:8px}
button{font-size:1rem;padding:10px 18px;margin:6px;border:0;border-radius:8px;background:#2d7ff9;color:#fff}
audio{width:100%;max-width:480px}
</style></head><body>
<h2>VISTA-IA · ESP32-S3</h2>
<img id="v" alt="video">
<div>
  <button onclick="document.getElementById('v').src='http://'+location.hostname+':81/stream'">▶ Vídeo</button>
  <button onclick="document.getElementById('v').src='/capture?'+Date.now()">📷 Foto</button>
  <button onclick="fetch('/beep')">🔊 Probar altavoz</button>
</div>
<div><p>Micrófono</p>
<button onclick="var a=document.getElementById('a');a.src='http://'+location.hostname+':82/mic';a.play()">🎤 Escuchar</button>
<button onclick="var a=document.getElementById('a');a.pause();a.removeAttribute('src');a.load()">⏹ Parar</button>
<br><audio id="a" controls></audio></div>
</body></html>
)rawliteral";

esp_err_t handle_index(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, INDEX_HTML, HTTPD_RESP_USE_STRLEN);
}

static void send_503(httpd_req_t *req) {
  httpd_resp_set_status(req, "503 Service Unavailable");
  httpd_resp_send(req, "Camara no disponible", HTTPD_RESP_USE_STRLEN);
}

esp_err_t handle_capture(httpd_req_t *req) {
  if (!camera_ok) {
    send_503(req);
    return ESP_FAIL;
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

esp_err_t handle_beep(httpd_req_t *req) {
  spk_beep(880, 500);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, "ok", HTTPD_RESP_USE_STRLEN);
}

// POST /play  -> cuerpo = PCM s16le mono 16 kHz
esp_err_t handle_play(httpd_req_t *req) {
  uint8_t buf[1024];
  size_t remaining = req->content_len;
  bool have_carry = false;
  uint8_t carry = 0;

  while (remaining > 0) {
    size_t off = 0;
    if (have_carry) { buf[0] = carry; off = 1; have_carry = false; }
    int n = httpd_req_recv(req, (char*)buf + off, min(remaining, sizeof(buf) - off));
    if (n <= 0) {
      if (n == HTTPD_SOCK_ERR_TIMEOUT) continue;
      return ESP_FAIL;
    }
    remaining -= n;
    size_t total = off + n;
    if (total & 1) { carry = buf[total - 1]; have_carry = true; total--; }
    spk_play_mono((const int16_t*)buf, total / 2);
  }
  i2s_zero_dma_buffer(I2S_NUM_0);
  return httpd_resp_send(req, "ok", HTTPD_RESP_USE_STRLEN);
}

// ── Stream de vídeo MJPEG (puerto 81) ──
#define PART_BOUNDARY "vistaframe"
static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

esp_err_t handle_stream(httpd_req_t *req) {
  if (!camera_ok) {
    send_503(req);
    return ESP_FAIL;
  }
  char part_buf[64];
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { res = ESP_FAIL; break; }

    res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) {
      size_t hlen = snprintf(part_buf, sizeof(part_buf), STREAM_PART, (unsigned)fb->len);
      res = httpd_resp_send_chunk(req, part_buf, hlen);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
    }
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  return res;
}

// ── Stream de micrófono WAV (puerto 82) ──
struct WavHeader {
  char riff[4] = {'R','I','F','F'};
  uint32_t size = 0xFFFFFFFF;
  char wave[4] = {'W','A','V','E'};
  char fmt[4]  = {'f','m','t',' '};
  uint32_t fmtSize = 16;
  uint16_t format = 1;
  uint16_t channels = 1;
  uint32_t rate = SAMPLE_RATE;
  uint32_t byteRate = SAMPLE_RATE * 2;
  uint16_t blockAlign = 2;
  uint16_t bits = 16;
  char data[4] = {'d','a','t','a'};
  uint32_t dataSize = 0xFFFFFFFF;
};

esp_err_t handle_mic(httpd_req_t *req) {
  httpd_resp_set_type(req, "audio/wav");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-cache");

  WavHeader hdr;
  esp_err_t res = httpd_resp_send_chunk(req, (const char*)&hdr, sizeof(hdr));

  static int32_t raw[256];
  static int16_t pcm[256];
  i2s_zero_dma_buffer(I2S_NUM_1);

  while (res == ESP_OK) {
    size_t bytes = 0;
    i2s_read(I2S_NUM_1, raw, sizeof(raw), &bytes, portMAX_DELAY);
    size_t n = bytes / 4;
    for (size_t i = 0; i < n; i++) {
      int32_t s = raw[i] >> MIC_SHIFT;
      if (s > 32767) s = 32767;
      if (s < -32768) s = -32768;
      pcm[i] = (int16_t)s;
    }
    res = httpd_resp_send_chunk(req, (const char*)pcm, n * 2);
  }
  return res;
}

void http_setup() {
  // ── Servidor principal :80 ──
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.ctrl_port = 32768;
  cfg.stack_size = 8192;
  cfg.max_uri_handlers = 8;
  cfg.lru_purge_enable = true;

  httpd_uri_t u_index   = { "/",        HTTP_GET,  handle_index,   NULL };
  httpd_uri_t u_capture = { "/capture", HTTP_GET,  handle_capture, NULL };
  httpd_uri_t u_beep    = { "/beep",    HTTP_GET,  handle_beep,    NULL };
  httpd_uri_t u_play    = { "/play",    HTTP_POST, handle_play,    NULL };
  httpd_uri_t u_stream80 = { "/stream", HTTP_GET,  handle_stream,  NULL };

  if (httpd_start(&srv_main, &cfg) == ESP_OK) {
    httpd_register_uri_handler(srv_main, &u_index);
    httpd_register_uri_handler(srv_main, &u_capture);
    httpd_register_uri_handler(srv_main, &u_beep);
    httpd_register_uri_handler(srv_main, &u_play);
    httpd_register_uri_handler(srv_main, &u_stream80);   // compatibilidad con el gateway
    Serial.println("✅ HTTP :80  (/, /capture, /beep, /play)");
  } else {
    Serial.println("❌ No arranca HTTP :80");
  }

  // ── Stream de vídeo :81 ──
  httpd_config_t cfg2 = HTTPD_DEFAULT_CONFIG();
  cfg2.server_port = 81;
  cfg2.ctrl_port = 32769;
  cfg2.stack_size = 8192;
  httpd_uri_t u_stream = { "/stream", HTTP_GET, handle_stream, NULL };
  if (httpd_start(&srv_stream, &cfg2) == ESP_OK) {
    httpd_register_uri_handler(srv_stream, &u_stream);
    Serial.println("✅ Vídeo :81/stream");
  } else {
    Serial.println("❌ No arranca HTTP :81");
  }

  // ── Micrófono :82 ──
  httpd_config_t cfg3 = HTTPD_DEFAULT_CONFIG();
  cfg3.server_port = 82;
  cfg3.ctrl_port = 32770;
  cfg3.stack_size = 8192;
  httpd_uri_t u_mic = { "/mic", HTTP_GET, handle_mic, NULL };
  if (httpd_start(&srv_mic, &cfg3) == ESP_OK) {
    httpd_register_uri_handler(srv_mic, &u_mic);
    Serial.println("✅ Micrófono :82/mic");
  } else {
    Serial.println("❌ No arranca HTTP :82");
  }
}

// ══════════════════════════════════════════════════════════════
//  GATEWAY — TCP (audio TTS/beeps) y UDP (radio)
// ══════════════════════════════════════════════════════════════
#if ENABLE_GATEWAY
static uint8_t tcp_buf[4096];     // estáticos: no caben en la pila de la tarea
static uint8_t udp_buf[2048];

// Protocolo: 4 bytes little-endian = longitud, luego PCM estéreo 16-bit @ SAMPLE_RATE
void tcp_audio_task(void *param) {
  WiFiClient client;
  for (;;) {
    if (!client.connected()) {
      Serial.printf("🔌 Conectando a gateway TCP %s:%d ...\n", SERVER_IP, TCP_SPK_PORT);
      if (client.connect(SERVER_IP, TCP_SPK_PORT)) {
        client.setNoDelay(true);
        Serial.println("✅ Conectado al gateway (altavoz)");
      } else {
        vTaskDelay(pdMS_TO_TICKS(2000));
        continue;
      }
    }

    if (client.available() >= 4) {
      uint8_t hdr[4];
      client.readBytes(hdr, 4);
      uint32_t remaining = hdr[0] | (hdr[1] << 8) | (hdr[2] << 16) | ((uint32_t)hdr[3] << 24);

      while (remaining > 0) {
        size_t chunk = min((uint32_t)sizeof(tcp_buf), remaining);
        int n = client.readBytes(tcp_buf, chunk);
        if (n <= 0) break;
        size_t written = 0;
        i2s_write(I2S_NUM_0, tcp_buf, n, &written, portMAX_DELAY);
        remaining -= n;
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }

    if (!client.connected()) client.stop();
  }
}

// Paquetes PCM MONO 16-bit sin cabecera; se duplican a estéreo
void udp_radio_task(void *param) {
  udpRadio.begin(UDP_RADIO_PORT);
  Serial.printf("✅ Radio UDP en puerto %d\n", UDP_RADIO_PORT);
  for (;;) {
    int packetSize = udpRadio.parsePacket();
    if (packetSize > 0) {
      int n = udpRadio.read(udp_buf, min((size_t)packetSize, sizeof(udp_buf)));
      if (n > 1) spk_play_mono((const int16_t*)udp_buf, n / 2);
    } else {
      vTaskDelay(pdMS_TO_TICKS(5));
    }
  }
}
#endif

// ══════════════════════════════════════════════════════════════
//  WIFI
// ══════════════════════════════════════════════════════════════
void wifi_setup() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);

#if USE_STATIC_IP
  if (!WiFi.config(local_IP, gateway_IP, subnet, dns1)) {
    Serial.println("⚠️ No se pudo fijar IP estática, se usará DHCP");
  }
#endif

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("📶 Conectando a %s", WIFI_SSID);
  int intentos = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
    if (++intentos > 60) {
      Serial.println("\n❌ Sin WiFi tras 24s, reiniciando...");
      ESP.restart();
    }
  }
  Serial.printf("\n✅ WiFi conectado. IP: %s\n", WiFi.localIP().toString().c_str());
}

// ══════════════════════════════════════════════════════════════
//  SETUP / LOOP
// ══════════════════════════════════════════════════════════════
static void step(const char* msg) {
  Serial.printf("[setup] %s\n", msg);
  Serial.flush();
  delay(100);
}

void setup() {
  Serial.begin(115200);
  delay(2000);   // tiempo para abrir el monitor serie (USB CDC)
  Serial.println("\n═══ VISTA-IA · ESP32-S3 CAM (local + OTA) ═══");
  Serial.printf("PSRAM: %s (%u bytes)\n", psramFound() ? "sí" : "NO", (unsigned)ESP.getPsramSize());

  step("1/7 WiFi");
  wifi_setup();

#if ENABLE_CAMERA
  step("2/7 camara");
  camera_ok = camera_setup();
  if (!camera_ok) Serial.println("⚠️ Sigo sin cámara: revisa el pinout de tu placa");
#else
  camera_ok = false;
#endif

#if ENABLE_SPK
  step("3/7 altavoz I2S");
  spk_setup();
#endif

#if ENABLE_MIC
  step("4/7 microfono I2S");
  mic_setup();
#endif

  step("5/7 servidores HTTP");
  http_setup();

#if ENABLE_OTA
  step("6/7 OTA");
  ArduinoOTA.setHostname("vista-ia");
  ArduinoOTA.begin();
#endif

#if ENABLE_GATEWAY && ENABLE_SPK
  step("7/7 gateway TCP/UDP");
  xTaskCreatePinnedToCore(tcp_audio_task, "tcp_audio", 6144, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(udp_radio_task, "udp_radio", 4096, NULL, 1, NULL, 1);
#endif

  Serial.println("✅ Lista. Abre http://" + WiFi.localIP().toString() + "/");
}

void loop() {
  ArduinoOTA.handle();

  static uint32_t lastCheck = 0;
  if (millis() - lastCheck > 10000) {
    lastCheck = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("⚠️ WiFi caída, reconectando...");
      WiFi.disconnect();
      WiFi.reconnect();
    }
  }
  delay(10);
}
