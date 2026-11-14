idf_component_register(
    SRCS "main.c" "portal.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash esp_timer esp_http_server lwip
)
