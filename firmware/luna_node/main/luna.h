#pragma once
#include <stddef.h>
#include <stdint.h>

#include "esp_netif.h"

// Participant portal (portal.c), AP build only.
void luna_portal_start(esp_netif_t *ap_netif);
// Copies the nickname a phone chose on the portal ("" if none). Safe to embed in JSON: portal.c whitelists chars.
void luna_portal_nickname(const uint8_t mac[6], char *out, size_t out_len);
// Number of keep-alive pings served; reported in the "sta" packet so the dashboard can show portal activity.
uint32_t luna_portal_hits(void);
