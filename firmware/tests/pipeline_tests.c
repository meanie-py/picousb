#include "storage_pipeline.h"
#include <assert.h>
#include <string.h>
static db_device d;
static db_pipeline p;
static unsigned sends, completions;
static bool success;
static uint8_t sent[DB_MAX], frame[DB_MAX], data[DB_PAYLOAD], output[DB_PAYLOAD];
static db_message request;
static bool send(void *ctx, const uint8_t *bytes, size_t n) {
    (void)ctx;
    memcpy(sent, bytes, n);
    assert(db_decode(&request, sent, n));
    sends++;
    return true;
}
static void done(void *ctx, uint8_t op, bool ok, const uint8_t *bytes, uint32_t n) {
    (void)ctx;
    completions++;
    success = ok;
    if (ok && op == DB_READ) memcpy(output, bytes, n);
}
static void complete(void *ctx, bool ok, const uint8_t *bytes, size_t n) {
    (void)ctx;
    db_pipeline_complete(&p, ok, bytes, n);
}
static void setup(void) {
    db_config config = {0};
    db_init(&d, &config, send, complete, NULL);
    memset(&p, 0, sizeof(p));
    p.device = &d; p.done = done;
    d.authenticated = d.media = true;
    sends = completions = 0;
    memset(data, 0x5a, sizeof(data));
}
static void reply(bool ok) {
    db_message response = {.op = request.op | 128, .session = d.session,
        .id = request.id, .status = ok ? 0 : DB_IO,
        .length = ok && request.op == DB_READ ? request.b * 512 : 0, .data = data};
    assert(db_receive(&d, frame, db_encode(frame, &response)));
}
static void start_read(uint32_t lba, uint32_t n) {
    assert(db_pipeline_start(&p, DB_READ, lba, NULL, n));
    db_pipeline_tick(&p, 100);
}
static void prime(void) {
    setup(); start_read(0, DB_PAYLOAD); reply(true);
    assert(completions == 1 && success);
    db_pipeline_tick(&p, 101);
    assert(p.prefetch && d.pending && request.a == 32 && sends == 2);
}
int main(void) {
    /* In-flight hit waits without completing USB for a speculative reply. */
    prime(); start_read(32, DB_PAYLOAD);
    assert(sends == 2 && completions == 1);
    reply(true); assert(completions == 1 && p.cache_valid);
    db_pipeline_tick(&p, 102);
    assert(completions == 2 && success && sends == 2 && output[0] == 0x5a);
    db_pipeline_tick(&p, 103); assert(sends == 3 && request.a == 64);

    /* A ready subrange hit copies the right offset and never reads past cache. */
    prime(); data[512] = 0xa7; reply(true); start_read(33, 512);
    assert(completions == 2 && sends == 2 && output[0] == 0xa7);

    /* Random read waits for the sole speculative operation, then misses safely. */
    prime(); start_read(100, 512); reply(true); db_pipeline_tick(&p, 102);
    assert(sends == 3 && request.a == 100 && completions == 1);
    reply(true); assert(completions == 2 && success);

    /* Write invalidates both an in-flight prediction and any ready cached data. */
    prime(); assert(db_pipeline_start(&p, DB_WRITE, 32, data, 512));
    reply(true); assert(!p.cache_valid && completions == 1);
    db_pipeline_tick(&p, 102); assert(request.op == DB_WRITE && sends == 3);
    assert(completions == 1); /* No acknowledgment until durable host response. */
    reply(true); assert(completions == 2 && success);
    start_read(32, 512); assert(sends == 4); reply(true);
    prime(); reply(true); assert(p.cache_valid);
    assert(db_pipeline_start(&p, DB_WRITE, 32, data, 512)); assert(!p.cache_valid);

    /* Flush/eject remain ordered behind the outstanding speculative read. */
    for (uint8_t op = DB_FLUSH; op <= DB_EJECT; op++) {
        prime(); assert(db_pipeline_start(&p, op, 0, NULL, 0));
        reply(true); db_pipeline_tick(&p, 102);
        assert(request.op == op && completions == 1); reply(true);
        assert(completions == 2 && success && !p.cache_valid && !p.hint);
    }

    /* Timeout/error does not signal completion for a nonexistent USB request. */
    prime(); db_tick(&d, d.deadline); assert(completions == 1 && !d.media);
    prime(); start_read(32, DB_PAYLOAD); db_tick(&d, d.deadline);
    assert(completions == 1); db_pipeline_tick(&p, 6000);
    assert(completions == 2 && !success);
    prime(); reply(false); assert(completions == 1 && !p.cache_valid);

    /* Cache cannot survive session/media replacement, even within one loop. */
    prime(); reply(true); db_disconnect(&d); d.authenticated = d.media = true;
    start_read(32, DB_PAYLOAD); assert(sends == 3 && completions == 1);
    prime(); reply(true); d.media_generation++; start_read(32, 512);
    assert(sends == 3 && completions == 1);
    prime(); start_read(32, 512); db_disconnect(&d);
    db_pipeline_tick(&p, 200); assert(completions == 2 && !success);

    /* BOT abort discards pointers and rejects the late response at the core. */
    prime(); start_read(32, DB_PAYLOAD); db_pipeline_abort(&p);
    db_message late = {.op = DB_READ | 128, .id = request.id, .session = d.session,
                      .length = DB_PAYLOAD, .data = data};
    assert(!db_receive(&d, frame, db_encode(frame, &late)));
    db_pipeline_tick(&p, 300); assert(completions == 1 && !p.write_data);

    /* End-of-media prediction is clamped, with no wrap or out-of-range request. */
    setup(); d.sectors = 33; start_read(0, DB_PAYLOAD); reply(true);
    db_pipeline_tick(&p, 101); assert(request.a == 32 && request.b == 1);
    reply(true); start_read(32, 512); assert(completions == 2 && sends == 2);
    db_pipeline_tick(&p, 102); assert(sends == 2);
    assert(!db_pipeline_start(&p, DB_READ, UINT32_MAX, NULL, 512));
    assert(!db_pipeline_start(&p, DB_READ, 0, NULL, DB_PAYLOAD + 512));
    d.read_only = true; assert(!db_pipeline_start(&p, DB_WRITE, 0, data, 512));
    return 0;
}
