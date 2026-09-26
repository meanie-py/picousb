#include "devbox.h"
#include "tusb.h"
#include <assert.h>
#include <string.h>
db_device device;
static bool ready = true;
static uint8_t protocol = HID_PROTOCOL_REPORT, sense;
static unsigned reports, completions;
static uint16_t report_length;
static int32_t last_complete;
bool tud_hid_n_ready(uint8_t i) {
    (void)i;
    return ready;
}
uint8_t tud_hid_n_get_protocol(uint8_t i) {
    (void)i;
    return protocol;
}
bool tud_hid_n_report(uint8_t i, uint8_t id, const void *p, uint16_t n) {
    (void)i;
    (void)id;
    (void)p;
    reports++;
    report_length = n;
    return true;
}
bool tud_hid_n_keyboard_report(uint8_t i, uint8_t id, uint8_t mods, const uint8_t *keys) {
    (void)mods;
    return tud_hid_n_report(i, id, keys, 8);
}
bool tud_msc_set_sense(uint8_t lun, uint8_t key, uint8_t code, uint8_t qualifier) {
    (void)lun;
    (void)code;
    (void)qualifier;
    sense = key;
    return true;
}
bool tud_msc_async_io_done(int32_t n, bool irq) {
    (void)irq;
    completions++;
    last_complete = n;
    return true;
}
static bool send(void *ctx, const uint8_t *data, size_t n) {
    (void)ctx;
    (void)data;
    (void)n;
    return true;
}
void usb_complete(void *, bool, const uint8_t *, size_t);
void usb_reports(void);
void tud_msc_abort_cb(void);
bool tud_msc_test_unit_ready_cb(uint8_t);
bool tud_msc_is_writable_cb(uint8_t);
void tud_msc_capacity_cb(uint8_t, uint32_t *, uint16_t *);
int32_t tud_msc_read10_cb(uint8_t, uint32_t, uint32_t, void *, uint32_t);
int32_t tud_msc_write10_cb(uint8_t, uint32_t, uint32_t, uint8_t *, uint32_t);
int32_t tud_msc_scsi_cb(uint8_t, const uint8_t *, void *, uint16_t);
uint16_t tud_hid_get_report_cb(uint8_t, uint8_t, hid_report_type_t, uint8_t *, uint16_t);
int main(void) {
    db_config c = {0};
    db_init(&device, &c, send, usb_complete, NULL);
    db_hid_reset(&device.hid);
    ready = false;
    usb_reports();
    assert(!reports);
    ready = true;
    usb_reports();
    assert(report_length == 8);
    protocol = HID_PROTOCOL_BOOT;
    usb_reports();
    assert(report_length == 3);
    uint8_t buffer[512] = {0}, payload[512];
    memset(payload, 0x5a, 512);
    assert(tud_hid_get_report_cb(1, 0, HID_REPORT_TYPE_INPUT, buffer, 8) == 3);
    protocol = HID_PROTOCOL_REPORT;
    assert(tud_hid_get_report_cb(1, 0, HID_REPORT_TYPE_INPUT, buffer, 8) == 5);
    assert(!tud_hid_get_report_cb(0, 0, HID_REPORT_TYPE_OUTPUT, buffer, 8));
    assert(!tud_msc_test_unit_ready_cb(0) && sense == SCSI_SENSE_NOT_READY);
    assert(tud_msc_read10_cb(0, 0, 0, buffer, 512) == -1);
    device.authenticated = true;
    device.media = true;
    device.changed = true;
    assert(!tud_msc_test_unit_ready_cb(0) && sense == SCSI_SENSE_UNIT_ATTENTION);
    assert(tud_msc_test_unit_ready_cb(0));
    assert(tud_msc_read10_cb(0, 0, 1, buffer, 512) == -1);
    assert(tud_msc_read10_cb(0, 0, 0, buffer, 512) == -2 && device.pending && !completions);
    usb_complete(NULL, true, payload, 512);
    assert(completions == 1 && last_complete == 512 && !memcmp(buffer, payload, 512));
    device.pending = false;
    assert(tud_msc_write10_cb(0, 0, 0, buffer, 512) == -2);
    db_disconnect(&device);
    assert(last_complete == -1 && !device.media);
    device.authenticated = true;
    device.media = true;
    uint8_t cmd[16] = {0x35};
    assert(tud_msc_scsi_cb(0, cmd, NULL, 0) == -2 && device.pending);
    usb_complete(NULL, true, NULL, 0);
    assert(last_complete == 1);
    device.pending = false;
    assert(tud_msc_read10_cb(0, 0, 0, buffer, 512) == -2);
    tud_msc_abort_cb();
    assert(!device.pending && !device.media);
    device.media = true;
    tud_msc_abort_cb();
    assert(device.media);
    device.read_only = true;
    assert(tud_msc_write10_cb(0, 0, 0, buffer, 512) == -1 && sense == 7);
    assert(!device.pending);
    assert(!tud_msc_is_writable_cb(0));
    device.sectors = 2097152;
    uint32_t capacity;
    uint16_t sector_size;
    tud_msc_capacity_cb(0, &capacity, &sector_size);
    assert(capacity == 2097152 && sector_size == 512);
    uint8_t bulk[DB_PAYLOAD], received[DB_PAYLOAD];
    memset(bulk, 0xa7, sizeof(bulk));
    assert(tud_msc_read10_cb(0, capacity - 31, 0, received, sizeof(received)) == -1);
    assert(!device.pending);
    assert(tud_msc_read10_cb(0, capacity - 32, 0, received, sizeof(received)) == -2);
    assert(device.pending_bytes == DB_PAYLOAD);
    usb_complete(NULL, true, bulk, sizeof(bulk));
    assert(last_complete == DB_PAYLOAD && !memcmp(bulk, received, sizeof(bulk)));
    device.pending = false;
    device.read_only = false;
    assert(tud_msc_is_writable_cb(0));
    assert(tud_msc_write10_cb(0, 0, 0, bulk, sizeof(bulk)) == -2);
    tud_msc_abort_cb();
    assert(!device.pending && !device.media);
    return 0;
}
