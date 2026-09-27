#ifndef SERIAL_BRIDGE_H
#define SERIAL_BRIDGE_H
/* Target console bridge: portable state for the GPIO UART <-> TCP relay.
 *
 * RX (target -> host) is a single-producer ring written from the UART IRQ.
 * The producer never waits and never reads the consumer's position: when no
 * client drains it, the newest bytes overwrite the oldest, because the tail of
 * a crash log is what matters. The consumer detects overwrite after copying
 * and reports the loss instead of forwarding torn data.
 *
 * TX (host -> target) is a small FIFO filled from the network and drained into
 * the UART FIFO from the main loop. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define SB_RX_SIZE 131072u /* power of two; ~11 s of 115200 8N1 */
#define SB_TX_SIZE 4096u
#define SB_HELLO "PICOSERIAL 1"
#define SB_LINE_MAX 160
typedef struct {
    uint8_t rx[SB_RX_SIZE];
    volatile uint32_t head; /* producer: total bytes ever received */
    uint32_t tail;          /* consumer: total bytes accounted for */
    uint32_t dropped;       /* total bytes overwritten before a client read them */
    uint8_t tx[SB_TX_SIZE];
    uint32_t tx_head, tx_count;
} sb_bridge;
typedef struct {
    char line[SB_LINE_MAX];
    size_t used;
} sb_auth;
void sb_init(sb_bridge *);
/* IRQ side. */
static inline void sb_rx_put(sb_bridge *b, uint8_t c) {
    uint32_t h = b->head;
    b->rx[h & (SB_RX_SIZE - 1)] = c;
    __asm__ volatile("" ::: "memory"); /* byte before the index that publishes it */
    b->head = h + 1;
}
/* Consumer: copy up to `cap` pending bytes into `out`. Returns the byte count;
 * `*lost` receives how many bytes were overwritten since the last call. The
 * caller must commit exactly the returned count with sb_rx_commit once sent. */
size_t sb_rx_peek(sb_bridge *, uint8_t *out, size_t cap, uint32_t *lost);
void sb_rx_commit(sb_bridge *, size_t n);
/* Discard everything received so far without reporting it as lost. */
void sb_rx_clear(sb_bridge *);
size_t sb_tx_space(const sb_bridge *);
/* Queues all of `n` or nothing. */
bool sb_tx_push(sb_bridge *, const uint8_t *, size_t n);
bool sb_tx_pop(sb_bridge *, uint8_t *);
void sb_tx_clear(sb_bridge *);
/* Feeds handshake bytes. Returns 1 when the line `PICOSERIAL 1 <token>\n`
 * (or `PICOSERIAL 1\n` when no token is configured) matched, -1 on a mismatch
 * or overlong line, 0 when more bytes are needed. `*consumed` is how many of
 * the `n` bytes belonged to the handshake; the rest are console input. */
int sb_auth_feed(sb_auth *, const char *token, const uint8_t *, size_t n, size_t *consumed);
#endif
