#include "devbox.h"
#include "storage_pipeline.h"
#include "pico/time.h"
#include "tusb.h"
#include <string.h>
#ifdef PICOUSB_DIAGNOSTICS
#include <stdio.h>
extern uint32_t diagnostic_phase;
#endif
extern db_device device;
static uint8_t *read_buffer;
static bool prevent_removal;
static bool timing_network, timing_read;
static uint32_t network_started, read_started;
static bool hid_pending[3];
static uint32_t hid_epoch[3];
static void deliver(void *, uint8_t, bool, const uint8_t *, uint32_t);
static db_pipeline pipeline = {.device = &device, .done = deliver};
void usb_storage_tick(void) {
    bool pending = device.pending;
    uint32_t started = time_us_32();
    db_pipeline_tick(&pipeline, to_ms_since_boot(get_absolute_time()));
    if (!pending && device.pending) {
        timing_network = device.pending_op == DB_READ || device.pending_op == DB_WRITE;
        network_started = started;
    }
}
static const uint8_t keyboard_desc[] = {TUD_HID_REPORT_DESC_KEYBOARD()};
static const uint8_t mouse_desc[] = {TUD_HID_REPORT_DESC_MOUSE()};
/* Separate tablet-style absolute pointer, three buttons and unsigned X/Y.
 * Generic Desktop Mouse usage supports standard absolute-pointer consumers. */
static const uint8_t pointer_desc[] = {
    0x05,0x01, 0x09,0x02, 0xa1,0x01, 0x09,0x01, 0xa1,0x00,
    0x05,0x09, 0x19,0x01, 0x29,0x03, 0x15,0x00, 0x25,0x01,
    0x95,0x03, 0x75,0x01, 0x81,0x02, 0x95,0x01, 0x75,0x05, 0x81,0x03,
    0x05,0x01, 0x09,0x30, 0x09,0x31, 0x15,0x00, 0x26,0xff,0x7f,
    0x75,0x10, 0x95,0x02, 0x81,0x02, 0xc0,0xc0
};
static const tusb_desc_device_t desc = {.bLength = sizeof(tusb_desc_device_t),
                                        .bDescriptorType = TUSB_DESC_DEVICE,
                                        .bcdUSB = 0x0200,
                                        .bMaxPacketSize0 = 64,
                                        .idVendor = 0xcafe,
                                        .idProduct = 0x4013,
                                        .bcdDevice = 0x0101,
                                        .iManufacturer = 1,
                                        .iProduct = 2,
                                        .iSerialNumber = 3,
                                        .bNumConfigurations = 1};
enum { CONFIG_LENGTH = TUD_CONFIG_DESC_LEN + 3 * TUD_HID_DESC_LEN + TUD_MSC_DESC_LEN };
static const uint8_t config_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, 4, 0, CONFIG_LENGTH, 0, 250),
    TUD_HID_DESCRIPTOR(0, 0, HID_ITF_PROTOCOL_KEYBOARD, sizeof(keyboard_desc), 0x81, 16, 5),
    TUD_HID_DESCRIPTOR(1, 0, HID_ITF_PROTOCOL_MOUSE, sizeof(mouse_desc), 0x82, 16, 5),
    TUD_MSC_DESCRIPTOR(2, 0, 0x03, 0x83, 64),
    TUD_HID_DESCRIPTOR(3, 0, 0, sizeof(pointer_desc), 0x84, 16, 5)};
const uint8_t *tud_descriptor_device_cb(void) {
    return (const uint8_t *)&desc;
}
const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return config_desc;
}
const uint8_t *tud_hid_descriptor_report_cb(uint8_t instance) {
    return instance == 0 ? keyboard_desc : instance == 1 ? mouse_desc : instance == 2 ? pointer_desc : NULL;
}
const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t lang) {
    (void)lang;
    static uint16_t result[32];
    const char *strs[] = {"", "PicoUSB", "Pico DevBox", "PDB0001"};
#ifdef PICOUSB_DIAGNOSTICS
    char diagnostic_serial[24];
    if (diagnostic_phase) {
        snprintf(diagnostic_serial, sizeof(diagnostic_serial), "PDBFAIL%02lu",
                 (unsigned long)diagnostic_phase);
        strs[3] = diagnostic_serial;
    }
#endif
    if (index == 0) {
        result[0] = (TUSB_DESC_STRING << 8) | 4;
        result[1] = 0x409;
        return result;
    }
    if (index > 3)
        return NULL;
    size_t n = strlen(strs[index]);
    result[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * n + 2));
    for (size_t i = 0; i < n; i++)
        result[i + 1] = strs[index][i];
    return result;
}
uint16_t tud_hid_get_report_cb(uint8_t inst, uint8_t id, hid_report_type_t type, uint8_t *buf,
                               uint16_t n) {
    (void)id;
    if (type != HID_REPORT_TYPE_INPUT)
        return 0;
    if (inst >= 3) return 0;
    if (inst == 2) {
        if (n < 5) return 0;
        memcpy(buf, device.hid.pointer, 5); return 5;
    }
    uint8_t keyboard[8] = {device.hid.modifiers, 0};
    memcpy(keyboard + 2, device.hid.keys, 6);
    uint8_t mouse[5] = {device.hid.buttons, 0, 0, 0, 0};
    uint16_t len = inst ? (tud_hid_n_get_protocol(1) == HID_PROTOCOL_BOOT ? 3 : 5) : 8;
    if (n < len)
        return 0;
    memcpy(buf, inst ? mouse : keyboard, len);
    return len;
}
void tud_hid_set_report_cb(uint8_t i, uint8_t id, hid_report_type_t t, const uint8_t *b,
                           uint16_t n) {
    (void)i;
    (void)id;
    (void)t;
    (void)b;
    (void)n;
}
void usb_reports(void) {
    device.usb.mounted = tud_mounted();
    device.usb.suspended = tud_suspended();
    db_report r;
    uint16_t slot;
    /* Each interface is its own pipe: a report waiting on an endpoint the host
     * does not poll must not hold back the others (a BIOS polls only the boot
     * keyboard, and one stuck mouse report used to block every key). */
    bool busy[3];
    for (unsigned i = 0; i < 3; i++) busy[i] = hid_pending[i] || !tud_hid_n_ready((uint8_t)i);
    if (!db_hid_next(&device.hid, busy, &r, &slot))
        return;
    if (r.kind >= 3) {
        db_hid_remove(&device.hid, slot);
        return;
    }
    uint8_t mouse[5] = {r.buttons, (uint8_t)r.x, (uint8_t)r.y, (uint8_t)r.wheel, 0};
    bool ok = r.length ? tud_hid_n_report(r.kind, 0, r.raw,
                         r.kind == 1 && tud_hid_n_get_protocol(1) == HID_PROTOCOL_BOOT ? 3 : r.length)
              : r.kind ? tud_hid_n_report(1, 0, mouse,
                                        tud_hid_n_get_protocol(1) == HID_PROTOCOL_BOOT ? 3 : 5)
                     : tud_hid_n_keyboard_report(0, 0, r.modifiers, r.keys);
    if (ok) {
        hid_pending[r.kind] = true;
        hid_epoch[r.kind] = device.hid.epoch;
        device.hid.counts[r.kind].submitted++;
        db_hid_remove(&device.hid, slot);
    }
}
void tud_hid_report_complete_cb(uint8_t instance, const uint8_t *report, uint16_t len) {
    (void)report; (void)len;
    if (instance >= 3 || !hid_pending[instance]) return;
    hid_pending[instance] = false;
    device.hid.polled[instance] = true;
    device.usb.completed[instance]++;
    device.usb.last_ms[instance] = to_ms_since_boot(get_absolute_time());
    if (hid_epoch[instance] != device.hid.epoch) return;
    device.hid.counts[instance].completed++;
    device.hid.counts[instance].last_ms = to_ms_since_boot(get_absolute_time());
}
void tud_hid_set_protocol_cb(uint8_t instance, uint8_t protocol) {
    if (instance >= 3) return;
    device.usb.protocol[instance]++;
    device.usb.protocol_value[instance] = protocol;
}
bool tud_hid_set_idle_cb(uint8_t instance, uint8_t rate) {
    (void)rate;
    if (instance >= 3) return false;
    device.usb.idle[instance]++; return true;
}
void tud_mount_cb(void) {
    device.usb.mounted = true;
    if (!device.usb.configured_ms) device.usb.configured_ms = to_ms_since_boot(get_absolute_time());
}
void tud_suspend_cb(bool remote_wakeup_en) { (void)remote_wakeup_en; device.usb.suspended = true; }
void tud_resume_cb(void) { device.usb.suspended = false; }
void tud_picousb_configuration_cb(void) { device.usb.configurations++; }
/* Main-loop hooks in pinned TinyUSB, not interrupt-context mutations. */
void tud_picousb_bus_reset_cb(void) {
    device.usb.resets++;
    device.usb.mounted = device.usb.suspended = false;
    memset(hid_pending, 0, sizeof(hid_pending));
    for (unsigned i = 0; i < 3; i++) device.usb.protocol_value[i] = HID_PROTOCOL_REPORT;
    memset(device.hid.polled, 0, sizeof(device.hid.polled));
    db_hid_reset(&device.hid);
}
void tud_msc_inquiry_cb(uint8_t lun, uint8_t vendor[8], uint8_t product[16], uint8_t rev[4]) {
    (void)lun;
    memcpy(vendor, "PICOUSB ", 8);
    memcpy(product, "REMOTE DISK     ", 16);
    memcpy(rev, "0100", 4);
}
bool tud_msc_test_unit_ready_cb(uint8_t lun) {
    if (device.changed) {
        device.changed = false;
        tud_msc_set_sense(lun, SCSI_SENSE_UNIT_ATTENTION, 0x28, 0);
        return false;
    }
    if (!device.media) {
        tud_msc_set_sense(lun, SCSI_SENSE_NOT_READY, 0x3a, 0);
        return false;
    }
    return true;
}
void tud_msc_capacity_cb(uint8_t lun, uint32_t *count, uint16_t *size) {
    (void)lun;
    *count = device.sectors;
    *size = 512;
}
bool tud_msc_is_writable_cb(uint8_t lun) {
    (void)lun;
    return device.media && !device.read_only;
}
static int32_t begin(uint8_t op, uint32_t lba, const uint8_t *data, uint32_t n) {
    if (!db_pipeline_start(&pipeline, op, lba, data, n)) {
        tud_msc_set_sense(0, SCSI_SENSE_NOT_READY, 0x3a, 0);
        return TUD_MSC_RET_ERROR;
    }
    return TUD_MSC_RET_ASYNC;
}
int32_t tud_msc_read10_cb(uint8_t lun, uint32_t lba, uint32_t offset, void *buffer, uint32_t n) {
    (void)lun;
    if (offset || !n || n > DB_PAYLOAD || n % 512)
        return -1;
    read_buffer = buffer;
    int32_t r = begin(DB_READ, lba, NULL, n);
    if (r != -2)
        read_buffer = NULL;
    return r;
}
int32_t tud_msc_write10_cb(uint8_t lun, uint32_t lba, uint32_t offset, uint8_t *buffer,
                           uint32_t n) {
    if (device.read_only) {
        tud_msc_set_sense(lun, 0x07, 0x27, 0);
        return TUD_MSC_RET_ERROR;
    }
    if (offset || !n || n > DB_PAYLOAD || n % 512)
        return -1;
    return begin(DB_WRITE, lba, buffer, n);
}
bool tud_msc_prevent_allow_medium_removal_cb(uint8_t lun, uint8_t prohibit, uint8_t control) {
    (void)lun;
    (void)control;
    prevent_removal = prohibit != 0;
    return true;
}
int32_t tud_msc_scsi_cb(uint8_t lun, const uint8_t cmd[16], void *buf, uint16_t n) {
    (void)buf;
    if (n)
        return -1;
    if (cmd[0] == 0x35) {
        if (cmd[1] & 2)
            return -1;
        return begin(DB_FLUSH, 0, NULL, 0);
    }
    if (cmd[0] == 0x1b) {
        if ((cmd[4] & 3) == 2) {
            if (prevent_removal) {
                tud_msc_set_sense(lun, SCSI_SENSE_ILLEGAL_REQUEST, 0x53, 2);
                return -1;
            }
            return begin(DB_EJECT, 0, NULL, 0);
        }
        if (cmd[4] & 1)
            return device.media ? 0 : -1;
        return begin(DB_FLUSH, 0, NULL, 0);
    }
    tud_msc_set_sense(lun, SCSI_SENSE_ILLEGAL_REQUEST, 0x20, 0);
    return -1;
}
void usb_complete(void *ctx, bool ok, const uint8_t *data, size_t n) {
    (void)ctx;
    uint32_t now = time_us_32();
    if (timing_network && ok)
        db_metric_add(&device.metrics[device.pending_op == DB_READ ? DB_NET_READ : DB_NET_WRITE],
                      now - network_started);
    timing_network = false;
    db_pipeline_complete(&pipeline, ok, data, n);
}
static void deliver(void *ctx, uint8_t op, bool ok, const uint8_t *data, uint32_t n) {
    (void)ctx;
    uint32_t now = time_us_32();
    timing_read = ok && op == DB_READ && read_buffer;
    read_started = now;
    if (ok && op == DB_READ && read_buffer)
        memcpy(read_buffer, data, n);
    read_buffer = NULL;
    if (!ok)
        tud_msc_set_sense(0, SCSI_SENSE_MEDIUM_ERROR, 0x11, 0);
    if (ok && op == DB_EJECT)
        device.media = false;
    tud_msc_async_io_done(ok ? (op <= DB_WRITE ? (int32_t)n : 1) : -1, false);
}
/* Pinned TinyUSB hook: data-stage completion for EACH read batch, before
 * requesting the next one. Includes async dispatch and main-loop scheduling. */
void tud_msc_read_batch_complete_cb(uint32_t bytes) {
    (void)bytes;
    if (timing_read)
        db_metric_add(&device.metrics[DB_USB_READ], time_us_32() - read_started);
    timing_read = false;
}
/* Patched TinyUSB calls this before invalidating an outstanding BOT operation. */
void tud_msc_abort_cb(void) {
    timing_network = timing_read = false;
    read_buffer = NULL;
    db_pipeline_abort(&pipeline);
    prevent_removal = false;
}
void tud_umount_cb(void) {
    device.usb.mounted = false;
    memset(hid_pending, 0, sizeof(hid_pending));
    memset(device.hid.polled, 0, sizeof(device.hid.polled));
    tud_msc_abort_cb();
    db_hid_reset(&device.hid);
}
