/* Software host transport for testing the real firmware core over framed stdio. */
#include "devbox.h"
#include <stdio.h>
#include <string.h>
static bool send(void *ctx, const uint8_t *p, size_t n) {
    (void)ctx;
    uint8_t h[2] = {(uint8_t)n, (uint8_t)(n >> 8)};
    return fwrite(h, 1, 2, stdout) == 2 && fwrite(p, 1, n, stdout) == n && fflush(stdout) == 0;
}
static void done(void *ctx, bool ok, const uint8_t *p, size_t n) {
    (void)ctx;
    (void)ok;
    (void)p;
    (void)n;
}
int main(void) {
    db_config c = {
        .ssid = "synthetic", .password = "synthetic-password", .token = "synthetic-test-token"};
    db_device d;
    db_init(&d, &c, send, done, NULL);
    db_connect(&d);
    d.usb.mounted = true;
    uint8_t h[2], buf[DB_MAX];
    while (fread(h, 1, 2, stdin) == 2) {
        size_t n = h[0] | ((size_t)h[1] << 8);
        if (n > sizeof(buf) || fread(buf, 1, n, stdin) != n)
            return 1;
        if (!db_receive(&d, buf, n))
            return 2;
        while (d.hid.count) {
            uint8_t kind = d.hid.queue[d.hid.head].kind;
            d.hid.counts[kind].submitted++;
            d.hid.counts[kind].completed++;
            db_hid_pop(&d.hid);
        }
    }
    return 0;
}
