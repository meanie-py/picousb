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
    bool ssid = false, pw = false, token = false;
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
void db_hid_reset(db_hid *h) {
    memset(h, 0, sizeof(*h));
    report(h, 0, 0, 0, 0);
    report(h, 1, 0, 0, 0);
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
}
void db_disconnect(db_device *d) {
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
    uint8_t buf[DB_MAX];
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
        bool ok = !m.status && m.length == (d->pending_op == DB_READ ? 512u : 0u);
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
    } else if (m.op == DB_ATTACH) {
        if (m.a != DB_SECTORS || m.b != 512 || m.length)
            r.status = DB_ARGUMENT;
        else if (d->media || d->pending)
            r.status = DB_BUSY;
        else {
            d->media = true;
            d->changed = true;
        }
    } else if (m.op == DB_DETACH) {
        if (m.a || m.b || m.length)
            r.status = DB_ARGUMENT;
        else if (d->pending)
            r.status = DB_BUSY;
        else {
            d->media = false;
            d->changed = true;
        }
    } else
        r.status = (uint8_t)db_hid_command(&d->hid, &m);
    return send_message(d, &r);
}
bool db_storage_begin(db_device *d, uint8_t op, uint32_t lba, const uint8_t *data, uint32_t now) {
    if (!d->authenticated || !d->media || d->pending || lba >= DB_SECTORS ||
        d->next_storage == UINT32_MAX)
        return false;
    if (op < DB_READ || op > DB_EJECT)
        return false;
    db_message m = {.op = op,
                    .id = ++d->next_storage,
                    .session = d->session,
                    .a = lba,
                    .b = (op == DB_READ || op == DB_WRITE) ? 1 : 0,
                    .length = op == DB_WRITE ? 512 : 0,
                    .data = data};
    d->pending = true;
    d->pending_id = m.id;
    d->pending_op = op;
    d->deadline = now + (op >= DB_FLUSH ? 30000 : 5000);
    if (!send_message(d, &m)) {
        d->pending = false;
        d->media = false;
        return false;
    }
    return true;
}
void db_tick(db_device *d, uint32_t now) {
    if (d->pending && (int32_t)(now - d->deadline) >= 0) {
        d->pending = false;
        d->media = false;
        d->complete(d->ctx, false, NULL, 0);
    }
}
