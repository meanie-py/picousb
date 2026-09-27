#include "devbox.h"
#include "lwip/apps/mdns.h"
#include "pico/bootrom.h"
#include "pico/cyw43_arch.h"
#include "pico/stdlib.h"
#include "tusb.h"
#include <stdio.h>
#ifdef PICOUSB_DIAGNOSTICS
#include "hardware/structs/watchdog.h"
#include "hardware/watchdog.h"
uint32_t diagnostic_phase;
static void phase(uint32_t value) {
    watchdog_hw->scratch[1] = value;
}
#else
static void phase(uint32_t value) {
    (void)value;
}
#endif
db_device device;
bool network_send(void *, const uint8_t *, size_t);
bool network_start(void);
void network_tick(uint32_t, bool);
void usb_complete(void *, bool, const uint8_t *, size_t);
void usb_reports(void);
void usb_storage_tick(void);
void serial_init(void);
bool serial_start(void);
void serial_tick(uint32_t, bool);
int main(void) {
#ifdef PICOUSB_DIAGNOSTICS
    if (watchdog_caused_reboot() && watchdog_hw->scratch[0] == 0x50444247)
        diagnostic_phase = watchdog_hw->scratch[1];
#endif
    stdio_init_all();
    serial_init();
    db_config config;
    bool valid =
        db_config_read(&config, (const uint8_t *)(XIP_BASE + PICO_FLASH_SIZE_BYTES - 4096), 4096);
    db_init(&device, &config, network_send, usb_complete, NULL);
    tusb_init();
#ifdef PICOUSB_DIAGNOSTICS
    if (diagnostic_phase) {
        while (true) {
            tud_task();
            sleep_ms(1);
        }
    }
#endif
    if (!valid) {
        printf("{\"event\":\"config_invalid\"}\n");
        while (true) {
            tud_task();
            sleep_ms(1);
        }
    }
    if (cyw43_arch_init()) {
        printf("{\"event\":\"radio_init_failed\"}\n");
        while (true) {
            tud_task();
            sleep_ms(1);
        }
    }
    cyw43_arch_enable_sta_mode();
#ifdef PICOUSB_WIFI_NO_POWERSAVE
    if (cyw43_wifi_pm(&cyw43_state, CYW43_NONE_PM)) {
        printf("{\"event\":\"power_mode_failed\"}\n");
        /* Do not present a failed experiment as a valid comparison build. */
        while (true) {
            tud_task();
            sleep_ms(1);
        }
    }
#endif
    netif_set_hostname(&cyw43_state.netif[0], "picousb");
    mdns_resp_init();
    mdns_resp_add_netif(&cyw43_state.netif[0], "picousb");
    if (!network_start() || !serial_start()) {
        printf("{\"event\":\"server_failed\"}\n");
        while (true) {
            tud_task();
            sleep_ms(1);
        }
    }
    uint32_t retry = 0, delay = 1000;
    int previous = -100;
#ifdef PICOUSB_DIAGNOSTICS
    watchdog_hw->scratch[0] = 0x50444247;
    watchdog_hw->scratch[2] = 0;
    watchdog_enable(5000, false);
#endif
    uint32_t last_loop = time_us_32();
    for (;;) {
        uint32_t stamp = time_us_32();
        db_metric_add(&device.metrics[DB_LOOP], stamp - last_loop);
        last_loop = stamp;
        phase(1);
        tud_task();
        db_metric_add(&device.metrics[DB_USB_TASK], time_us_32() - stamp);
        phase(2);
        stamp = time_us_32();
        cyw43_arch_poll();
        db_metric_add(&device.metrics[DB_WIFI_POLL], time_us_32() - stamp);
        phase(3);
        usb_reports();
        phase(4);
        uint32_t now = to_ms_since_boot(get_absolute_time());
        int status = cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);
        bool online = status == CYW43_LINK_UP;
        if (online && !device.usb.network_ms) device.usb.network_ms = now;
        if (status != previous) {
            phase(5);
            printf("{\"event\":\"network\",\"state\":%d}\n", status);
            previous = status;
            if (online)
                printf("{\"event\":\"address\",\"ipv4\":\"%s\"}\n",
                       ip4addr_ntoa(netif_ip4_addr(&cyw43_state.netif[0])));
        }
        if (online) {
            delay = 1000;
            retry = now + 15000;
        } else if ((int32_t)(now - retry) >= 0) {
            phase(6);
            cyw43_arch_wifi_connect_async(config.ssid, config.password, CYW43_AUTH_WPA2_AES_PSK);
            retry = now + 15000 + delay;
            if (delay < 16000)
                delay *= 2;
        }
        phase(7);
        network_tick(now, online);
        serial_tick(now, true);
        phase(8);
        db_tick(&device, now);
        usb_storage_tick();
        phase(9);
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, online);
        phase(10);
        if (device.bootsel) {
            /* Let the reply leave before the radio goes away with the reset. */
            for (int i = 0; i < 200; i++) {
                cyw43_arch_poll();
                sleep_ms(1);
            }
            reset_usb_boot(0, 0);
        }
        sleep_ms(1);
#ifdef PICOUSB_DIAGNOSTICS
        watchdog_hw->scratch[2]++;
        watchdog_update();
#endif
    }
}
