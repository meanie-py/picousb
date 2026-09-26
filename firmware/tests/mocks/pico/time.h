#include <stdint.h>
extern uint32_t test_time_us;
static inline uint32_t time_us_32(void) { return test_time_us; }
static inline uint32_t get_absolute_time(void) {
    return 100;
}
static inline uint32_t to_ms_since_boot(uint32_t time) {
    return time;
}
