#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "lwip/sockets.h"

#include "luna.h"

// Plain-http page on the AP for phones that joined: shows what is measured, takes a nickname, and can generate
// steady uplink traffic. Motion sensors need https, so those live on the laptop page (server/static/phone.js).

#define NICK_SLOTS 16
#define NICK_MAX 16

static const char *TAG = "portal";

typedef struct {
    bool used;
    uint8_t mac[6];
    uint32_t touched;
    char nick[NICK_MAX + 1];
} nick_t;

static nick_t g_nicks[NICK_SLOTS];
static uint32_t g_clock;
static SemaphoreHandle_t g_nick_lock;
static esp_netif_t *g_ap;
static volatile uint32_t g_hits;

uint32_t luna_portal_hits(void) { return g_hits; }

void luna_portal_nickname(const uint8_t mac[6], char *out, size_t out_len) {
    if (!out_len) return;
    out[0] = '\0';
    if (!g_nick_lock) return;
    xSemaphoreTake(g_nick_lock, portMAX_DELAY);
    for (int i = 0; i < NICK_SLOTS; i++) {
        if (g_nicks[i].used && memcmp(g_nicks[i].mac, mac, 6) == 0) {
            strlcpy(out, g_nicks[i].nick, out_len);
            break;
        }
    }
    xSemaphoreGive(g_nick_lock);
}

// Needs g_nick_lock held. Reuses the MAC's slot, else a free one, else the least recently touched.
static nick_t *nick_slot(const uint8_t mac[6]) {
    nick_t *empty = NULL, *stalest = NULL;
    for (int i = 0; i < NICK_SLOTS; i++) {
        nick_t *n = &g_nicks[i];
        if (!n->used) {
            if (!empty) empty = n;
            continue;
        }
        if (memcmp(n->mac, mac, 6) == 0) return n;
        if (!stalest || n->touched < stalest->touched) stalest = n;
    }
    nick_t *slot = empty ? empty : stalest;
    memset(slot, 0, sizeof(*slot));
    memcpy(slot->mac, mac, 6);
    slot->used = true;
    return slot;
}

static void remember_nick(const uint8_t mac[6], const char *nick) {
    xSemaphoreTake(g_nick_lock, portMAX_DELAY);
    nick_t *slot = nick_slot(mac);
    strlcpy(slot->nick, nick, sizeof(slot->nick));
    slot->touched = ++g_clock;
    xSemaphoreGive(g_nick_lock);
}

static uint32_t peer_ipv4(httpd_req_t *req) {
    struct sockaddr_storage addr;
    socklen_t len = sizeof(addr);
    if (getpeername(httpd_req_to_sockfd(req), (struct sockaddr *)&addr, &len) != 0) return 0;
    if (addr.ss_family == AF_INET) return ((struct sockaddr_in *)&addr)->sin_addr.s_addr;
#if CONFIG_LWIP_IPV6
    if (addr.ss_family == AF_INET6) return ((struct sockaddr_in6 *)&addr)->sin6_addr.un.u32_addr[3];
#endif
    return 0;
}

// HTTP only reveals the client IP, so map it back to a MAC through the AP's DHCP leases.
static bool mac_for_ip(uint32_t ip, uint8_t mac[6]) {
    wifi_sta_list_t stations;
    if (!g_ap || !ip || esp_wifi_ap_get_sta_list(&stations) != ESP_OK || !stations.num) return false;
    int count = stations.num < ESP_WIFI_MAX_CONN_NUM ? stations.num : ESP_WIFI_MAX_CONN_NUM;
    esp_netif_pair_mac_ip_t pairs[ESP_WIFI_MAX_CONN_NUM];
    for (int i = 0; i < count; i++) {
        memcpy(pairs[i].mac, stations.sta[i].mac, 6);
        pairs[i].ip.addr = 0;
    }
    if (esp_netif_dhcps_get_clients_by_mac(g_ap, count, pairs) != ESP_OK) return false;
    for (int i = 0; i < count; i++) {
        if (pairs[i].ip.addr == ip) {
            memcpy(mac, pairs[i].mac, 6);
            return true;
        }
    }
    return false;
}

static const char PORTAL_HTML[] =
    "<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>ActiasLuna demo</title><style>"
    "body{margin:0;padding:16px;max-width:560px;font-family:system-ui,-apple-system,sans-serif;background:#0f1320;color:#e6ebf5}"
    "h1{font-size:22px;margin:4px 0 10px}section{background:#1a2033;border-radius:12px;padding:14px;margin:12px 0}"
    "button{font-size:17px;padding:12px 14px;margin:4px 4px 4px 0;border:0;border-radius:10px;background:#2a3350;color:#e6ebf5}"
    "button.on{background:#3aa57a}input{font-size:17px;padding:10px;width:60%;border-radius:8px;border:1px solid #445;"
    "background:#0a0d16;color:#fff}code{background:#0a0d16;padding:2px 6px;border-radius:6px}"
    ".count{font-size:28px;font-variant-numeric:tabular-nums}small{color:#9aa8c0}"
    "</style></head><body><h1>Indoor positioning demo (opt-in)</h1>"
    "<section><b>What happens here:</b> while your phone is on this Wi-Fi, small radios in the room measure the "
    "<i>signal strength</i> of the packets it sends to this network, and a laptop estimates where you are. Only signal "
    "strength and your address on this network are used. Nothing you browse, type or send is read, and this network "
    "has no internet. To stop, forget this Wi-Fi network in your phone's settings.</section>"
    "<section>Your device here: <code id=mac>...</code><br><small id=ip></small><br><br>"
    "Name to show on the map (optional):<br><input id=nick maxlength=16 placeholder='e.g. Alex'> "
    "<button onclick=save()>Save</button></section>"
    "<section><b>Keep-alive traffic</b> (makes your phone send packets steadily so it can be located)<br>"
    "<button id=r0 onclick=rate(0)>Off</button><button id=r1 onclick=rate(1)>1 per 5 s</button>"
    "<button id=r2 onclick=rate(2)>2 / s</button><button id=r3 onclick=rate(3)>10 / s</button>"
    "<div class=count><span id=good>0</span> ok &middot; <span id=fail>0</span> failed</div><small id=rtt></small>"
    "<p><small>Keep this page open with the screen on while calibrating. Most browsers pause timers when the screen "
    "locks or the tab is hidden.</small></p></section>"
    "<script>"
    "var timer=null,good=0,fail=0,busy=0,EVERY=[0,5000,500,100];"
    "function $(id){return document.getElementById(id)}"
    "function fetchT(url,ms){var c=new AbortController(),t=setTimeout(function(){c.abort()},ms);"
    "return fetch(url,{cache:'no-store',signal:c.signal}).finally(function(){clearTimeout(t)})}"
    "function save(){var v=$('nick').value.trim();fetchT('/hello?name='+encodeURIComponent(v),4000).then(function(r){return r.json()})"
    ".then(function(j){$('mac').textContent=j.mac||'unknown';$('ip').textContent='IP '+j.ip;try{localStorage.nick=v}catch(e){}})"
    ".catch(function(){$('mac').textContent='retrying...';setTimeout(save,2000)})}"
    "function beat(){if(busy>3)return;busy++;var t0=performance.now();fetchT('/p?t='+Date.now(),3000)"
    ".then(function(){good++;$('rtt').textContent='round trip '+Math.round(performance.now()-t0)+' ms'})"
    ".catch(function(){fail++}).finally(function(){busy--;$('good').textContent=good;$('fail').textContent=fail})}"
    "function rate(k){if(timer)clearInterval(timer);timer=k?setInterval(beat,EVERY[k]):null;"
    "for(var i=0;i<4;i++)$('r'+i).className=i==k?'on':'';try{localStorage.rate=k}catch(e){}}"
    "try{$('nick').value=localStorage.nick||''}catch(e){}save();"
    "var k0=3;try{if(localStorage.rate)k0=+localStorage.rate}catch(e){}rate(k0>=0&&k0<4?k0:3);"
    "</script></body></html>";

static esp_err_t reply(httpd_req_t *req, const char *type, const char *body, ssize_t len) {
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    if (type) httpd_resp_set_type(req, type);
    return httpd_resp_send(req, body, len);
}

static esp_err_t serve_page(httpd_req_t *req) { return reply(req, "text/html", PORTAL_HTML, sizeof(PORTAL_HTML) - 1); }

static esp_err_t serve_ping(httpd_req_t *req) {
    g_hits++;
    httpd_resp_set_status(req, "204 No Content");
    return reply(req, NULL, NULL, 0);
}

static int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    c = (char)tolower((unsigned char)c);
    return c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

// Strict whitelist: nicknames go into the "sta" JSON and onto the dashboard without any further escaping.
static void clean_nick(const char *raw, char *out) {
    int n = 0;
    for (const char *p = raw; *p && n < NICK_MAX; p++) {
        char c = *p;
        if (c == '+') {
            c = ' ';
        } else if (c == '%' && hex_digit(p[1]) >= 0 && p[2] && hex_digit(p[2]) >= 0) {
            c = (char)(hex_digit(p[1]) * 16 + hex_digit(p[2]));
            p += 2;
        }
        if (isalnum((unsigned char)c) || strchr(" -_.", c)) out[n++] = c;
    }
    while (n && out[n - 1] == ' ') n--;
    out[n] = '\0';
}

static esp_err_t serve_hello(httpd_req_t *req) {
    char query[160] = {0}, raw[120] = {0}, nick[NICK_MAX + 1] = {0}, body[128];
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK)
        httpd_query_key_value(query, "name", raw, sizeof(raw));
    clean_nick(raw, nick);
    uint32_t ip = peer_ipv4(req);
    uint8_t mac[6];
    if (mac_for_ip(ip, mac)) {
        remember_nick(mac, nick);
        ESP_LOGI(TAG, "hello " MACSTR " '%s'", MAC2STR(mac), nick);
        snprintf(body, sizeof(body), "{\"mac\":\"" MACSTR "\",\"ip\":\"" IPSTR "\",\"name\":\"%s\"}", MAC2STR(mac),
                 IP2STR((esp_ip4_addr_t *)&ip), nick);
    } else {
        snprintf(body, sizeof(body), "{\"mac\":null,\"ip\":\"" IPSTR "\"}", IP2STR((esp_ip4_addr_t *)&ip));
    }
    return reply(req, "application/json", body, HTTPD_RESP_USE_STRLEN);
}

void luna_portal_start(esp_netif_t *ap_netif) {
    g_ap = ap_netif;
    g_nick_lock = xSemaphoreCreateMutex();
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    // Sockets are capped at CONFIG_LWIP_MAX_SOCKETS=24: httpd takes max_open_sockets + 3, main.c takes two more.
    cfg.max_open_sockets = 10;
    cfg.lru_purge_enable = true;
    cfg.stack_size = 6144;
    cfg.recv_wait_timeout = 3;
    cfg.send_wait_timeout = 3;
    httpd_handle_t server = NULL;
    if (httpd_start(&server, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "http server failed to start");
        return;
    }
    const httpd_uri_t routes[] = {
        {.uri = "/", .method = HTTP_GET, .handler = serve_page},
        {.uri = "/p", .method = HTTP_GET, .handler = serve_ping},
        {.uri = "/hello", .method = HTTP_GET, .handler = serve_hello},
    };
    for (size_t i = 0; i < sizeof(routes) / sizeof(routes[0]); i++) httpd_register_uri_handler(server, &routes[i]);
    ESP_LOGI(TAG, "portal at http://192.168.4.1/");
}
