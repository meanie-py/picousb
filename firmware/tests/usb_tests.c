#include "devbox.h"
#include "tusb.h"
#include <assert.h>
#include <string.h>
db_device device;
uint32_t test_time_us;
static bool ready = true;
static uint8_t protocol = HID_PROTOCOL_REPORT, sense;
static unsigned reports, completions;
static uint16_t report_length;
static uint8_t last_instance;
static int32_t last_complete;
void tud_hid_report_complete_cb(uint8_t, const uint8_t *, uint16_t);
void tud_picousb_bus_reset_cb(void);
void tud_picousb_configuration_cb(void);
void tud_hid_set_protocol_cb(uint8_t, uint8_t);
bool tud_hid_set_idle_cb(uint8_t, uint8_t);
void tud_mount_cb(void);
bool tud_mounted(void) { return true; }
bool tud_suspended(void) { return false; }
bool tud_hid_n_ready(uint8_t i) {
    (void)i;
    return ready;
}
uint8_t tud_hid_n_get_protocol(uint8_t i) {
    (void)i;
    return protocol;
}
bool tud_hid_n_report(uint8_t i, uint8_t id, const void *p, uint16_t n) {
    (void)id;
    last_instance = i;
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
void tud_msc_read_batch_complete_cb(uint32_t);
bool tud_msc_test_unit_ready_cb(uint8_t);
bool tud_msc_is_writable_cb(uint8_t);
void tud_msc_capacity_cb(uint8_t, uint32_t *, uint16_t *);
int32_t tud_msc_read10_cb(uint8_t, uint32_t, uint32_t, void *, uint32_t);
int32_t tud_msc_write10_cb(uint8_t, uint32_t, uint32_t, uint8_t *, uint32_t);
int32_t tud_msc_scsi_cb(uint8_t, const uint8_t *, void *, uint16_t);
uint16_t tud_hid_get_report_cb(uint8_t, uint8_t, hid_report_type_t, uint8_t *, uint16_t);
void usb_storage_tick(void);
static int32_t read_request(uint8_t lun, uint32_t lba, uint32_t offset, void *buffer, uint32_t n) {
    int32_t result = tud_msc_read10_cb(lun, lba, offset, buffer, n);
    usb_storage_tick();
    return result;
}
static int32_t write_request(uint8_t lun, uint32_t lba, uint32_t offset, uint8_t *buffer, uint32_t n) {
    int32_t result = tud_msc_write10_cb(lun, lba, offset, buffer, n);
    usb_storage_tick();
    return result;
}
static int32_t scsi_request(uint8_t lun, const uint8_t *cmd, void *buffer, uint16_t n) {
    int32_t result = tud_msc_scsi_cb(lun, cmd, buffer, n);
    usb_storage_tick();
    return result;
}
#define tud_msc_read10_cb read_request
#define tud_msc_write10_cb write_request
#define tud_msc_scsi_cb scsi_request
int main(void) {
    db_config c = {0};
    db_init(&device, &c, send, usb_complete, NULL);
    device.hid.polled[0] = device.hid.polled[1] = true;
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
    test_time_us = 7000;
    usb_complete(NULL, true, payload, 512);
    assert(device.metrics[DB_NET_READ].count == 1);
    assert(device.metrics[DB_NET_READ].total == 7000);
    test_time_us = 19000;
    tud_msc_read_batch_complete_cb(512);
    tud_msc_read_batch_complete_cb(512); /* duplicate must not add a sample */
    assert(device.metrics[DB_USB_READ].count == 1);
    assert(device.metrics[DB_USB_READ].total == 12000);
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
    tud_msc_read_batch_complete_cb(512);
    assert(device.metrics[DB_USB_READ].count == 1);
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
    test_time_us = UINT32_MAX - 99;
    assert(tud_msc_read10_cb(0, capacity - 32, 0, received, sizeof(received)) == -2);
    assert(device.pending_bytes == DB_PAYLOAD);
    test_time_us = 100;
    usb_complete(NULL, true, bulk, sizeof(bulk));
    assert(device.metrics[DB_NET_READ].total == 7200); /* timer wraps safely */
    assert(last_complete == DB_PAYLOAD && !memcmp(bulk, received, sizeof(bulk)));
    device.pending = false;
    device.read_only = false;
    assert(tud_msc_is_writable_cb(0));
    assert(tud_msc_write10_cb(0, 0, 0, bulk, sizeof(bulk)) == -2);
    tud_msc_abort_cb();
    assert(!device.pending && !device.media);
    /* Submitted is not completed; duplicate/stale completions cannot satisfy a fence. */
    tud_picousb_bus_reset_cb();
    uint32_t epoch = device.hid.epoch;
    usb_reports();
    assert(device.hid.counts[0].accepted == 1 && device.hid.counts[0].submitted == 1);
    assert(device.hid.counts[0].completed == 0);
    tud_hid_report_complete_cb(0, NULL, 8);
    tud_hid_report_complete_cb(0, NULL, 8);
    assert(device.hid.counts[0].completed == 1);
    usb_reports();
    db_disconnect(&device);
    assert(device.hid.epoch != epoch);
    tud_hid_report_complete_cb(1, NULL, 5);
    assert(!device.hid.counts[1].completed);
    tud_picousb_bus_reset_cb();
    tud_picousb_configuration_cb(); tud_mount_cb();
    assert(device.usb.resets == 2 && device.usb.configurations == 1 && device.usb.mounted);
    assert(tud_hid_set_idle_cb(1, 0)); tud_hid_set_protocol_cb(1, 0);
    assert(device.usb.idle[1] == 1 && device.usb.protocol[1] == 1 && !device.usb.protocol_value[1]);
    assert(!tud_hid_set_idle_cb(3, 0));
    /* A host that polls the mouse too, so a reset releases it as well. */
    db_message nudge = {.op = DB_MOUSE_MOVE, .a = 1};
    assert(db_hid_command(&device.hid, &nudge) == DB_OK);
    while (device.hid.count) { usb_reports(); tud_hid_report_complete_cb(0,NULL,8); tud_hid_report_complete_cb(1,NULL,5); }
    uint8_t button = 1;
    db_message pointer = {.op=DB_POINTER, .a=32767, .b=1234, .length=1, .data=&button};
    assert(db_hid_command(&device.hid,&pointer) == DB_OK);
    assert(device.hid.counts[2].accepted == 1);
    usb_reports(); assert(report_length == 5 && !device.hid.counts[2].completed);
    tud_hid_report_complete_cb(2,NULL,5);
    assert(device.hid.counts[2].completed == 1);
    assert(tud_hid_get_report_cb(2,0,HID_REPORT_TYPE_INPUT,buffer,5)==5);
    assert(buffer[0]==1 && buffer[1]==255 && buffer[2]==127);
    pointer.a=32768;
    assert(db_hid_command(&device.hid,&pointer)==DB_ARGUMENT && !device.hid.count);
    db_hid_reset(&device.hid);
    assert(device.hid.count==3 && device.hid.pointer[0]==0);
    uint8_t raw[8] = {2,0,4};
    db_message report = {.op=DB_RAW_REPORT, .length=8, .data=raw};
    assert(db_hid_command(&device.hid,&report)==DB_OK && device.hid.modifiers==2);
    raw[1]=1; assert(db_hid_command(&device.hid,&report)==DB_ARGUMENT);
    /* A BIOS polls only the boot keyboard. No release is queued for an interface
     * the host never read, and a report stuck on one interface does not hold back
     * another: one unread mouse report used to block every key behind it. */
    tud_picousb_bus_reset_cb();
    assert(device.hid.count == 1 && device.hid.queue[device.hid.head].kind == 0);
    assert(!device.hid.counts[1].accepted && !device.hid.counts[2].accepted);
    usb_reports(); assert(last_instance == 0);
    tud_hid_report_complete_cb(0, NULL, 8);
    db_message move = {.op = DB_MOUSE_MOVE, .a = 1};
    assert(db_hid_command(&device.hid, &move) == DB_OK);
    usb_reports(); assert(last_instance == 1);
    assert(db_hid_command(&device.hid, &move) == DB_OK);
    db_message key = {.op = DB_KEY_PRESS, .a = 0x51};
    assert(db_hid_command(&device.hid, &key) == DB_OK);
    usb_reports(); assert(last_instance == 0);
    tud_hid_report_complete_cb(0, NULL, 8);
    usb_reports(); assert(last_instance == 0);
    tud_hid_report_complete_cb(0, NULL, 8);
    assert(device.hid.counts[0].completed == 3 && device.hid.count == 1);
    assert(device.hid.queue[device.hid.head].kind == 1);
    db_hid_reset(&device.hid);
    assert(device.hid.count == 1 && device.hid.queue[device.hid.head].kind == 0);
    return 0;
}
