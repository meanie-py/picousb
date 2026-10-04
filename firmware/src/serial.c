/* Target console bridge: the GPIO UART wired to the target Pi's UART, relayed
 * to one authenticated TCP client on port 8082 (docs/protocol.md, "Serial
 * console"). Independent of the control session, so a long-running capture
 * can stay attached while other tools come and go.
 *
 * The Pico can be powered from VSYS while the target is off. Driving TX (or a
 * pull-up on RX) into an unpowered target back-feeds it through its pin
 * protection diodes, so both pins go passive whenever VBUS - the target's USB
 * power - is absent. */
#include "devbox.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/uart.h"
#include "lwip/tcp.h"
#include "pico/cyw43_arch.h"
#include "serial_bridge.h"
#include <stdio.h>
#include <string.h>
#define CONSOLE (PICOUSB_CONSOLE_UART ? uart1 : uart0)
#define SERIAL_PORT 8082
#define AUTH_MS 5000
#define VBUS_POLL_MS 100
extern db_device device;
static sb_bridge bridge;
static struct tcp_pcb *client, *pending;
static sb_auth pending_auth;
static uint32_t pending_opened, vbus_checked, pending_lost;
static bool target_powered;
static volatile uint32_t rx_errors;
static uint32_t reported_rx, reported_errors, reported_ms;
static void rx_irq(void) {
    while (uart_is_readable(CONSOLE)) {
        uint32_t dr = uart_get_hw(CONSOLE)->dr;
        /* Framing/break errors are line noise while the target powers up or
         * down, not console bytes. Overrun still carries a valid byte. */
        if (!(dr & (UART_UARTDR_FE_BITS | UART_UARTDR_BE_BITS)))
            sb_rx_put(&bridge, (uint8_t)dr);
        else
            rx_errors++;
    }
}
/* Wiring diagnostics on the diagnostic UART: a console that stays silent while
 * the target boots is either no signal (both zero), a garbled one (errors), or
 * a forwarding fault (bytes but nothing reaches the client). */
static void report(uint32_t now) {
    uint32_t rx = bridge.head, errors = rx_errors;
    if ((rx == reported_rx && errors == reported_errors) || (uint32_t)(now - reported_ms) < 5000)
        return;
    reported_rx = rx;
    reported_errors = errors;
    reported_ms = now;
    printf("{\"event\":\"console_rx\",\"bytes\":%lu,\"errors\":%lu,\"dropped\":%lu,"
           "\"client\":%d,\"rx_level\":%d}\n",
           (unsigned long)rx, (unsigned long)errors, (unsigned long)bridge.dropped, client != NULL,
           gpio_get(PICOUSB_CONSOLE_RX_PIN));
}
static void set_target_powered(bool on) {
    target_powered = on;
    if (on) {
        gpio_set_function(PICOUSB_CONSOLE_TX_PIN, GPIO_FUNC_UART);
        gpio_disable_pulls(PICOUSB_CONSOLE_TX_PIN);
        gpio_pull_up(PICOUSB_CONSOLE_RX_PIN);
    } else {
        gpio_set_function(PICOUSB_CONSOLE_TX_PIN, GPIO_FUNC_NULL);
        gpio_pull_down(PICOUSB_CONSOLE_TX_PIN);
        gpio_pull_down(PICOUSB_CONSOLE_RX_PIN);
        sb_tx_clear(&bridge);
    }
    printf("{\"event\":\"console\",\"target_powered\":%d}\n", on);
}
void serial_init(void) {
    sb_init(&bridge);
    uart_init(CONSOLE, PICOUSB_CONSOLE_BAUD);
    uart_set_hw_flow(CONSOLE, false, false);
    uart_set_format(CONSOLE, 8, 1, UART_PARITY_NONE);
    uart_set_fifo_enabled(CONSOLE, true);
    gpio_set_function(PICOUSB_CONSOLE_RX_PIN, GPIO_FUNC_UART);
    /* Passive until the radio is up and VBUS can be read. */
    set_target_powered(false);
    irq_set_exclusive_handler(UART_IRQ_NUM(CONSOLE), rx_irq);
    irq_set_enabled(UART_IRQ_NUM(CONSOLE), true);
    uart_set_irq_enables(CONSOLE, true, false);
}
static void forget(struct tcp_pcb *p) {
    tcp_arg(p, NULL);
    tcp_recv(p, NULL);
    tcp_err(p, NULL);
    tcp_sent(p, NULL);
}
static void drop(struct tcp_pcb **slot) {
    if (*slot) {
        struct tcp_pcb *p = *slot;
        *slot = NULL;
        forget(p);
        tcp_abort(p);
    }
}
static void errored(void *arg, err_t err) {
    (void)err;
    /* lwIP already freed the pcb. */
    struct tcp_pcb **slot = arg;
    if (slot)
        *slot = NULL;
}
static bool push_input(struct pbuf *packet, size_t skip) {
    for (struct pbuf *q = packet; q; q = q->next) {
        if (skip >= q->len) {
            skip -= q->len;
            continue;
        }
        const uint8_t *p = (const uint8_t *)q->payload + skip;
        size_t n = q->len - skip;
        skip = 0;
        if (target_powered && !sb_tx_push(&bridge, p, n))
            return false;
    }
    return true;
}
static err_t client_recv(void *arg, struct tcp_pcb *p, struct pbuf *packet, err_t err) {
    (void)arg;
    (void)err;
    if (!packet) {
        if (client == p) {
            forget(p);
            client = NULL;
            tcp_close(p);
        }
        return ERR_OK;
    }
    /* Backpressure: lwIP keeps refused data and offers it again later. */
    if (target_powered && packet->tot_len > sb_tx_space(&bridge))
        return ERR_MEM;
    push_input(packet, 0);
    tcp_recved(p, packet->tot_len);
    pbuf_free(packet);
    return ERR_OK;
}
static err_t pending_recv(void *arg, struct tcp_pcb *p, struct pbuf *packet, err_t err) {
    (void)arg;
    (void)err;
    if (!packet) {
        drop(&pending);
        return ERR_ABRT;
    }
    size_t offset = 0;
    int verdict = 0;
    for (struct pbuf *q = packet; q && !verdict; q = q->next) {
        size_t used;
        verdict = sb_auth_feed(&pending_auth, device.config.token, q->payload, q->len, &used);
        offset += used;
    }
    if (verdict < 0) {
        pbuf_free(packet);
        drop(&pending);
        return ERR_ABRT;
    }
    if (verdict > 0) {
        /* A new authenticated client replaces the old one: a capture that
         * lost Wi-Fi without a FIN must not lock the console out. */
        drop(&client);
        client = pending;
        pending = NULL;
        tcp_arg(client, &client);
        tcp_recv(client, client_recv);
        tcp_write(client, "OK\n", 3, TCP_WRITE_FLAG_COPY);
        tcp_output(client);
        if (!push_input(packet, offset)) {
            pbuf_free(packet);
            drop(&client);
            return ERR_ABRT;
        }
    }
    tcp_recved(p, packet->tot_len);
    pbuf_free(packet);
    return ERR_OK;
}
static err_t accepted(void *arg, struct tcp_pcb *p, err_t err) {
    (void)arg;
    if (err != ERR_OK || !p)
        return ERR_VAL;
    if (pending) {
        tcp_abort(p);
        return ERR_ABRT;
    }
    pending = p;
    memset(&pending_auth, 0, sizeof(pending_auth));
    pending_opened = to_ms_since_boot(get_absolute_time());
    tcp_nagle_disable(p);
    p->so_options |= SOF_KEEPALIVE;
    p->keep_idle = 5000;
    p->keep_intvl = 2000;
    p->keep_cnt = 3;
    tcp_arg(p, &pending);
    tcp_recv(p, pending_recv);
    tcp_err(p, errored);
    return ERR_OK;
}
bool serial_start(void) {
    struct tcp_pcb *p = tcp_new_ip_type(IPADDR_TYPE_V4);
    if (!p)
        return false;
    if (tcp_bind(p, IP_ADDR_ANY, SERIAL_PORT) != ERR_OK) {
        tcp_close(p);
        return false;
    }
    struct tcp_pcb *listener = tcp_listen_with_backlog(p, 1);
    if (!listener) {
        tcp_close(p);
        return false;
    }
    tcp_accept(listener, accepted);
    return true;
}
static void forward(void) {
    static uint8_t chunk[2048];
    if (!client)
        return;
    if (pending_lost) {
        char note[64];
        int n = snprintf(note, sizeof(note), "\r\n[picousb: %lu console bytes lost]\r\n",
                         (unsigned long)pending_lost);
        if ((size_t)n > tcp_sndbuf(client))
            return;
        if (tcp_write(client, note, (u16_t)n, TCP_WRITE_FLAG_COPY) != ERR_OK)
            return;
        pending_lost = 0;
    }
    size_t room = tcp_sndbuf(client);
    if (room > sizeof(chunk))
        room = sizeof(chunk);
    if (!room)
        return;
    uint32_t lost;
    size_t n = sb_rx_peek(&bridge, chunk, room, &lost);
    pending_lost += lost;
    if (lost)
        return; /* Report the gap before the bytes that follow it. */
    if (n && tcp_write(client, chunk, (u16_t)n, TCP_WRITE_FLAG_COPY) == ERR_OK)
        sb_rx_commit(&bridge, n);
    tcp_output(client);
}
void serial_tick(uint32_t now, bool radio) {
    if (radio && (uint32_t)(now - vbus_checked) >= VBUS_POLL_MS) {
        vbus_checked = now;
        bool vbus = cyw43_arch_gpio_get(CYW43_WL_GPIO_VBUS_PIN);
        if (vbus != target_powered)
            set_target_powered(vbus);
    }
    uint8_t c;
    while (target_powered && uart_is_writable(CONSOLE) && sb_tx_pop(&bridge, &c))
        uart_get_hw(CONSOLE)->dr = c;
    if (pending && (uint32_t)(now - pending_opened) > AUTH_MS)
        drop(&pending);
    forward();
    report(now);
    device.console = (db_console){
        .target_powered = target_powered,
        .client = client != NULL,
        .rx_level = (uint8_t)gpio_get(PICOUSB_CONSOLE_RX_PIN),
        .rx_bytes = bridge.head,
        .rx_errors = rx_errors,
        .dropped = bridge.dropped,
        .tx_queued = bridge.tx_count,
    };
}
