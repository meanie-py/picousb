/* TinyUSB transport mock; descriptors are physically checked on the board. */
#ifndef TEST_TUSB_H
#define TEST_TUSB_H
#include <stdbool.h>
#include <stdint.h>
#define TUSB_DESC_DEVICE 1
#define TUSB_DESC_STRING 3
#define HID_ITF_PROTOCOL_KEYBOARD 1
#define HID_ITF_PROTOCOL_MOUSE 2
#define HID_PROTOCOL_BOOT 0
#define HID_PROTOCOL_REPORT 1
#define TUD_MSC_RET_ERROR -1
#define TUD_MSC_RET_ASYNC -2
#define SCSI_SENSE_UNIT_ATTENTION 6
#define SCSI_SENSE_NOT_READY 2
#define SCSI_SENSE_ILLEGAL_REQUEST 5
#define SCSI_SENSE_MEDIUM_ERROR 3
#define TUD_HID_REPORT_DESC_KEYBOARD() 0
#define TUD_HID_REPORT_DESC_MOUSE() 0
#define TUD_CONFIG_DESC_LEN 9
#define TUD_HID_DESC_LEN 25
#define TUD_MSC_DESC_LEN 23
#define TUD_CONFIG_DESCRIPTOR(...) 0
#define TUD_HID_DESCRIPTOR(...) 0
#define TUD_MSC_DESCRIPTOR(...) 0
typedef enum { HID_REPORT_TYPE_INPUT = 1, HID_REPORT_TYPE_OUTPUT = 2 } hid_report_type_t;
typedef struct {
    uint8_t bLength, bDescriptorType;
    uint16_t bcdUSB;
    uint8_t bMaxPacketSize0;
    uint16_t idVendor, idProduct, bcdDevice;
    uint8_t iManufacturer, iProduct, iSerialNumber, bNumConfigurations;
} tusb_desc_device_t;
bool tud_hid_n_ready(uint8_t);
bool tud_mounted(void);
bool tud_suspended(void);
uint8_t tud_hid_n_get_protocol(uint8_t);
bool tud_hid_n_report(uint8_t, uint8_t, const void *, uint16_t);
bool tud_hid_n_keyboard_report(uint8_t, uint8_t, uint8_t, const uint8_t *);
bool tud_msc_set_sense(uint8_t, uint8_t, uint8_t, uint8_t);
bool tud_msc_async_io_done(int32_t, bool);
#endif
