#ifndef DB_WEBSOCKET_H
#define DB_WEBSOCKET_H
#include <stddef.h>
#include <stdint.h>
/* 0 incomplete, -1 invalid, otherwise bytes consumed. Client frames MUST mask. */
int ws_decode(const uint8_t *in, size_t n, uint8_t *op, uint8_t *out, size_t *length);
size_t ws_encode(uint8_t *out, uint8_t op, const uint8_t *data, size_t length);
#endif
