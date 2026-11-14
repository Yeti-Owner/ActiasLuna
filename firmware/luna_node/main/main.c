#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"

#include "luna.h"
#include "luna_secrets.h"

// Built twice: LUNA_ROLE_AP=1 runs the soft-AP (and still listens + serves the portal), 0 is a receiver station.
// Privacy: only 802.11 header fields plus RSSI/CSI are touched. Payload bytes are never parsed or forwarded.
// The UDP JSON written here is parsed by server/tracker.py and faked by tools/sim_nodes.py: change all together.

#define FW_VERSION "1.0.0"
#define WINDOW_MS 250
#define MAX_SENDERS 24
#define MAX_EVENTS 8
#define MAX_PEERS 4
#define PKT_BYTES 1400
#define SUB_TTL_US (30LL * 1000 * 1000)
#define REJOIN_US (1000 * 1000)
#define STATUS_LOG_US (2LL * 1000 * 1000)
#define STA_LIST_US (1000 * 1000)
// LLTF CSI: 64 subcarriers. Last-frame amplitudes are kept for this many senders to compute frame-to-frame change.
#define CSI_SUBCARRIERS 64
#define CSI_HISTORY 8
// Device-free mode: each board pings every peer at PING_RATE_HZ. Link CSI is folded into SHAPE_BINS bins of
// 4 usable subcarriers each; server/sensing.py N_BINS must equal SHAPE_BINS.
#define PING_RATE_HZ 40
#define SHAPE_BINS 13
// 1 dB RSSI histogram from RSSI_FLOOR dBm, used for the upper-quartile field "q3".
#define RSSI_FLOOR (-100)
#define RSSI_BUCKETS 80

static const char *TAG = "luna";

typedef struct {
    uint8_t mac[6];
    uint16_t frames, data, mgmt, ctrl;
    int32_t rssi_sum, rssi_sq;
    int8_t rssi_min, rssi_max;
    uint8_t buckets[RSSI_BUCKETS];
    uint16_t csi_frames, csi_deltas;
    uint32_t csi_amp_q4, csi_delta_q4;
    uint16_t shape_frames;
    uint32_t shape[SHAPE_BINS];
} sender_t;

typedef struct {
    uint8_t mac[6];
    bool joined;
    uint8_t aid;
    uint16_t reason;
} event_t;

typedef struct {
    bool used;
    uint8_t mac[6];
    uint8_t count;
    uint8_t amp[CSI_SUBCARRIERS];
} csi_last_t;

typedef struct {
    uint8_t mac[6];
    uint32_t frames, ctrl;
    int32_t rssi_sum;
} log_row_t;

static portMUX_TYPE g_lock = portMUX_INITIALIZER_UNLOCKED;
static sender_t g_senders[MAX_SENDERS];
static int g_sender_count;
static uint32_t g_overflow;
static event_t g_events[MAX_EVENTS];
static int g_event_count;

// "id" = factory base MAC (what esptool prints, used in config/boards.json). "ifm" = MAC actually used on air
// (soft-AP is base+1); the server matches ifm to tell board-to-board link frames apart from phones.
static uint8_t g_base_mac[6];
static uint8_t g_air_mac[6];
static uint8_t g_bssid[6];
static volatile bool g_bssid_known;

static volatile bool g_csi_enabled;
static volatile bool g_sensing;
static uint32_t g_peer_ip[MAX_PEERS];
static volatile int g_peer_count;
static uint8_t g_link_mac[MAX_PEERS][6];
static volatile int g_link_count;

static csi_last_t g_csi_last[CSI_HISTORY];
static int g_csi_next;

static esp_netif_t *g_netif;
static esp_timer_handle_t g_rejoin_timer;
static volatile uint32_t g_disconnects;

static int g_sock = -1;
static int g_ping_sock = -1;
static struct sockaddr_in g_subscriber;
static int64_t g_sub_at_us = -1;
static uint32_t g_seq, g_tx_ok, g_tx_fail;

static log_row_t g_log[MAX_SENDERS];
static int g_log_count;

static inline bool same_mac(const uint8_t *a, const uint8_t *b) { return memcmp(a, b, 6) == 0; }

static void hex_mac(const uint8_t *mac, char out[13]) {
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < 6; i++) {
        out[i * 2] = digits[mac[i] >> 4];
        out[i * 2 + 1] = digits[mac[i] & 0x0f];
    }
    out[12] = '\0';
}

static sender_t *find_sender(const uint8_t *mac) {
    for (int i = 0; i < g_sender_count; i++)
        if (same_mac(g_senders[i].mac, mac)) return &g_senders[i];
    return NULL;
}

static sender_t *sender_slot(const uint8_t *mac) {
    sender_t *s = find_sender(mac);
    if (s) return s;
    if (g_sender_count == MAX_SENDERS) {
        g_overflow++;
        return NULL;
    }
    s = &g_senders[g_sender_count++];
    memset(s, 0, sizeof(*s));
    memcpy(s->mac, mac, 6);
    s->rssi_min = INT8_MAX;
    s->rssi_max = INT8_MIN;
    return s;
}

static bool is_link_peer(const uint8_t *mac) {
    for (int i = 0; i < g_link_count; i++)
        if (same_mac(g_link_mac[i], mac)) return true;
    return false;
}

// Called from the Wi-Fi driver task for each frame, so it must stay fast: no logs, no blocking calls.
// Admitted frames (transmitter = addr2), all addressed to our BSSID:
//   mgmt: BSSID in addr3;  data ToDS=1/FromDS=0: BSSID in addr1;
//   control RTS / PS-Poll / BlockAckReq / BlockAck with RA = BSSID (ACK and CTS carry no TA and are filtered out).
// Everything for foreign networks is dropped here. When sensing, frames sent by the AP itself (beacons, downlink
// data with FromDS=1) are also admitted: that is the AP -> this board link.
static void on_frame(void *buf, wifi_promiscuous_pkt_type_t kind) {
    if (!g_bssid_known) return;
    const wifi_promiscuous_pkt_t *pkt = (const wifi_promiscuous_pkt_t *)buf;
    const uint8_t *hdr = pkt->payload;
    const unsigned len = pkt->rx_ctrl.sig_len;
    const uint8_t type = (hdr[0] >> 2) & 0x3;
    const uint8_t ds = hdr[1] & 0x3;
    const uint8_t *addr1 = hdr + 4;
    const uint8_t *tx = hdr + 10;
    const uint8_t *bss;
    uint8_t ctrl_ta[6];

    if (kind == WIFI_PKT_MGMT && type == 0) {
        if (len < 24) return;
        bss = hdr + 16;
    } else if (kind == WIFI_PKT_DATA && type == 2) {
        if (len < 24) return;
        if (ds == 0x1) bss = addr1;
        else if (ds == 0x2) bss = tx;
        else return;
    } else if (kind == WIFI_PKT_CTRL && type == 1) {
        uint8_t subtype = (hdr[0] >> 4) & 0xf;
        if (subtype < 8 || subtype > 11 || len < 16) return;
        // In control frames bit 0 of the TA signals bandwidth, it is not the group bit.
        memcpy(ctrl_ta, tx, 6);
        ctrl_ta[0] &= 0xfe;
        tx = ctrl_ta;
        bss = addr1;
    } else {
        return;
    }
    if (tx[0] & 0x01) return;
    if (!same_mac(bss, g_bssid) || same_mac(tx, g_air_mac)) return;
    if (!g_sensing && same_mac(tx, g_bssid)) return;

    const int8_t rssi = pkt->rx_ctrl.rssi;
    int bucket = rssi - RSSI_FLOOR;
    if (bucket < 0) bucket = 0;
    if (bucket >= RSSI_BUCKETS) bucket = RSSI_BUCKETS - 1;

    portENTER_CRITICAL(&g_lock);
    sender_t *s = sender_slot(tx);
    if (s && s->frames < UINT16_MAX) {
        s->frames++;
        s->rssi_sum += rssi;
        s->rssi_sq += (int32_t)rssi * rssi;
        if (rssi < s->rssi_min) s->rssi_min = rssi;
        if (rssi > s->rssi_max) s->rssi_max = rssi;
        if (s->buckets[bucket] < UINT8_MAX) s->buckets[bucket]++;
        if (type == 2) s->data++;
        else if (type == 1) s->ctrl++;
        else s->mgmt++;
    }
    portEXIT_CRITICAL(&g_lock);
}

static csi_last_t *csi_history_for(const uint8_t *mac, bool *fresh) {
    for (int i = 0; i < CSI_HISTORY; i++)
        if (g_csi_last[i].used && same_mac(g_csi_last[i].mac, mac)) {
            *fresh = false;
            return &g_csi_last[i];
        }
    csi_last_t *slot = &g_csi_last[g_csi_next];
    g_csi_next = (g_csi_next + 1) % CSI_HISTORY;
    memcpy(slot->mac, mac, 6);
    slot->used = true;
    slot->count = 0;
    *fresh = true;
    return slot;
}

// Only active when the server asks (csi=1 or sense=1). The buffer holds int8 (imag, real) pairs per subcarrier.
// Senders not already admitted by on_frame() this window are ignored.
static void on_csi(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf || info->len < 16) return;
    portENTER_CRITICAL(&g_lock);
    bool admitted = find_sender(info->mac) != NULL;
    portEXIT_CRITICAL(&g_lock);
    if (!admitted) return;

    int count = info->len / 2;
    if (count > CSI_SUBCARRIERS) count = CSI_SUBCARRIERS;
    uint8_t amp[CSI_SUBCARRIERS];
    uint32_t total = 0;
    for (int k = 0; k < count; k++) {
        int im = info->buf[2 * k];
        int re = info->buf[2 * k + 1];
        float a = sqrtf((float)(im * im + re * re));
        amp[k] = a >= 255.f ? 255 : (uint8_t)a;
        total += amp[k];
    }
    const uint32_t mean_q4 = (total << 4) / (uint32_t)count;

    // LLTF buffer order is subcarrier 0..31 then -32..-1. Usable ones in rising frequency: -26..-1 sit at
    // indices 38..63, then +1..+26 at indices 1..26.
    uint32_t shape[SHAPE_BINS] = {0};
    const bool link = count == CSI_SUBCARRIERS && is_link_peer(info->mac);
    if (link)
        for (int j = 0; j < 4 * SHAPE_BINS; j++) shape[j / 4] += amp[j < 26 ? 38 + j : j - 25];

    bool fresh;
    csi_last_t *last = csi_history_for(info->mac, &fresh);
    bool have_delta = !fresh && last->count == count;
    uint32_t delta_q4 = 0;
    if (have_delta) {
        uint32_t diff = 0;
        for (int k = 0; k < count; k++) diff += (uint32_t)abs((int)amp[k] - (int)last->amp[k]);
        delta_q4 = (diff << 4) / (uint32_t)count;
    }
    memcpy(last->amp, amp, count);
    last->count = (uint8_t)count;

    portENTER_CRITICAL(&g_lock);
    sender_t *s = find_sender(info->mac);
    if (s) {
        s->csi_frames++;
        s->csi_amp_q4 += mean_q4;
        if (have_delta) {
            s->csi_deltas++;
            s->csi_delta_q4 += delta_q4;
        }
        if (link && s->shape_frames < UINT16_MAX) {
            s->shape_frames++;
            for (int b = 0; b < SHAPE_BINS; b++) s->shape[b] += shape[b];
        }
    }
    portEXIT_CRITICAL(&g_lock);
}

static void csi_enable(bool want) {
    if (want == g_csi_enabled) return;
    if (!want) {
        esp_wifi_set_csi(false);
        g_csi_enabled = false;
        ESP_LOGI(TAG, "CSI off");
        return;
    }
    wifi_csi_config_t cfg = {
        .lltf_en = true,
        .htltf_en = false,
        .stbc_htltf2_en = false,
        .ltf_merge_en = false,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
    };
    esp_err_t err = esp_wifi_set_csi_config(&cfg);
    if (err == ESP_OK) err = esp_wifi_set_csi_rx_cb(on_csi, NULL);
    if (err == ESP_OK) err = esp_wifi_set_csi(true);
    g_csi_enabled = err == ESP_OK;
    ESP_LOGI(TAG, "CSI on: %s", esp_err_to_name(err));
}

static void queue_event(const uint8_t *mac, bool joined, uint8_t aid, uint16_t reason) {
    portENTER_CRITICAL(&g_lock);
    if (g_event_count < MAX_EVENTS) {
        event_t *e = &g_events[g_event_count++];
        memcpy(e->mac, mac, 6);
        e->joined = joined;
        e->aid = aid;
        e->reason = reason;
    }
    portEXIT_CRITICAL(&g_lock);
}

#if !LUNA_ROLE_AP
static void rejoin(void *arg) { esp_wifi_connect(); }
#endif

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == IP_EVENT) {
        if (id == IP_EVENT_STA_GOT_IP) {
            ip_event_got_ip_t *e = data;
            ESP_LOGI(TAG, "ip " IPSTR, IP2STR(&e->ip_info.ip));
        } else if (id == IP_EVENT_AP_STAIPASSIGNED) {
            ip_event_ap_staipassigned_t *e = data;
            ESP_LOGI(TAG, "dhcp " MACSTR " = " IPSTR, MAC2STR(e->mac), IP2STR(&e->ip));
        }
        return;
    }
    if (base != WIFI_EVENT) return;
    if (id == WIFI_EVENT_AP_STACONNECTED) {
        wifi_event_ap_staconnected_t *e = data;
        ESP_LOGI(TAG, "join " MACSTR " aid=%d", MAC2STR(e->mac), e->aid);
        queue_event(e->mac, true, e->aid, 0);
    } else if (id == WIFI_EVENT_AP_STADISCONNECTED) {
        wifi_event_ap_stadisconnected_t *e = data;
        ESP_LOGI(TAG, "leave " MACSTR " aid=%d reason=%d", MAC2STR(e->mac), e->aid, e->reason);
        queue_event(e->mac, false, e->aid, e->reason);
    } else if (id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (id == WIFI_EVENT_STA_CONNECTED) {
        wifi_event_sta_connected_t *e = data;
        memcpy(g_bssid, e->bssid, 6);
        g_bssid_known = true;
        // With modem sleep the receiver dozes between beacons and misses most of the phone's uplink frames.
        esp_wifi_set_ps(WIFI_PS_NONE);
        ESP_LOGI(TAG, "associated " MACSTR " ch=%d", MAC2STR(e->bssid), e->channel);
    } else if (id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *e = data;
        g_disconnects++;
        ESP_LOGW(TAG, "lost AP reason=%d rssi=%d, rejoining", e->reason, e->rssi);
        if (g_rejoin_timer) {
            esp_timer_stop(g_rejoin_timer);
            esp_timer_start_once(g_rejoin_timer, REJOIN_US);
        }
    }
}

static void start_wifi(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    wifi_config_t cfg = {0};
#if LUNA_ROLE_AP
    g_netif = esp_netif_create_default_wifi_ap();
#else
    g_netif = esp_netif_create_default_wifi_sta();
    const esp_timer_create_args_t timer_args = {.callback = rejoin, .name = "rejoin"};
    ESP_ERROR_CHECK(esp_timer_create(&timer_args, &g_rejoin_timer));
#endif
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

#if LUNA_ROLE_AP
    cfg.ap.ssid_len = strlen(LUNA_SSID);
    memcpy(cfg.ap.ssid, LUNA_SSID, cfg.ap.ssid_len);
    strncpy((char *)cfg.ap.password, LUNA_PASS, sizeof(cfg.ap.password) - 1);
    cfg.ap.channel = LUNA_CHANNEL;
    cfg.ap.max_connection = LUNA_MAX_STA;
    cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    cfg.ap.pairwise_cipher = WIFI_CIPHER_TYPE_CCMP;
    cfg.ap.pmf_cfg.required = false;
    cfg.ap.beacon_interval = 100;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &cfg));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_AP, WIFI_BW_HT20));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_AP, g_air_mac));
    memcpy(g_bssid, g_air_mac, 6);
    g_bssid_known = true;
#else
    memcpy(cfg.sta.ssid, LUNA_SSID, strlen(LUNA_SSID));
    strncpy((char *)cfg.sta.password, LUNA_PASS, sizeof(cfg.sta.password) - 1);
    cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    cfg.sta.channel = LUNA_CHANNEL;
    cfg.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    cfg.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, g_air_mac));
    esp_wifi_set_ps(WIFI_PS_NONE);
#endif

    wifi_promiscuous_filter_t frames = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA | WIFI_PROMIS_FILTER_MASK_CTRL};
    // Keep only control subtypes that carry a transmitter; ACK/CTS dominate control traffic and would waste CPU.
    wifi_promiscuous_filter_t ctrl = {
        .filter_mask = WIFI_PROMIS_CTRL_FILTER_MASK_RTS | WIFI_PROMIS_CTRL_FILTER_MASK_PSPOLL |
                       WIFI_PROMIS_CTRL_FILTER_MASK_BA | WIFI_PROMIS_CTRL_FILTER_MASK_BAR};
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&frames));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_ctrl_filter(&ctrl));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(on_frame));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
}

static bool subscribed(void) { return g_sub_at_us > 0 && esp_timer_get_time() - g_sub_at_us < SUB_TTL_US; }

static void transmit(const char *buf, int len) {
    if (g_sock < 0 || len <= 0) return;
    if (sendto(g_sock, buf, len, 0, (struct sockaddr *)&g_subscriber, sizeof(g_subscriber)) < 0) g_tx_fail++;
    else g_tx_ok++;
}

// Bounded printf-append into a packet buffer. Output that would not fit is cut at cap-1 instead of overflowing,
// so every packet write must go through here.
static int append(char *buf, int len, int cap, const char *fmt, ...) {
    if (len >= cap - 1) return len;
    va_list args;
    va_start(args, fmt);
    int wrote = vsnprintf(buf + len, cap - len, fmt, args);
    va_end(args);
    if (wrote < 0) return len;
    return len + wrote >= cap ? cap - 1 : len + wrote;
}

// seq advances once per packet built; the server treats gaps in seq as lost packets.
static int begin_packet(char *buf, int cap, const char *type) {
    char id[13], air[13];
    hex_mac(g_base_mac, id);
    hex_mac(g_air_mac, air);
    return append(buf, 0, cap, "{\"v\":1,\"t\":\"%s\",\"role\":\"%s\",\"id\":\"%s\",\"ifm\":\"%s\",\"seq\":%lu,\"up\":%llu", type,
                  LUNA_ROLE_AP ? "ap" : "rx", id, air, (unsigned long)g_seq++,
                  (unsigned long long)(esp_timer_get_time() / 1000));
}

static int parse_peer_ips(const char *p) {
    int n = 0;
    while (n < MAX_PEERS && *p && *p != ' ') {
        unsigned a, b, c, d;
        if (sscanf(p, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) break;
        g_peer_ip[n++] = htonl((a << 24) | (b << 16) | (c << 8) | d);
        p += strcspn(p, ", ");
        if (*p == ',') p++;
    }
    return n;
}

static int parse_link_macs(const char *p) {
    int n = 0;
    while (n < MAX_PEERS && strlen(p) >= 12) {
        uint8_t mac[6];
        for (int i = 0; i < 6; i++) {
            unsigned byte;
            if (sscanf(p + 2 * i, "%2x", &byte) != 1) return n;
            mac[i] = (uint8_t)byte;
        }
        portENTER_CRITICAL(&g_lock);
        memcpy(g_link_mac[n], mac, 6);
        portEXIT_CRITICAL(&g_lock);
        n++;
        p += 12;
        if (*p != ',') break;
        p++;
    }
    return n;
}

// "peers=ip,ip" and "lk=mac,mac" from the LUNA_SUB line written by server/luna_server.py (subscribe_loop).
static void apply_subscription(const char *msg) {
    const char *peers = strstr(msg, "peers=");
    g_peer_count = peers ? parse_peer_ips(peers + 6) : 0;
    const char *links = strstr(msg, "lk=");
    g_link_count = links ? parse_link_macs(links + 3) : 0;
    g_sensing = strstr(msg, "sense=1") != NULL;
    csi_enable(g_sensing || strstr(msg, "csi=1") != NULL);
}

// The laptop broadcasts "LUNA_SUB v1 csi=0|1 sense=0|1 peers=.. lk=.." each second. We answer to its source
// address and port: replies to the laptop's own outbound traffic get through Windows Firewall with no rule.
// A subscription lapses after 30 s of silence.
static void check_subscribers(void) {
    char msg[320];
    for (;;) {
        struct sockaddr_in from;
        socklen_t from_len = sizeof(from);
        int n = recvfrom(g_sock, msg, sizeof(msg) - 1, MSG_DONTWAIT, (struct sockaddr *)&from, &from_len);
        if (n <= 0) return;
        msg[n] = '\0';
        if (strncmp(msg, "LUNA_SUB", 8) != 0) continue;
        bool had = subscribed();
        g_subscriber = from;
        g_sub_at_us = esp_timer_get_time();
        apply_subscription(msg);
        if (!had) ESP_LOGI(TAG, "subscriber %s:%d", inet_ntoa(from.sin_addr), ntohs(from.sin_port));
        char out[300];
        int len = begin_packet(out, sizeof(out), "hi");
        len = append(out, len, sizeof(out), ",\"fw\":\"%s\",\"ssid\":\"%s\",\"ch\":%d,\"csi\":%d,\"sense\":%d}", FW_VERSION,
                     LUNA_SSID, LUNA_CHANNEL, g_csi_enabled ? 1 : 0, g_sensing ? 1 : 0);
        transmit(out, len);
    }
}

#if LUNA_ROLE_AP
static void report_stations(const wifi_sta_list_t *list) {
    esp_netif_pair_mac_ip_t pairs[ESP_WIFI_MAX_CONN_NUM];
    int count = list->num < ESP_WIFI_MAX_CONN_NUM ? list->num : ESP_WIFI_MAX_CONN_NUM;
    for (int i = 0; i < count; i++) {
        memcpy(pairs[i].mac, list->sta[i].mac, 6);
        pairs[i].ip.addr = 0;
    }
    if (count) esp_netif_dhcps_get_clients_by_mac(g_netif, count, pairs);
    static char out[PKT_BYTES];
    int len = begin_packet(out, sizeof(out), "sta");
    len = append(out, len, sizeof(out), ",\"http\":%lu,\"sta\":[", (unsigned long)luna_portal_hits());
    for (int i = 0; i < count && len < (int)sizeof(out) - 140; i++) {
        char mac[13], nick[20];
        hex_mac(list->sta[i].mac, mac);
        luna_portal_nickname(list->sta[i].mac, nick, sizeof(nick));
        len = append(out, len, sizeof(out), "%s{\"m\":\"%s\",\"ip\":\"" IPSTR "\",\"r\":%d,\"nm\":\"%s\"}", i ? "," : "",
                     mac, IP2STR(&pairs[i].ip), list->sta[i].rssi, nick);
    }
    len = append(out, len, sizeof(out), "]}");
    transmit(out, len);
}
#endif

static void log_window(const sender_t *s) {
    log_row_t *row = NULL;
    for (int i = 0; i < g_log_count && !row; i++)
        if (same_mac(g_log[i].mac, s->mac)) row = &g_log[i];
    if (!row) {
        if (g_log_count == MAX_SENDERS) return;
        row = &g_log[g_log_count++];
        memset(row, 0, sizeof(*row));
        memcpy(row->mac, s->mac, 6);
    }
    row->frames += s->frames;
    row->ctrl += s->ctrl;
    row->rssi_sum += s->rssi_sum;
}

static int upper_quartile(const sender_t *s) {
    int need = (3 * s->frames + 3) / 4, seen = 0;
    for (int b = 0; b < RSSI_BUCKETS; b++) {
        seen += s->buckets[b];
        if (seen >= need) return RSSI_FLOOR + b;
    }
    return s->rssi_max;
}

static int append_sender(char *buf, int len, int cap, const sender_t *s, bool first) {
    char mac[13];
    hex_mac(s->mac, mac);
    float mean = (float)s->rssi_sum / s->frames;
    float var = (float)s->rssi_sq / s->frames - mean * mean;
    len = append(buf, len, cap,
                 "%s{\"m\":\"%s\",\"n\":%u,\"r\":%.1f,\"sd\":%.2f,\"q3\":%d,\"mn\":%d,\"mx\":%d,\"dt\":%u,\"mg\":%u,\"ct\":%u",
                 first ? "" : ",", mac, s->frames, mean, var > 0 ? sqrtf(var) : 0.f, upper_quartile(s), s->rssi_min,
                 s->rssi_max, s->data, s->mgmt, s->ctrl);
    if (s->csi_frames)
        len = append(buf, len, cap, ",\"cn\":%u,\"ca\":%.2f,\"cm\":%.2f", s->csi_frames,
                     s->csi_amp_q4 / 16.0f / s->csi_frames,
                     s->csi_deltas ? s->csi_delta_q4 / 16.0f / s->csi_deltas : 0.0f);
    if (s->shape_frames) {
        len = append(buf, len, cap, ",\"cs\":[");
        for (int b = 0; b < SHAPE_BINS; b++)
            len = append(buf, len, cap, "%s%lu", b ? "," : "", (unsigned long)(s->shape[b] / 4 / s->shape_frames));
        len = append(buf, len, cap, "]");
    }
    return append(buf, len, cap, "}");
}

// One "tel" per window, split into numbered parts when the sender list overflows a datagram. An empty list is still
// sent as the heartbeat. Per-window board values (aprssi) are only meaningful on part 0.
static void report_window(const sender_t *senders, int count, const event_t *events, int event_count, uint32_t overflow,
                          int ap_rssi) {
    static char out[PKT_BYTES];
    for (int i = 0; i < event_count; i++) {
        char mac[13];
        hex_mac(events[i].mac, mac);
        int len = begin_packet(out, sizeof(out), "ev");
        len = append(out, len, sizeof(out), ",\"e\":\"%s\",\"m\":\"%s\",\"aid\":%d,\"reason\":%d}",
                     events[i].joined ? "join" : "leave", mac, events[i].aid, events[i].reason);
        transmit(out, len);
    }
    int next = 0, part = 0;
    do {
        int len = begin_packet(out, sizeof(out), "tel");
        len = append(out, len, sizeof(out),
                     ",\"part\":%d,\"win\":%d,\"ch\":%d,\"aprssi\":%d,\"ovf\":%lu,\"disc\":%lu,\"heap\":%lu,\"csi\":%d,\"obs\":[",
                     part++, WINDOW_MS, LUNA_CHANNEL, ap_rssi, (unsigned long)overflow, (unsigned long)g_disconnects,
                     (unsigned long)esp_get_free_heap_size(), g_csi_enabled ? 1 : 0);
        bool first = true;
        for (; next < count && len < (int)sizeof(out) - 300; next++) {
            if (!senders[next].frames) continue;
            len = append_sender(out, len, sizeof(out), &senders[next], first);
            first = false;
        }
        len = append(out, len, sizeof(out), "]}");
        transmit(out, len);
    } while (next < count);
}

static void print_status(int64_t now, uint32_t overflow, int ap_rssi, bool sub) {
    ESP_LOGI(TAG, "[%s] up=%llus sub=%d tx=%lu fail=%lu ovf=%lu disc=%lu aprssi=%d heap=%lu", LUNA_ROLE_AP ? "AP" : "RX",
             (unsigned long long)(now / 1000000), sub, (unsigned long)g_tx_ok, (unsigned long)g_tx_fail,
             (unsigned long)overflow, (unsigned long)g_disconnects, ap_rssi, (unsigned long)esp_get_free_heap_size());
    for (int i = 0; i < g_log_count; i++) {
        const log_row_t *row = &g_log[i];
        ESP_LOGI(TAG, "  heard " MACSTR " %lu frames/2s (%lu ctrl) avg %.1f dBm", MAC2STR(row->mac),
                 (unsigned long)row->frames, (unsigned long)row->ctrl,
                 row->frames ? (float)row->rssi_sum / row->frames : 0.f);
    }
    g_log_count = 0;
}

static void window_task(void *arg) {
    static sender_t taken[MAX_SENDERS];
    event_t events[MAX_EVENTS];
    TickType_t wake = xTaskGetTickCount();
    int64_t logged_at = 0;
#if LUNA_ROLE_AP
    int64_t listed_at = 0;
    static wifi_sta_list_t stations;
#endif
    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(WINDOW_MS));
        const int64_t now = esp_timer_get_time();
        check_subscribers();

        portENTER_CRITICAL(&g_lock);
        const int count = g_sender_count;
        const int event_count = g_event_count;
        const uint32_t overflow = g_overflow;
        memcpy(taken, g_senders, count * sizeof(sender_t));
        memcpy(events, g_events, event_count * sizeof(event_t));
        g_sender_count = 0;
        g_event_count = 0;
        portEXIT_CRITICAL(&g_lock);

        for (int i = 0; i < count; i++)
            if (taken[i].frames) log_window(&taken[i]);

        int ap_rssi = 0;
#if !LUNA_ROLE_AP
        wifi_ap_record_t ap;
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) ap_rssi = ap.rssi;
#endif
        const bool sub = subscribed();
        if (sub) report_window(taken, count, events, event_count, overflow, ap_rssi);

        const bool log_due = now - logged_at >= STATUS_LOG_US;
#if LUNA_ROLE_AP
        const bool list_due = now - listed_at >= STA_LIST_US;
        bool have_list = (list_due || log_due) && esp_wifi_ap_get_sta_list(&stations) == ESP_OK;
        if (list_due) {
            listed_at = now;
            if (sub && have_list) report_stations(&stations);
        }
#endif
        if (log_due) {
            logged_at = now;
#if LUNA_ROLE_AP
            for (int i = 0; have_list && i < stations.num; i++)
                ESP_LOGI(TAG, "  station " MACSTR " rssi=%d", MAC2STR(stations.sta[i].mac), stations.sta[i].rssi);
#endif
            print_status(now, overflow, ap_rssi, sub);
        }
    }
}

// Device-free sensing: tiny UDP datagrams to every peer's ping port keep each board-to-board link busy (the content
// is irrelevant). Incoming pings are drained here so the socket's queue never fills.
static void ping_task(void *arg) {
    static const char token[] = "LP";
    char drain[16];
    TickType_t wake = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(1000 / PING_RATE_HZ));
        while (recv(g_ping_sock, drain, sizeof(drain), MSG_DONTWAIT) > 0) {
        }
        if (!g_sensing || !subscribed()) continue;
        esp_netif_ip_info_t info;
        const uint32_t self = esp_netif_get_ip_info(g_netif, &info) == ESP_OK ? info.ip.addr : 0;
        for (int i = 0; i < g_peer_count; i++) {
            if (g_peer_ip[i] == self) continue;
            struct sockaddr_in to = {
                .sin_family = AF_INET, .sin_port = htons(LUNA_NODE_PORT + 1), .sin_addr.s_addr = g_peer_ip[i]};
            sendto(g_ping_sock, token, sizeof(token) - 1, 0, (struct sockaddr *)&to, sizeof(to));
        }
    }
}

static int bound_udp(uint16_t port) {
    int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s < 0) return -1;
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(s);
        return -1;
    }
    return s;
}

static void open_sockets(void) {
    g_sock = bound_udp(LUNA_NODE_PORT);
    if (g_sock < 0) {
        ESP_LOGE(TAG, "telemetry socket failed (errno %d), restarting", errno);
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_restart();
    }
    int on = 1;
    setsockopt(g_sock, SOL_SOCKET, SO_BROADCAST, &on, sizeof(on));
    g_ping_sock = bound_udp(LUNA_NODE_PORT + 1);
    if (g_ping_sock < 0) ESP_LOGW(TAG, "ping socket failed, sensing pings disabled");
}

void app_main(void) {
    // Only the NVS partition is ever erased; this firmware must never touch eFuses or security config.
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_read_mac(g_base_mac, ESP_MAC_WIFI_STA));
    ESP_LOGI(TAG, "ActiasLuna node %s, role %s, id " MACSTR ", ssid '%s' ch %d", FW_VERSION, LUNA_ROLE_AP ? "AP" : "RX",
             MAC2STR(g_base_mac), LUNA_SSID, LUNA_CHANNEL);
    start_wifi();
    ESP_LOGI(TAG, "air MAC " MACSTR, MAC2STR(g_air_mac));
    open_sockets();
#if LUNA_ROLE_AP
    luna_portal_start(g_netif);
#endif
    xTaskCreatePinnedToCore(window_task, "window", 8192, NULL, 5, NULL, 1);
    if (g_ping_sock >= 0) xTaskCreatePinnedToCore(ping_task, "ping", 3072, NULL, 4, NULL, 1);
}
