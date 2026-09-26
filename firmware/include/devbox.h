#ifndef DEVBOX_H
#define DEVBOX_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "metrics.h"
#define DB_CONFIG_SIZE 4096
#define DB_HEADER 28
#define DB_PAYLOAD 16384
#define DB_MAX (DB_HEADER + DB_PAYLOAD)
#define DB_SECTORS 65536u
#define DB_REPORTS 256
enum { DB_OK, DB_PROTOCOL, DB_AUTH, DB_LIMIT, DB_BUSY, DB_OFFLINE, DB_IO, DB_TIMEOUT, DB_ARGUMENT };
enum {
    DB_AUTH_OP = 1,
    DB_STATUS = 2,
    DB_METRICS = 3,
    DB_UDP_PROBE = 4,
    DB_KEY_DOWN = 10,
    DB_KEY_UP,
    DB_KEY_PRESS,
    DB_KEY_RELEASE,
    DB_KEY_TEXT,
    DB_MOUSE_MOVE = 20,
    DB_MOUSE_DOWN,
    DB_MOUSE_UP,
    DB_MOUSE_SCROLL,
    DB_MOUSE_CLICK,
    DB_MOUSE_RELEASE,
    DB_ATTACH = 30,
    DB_DETACH,
    DB_READ = 40,
    DB_WRITE,
    DB_FLUSH,
    DB_EJECT
};
typedef struct {
    char ssid[33], password[64], token[129];
} db_config;
typedef struct {
    uint8_t op, status;
    uint32_t id, session, a, b, length;
    const uint8_t *data;
} db_message;
typedef struct {
    uint8_t kind, modifiers, keys[6], buttons;
    int8_t x, y, wheel;
} db_report;
typedef struct {
    uint8_t modifiers, keys[6], buttons;
    db_report queue[DB_REPORTS];
    uint16_t head, count;
} db_hid;
typedef bool (*db_send_fn)(void *, const uint8_t *, size_t);
typedef void (*db_complete_fn)(void *, bool, const uint8_t *, size_t);
typedef struct {
    db_config config;
    db_hid hid;
    db_send_fn send;
    db_complete_fn complete;
    void *ctx;
    bool authenticated, media, changed, pending, read_only;
    uint32_t sectors;
    uint8_t pending_op;
    uint32_t session, next_storage, pending_id, deadline;
    uint32_t last_request;
    uint32_t pending_bytes;
    uint32_t media_generation;
    db_metric metrics[DB_METRIC_COUNT];
    uint32_t clock_ms, probe_until, probe_last, probe_stats[5];
    uint8_t probe_cookie[16];
    bool probe_active, probe_sent;
} db_device;
uint32_t db_crc32(const uint8_t *, size_t);
bool db_config_read(db_config *, const uint8_t *, size_t);
bool db_decode(db_message *, const uint8_t *, size_t);
size_t db_encode(uint8_t *, const db_message *);
bool db_ascii(uint8_t, uint8_t *, uint8_t *);
int db_hid_command(db_hid *, const db_message *);
bool db_hid_peek(const db_hid *, db_report *);
void db_hid_pop(db_hid *);
void db_hid_reset(db_hid *);
void db_init(db_device *, const db_config *, db_send_fn, db_complete_fn, void *);
void db_connect(db_device *);
void db_disconnect(db_device *);
bool db_receive(db_device *, const uint8_t *, size_t);
bool db_storage_begin(db_device *, uint8_t, uint32_t, const uint8_t *, uint32_t);
bool db_storage_begin_blocks(db_device *, uint8_t, uint32_t, const uint8_t *, uint32_t, uint32_t);
void db_tick(db_device *, uint32_t);
bool db_udp_probe(db_device *, const uint8_t *, size_t, uint32_t);
#endif
