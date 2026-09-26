#ifndef DB_METRICS_H
#define DB_METRICS_H
#include <stdint.h>
enum { DB_NET_READ, DB_NET_WRITE, DB_USB_READ, DB_WIFI_POLL, DB_USB_TASK, DB_LOOP,
       DB_METRIC_COUNT };
typedef struct {
    uint32_t count, maximum, bins[8];
    uint64_t total;
} db_metric;
/* Non-overlapping bins: <=100us, <=1ms, <=5ms, <=10ms, <=25ms,
 * <=50ms, <=100ms, >100ms. Stop at count saturation, never wrap. */
static inline void db_metric_add(db_metric *m, uint32_t us) {
    static const uint32_t limits[] = {100, 1000, 5000, 10000, 25000, 50000, 100000};
    if (m->count == UINT32_MAX)
        return;
    unsigned bin = 0;
    while (bin < 7 && us > limits[bin])
        bin++;
    m->bins[bin]++;
    m->count++;
    m->total += us;
    if (us > m->maximum)
        m->maximum = us;
}
#endif
