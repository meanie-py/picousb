#include "devbox.h"
#include "websocket.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static uint8_t sent[DB_MAX];
static size_t sent_n;
static int completions;
static bool completed_ok;
static bool send(void *ctx, const uint8_t *p, size_t n) {
    (void)ctx;
    memcpy(sent, p, n);
    sent_n = n;
    return true;
}
static void complete(void *ctx, bool ok, const uint8_t *p, size_t n) {
    (void)ctx;
    (void)p;
    (void)n;
    completions++;
    completed_ok = ok;
}
static void put32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; i++)
        p[i] = (uint8_t)(v >> (8 * i));
}
static void config_test(void) {
    uint8_t b[4096];
    memset(b, 255, sizeof(b));
    const char values[] =
        "WIFI_SSID=test\0WIFI_PASSWORD=synthetic\0DEVICE_TOKEN=synthetic-test-token\0OTHER=value\0";
    size_t n = sizeof(values) - 1;
    memcpy(b, "PICOUSB\0", 8);
    b[8] = 1;
    b[9] = 0;
    b[10] = (uint8_t)n;
    b[11] = 0;
    memcpy(b + 16, values, n);
    put32(b + 12, db_crc32(b + 16, n));
    db_config c;
    assert(db_config_read(&c, b, sizeof(b)));
    assert(!strcmp(c.ssid, "test"));
    assert(!strcmp(c.token, "synthetic-test-token"));
    for (size_t i = 0; i < 16 + n; i++) {
        b[i] ^= 1;
        assert(!db_config_read(&c, b, sizeof(b)));
        b[i] ^= 1;
    }
    assert(!db_config_read(&c, b, 4095));
    assert(db_crc32((const uint8_t *)"123456789", 9) == 0xcbf43926);
}
static void hid_test(void) {
    db_hid h = {0};
    db_message m = {.op = DB_KEY_DOWN, .a = 224};
    assert(!db_hid_command(&h, &m));
    assert(h.modifiers == 1);
    m.op = DB_KEY_PRESS;
    m.a = 4;
    m.b = 2;
    assert(!db_hid_command(&h, &m));
    assert(h.modifiers == 1 && !h.keys[0]);
    assert(h.queue[1].modifiers == 3);
    m = (db_message){.op = DB_KEY_RELEASE};
    assert(!db_hid_command(&h, &m));
    assert(!h.modifiers);
    for (int i = 4; i < 10; i++) {
        m.op = DB_KEY_DOWN;
        m.a = (uint32_t)i;
        assert(!db_hid_command(&h, &m));
    }
    m.a = 10;
    assert(db_hid_command(&h, &m) == DB_LIMIT);
    db_hid_reset(&h);
    uint8_t key, mod;
    for (int i = 32; i < 127; i++)
        assert(db_ascii((uint8_t)i, &key, &mod));
    assert(!db_ascii(128, &key, &mod));
    m = (db_message){.op = DB_KEY_TEXT, .data = (uint8_t *)"aA!\n", .length = 4};
    assert(!db_hid_command(&h, &m));
    assert(h.count == 10);
    assert(h.queue[4].modifiers == 2);
    m.data = (uint8_t *)"\x80";
    m.length = 1;
    unsigned before = h.count;
    assert(db_hid_command(&h, &m) == DB_ARGUMENT);
    assert(h.count == before);
    m = (db_message){.op = DB_MOUSE_MOVE, .a = (uint32_t)-127, .b = 127};
    assert(!db_hid_command(&h, &m));
    m.a = 128;
    assert(db_hid_command(&h, &m) == DB_ARGUMENT);
    m = (db_message){.op = DB_MOUSE_DOWN, .a = 1};
    assert(!db_hid_command(&h, &m));
    assert(h.buttons == 1);
    m.op = DB_MOUSE_CLICK;
    m.a = 2;
    assert(!db_hid_command(&h, &m));
    assert(h.buttons == 1);
    h.count = 255;
    m.op = DB_KEY_PRESS;
    m.a = 4;
    assert(db_hid_command(&h, &m) == DB_BUSY);
}
static void protocol_test(void) {
    db_config c = {.token = "synthetic-test-token"};
    db_device d;
    db_init(&d, &c, send, complete, NULL);
    db_connect(&d);
    uint8_t b[DB_MAX];
    db_message m = {.op = DB_STATUS, .id = 1}, r;
    size_t n = db_encode(b, &m);
    assert(db_receive(&d, b, n));
    assert(db_decode(&r, sent, sent_n) && r.status == DB_AUTH);
    m = (db_message){.op = DB_AUTH_OP, .id = 2, .data = (uint8_t *)"bad", .length = 3};
    n = db_encode(b, &m);
    assert(db_receive(&d, b, n));
    assert(!d.authenticated);
    m.id = 3;
    m.data = (uint8_t *)c.token;
    m.length = (uint32_t)strlen(c.token);
    n = db_encode(b, &m);
    assert(db_receive(&d, b, n) && d.authenticated);
    assert(!db_receive(&d, b, n));
    m = (db_message){.op = DB_ATTACH, .id = 4, .session = d.session, .a = DB_SECTORS, .b = 512};
    n = db_encode(b, &m);
    assert(db_receive(&d, b, n) && d.media);
    assert(db_storage_begin(&d, DB_READ, 0, NULL, 100));
    assert(!db_storage_begin(&d, DB_READ, 1, NULL, 100));
    db_tick(&d, 5099);
    assert(d.pending);
    db_tick(&d, 5100);
    assert(!d.pending && !d.media && completions == 1 && !completed_ok);
    d.media = true;
    assert(db_storage_begin(&d, DB_WRITE, 0, b, UINT32_MAX - 100));
    db_tick(&d, 4899);
    assert(!d.pending && completions == 2);
    d.media = true;
    assert(db_storage_begin(&d, DB_READ, 0, NULL, 0));
    db_disconnect(&d);
    assert(completions == 3 && !d.media && !d.authenticated);
    m = (db_message){.op = DB_STATUS, .id = 2, .length = 1024, .data = b};
    n = db_encode(sent, &m);
    assert(db_decode(&r, sent, n));
    assert(!db_decode(&r, sent, n - 1));
    sent[0] = 'X';
    assert(!db_decode(&r, sent, n));
}
static void websocket_test(void) {
    uint8_t p[] = {0x82, 0x83, 1, 2, 3, 4, 'a' ^ 1, 'b' ^ 2, 'c' ^ 3}, out[DB_MAX], op;
    size_t n;
    for (size_t i = 0; i < sizeof(p); i++)
        assert(ws_decode(p, i, &op, out, &n) == 0);
    assert(ws_decode(p, sizeof(p), &op, out, &n) == 9 && n == 3 && !memcmp(out, "abc", 3));
    p[1] = 3;
    assert(ws_decode(p, sizeof(p), &op, out, &n) == -1);
    uint8_t frame[DB_MAX + 8], data[DB_MAX];
    memset(data, 'x', sizeof(data));
    assert(ws_encode(frame, 2, data, 3) == 5 && frame[1] == 3);
    assert(ws_encode(frame, 2, data, DB_MAX) == DB_MAX + 4);
    assert(frame[1] == 126 && frame[2] == (DB_MAX >> 8));
    assert(!ws_encode(frame, 2, data, DB_MAX + 1));
    frame[0] = 0x82;
    frame[1] = 0xfe;
    frame[2] = 2;
    frame[3] = 0;
    memset(frame + 4, 0, 4);
    memcpy(frame + 8, data, 512);
    assert(ws_decode(frame, 519, &op, out, &n) == 0);
    assert(ws_decode(frame, 520, &op, out, &n) == 520 && n == 512);
    frame[0] = 0x89;
    assert(ws_decode(frame, 520, &op, out, &n) == -1);
    frame[0] = 0x82;
    frame[2] = 0;
    frame[3] = 1;
    assert(ws_decode(frame, 520, &op, out, &n) == -1);
    uint32_t random = 1;
    for (int j = 0; j < 100000; j++) {
        for (size_t i = 0; i < sizeof(out); i++) {
            random = random * 1664525u + 1013904223u;
            out[i] = (uint8_t)(random >> 24);
        }
        db_message m;
        db_decode(&m, out, (size_t)(random % sizeof(out)));
        uint8_t decoded[DB_MAX];
        ws_decode(out, (size_t)(random % sizeof(out)), &op, decoded, &n);
    }
}
static void storage_success_test(void) {
    db_config c = {0};
    db_device d;
    db_init(&d, &c, send, complete, NULL);
    db_connect(&d);
    uint8_t buf[DB_MAX], sector[512] = {0};
    db_message m = {.op = DB_AUTH_OP, .id = 1};
    assert(db_receive(&d, buf, db_encode(buf, &m)) && d.authenticated);
    m = (db_message){.op = DB_ATTACH, .id = 2, .session = d.session, .a = DB_SECTORS, .b = 512};
    assert(db_receive(&d, buf, db_encode(buf, &m)) && d.media);
    assert(!db_storage_begin(&d, DB_READ, DB_SECTORS, NULL, 0));
    assert(db_storage_begin(&d, DB_READ, DB_SECTORS - 1, NULL, 0));
    m = (db_message){.op = DB_READ | 128,
                     .id = d.pending_id,
                     .session = d.session,
                     .data = sector,
                     .length = 512};
    assert(db_receive(&d, buf, db_encode(buf, &m)) && completed_ok && !d.pending);
    assert(!db_receive(&d, buf, db_encode(buf, &m)));
    assert(db_storage_begin(&d, DB_WRITE, 0, sector, 0));
    m = (db_message){.op = DB_WRITE | 128, .id = d.pending_id, .session = d.session};
    assert(db_receive(&d, buf, db_encode(buf, &m)) && completed_ok);
    assert(db_storage_begin(&d, DB_FLUSH, 0, NULL, 0));
    db_tick(&d, 5000);
    assert(d.pending);
    m = (db_message){.op = DB_FLUSH | 128, .id = d.pending_id, .session = d.session};
    assert(db_receive(&d, buf, db_encode(buf, &m)) && completed_ok);
    m = (db_message){.op = DB_DETACH, .id = 3, .session = d.session};
    assert(db_receive(&d, buf, db_encode(buf, &m)) && !d.media);
    assert(!db_storage_begin(&d, DB_READ, 0, NULL, 0));
    db_connect(&d);
    assert(d.session == 2 && !d.authenticated);
}
int main(void) {
    config_test();
    hid_test();
    protocol_test();
    websocket_test();
    storage_success_test();
    puts("portable core tests passed");
    return 0;
}
