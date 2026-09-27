#include "serial_bridge.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static sb_bridge b;
static int auth(const char *token, const char *text, size_t *consumed) {
    sb_auth a = {0};
    return sb_auth_feed(&a, token, (const uint8_t *)text, strlen(text), consumed);
}
static void rx_test(void) {
    static uint8_t out[SB_RX_SIZE];
    uint32_t lost;
    sb_init(&b);
    assert(sb_rx_peek(&b, out, sizeof(out), &lost) == 0 && lost == 0);
    for (int i = 0; i < 10; i++)
        sb_rx_put(&b, (uint8_t)('a' + i));
    /* A short read leaves the rest for the next one. */
    assert(sb_rx_peek(&b, out, 4, &lost) == 4 && !memcmp(out, "abcd", 4) && lost == 0);
    sb_rx_commit(&b, 4);
    assert(sb_rx_peek(&b, out, sizeof(out), &lost) == 6 && !memcmp(out, "efghij", 6));
    /* Uncommitted bytes are offered again. */
    assert(sb_rx_peek(&b, out, sizeof(out), &lost) == 6);
    sb_rx_commit(&b, 6);
    /* Overflow keeps the newest SB_RX_SIZE bytes and reports the rest lost. */
    for (uint32_t i = 0; i < SB_RX_SIZE + 100; i++)
        sb_rx_put(&b, (uint8_t)i);
    size_t n = sb_rx_peek(&b, out, sizeof(out), &lost);
    assert(n == SB_RX_SIZE && lost == 100 && b.dropped == 100);
    assert(out[0] == (uint8_t)100 && out[n - 1] == (uint8_t)(SB_RX_SIZE + 99));
    sb_rx_commit(&b, n);
    assert(sb_rx_peek(&b, out, sizeof(out), &lost) == 0 && lost == 0);
    /* Clearing discards without counting a loss. */
    sb_rx_put(&b, 'x');
    sb_rx_clear(&b);
    assert(sb_rx_peek(&b, out, sizeof(out), &lost) == 0 && lost == 0 && b.dropped == 100);
    /* Index wraparound of the 32-bit counters. */
    sb_init(&b);
    b.head = b.tail = 0xfffffffeu;
    for (int i = 0; i < 5; i++)
        sb_rx_put(&b, (uint8_t)('0' + i));
    assert(sb_rx_peek(&b, out, sizeof(out), &lost) == 5 && !memcmp(out, "01234", 5) && !lost);
}
static void tx_test(void) {
    uint8_t c, big[SB_TX_SIZE + 1] = {0};
    sb_init(&b);
    assert(!sb_tx_pop(&b, &c));
    assert(sb_tx_push(&b, (const uint8_t *)"hi\r", 3) && sb_tx_space(&b) == SB_TX_SIZE - 3);
    assert(sb_tx_pop(&b, &c) && c == 'h');
    /* All or nothing. */
    assert(!sb_tx_push(&b, big, SB_TX_SIZE));
    assert(sb_tx_push(&b, big, SB_TX_SIZE - 2));
    assert(sb_tx_pop(&b, &c) && c == 'i' && sb_tx_pop(&b, &c) && c == '\r');
    assert(sb_tx_pop(&b, &c) && c == 0);
    sb_tx_clear(&b);
    assert(!sb_tx_pop(&b, &c) && sb_tx_space(&b) == SB_TX_SIZE);
}
static void auth_test(void) {
    const char *token = "synthetic-test-token";
    size_t used;
    assert(auth(token, "PICOSERIAL 1 synthetic-test-token\n", &used) == 1 && used == 34);
    assert(auth(token, "PICOSERIAL 1 synthetic-test-token\r\nls\r", &used) == 1 && used == 35);
    assert(auth(token, "PICOSERIAL 1 synthetic-test-toke\n", &used) == -1);
    assert(auth(token, "PICOSERIAL 1 synthetic-test-tokenX\n", &used) == -1);
    assert(auth(token, "PICOSERIAL 1 Synthetic-test-token\n", &used) == -1);
    assert(auth(token, "PICOSERIAL 1\n", &used) == -1);
    assert(auth(token, "PICOSERIAL 2 synthetic-test-token\n", &used) == -1);
    assert(auth(token, "PICOSERIAL 1_synthetic-test-token\n", &used) == -1);
    assert(auth(token, "PICOSERIAL 1 synthetic", &used) == 0 && used == 22);
    /* No token configured: the bare hello only. */
    assert(auth("", "PICOSERIAL 1\n", &used) == 1);
    assert(auth("", "PICOSERIAL 1 anything\n", &used) == -1);
    /* Split across segments. */
    sb_auth a = {0};
    const char *first = "PICOSERIAL 1 synth", *rest = "etic-test-token\nX";
    assert(sb_auth_feed(&a, token, (const uint8_t *)first, strlen(first), &used) == 0);
    assert(sb_auth_feed(&a, token, (const uint8_t *)rest, strlen(rest), &used) == 1 && used == 16);
    /* Overlong line without a newline is refused rather than buffered forever. */
    char spam[SB_LINE_MAX + 1];
    memset(spam, 'A', sizeof(spam));
    sb_auth s = {0};
    assert(sb_auth_feed(&s, token, (const uint8_t *)spam, sizeof(spam), &used) == -1);
}
int main(void) {
    rx_test();
    tx_test();
    auth_test();
    puts("serial bridge tests passed");
    return 0;
}
