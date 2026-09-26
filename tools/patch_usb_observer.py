"""Main-loop telemetry hooks for pinned TinyUSB; no control-transfer changes."""

import sys
from pathlib import Path


def patch(path: Path) -> None:
    source = path.read_text()
    marker = "/* PICOUSB_OBSERVER_V1 */"
    if marker in source:
        return
    edits = {
        "static void usbd_reset(uint8_t rhport) {": marker
        + "\nTU_ATTR_WEAK void tud_picousb_bus_reset_cb(void) {}\n"
        + "TU_ATTR_WEAK void tud_picousb_configuration_cb(void) {}\n"
        + "static void usbd_reset(uint8_t rhport) {",
        "      case DCD_EVENT_BUS_RESET:\n": "      case DCD_EVENT_BUS_RESET:\n        tud_picousb_bus_reset_cb();\n",
        "        case TUSB_REQ_SET_CONFIGURATION: {\n": "        case TUSB_REQ_SET_CONFIGURATION: {\n          tud_picousb_configuration_cb();\n",
    }
    for old, new in edits.items():
        if source.count(old) != 1:
            raise RuntimeError("unexpected TinyUSB source; observer patch refused")
        source = source.replace(old, new)
    path.write_text(source)


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
