#ifndef DB_STORAGE_PIPELINE_H
#define DB_STORAGE_PIPELINE_H
#include "devbox.h"
typedef void (*db_pipeline_done)(void *, uint8_t, bool, const uint8_t *, uint32_t);
typedef struct {
    db_device *device;
    db_pipeline_done done;
    void *ctx;
    bool foreground, queued, prefetch, cache_valid, hint, discard;
    uint8_t op;
    uint32_t lba, bytes, cache_lba, cache_bytes, hint_lba, generation;
    const uint8_t *write_data; /* Owned by caller until completion or abort. */
    uint8_t cache[DB_PAYLOAD];
} db_pipeline;
bool db_pipeline_start(db_pipeline *, uint8_t, uint32_t, const uint8_t *, uint32_t);
void db_pipeline_tick(db_pipeline *, uint32_t);
void db_pipeline_complete(db_pipeline *, bool, const uint8_t *, size_t);
void db_pipeline_abort(db_pipeline *);
#endif
