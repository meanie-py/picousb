#include "devbox.h"
#include "lwip/tcp.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha1.h"
#include "pico/cyw43_arch.h"
#include "websocket.h"
#include <stdio.h>
#include <string.h>
#include <strings.h>
extern db_device device;
static struct tcp_pcb *peer;
static uint8_t rx[DB_MAX + 8 + TCP_MSS];
static size_t used;
static bool upgraded, closing;
static uint32_t opened;
static bool send_raw(const void *p, size_t n) {
    if (!peer || n > tcp_sndbuf(peer))
        return false;
    if (tcp_write(peer, p, (u16_t)n, TCP_WRITE_FLAG_COPY) != ERR_OK)
        return false;
    tcp_output(peer);
    return true;
}
bool network_send(void *ctx, const uint8_t *p, size_t n) {
    (void)ctx;
    static uint8_t frame[DB_MAX + 4];
    size_t len = ws_encode(frame, 2, p, n);
    bool ok = upgraded && send_raw(frame, len);
    if (!ok)
        closing = true;
    return ok;
}
static void dropped(void *arg, err_t err) {
    (void)arg;
    (void)err;
    peer = NULL;
    used = 0;
    upgraded = false;
    db_disconnect(&device);
}
static void close_peer(void) {
    if (peer) {
        struct tcp_pcb *p = peer;
        peer = NULL;
        tcp_arg(p, NULL);
        tcp_recv(p, NULL);
        tcp_err(p, NULL);
        tcp_abort(p);
    }
    used = 0;
    upgraded = false;
    closing = false;
    db_disconnect(&device);
}
static bool handshake(size_t end) {
    char header[2049];
    if (end > 2048)
        return false;
    memcpy(header, rx, end);
    header[end] = 0;
    if (strncmp(header, "GET /ws HTTP/1.1\r\n", 18))
        return false;
    char key[25] = {0};
    bool upgrade = false, connection = false, version = false;
    char *line = strstr(header, "\r\n") + 2;
    while (*line && line[0] != '\r') {
        char *next = strstr(line, "\r\n");
        if (!next)
            return false;
        *next = 0;
        char *colon = strchr(line, ':');
        if (!colon)
            return false;
        *colon++ = 0;
        while (*colon == ' ' || *colon == '\t')
            colon++;
        if (!strcasecmp(line, "Upgrade"))
            upgrade = !strcasecmp(colon, "websocket");
        if (!strcasecmp(line, "Connection")) {
            for (char *q = colon; *q; q++)
                if (*q >= 'A' && *q <= 'Z')
                    *q += 32;
            connection = strstr(colon, "upgrade") != NULL;
        }
        if (!strcasecmp(line, "Sec-WebSocket-Version"))
            version = !strcmp(colon, "13");
        if (!strcasecmp(line, "Sec-WebSocket-Key")) {
            if (key[0] || strlen(colon) != 24)
                return false;
            memcpy(key, colon, 24);
        }
        line = next + 2;
    }
    if (!upgrade || !connection || !version || !key[0])
        return false;
    uint8_t decoded[32];
    size_t decoded_len = 0;
    if (mbedtls_base64_decode(decoded, sizeof(decoded), &decoded_len, (uint8_t *)key, 24) ||
        decoded_len != 16)
        return false;
    char source[61];
    snprintf(source, sizeof(source), "%s258EAFA5-E914-47DA-95CA-C5AB0DC85B11", key);
    uint8_t hash[20], encoded[32];
    size_t written;
    if (mbedtls_sha1((uint8_t *)source, 60, hash) ||
        mbedtls_base64_encode(encoded, sizeof(encoded), &written, hash, 20))
        return false;
    encoded[written] = 0;
    char answer[200];
    int len = snprintf(answer, sizeof(answer),
                       "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: "
                       "Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n",
                       encoded);
    if (!send_raw(answer, (size_t)len))
        return false;
    upgraded = true;
    memmove(rx, rx + end, used - end);
    used -= end;
    return true;
}
static bool parse(void) {
    if (!upgraded) {
        size_t end = 0;
        for (size_t i = 3; i < used; i++)
            if (!memcmp(rx + i - 3, "\r\n\r\n", 4)) {
                end = i + 1;
                break;
            }
        if (!end)
            return used < 2048;
        if (!handshake(end))
            return false;
    }
    while (used) {
        uint8_t op;
        static uint8_t data[DB_MAX];
        size_t len = 0;
        int consumed = ws_decode(rx, used, &op, data, &len);
        if (consumed < 0)
            return false;
        if (!consumed)
            return true;
        if (op == 2) {
            if (!db_receive(&device, data, len))
                return false;
        } else if (op == 8)
            return false;
        else if (op == 9) {
            uint8_t pong[129];
            size_t n = ws_encode(pong, 10, data, len);
            if (!send_raw(pong, n))
                return false;
        }
        memmove(rx, rx + consumed, used - (size_t)consumed);
        used -= (size_t)consumed;
    }
    return true;
}
static err_t received(void *arg, struct tcp_pcb *p, struct pbuf *packet, err_t err) {
    (void)arg;
    (void)err;
    if (!packet) {
        close_peer();
        return ERR_ABRT;
    }
    for (struct pbuf *q = packet; q; q = q->next) {
        if (q->len > sizeof(rx) - used) {
            pbuf_free(packet);
            close_peer();
            return ERR_ABRT;
        }
        memcpy(rx + used, q->payload, q->len);
        used += q->len;
        if (!parse()) {
            pbuf_free(packet);
            close_peer();
            return ERR_ABRT;
        }
    }
    tcp_recved(p, packet->tot_len);
    pbuf_free(packet);
    return ERR_OK;
}
static err_t accepted(void *arg, struct tcp_pcb *p, err_t err) {
    (void)arg;
    (void)err;
    if (peer) {
        tcp_abort(p);
        return ERR_ABRT;
    }
    peer = p;
    used = 0;
    upgraded = false;
    closing = false;
    opened = to_ms_since_boot(get_absolute_time());
    db_connect(&device);
    tcp_nagle_disable(p);
    tcp_recv(p, received);
    tcp_err(p, dropped);
    return ERR_OK;
}
bool network_start(void) {
    struct tcp_pcb *p = tcp_new_ip_type(IPADDR_TYPE_V4);
    if (!p)
        return false;
    if (tcp_bind(p, IP_ADDR_ANY, 8080) != ERR_OK) {
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
void network_tick(uint32_t now, bool link) {
    if (peer && (closing || !link || (!device.authenticated && (uint32_t)(now - opened) > 10000)))
        close_peer();
}
