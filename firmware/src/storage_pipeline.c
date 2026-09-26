#include "storage_pipeline.h"
#include <string.h>

static void finish(db_pipeline *p, bool ok, const uint8_t *data) {
    uint8_t op = p->op;
    uint32_t bytes = p->bytes;
    p->foreground = p->queued = false;
    p->write_data = NULL;
    p->hint = ok && op == DB_READ && bytes == DB_PAYLOAD &&
              p->lba + bytes / 512 < p->device->sectors;
    p->hint_lba = p->lba + bytes / 512;
    p->done(p->ctx, op, ok, data, bytes);
}

bool db_pipeline_start(db_pipeline *p, uint8_t op, uint32_t lba,
                       const uint8_t *data, uint32_t bytes) {
    db_device *d = p->device;
    bool blocks = op == DB_READ || op == DB_WRITE;
    if (p->generation != d->media_generation && !p->foreground) {
        p->cache_valid = p->hint = false;
        p->discard = p->prefetch;
        p->generation = d->media_generation;
    }
    if (!d->authenticated || !d->media || p->foreground ||
        op < DB_READ || op > DB_EJECT ||
        (blocks ? (!bytes || bytes > DB_PAYLOAD || bytes % 512 ||
                   lba >= d->sectors || bytes / 512 > d->sectors - lba) : (bytes || lba)) ||
        (op == DB_WRITE && (!data || d->read_only)))
        return false;
    p->foreground = p->queued = true;
    p->op = op;
    p->lba = lba;
    p->bytes = bytes;
    p->write_data = data;
    p->hint = false;
    if (op != DB_READ) {
        p->cache_valid = false;
        p->discard = p->prefetch;
    }
    return true;
}

void db_pipeline_tick(db_pipeline *p, uint32_t now) {
    db_device *d = p->device;
    if (p->generation != d->media_generation) {
        p->cache_valid = p->hint = false;
        p->discard = p->prefetch;
        p->generation = d->media_generation;
        if (p->queued)
            finish(p, false, NULL);
    }
    if (!d->authenticated || !d->media) {
        p->hint = p->cache_valid = false;
        if (p->queued)
            finish(p, false, NULL);
        return;
    }
    if (d->pending || p->prefetch)
        return;
    if (p->queued) {
        if (p->op == DB_READ && p->cache_valid && p->lba >= p->cache_lba &&
            p->lba - p->cache_lba <= p->cache_bytes / 512 &&
            p->bytes / 512 <= p->cache_bytes / 512 - (p->lba - p->cache_lba)) {
            const uint8_t *data = p->cache + (p->lba - p->cache_lba) * 512;
            p->cache_valid = false; /* Consume once; no stale reuse after writes. */
            finish(p, true, data);
            return;
        }
        p->cache_valid = false;
        p->queued = false;
        if (!db_storage_begin_blocks(d, p->op, p->lba, p->write_data, now, p->bytes))
            finish(p, false, NULL);
    } else if (!p->foreground && p->hint) {
        p->hint = false;
        p->cache_lba = p->hint_lba;
        uint32_t remaining = d->sectors - p->cache_lba;
        p->cache_bytes = (remaining < DB_PAYLOAD / 512 ? remaining : DB_PAYLOAD / 512) * 512;
        p->cache_valid = p->discard = false;
        p->prefetch = db_storage_begin_blocks(d, DB_READ, p->cache_lba, NULL, now, p->cache_bytes);
    }
}

void db_pipeline_complete(db_pipeline *p, bool ok, const uint8_t *data, size_t bytes) {
    if (p->prefetch) {
        p->prefetch = false;
        p->cache_valid = ok && !p->discard && bytes == p->cache_bytes;
        if (p->cache_valid)
            memcpy(p->cache, data, bytes);
        p->discard = false;
        /* No TinyUSB completion: this was speculative, not a USB request. */
    } else if (p->foreground && !p->queued) {
        finish(p, ok, data);
    }
}

void db_pipeline_abort(db_pipeline *p) {
    if (p->device->pending)
        p->device->media = false;
    p->device->pending = false;
    p->foreground = p->queued = p->prefetch = p->cache_valid = p->hint = p->discard = false;
    p->write_data = NULL;
}
