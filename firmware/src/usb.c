#include "devbox.h"
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
static const uint8_t keyboard_desc[] = {TUD_HID_REPORT_DESC_KEYBOARD()};
static const uint8_t mouse_desc[] = {TUD_HID_REPORT_DESC_MOUSE()};
static const tusb_desc_device_t desc = {.bLength = sizeof(tusb_desc_device_t),
                                        .bDescriptorType = TUSB_DESC_DEVICE,
                                        .bcdUSB = 0x0200,
                                        .bMaxPacketSize0 = 64,
                                        .idVendor = 0xcafe,
                                        .idProduct = 0x4013,
                                        .bcdDevice = 0x0100,
                                        .iManufacturer = 1,
                                        .iProduct = 2,
                                        .iSerialNumber = 3,
                                        .bNumConfigurations = 1};
enum { CONFIG_LENGTH = TUD_CONFIG_DESC_LEN + 2 * TUD_HID_DESC_LEN + TUD_MSC_DESC_LEN };
static const uint8_t config_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, 3, 0, CONFIG_LENGTH, 0, 250),
    TUD_HID_DESCRIPTOR(0, 0, HID_ITF_PROTOCOL_KEYBOARD, sizeof(keyboard_desc), 0x81, 16, 5),
    TUD_HID_DESCRIPTOR(1, 0, HID_ITF_PROTOCOL_MOUSE, sizeof(mouse_desc), 0x82, 16, 5),
    TUD_MSC_DESCRIPTOR(2, 0, 0x03, 0x83, 64)};
const uint8_t *tud_descriptor_device_cb(void) {
    return (const uint8_t *)&desc;
}
const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return config_desc;
}
const uint8_t *tud_hid_descriptor_report_cb(uint8_t instance) {
    return instance ? mouse_desc : keyboard_desc;
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
    db_report r;
    if (!db_hid_peek(&device.hid, &r) || !tud_hid_n_ready(r.kind))
        return;
    uint8_t mouse[5] = {r.buttons, (uint8_t)r.x, (uint8_t)r.y, (uint8_t)r.wheel, 0};
    bool ok = r.kind ? tud_hid_n_report(1, 0, mouse,
                                        tud_hid_n_get_protocol(1) == HID_PROTOCOL_BOOT ? 3 : 5)
                     : tud_hid_n_keyboard_report(0, 0, r.modifiers, r.keys);
    if (ok)
        db_hid_pop(&device.hid);
}
void tud_msc_inquiry_cb(uint8_t lun, uint8_t vendor[8], uint8_t product[16], uint8_t rev[4]) {
    (void)lun;
    memcpy(vendor, "PICOUSB ", 8);
    memcpy(product, "REMOTE FAT16    ", 16);
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
    *count = DB_SECTORS;
    *size = 512;
}
bool tud_msc_is_writable_cb(uint8_t lun) {
    (void)lun;
    return device.media;
}
static int32_t begin(uint8_t op, uint32_t lba, const uint8_t *data) {
    if (!db_storage_begin(&device, op, lba, data, to_ms_since_boot(get_absolute_time()))) {
        tud_msc_set_sense(0, SCSI_SENSE_NOT_READY, 0x3a, 0);
        return TUD_MSC_RET_ERROR;
    }
    return TUD_MSC_RET_ASYNC;
}
int32_t tud_msc_read10_cb(uint8_t lun, uint32_t lba, uint32_t offset, void *buffer, uint32_t n) {
    (void)lun;
    if (offset || n != 512)
        return -1;
    read_buffer = buffer;
    int32_t r = begin(DB_READ, lba, NULL);
    if (r != -2)
        read_buffer = NULL;
    return r;
}
int32_t tud_msc_write10_cb(uint8_t lun, uint32_t lba, uint32_t offset, uint8_t *buffer,
                           uint32_t n) {
    (void)lun;
    if (offset || n != 512)
        return -1;
    return begin(DB_WRITE, lba, buffer);
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
        return begin(DB_FLUSH, 0, NULL);
    }
    if (cmd[0] == 0x1b) {
        if ((cmd[4] & 3) == 2) {
            if (prevent_removal) {
                tud_msc_set_sense(lun, SCSI_SENSE_ILLEGAL_REQUEST, 0x53, 2);
                return -1;
            }
            return begin(DB_EJECT, 0, NULL);
        }
        if (cmd[4] & 1)
            return device.media ? 0 : -1;
        return begin(DB_FLUSH, 0, NULL);
    }
    tud_msc_set_sense(lun, SCSI_SENSE_ILLEGAL_REQUEST, 0x20, 0);
    return -1;
}
void usb_complete(void *ctx, bool ok, const uint8_t *data, size_t n) {
    (void)ctx;
    if (ok && read_buffer && n == 512)
        memcpy(read_buffer, data, 512);
    read_buffer = NULL;
    if (!ok)
        tud_msc_set_sense(0, SCSI_SENSE_MEDIUM_ERROR, 0x11, 0);
    if (ok && device.pending_op == DB_EJECT)
        device.media = false;
    tud_msc_async_io_done(ok ? (device.pending_op <= DB_WRITE ? 512 : 1) : -1, false);
}
/* Patched TinyUSB calls this before invalidating an outstanding BOT operation. */
void tud_msc_abort_cb(void) {
    read_buffer = NULL;
    if (device.pending)
        device.media = false;
    device.pending = false;
    prevent_removal = false;
}
void tud_umount_cb(void) {
    tud_msc_abort_cb();
    db_hid_reset(&device.hid);
}
