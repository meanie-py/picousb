#include "devbox.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static db_device d;
static uint8_t sent[DB_MAX], source[DB_PAYLOAD], got[DB_PAYLOAD];
static size_t sent_n, got_n;
static unsigned completions;
static bool okay;
static bool send(void *ctx, const uint8_t *p, size_t n) {
    (void)ctx; memcpy(sent, p, n); sent_n = n; return true;
}
static void complete(void *ctx, bool ok, const uint8_t *p, size_t n) {
    (void)ctx; completions++; okay = ok; got_n = n;
    if (ok) memcpy(got, p, n);
}
static void put32(uint8_t *p, uint32_t v) {
    for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (i * 8));
}
static uint8_t cookie[16] = {42};
static unsigned command(unsigned id, unsigned a, unsigned b, unsigned n) {
    uint8_t buf[DB_MAX]; db_message r;
    db_message m = {.op=DB_UDP_READS, .id=id, .session=d.session, .a=a, .b=b,
                    .data=cookie, .length=n};
    assert(db_receive(&d, buf, db_encode(buf, &m)));
    assert(db_decode(&r, sent, sent_n));
    return r.status;
}
static void setup(unsigned size) {
    db_config c = {0}; db_init(&d, &c, send, complete, NULL); db_connect(&d);
    assert(command(1, 1, 0, 16) == DB_AUTH);
    d.authenticated = true;
    assert(command(2, 2, 0, 16) == DB_ARGUMENT);
    assert(command(3, 1, 1, 16) == DB_ARGUMENT);
    assert(command(4, 1, 0, 15) == DB_ARGUMENT);
    assert(command(5, 1, 0, 16) == DB_OK);
    d.media = true;
    assert(command(6, 1, 0, 16) == DB_BUSY);
    assert(db_storage_begin_blocks(&d, DB_READ, 0, NULL, 0, size));
    for (unsigned i = 0; i < DB_PAYLOAD; i++) source[i] = (uint8_t)(i * 71);
    completions = 0;
}
static size_t packet(uint8_t *p, unsigned index, unsigned size) {
    memcpy(p, "PDR1", 4); put32(p+4, d.session); put32(p+8, d.pending_id);
    memcpy(p+12, cookie, 16); p[28]=(uint8_t)size; p[29]=(uint8_t)(size>>8);
    p[30]=(uint8_t)index; p[31]=0; put32(p+32, db_crc32(source, size));
    unsigned bytes = size - index * 1024; if (bytes>1024) bytes=1024;
    memcpy(p+36, source + index*1024, bytes); return 36 + bytes;
}
int main(void) {
    uint8_t p[1061], ack[36]; size_t n;
    /* Reverse order, duplicate chunks, missing chunks, exact maximum. */
    setup(DB_PAYLOAD);
    for (int i=15; i>=0; i--) {
        n=packet(p, (unsigned)i, DB_PAYLOAD);
        assert(db_udp_read(&d, p, n, ack, 1)==36);
        assert(db_udp_read(&d, p, n, ack, 1)==36);
        assert(completions == (unsigned)(i==0));
    }
    assert(okay && got_n==DB_PAYLOAD && !memcmp(got,source,got_n));
    assert(ack[30]==255 && ack[31]==255 && !memcmp(ack,"PDA1",4));
    /* Final ACK retry while next request waits cannot complete it. */
    assert(db_storage_begin_blocks(&d, DB_READ, 32, NULL, 2, 512));
    assert(db_udp_read(&d,p,n,ack,3)==36 && completions==1 && d.pending);
    d.media_generation++;
    assert(!db_udp_read(&d,p,n,ack,3));
    /* Final half chunk and malformed bounds/identity. */
    setup(1536); n=packet(p,1,1536);
    assert(!db_udp_read(&d,p,35,ack,0));
    assert(!db_udp_read(&d,p,1061,ack,0));
    assert(!db_udp_read(&d,p,n-1,ack,0));
    unsigned fields[]={0,4,8,12,28,29,30,31};
    for(unsigned i=0;i<sizeof(fields)/sizeof(fields[0]);i++) {
        unsigned off=fields[i]; p[off]^=128;
        assert(!db_udp_read(&d,p,n,ack,0)); p[off]^=128;
    }
    assert(!db_udp_read(&d,p,n,ack,5000));
    assert(db_udp_read(&d,p,n,ack,0)==36 && !completions);
    n=packet(p,0,1536); assert(db_udp_read(&d,p,n,ack,0)==36);
    assert(okay && completions==1 && got_n==1536 && !memcmp(got,source,1536));
    /* Partial completion, abort, stale packets, reconnect. */
    setup(2048); n=packet(p,0,2048); assert(db_udp_read(&d,p,n,ack,0)==36);
    d.pending=false; d.media=false;
    assert(!db_udp_read(&d,p,n,ack,0) && !completions);
    db_disconnect(&d); assert(!d.udp_reads && !d.udp_cookie[0]);
    assert(!db_udp_read(&d,p,n,ack,0));
    /* CRC failure never releases data to USB; deadline completes exactly once. */
    setup(512); n=packet(p,0,512); p[36]^=1;
    assert(!db_udp_read(&d,p,n,ack,0) && completions==1 && !okay && !d.media);
    db_tick(&d,6000); assert(completions==1);
    setup(2048); n=packet(p,0,2048); assert(db_udp_read(&d,p,n,ack,0)==36);
    db_tick(&d,5000); assert(completions==1 && !okay);
    assert(!db_udp_read(&d,p,n,ack,5001));
    puts("UDP read reassembly tests passed");
}
