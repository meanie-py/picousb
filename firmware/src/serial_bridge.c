#include "serial_bridge.h"
#include <string.h>
void sb_init(sb_bridge *b) {
    memset(b, 0, sizeof(*b));
}
size_t sb_rx_peek(sb_bridge *b, uint8_t *out, size_t cap, uint32_t *lost) {
    *lost = 0;
    uint32_t head = b->head;
    __asm__ volatile("" ::: "memory");
    if (head - b->tail > SB_RX_SIZE) {
        *lost = head - b->tail - SB_RX_SIZE;
        b->dropped += *lost;
        b->tail = head - SB_RX_SIZE;
    }
    size_t n = head - b->tail;
    if (n > cap)
        n = cap;
    for (size_t i = 0; i < n; i++)
        out[i] = b->rx[(b->tail + i) & (SB_RX_SIZE - 1)];
    /* The producer may have lapped the bytes just copied. Anything it could
     * have overwritten is dropped from the front of the copy. */
    __asm__ volatile("" ::: "memory");
    uint32_t after = b->head;
    if (after - b->tail > SB_RX_SIZE) {
        uint32_t torn = after - b->tail - SB_RX_SIZE;
        if (torn >= n) {
            *lost += (uint32_t)n;
            b->dropped += (uint32_t)n;
            b->tail += (uint32_t)n;
            return 0;
        }
        memmove(out, out + torn, n - torn);
        *lost += torn;
        b->dropped += torn;
        b->tail += torn;
        n -= torn;
    }
    return n;
}
void sb_rx_commit(sb_bridge *b, size_t n) {
    b->tail += (uint32_t)n;
}
void sb_rx_clear(sb_bridge *b) {
    b->tail = b->head;
}
size_t sb_tx_space(const sb_bridge *b) {
    return SB_TX_SIZE - b->tx_count;
}
bool sb_tx_push(sb_bridge *b, const uint8_t *p, size_t n) {
    if (n > sb_tx_space(b))
        return false;
    for (size_t i = 0; i < n; i++)
        b->tx[(b->tx_head + b->tx_count + i) % SB_TX_SIZE] = p[i];
    b->tx_count += (uint32_t)n;
    return true;
}
bool sb_tx_pop(sb_bridge *b, uint8_t *c) {
    if (!b->tx_count)
        return false;
    *c = b->tx[b->tx_head];
    b->tx_head = (b->tx_head + 1) % SB_TX_SIZE;
    b->tx_count--;
    return true;
}
void sb_tx_clear(sb_bridge *b) {
    b->tx_head = b->tx_count = 0;
}
int sb_auth_feed(sb_auth *a, const char *token, const uint8_t *p, size_t n, size_t *consumed) {
    *consumed = 0;
    while (*consumed < n) {
        uint8_t c = p[(*consumed)++];
        if (c != '\n') {
            if (a->used >= SB_LINE_MAX - 1)
                return -1;
            a->line[a->used++] = (char)c;
            continue;
        }
        size_t len = a->used;
        if (len && a->line[len - 1] == '\r')
            len--;
        size_t hello = sizeof(SB_HELLO) - 1, tlen = strlen(token);
        uint8_t diff = (uint8_t)(len < hello || memcmp(a->line, SB_HELLO, hello));
        /* Constant-time over the token, like the control session's AUTH. */
        size_t given = len > hello ? len - hello - 1 : 0;
        if (tlen)
            diff |= (uint8_t)(len <= hello || a->line[hello] != ' ' || given != tlen);
        else
            diff |= (uint8_t)(len != hello);
        if (tlen && given == tlen && len > hello)
            for (size_t i = 0; i < tlen; i++)
                diff |= (uint8_t)(token[i] ^ a->line[hello + 1 + i]);
        memset(a->line, 0, sizeof(a->line));
        a->used = 0;
        return diff ? -1 : 1;
    }
    return 0;
}
