#include "devbox.h"
#include <string.h>

static uint16_t u16(const uint8_t *p) {
    return p[0] | ((uint16_t)p[1] << 8);
}
static uint32_t u32(const uint8_t *p) {
    return u16(p) | ((uint32_t)u16(p + 2) << 16);
}
static void put32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; i++)
        p[i] = (uint8_t)(v >> (8 * i));
}
uint32_t db_crc32(const uint8_t *p, size_t n) {
    uint32_t c = ~0u;
    while (n--) {
        c ^= *p++;
        for (int i = 0; i < 8; i++)
            c = (c >> 1) ^ ((0u - (c & 1)) & 0xedb88320u);
    }
    return ~c;
}
bool db_config_read(db_config *out, const uint8_t *p, size_t n) {
    memset(out, 0, sizeof(*out));
    if (n != 4096 || memcmp(p, "PICOUSB\0", 8) || u16(p + 8) != 1)
        return false;
    size_t len = u16(p + 10);
    if (!len || len > 4080 || p[16 + len - 1] || db_crc32(p + 16, len) != u32(p + 12))
        return false;
    bool ssid = false, pw = false, token = false, hostname = false;
    for (size_t i = 16; i < 16 + len;) {
        size_t end = i;
        while (end < 16 + len && p[end])
            end++;
        size_t eq = i;
        while (eq < end && p[eq] != '=')
            eq++;
        if (eq == i || eq == end || eq - i > 64)
            return false;
        for (size_t j = i; j < eq; j++)
            if (!((p[j] >= 'A' && p[j] <= 'Z') ||
                  (j > i && ((p[j] >= '0' && p[j] <= '9') || p[j] == '_'))))
                return false;
        size_t vlen = end - eq - 1;
        const uint8_t *v = p + eq + 1;
        char *dst = NULL;
        size_t max = 0;
        bool *seen = NULL;
        if (eq - i == 9 && !memcmp(p + i, "WIFI_SSID", 9)) {
            dst = out->ssid;
            max = 32;
            seen = &ssid;
        }
        if (eq - i == 13 && !memcmp(p + i, "WIFI_PASSWORD", 13)) {
            dst = out->password;
            max = 63;
            seen = &pw;
        }
        if (eq - i == 12 && !memcmp(p + i, "DEVICE_TOKEN", 12)) {
            dst = out->token;
            max = 128;
            seen = &token;
        }
        if (eq - i == 8 && !memcmp(p + i, "HOSTNAME", 8)) {
            /* A second bench Pico needs its own name; DNS label rules. */
            for (size_t j = 0; j < vlen; j++)
                if (!((v[j] >= 'a' && v[j] <= 'z') || (v[j] >= '0' && v[j] <= '9') ||
                      (v[j] == '-' && j && j + 1 < vlen)))
                    return false;
            dst = out->hostname;
            max = 32;
            seen = &hostname;
        }
        if (dst) {
            if (*seen || !vlen || vlen > max)
                return false;
            *seen = true;
            memcpy(dst, v, vlen);
        }
        i = end + 1;
    }
    return ssid && pw && strlen(out->password) >= 8 && (!token || strlen(out->token) >= 16);
}
bool db_decode(db_message *m, const uint8_t *p, size_t n) {
    if (n < DB_HEADER || memcmp(p, "PDB1", 4) || p[6] || p[7])
        return false;
    *m = (db_message){.op = p[4],
                      .status = p[5],
                      .id = u32(p + 8),
                      .session = u32(p + 12),
                      .a = u32(p + 16),
                      .b = u32(p + 20),
                      .length = u32(p + 24),
                      .data = p + 28};
    return m->id && m->length <= DB_PAYLOAD && n == DB_HEADER + m->length;
}
size_t db_encode(uint8_t *p, const db_message *m) {
    if (m->length > DB_PAYLOAD)
        return 0;
    memcpy(p, "PDB1", 4);
    p[4] = m->op;
    p[5] = m->status;
    p[6] = p[7] = 0;
    put32(p + 8, m->id);
    put32(p + 12, m->session);
    put32(p + 16, m->a);
    put32(p + 20, m->b);
    put32(p + 24, m->length);
    if (m->length)
        memcpy(p + 28, m->data, m->length);
    return 28 + m->length;
}
static void report(db_hid *h, uint8_t kind, int x, int y, int wheel) {
    h->counts[kind].accepted++;
    db_report *r = &h->queue[(h->head + h->count++) % DB_REPORTS];
    *r = (db_report){.kind = kind,
                     .modifiers = h->modifiers,
                     .buttons = h->buttons,
                     .x = (int8_t)x,
                     .y = (int8_t)y,
                     .wheel = (int8_t)wheel};
    memcpy(r->keys, h->keys, 6);
}
bool db_hid_peek(const db_hid *h, db_report *r) {
    if (!h->count)
        return false;
    *r = h->queue[h->head];
    return true;
}
void db_hid_pop(db_hid *h) {
    if (h->count) {
        h->head = (h->head + 1) % DB_REPORTS;
        h->count--;
    }
}
bool db_hid_next(const db_hid *h, const bool busy[3], db_report *r, uint16_t *slot) {
    for (uint16_t i = 0; i < h->count; i++) {
        uint16_t at = (uint16_t)((h->head + i) % DB_REPORTS);
        if (h->queue[at].kind < 3 && busy[h->queue[at].kind])
            continue;
        *r = h->queue[at];
        *slot = i;
        return true;
    }
    return false;
}
void db_hid_remove(db_hid *h, uint16_t slot) {
    if (slot >= h->count)
        return;
    for (uint16_t i = slot; i + 1 < h->count; i++)
        h->queue[(h->head + i) % DB_REPORTS] = h->queue[(h->head + i + 1) % DB_REPORTS];
    h->count--;
}
/* Queue a "nothing pressed" report for each interface the host reads, so a
 * session that dies mid-press leaves nothing held. An interface the host has
 * never polled (a BIOS reads only the boot keyboard) holds nothing, and a report
 * queued for it would never complete and would stall every HID fence. */
void db_hid_reset(db_hid *h) {
    uint32_t epoch = h->epoch + 1;
    bool pointer_seen = h->pointer_seen, polled[3];
    memcpy(polled, h->polled, sizeof(polled));
    uint8_t pointer[5]; memcpy(pointer, h->pointer, 5); pointer[0] = 0;
    memset(h, 0, sizeof(*h));
    h->epoch = epoch;
    memcpy(h->polled, polled, sizeof(polled));
    report(h, 0, 0, 0, 0);
    if (polled[1])
        report(h, 1, 0, 0, 0);
    if (pointer_seen) {
        h->pointer_seen = true; memcpy(h->pointer, pointer, 5);
        if (polled[2]) {
            report(h, 2, 0, 0, 0);
            db_report *r = &h->queue[(h->head + h->count - 1) % DB_REPORTS];
            r->length = 5; memcpy(r->raw, pointer, 5);
        }
    }
}
bool db_ascii(uint8_t ch, uint8_t *key, uint8_t *mod) {
    *mod = 0;
    if (ch >= 'a' && ch <= 'z') {
        *key = ch - 'a' + 4;
        return true;
    }
    if (ch >= 'A' && ch <= 'Z') {
        *key = ch - 'A' + 4;
        *mod = 2;
        return true;
    }
    if (ch >= '1' && ch <= '9') {
        *key = ch - '1' + 30;
        return true;
    }
    if (ch == '0') {
        *key = 39;
        return true;
    }
    if (ch == '\n' || ch == '\r') {
        *key = 40;
        return true;
    }
    if (ch == '\t') {
        *key = 43;
        return true;
    }
    if (ch == '\b') {
        *key = 42;
        return true;
    }
    const char *plain = " -=[]\\;\x27`,./";
    const uint8_t codes[] = {44, 45, 46, 47, 48, 49, 51, 52, 53, 54, 55, 56};
    const char *shift = "_+{}|:\"~<>?";
    const uint8_t shifted[] = {45, 46, 47, 48, 49, 51, 52, 53, 54, 55, 56};
    for (size_t i = 0; i < strlen(plain); i++)
        if (ch == (uint8_t)plain[i]) {
            *key = codes[i];
            return true;
        }
    for (size_t i = 0; i < strlen(shift); i++)
        if (ch == (uint8_t)shift[i]) {
            *key = shifted[i];
            *mod = 2;
            return true;
        }
    const char *nums = "!@#$%^&*()";
    for (int i = 0; i < 10; i++)
        if (ch == (uint8_t)nums[i]) {
            *key = i == 9 ? 39 : (uint8_t)(30 + i);
            *mod = 2;
            return true;
        }
    return false;
}
static int key_change(db_hid *h, uint32_t key, bool down) {
    if (key >= 224 && key <= 231) {
        if (down)
            h->modifiers |= 1u << (key - 224);
        else
            h->modifiers &= ~(1u << (key - 224));
        return DB_OK;
    }
    if (key < 4 || key > 115)
        return DB_ARGUMENT;
    int empty = -1;
    for (int i = 0; i < 6; i++) {
        if (h->keys[i] == key) {
            if (!down)
                h->keys[i] = 0;
            return DB_OK;
        }
        if (!h->keys[i])
            empty = i;
    }
    if (!down)
        return DB_OK;
    if (empty < 0)
        return DB_LIMIT;
    h->keys[empty] = (uint8_t)key;
    return DB_OK;
}
int db_hid_command(db_hid *h, const db_message *m) {
    if (m->op == DB_RAW_REPORT || m->op == DB_POINTER) {
        if (h->count == DB_REPORTS) return DB_BUSY;
        uint8_t kind = m->op == DB_POINTER ? 2 : (uint8_t)m->a;
        uint8_t raw[8] = {0};
        unsigned len = kind == 0 ? 8 : 5;
        if (m->op == DB_POINTER) {
            if (m->a > 32767 || m->b > 32767 || m->length != 1 || m->data[0] > 7)
                return DB_ARGUMENT;
            raw[0] = m->data[0]; raw[1] = (uint8_t)m->a; raw[2] = (uint8_t)(m->a >> 8);
            raw[3] = (uint8_t)m->b; raw[4] = (uint8_t)(m->b >> 8);
        } else {
            if (m->a > 2 || m->b || m->length != len) return DB_ARGUMENT;
            memcpy(raw, m->data, len);
            if ((kind == 0 && raw[1]) || (kind == 2 && (raw[0] > 7 || raw[2] > 127 || raw[4] > 127)))
                return DB_ARGUMENT;
        }
        if (!kind) { h->modifiers = raw[0]; memcpy(h->keys, raw + 2, 6); }
        if (kind == 1) h->buttons = raw[0];
        if (kind == 2) { h->pointer_seen = true; memcpy(h->pointer, raw, 5); }
        report(h, kind, 0, 0, 0);
        db_report *r = &h->queue[(h->head + h->count - 1) % DB_REPORTS];
        r->length = (uint8_t)len; memcpy(r->raw, raw, len);
        return DB_OK;
    }
    if (m->op != DB_KEY_TEXT && m->length)
        return DB_ARGUMENT;
    unsigned need = m->op == DB_KEY_TEXT ? m->length * 2 : 2;
    if (need > (unsigned)(DB_REPORTS - h->count))
        return DB_BUSY;
    if (m->op == DB_KEY_TEXT) {
        if (m->length > 64 || m->a || m->b)
            return DB_ARGUMENT;
        if (h->modifiers || memcmp(h->keys, "\0\0\0\0\0\0", 6))
            return DB_BUSY;
        uint8_t k, mod;
        for (uint32_t i = 0; i < m->length; i++)
            if (!db_ascii(m->data[i], &k, &mod))
                return DB_ARGUMENT;
        for (uint32_t i = 0; i < m->length; i++) {
            db_ascii(m->data[i], &k, &mod);
            h->keys[0] = k;
            h->modifiers = mod;
            report(h, 0, 0, 0, 0);
            h->keys[0] = 0;
            h->modifiers = 0;
            report(h, 0, 0, 0, 0);
        }
        return DB_OK;
    }
    if (m->op >= DB_KEY_DOWN && m->op <= DB_KEY_RELEASE) {
        if (m->b > 255 || (m->op != DB_KEY_PRESS && m->b))
            return DB_ARGUMENT;
        if (m->op == DB_KEY_RELEASE) {
            if (m->a)
                return DB_ARGUMENT;
            h->modifiers = 0;
            memset(h->keys, 0, 6);
            report(h, 0, 0, 0, 0);
            return DB_OK;
        }
        uint8_t oldkeys[6], oldmod = h->modifiers;
        memcpy(oldkeys, h->keys, 6);
        if (m->op == DB_KEY_PRESS) {
            for (int i = 0; i < 6; i++)
                if (h->keys[i] == m->a)
                    return DB_BUSY;
        }
        int e = key_change(h, m->a, m->op != DB_KEY_UP);
        if (e)
            return e;
        if (m->op == DB_KEY_PRESS)
            h->modifiers |= (uint8_t)m->b;
        report(h, 0, 0, 0, 0);
        if (m->op == DB_KEY_PRESS) {
            memcpy(h->keys, oldkeys, 6);
            h->modifiers = oldmod;
            report(h, 0, 0, 0, 0);
        }
        return DB_OK;
    }
    int32_t a = (int32_t)m->a, b = (int32_t)m->b;
    if (m->op == DB_MOUSE_MOVE) {
        if (a < -127 || a > 127 || b < -127 || b > 127)
            return DB_ARGUMENT;
        report(h, 1, a, b, 0);
        return DB_OK;
    }
    if (m->op == DB_MOUSE_SCROLL) {
        if (a < -127 || a > 127 || b)
            return DB_ARGUMENT;
        report(h, 1, 0, 0, a);
        return DB_OK;
    }
    if (m->op == DB_MOUSE_RELEASE) {
        if (m->a || m->b)
            return DB_ARGUMENT;
        h->buttons = 0;
        report(h, 1, 0, 0, 0);
        return DB_OK;
    }
    if (m->op >= DB_MOUSE_DOWN && m->op <= DB_MOUSE_CLICK && m->op != DB_MOUSE_SCROLL) {
        if ((a != 1 && a != 2 && a != 4) || b)
            return DB_ARGUMENT;
        uint8_t old = h->buttons;
        if (m->op == DB_MOUSE_CLICK && (old & a))
            return DB_BUSY;
        if (m->op == DB_MOUSE_UP)
            h->buttons &= ~a;
        else
            h->buttons |= a;
        report(h, 1, 0, 0, 0);
        if (m->op == DB_MOUSE_CLICK) {
            h->buttons = old;
            report(h, 1, 0, 0, 0);
        }
        return DB_OK;
    }
    return DB_ARGUMENT;
}
void db_init(db_device *d, const db_config *c, db_send_fn send, db_complete_fn complete,
             void *ctx) {
    memset(d, 0, sizeof(*d));
    d->config = *c;
    d->send = send;
    d->complete = complete;
    d->ctx = ctx;
    d->next_storage = 0x80000000u;
    d->sectors = DB_SECTORS;
    for (unsigned i = 0; i < 3; i++) d->usb.protocol_value[i] = 1;
}
void db_disconnect(db_device *d) {
    d->udp_reads = false;
    d->udp_id = 0;
    memset(d->udp_cookie, 0, sizeof(d->udp_cookie));
    d->probe_active = false;
    memset(d->probe_cookie, 0, sizeof(d->probe_cookie));
    d->media_generation++;
    d->authenticated = false;
    d->media = false;
    db_hid_reset(&d->hid);
    if (d->pending) {
        d->pending = false;
        d->complete(d->ctx, false, NULL, 0);
    }
}
void db_connect(db_device *d) {
    db_disconnect(d);
    d->session++;
    if (!d->session)
        d->session++;
    d->last_request = 0;
}
static bool send_message(db_device *d, const db_message *m) {
    static uint8_t buf[DB_MAX]; /* Single-threaded transport; keep bulk data off stack. */
    size_t n = db_encode(buf, m);
    return n && d->send(d->ctx, buf, n);
}
bool db_receive(db_device *d, const uint8_t *p, size_t n) {
    db_message m;
    if (!db_decode(&m, p, n))
        return false;
    if (m.op & 128) {
        if (!d->authenticated || m.session != d->session || !d->pending || m.id != d->pending_id ||
            m.op != (d->pending_op | 128))
            return false;
        if (d->udp_reads && d->pending_op == DB_READ && !m.status) return false;
        bool ok = !m.status && m.length == (d->pending_op == DB_READ ? d->pending_bytes : 0u);
        d->pending = false;
        if (!ok)
            d->media = false;
        d->complete(d->ctx, ok, m.data, m.length);
        return true;
    }
    db_message r = {.op = m.op | 128, .id = m.id, .session = d->session};
    if (m.status || m.id >= 0x80000000u || m.id <= d->last_request)
        return false;
    d->last_request = m.id;
    if (m.op == DB_AUTH_OP) {
        size_t len = strlen(d->config.token);
        uint8_t diff = (uint8_t)(len != m.length);
        if (m.length == len)
            for (size_t i = 0; i < len; i++)
                diff |= d->config.token[i] ^ m.data[i];
        if (d->authenticated || m.session || m.a || m.b || diff)
            r.status = DB_AUTH;
        else
            d->authenticated = true;
    } else if (!d->authenticated || m.session != d->session)
        r.status = DB_AUTH;
    else if (m.op == DB_STATUS) {
        r.a = d->media;
        r.b = d->hid.count;
        if (m.length || m.a || m.b)
            r.status = DB_ARGUMENT;
    } else if (m.op == DB_CONSOLE_STATUS) {
        if (m.length || m.a || m.b)
            r.status = DB_ARGUMENT;
        else {
            uint8_t out[32];
            uint32_t words[8] = {1, d->console.target_powered, d->console.client,
                                 d->console.rx_level, d->console.rx_bytes, d->console.rx_errors,
                                 d->console.dropped, d->console.tx_queued};
            for (unsigned i = 0; i < 8; i++)
                put32(out + i * 4, words[i]);
            r.data = out;
            r.length = sizeof(out);
            return send_message(d, &r);
        }
    } else if (m.op == DB_BOOTSEL) {
        if (m.length || m.a || m.b)
            r.status = DB_ARGUMENT;
        else
            d->bootsel = true;
    } else if (m.op == DB_USB_STATUS) {
        if (m.a || m.b || m.length) r.status = DB_ARGUMENT;
        else {
            uint8_t out[156];
            uint32_t words[39] = {1, d->usb.mounted, d->usb.suspended, d->usb.resets,
                d->usb.configurations, d->hid.epoch, d->clock_ms,
                d->usb.configured_ms, d->usb.network_ms};
            for (unsigned i = 0; i < 3; i++) {
                unsigned j = 9 + i * 10;
                db_hid_counts *v = &d->hid.counts[i];
                words[j] = v->accepted; words[j+1] = v->submitted; words[j+2] = v->completed;
                words[j+3] = v->completed ? d->clock_ms - v->last_ms : UINT32_MAX;
                words[j+4] = d->usb.idle[i]; words[j+5] = d->usb.protocol[i];
                words[j+6] = d->usb.protocol_value[i]; words[j+7] = i == 2 ? 0x84 : 0x81 + i;
                words[j+8] = d->usb.completed[i];
                words[j+9] = d->usb.completed[i] ? d->clock_ms - d->usb.last_ms[i] : UINT32_MAX;
            }
            for (unsigned i = 0; i < 39; i++) put32(out + i*4, words[i]);
            r.data = out; r.length = sizeof(out); return send_message(d, &r);
        }
    } else if (m.op == DB_METRICS) {
        if (m.length || m.a || m.b) {
            r.status = DB_ARGUMENT;
        } else {
            uint8_t payload[DB_METRIC_COUNT * 48];
            for (unsigned i = 0; i < DB_METRIC_COUNT; i++) {
                const db_metric *v = &d->metrics[i];
                uint8_t *out = payload + i * 48;
                put32(out, v->count);
                put32(out + 4, (uint32_t)v->total);
                put32(out + 8, (uint32_t)(v->total >> 32));
                put32(out + 12, v->maximum);
                for (unsigned j = 0; j < 8; j++)
                    put32(out + 16 + j * 4, v->bins[j]);
            }
            r.a = 1; /* metrics layout version */
            r.b = DB_METRIC_COUNT;
            r.length = sizeof(payload);
            r.data = payload;
            return send_message(d, &r);
        }
    } else if (m.op == DB_UDP_PROBE) {
        if (m.b || (m.length != 0 && m.length != 16) ||
            (!m.length && m.a) || (m.length && (!m.a || m.a > 60000))) {
            r.status = DB_ARGUMENT;
        } else {
            if (m.length) {
                memcpy(d->probe_cookie, m.data, 16);
                d->probe_until = d->clock_ms + m.a;
                d->probe_active = true;
                d->probe_sent = false;
                memset(d->probe_stats, 0, sizeof(d->probe_stats));
            }
            uint8_t stats[20];
            for (unsigned i = 0; i < 5; i++) put32(stats + 4*i, d->probe_stats[i]);
            r.a = 1;
            r.b = 8081;
            r.data = stats;
            r.length = sizeof(stats);
            return send_message(d, &r);
        }
    } else if (m.op == DB_UDP_READS) {
        if (m.a != 1 || m.b || m.length != 16)
            r.status = DB_ARGUMENT;
        else if (d->media || d->pending)
            r.status = DB_BUSY;
        else {
            d->udp_reads = true;
            d->udp_id = 0;
            memcpy(d->udp_cookie, m.data, 16);
            r.a = 1;
            r.b = 8081;
        }
    } else if (m.op == DB_ATTACH) {
        if (!m.a || m.a > 0x7fffffffu || m.b != 512 ||
            (m.length && (m.length != 1 || m.data[0] > 1)))
            r.status = DB_ARGUMENT;
        else if (d->media || d->pending)
            r.status = DB_BUSY;
        else {
            d->media_generation++;
            d->media = true;
            d->changed = true;
            d->sectors = m.a;
            d->read_only = m.length && m.data[0];
        }
    } else if (m.op == DB_DETACH) {
        if (m.a || m.b || m.length)
            r.status = DB_ARGUMENT;
        else if (d->pending)
            r.status = DB_BUSY;
        else {
            d->media_generation++;
            d->media = false;
            d->changed = true;
        }
    } else {
        r.status = (uint8_t)db_hid_command(&d->hid, &m);
        if (!r.status) {
            uint8_t fence[16]; put32(fence, d->hid.epoch);
            for (unsigned i=0; i<3; i++) put32(fence+4+i*4, d->hid.counts[i].accepted);
            r.data=fence; r.length=sizeof(fence); return send_message(d,&r);
        }
    }
    return send_message(d, &r);
}
bool db_storage_begin(db_device *d, uint8_t op, uint32_t lba, const uint8_t *data, uint32_t now) {
    return db_storage_begin_blocks(d, op, lba, data, now, op <= DB_WRITE ? 512 : 0);
}
bool db_storage_begin_blocks(db_device *d, uint8_t op, uint32_t lba, const uint8_t *data,
                            uint32_t now, uint32_t bytes) {
    bool blocks = op == DB_READ || op == DB_WRITE;
    if (blocks ? (!bytes || bytes > DB_PAYLOAD || bytes % 512 ||
                  lba >= d->sectors || bytes / 512 > d->sectors - lba) : (bytes || lba))
        return false;
    if (op == DB_WRITE && !data)
        return false;
    if (!d->authenticated || !d->media || d->pending || lba >= d->sectors ||
        (op == DB_WRITE && d->read_only) ||
        d->next_storage == UINT32_MAX)
        return false;
    if (op < DB_READ || op > DB_EJECT)
        return false;
    db_message m = {.op = op,
                    .id = ++d->next_storage,
                    .session = d->session,
                    .a = lba,
                    .b = blocks ? bytes / 512 : 0,
                    .length = op == DB_WRITE ? bytes : 0,
                    .data = data};
    d->pending = true;
    d->pending_id = m.id;
    d->pending_op = op;
    d->pending_bytes = bytes;
    d->deadline = now + (op >= DB_FLUSH ? 30000 : 5000);
    if (!send_message(d, &m)) {
        d->pending = false;
        d->media = false;
        return false;
    }
    return true;
}
void db_tick(db_device *d, uint32_t now) {
    d->clock_ms = now;
    if (d->pending && (int32_t)(now - d->deadline) >= 0) {
        d->pending = false;
        d->media = false;
        d->complete(d->ctx, false, NULL, 0);
    }
}

bool db_udp_probe(db_device *d, const uint8_t *data, size_t n, uint32_t now) {
    if (!d->authenticated || !d->probe_active || (int32_t)(now - d->probe_until) >= 0)
        return false;
    if (n < 28 || n > 1200 || memcmp(data, "PDU1", 4) || u32(data + 4) != d->session ||
        memcmp(data + 8, d->probe_cookie, 16) || !u32(data + 24)) {
        d->probe_stats[2]++;
        return false;
    }
    d->probe_stats[0]++;
    if (d->probe_sent && (uint32_t)(now - d->probe_last) < 2) {
        d->probe_stats[3]++;
        return false;
    }
    d->probe_sent = true;
    d->probe_last = now;
    return true;
}

size_t db_udp_read(db_device *d, const uint8_t *p, size_t n, uint8_t *ack, uint32_t now) {
    if (!d->authenticated || !d->media || !d->udp_reads || n < 36 || n > 1060 ||
        memcmp(p, "PDR1", 4) || u32(p + 4) != d->session ||
        memcmp(p + 12, d->udp_cookie, 16)) return 0;
    uint32_t id = u32(p + 8), crc = u32(p + 32);
    unsigned size = p[28] | (unsigned)p[29] << 8;
    unsigned index = p[30] | (unsigned)p[31] << 8;
    if (!size || size > DB_PAYLOAD || size % 512 || index >= (size + 1023)/1024) return 0;
    unsigned offset = index * 1024, bytes = size - offset;
    if (bytes > 1024) bytes = 1024;
    if (n != 36 + bytes) return 0;
    uint16_t full = (uint16_t)((1u << ((size + 1023)/1024)) - 1);
    bool same = id == d->udp_id && d->udp_generation == d->media_generation;
    /* A lost final ACK may be retried after USB has queued its next request. */
    bool completed = same && d->udp_mask == full && d->udp_size == size && d->udp_crc == crc;
    if (!completed) {
        if (!d->pending || d->pending_op != DB_READ || id != d->pending_id ||
            size != d->pending_bytes || (int32_t)(now - d->deadline) >= 0) return 0;
        if (!same) {
            d->udp_id = id;
            d->udp_generation = d->media_generation;
            d->udp_size = (uint16_t)size;
            d->udp_crc = crc;
            d->udp_mask = 0;
        }
        if (d->udp_size != size || d->udp_crc != crc) return 0;
        uint16_t bit = (uint16_t)(1u << index);
        if (!(d->udp_mask & bit)) {
            memcpy(d->udp_data + offset, p + 36, bytes);
            d->udp_mask |= bit;
        }
        if (d->udp_mask == full) {
            if (db_crc32(d->udp_data, size) != crc) {
                d->pending = false;
                d->media = false;
                d->complete(d->ctx, false, NULL, 0);
                return 0;
            } else {
                d->pending = false;
                d->complete(d->ctx, true, d->udp_data, size);
            }
        }
    }
    memcpy(ack, p, 36);
    memcpy(ack, "PDA1", 4);
    ack[30] = (uint8_t)d->udp_mask;
    ack[31] = (uint8_t)(d->udp_mask >> 8);
    return 36;
}
