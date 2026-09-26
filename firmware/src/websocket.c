#include "websocket.h"
#include "devbox.h"
#include <string.h>
int ws_decode(const uint8_t *p, size_t n, uint8_t *op, uint8_t *out, size_t *length) {
    if (n < 2)
        return 0;
    *op = p[0] & 15;
    if (!(p[0] & 128) || (p[0] & 112) || !(p[1] & 128) ||
        (*op != 2 && *op != 8 && *op != 9 && *op != 10))
        return -1;
    size_t len = p[1] & 127, head = 2;
    if (len == 127)
        return -1;
    if (len == 126) {
        if (n < 4)
            return 0;
        len = ((size_t)p[2] << 8) | p[3];
        head = 4;
        if (len < 126)
            return -1;
    }
    if (len > DB_MAX || (*op >= 8 && len > 125) || (*op == 8 && len == 1))
        return -1;
    if (n < head + 4 + len)
        return 0;
    for (size_t i = 0; i < len; i++)
        out[i] = p[head + 4 + i] ^ p[head + (i % 4)];
    *length = len;
    return (int)(head + 4 + len);
}
size_t ws_encode(uint8_t *out, uint8_t op, const uint8_t *data, size_t len) {
    if (len > DB_MAX)
        return 0;
    out[0] = 128 | op;
    size_t head = 2;
    if (len < 126)
        out[1] = (uint8_t)len;
    else {
        out[1] = 126;
        out[2] = (uint8_t)(len >> 8);
        out[3] = (uint8_t)len;
        head = 4;
    }
    if (len)
        memcpy(out + head, data, len);
    return head + len;
}
