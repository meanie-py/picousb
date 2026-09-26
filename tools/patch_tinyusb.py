"""Pinned TinyUSB 0.19 extension for async zero-data flush/eject and reset cancellation.

Fails on unexpected source rather than silently applying to an unreviewed version.
"""

import sys
from pathlib import Path


def patch(path: Path) -> None:
    text = path.read_text()
    marker = "/* PICOUSB_ASYNC_SCSI_V1 */"
    if marker in text:
        patch_metrics(path)
        return
    edits = [
        (
            "static void proc_async_io_done(void *bytes_io) {",
            marker
            + "\nTU_ATTR_WEAK void tud_msc_abort_cb(void) {}\n\nstatic void proc_async_io_done(void *bytes_io) {",
        ),
        (
            "    default: break;\n  }\n\n  // send status if stage is transitioned to STATUS",
            "    default:\n      if (nbytes < 0) fail_scsi_op(p_msc, MSC_CSW_STATUS_FAILED);\n      else p_msc->stage = MSC_STAGE_STATUS;\n      break;\n  }\n\n  // send status if stage is transitioned to STATUS",
        ),
        (
            "          if (resplen < 0) {\n            // unsupported command",
            "          if (resplen == TUD_MSC_RET_ASYNC && p_cbw->total_bytes == 0) {\n            p_msc->pending_io = 1;\n          } else if (resplen < 0) {\n            // unsupported command",
        ),
    ]
    start = text.index(
        "    case SCSI_CMD_START_STOP_UNIT:\n",
        text.index("static int32_t proc_builtin_scsi", 10000),
    )
    end = text.index("    case SCSI_CMD_PREVENT_ALLOW_MEDIUM_REMOVAL:", start)
    text = (
        text[:start]
        + "    case SCSI_CMD_START_STOP_UNIT:\n      return -1; // Delegate to async-capable application SCSI callback.\n\n"
        + text[end:]
    )
    for old, new in edits:
        if text.count(old) != 1:
            raise RuntimeError("unexpected TinyUSB source; patch refused")
        text = text.replace(old, new)
    # Called before both BOT and bus reset clear pending_io.
    text = text.replace(
        "static void proc_bot_reset(mscd_interface_t* p_msc) {",
        "static void proc_bot_reset(mscd_interface_t* p_msc) {\n  tud_msc_abort_cb();",
    )
    text = text.replace(
        "void mscd_reset(uint8_t rhport) {",
        "void mscd_reset(uint8_t rhport) {\n  tud_msc_abort_cb();",
    )
    if text.count("  tud_msc_abort_cb();") != 2:
        raise RuntimeError("unexpected reset implementation; patch refused")
    path.write_text(text)
    patch_metrics(path)


def patch_metrics(path: Path) -> None:
    text = path.read_text()
    marker = "/* PICOUSB_READ_BATCH_TIMING_V1 */"
    if marker in text:
        return
    old = "        p_msc->xferred_len += xferred_bytes;\n\n        if ( p_msc->xferred_len >= p_msc->total_len )"
    new = "        tud_msc_read_batch_complete_cb(xferred_bytes);\n" + old
    declaration = "bool mscd_xfer_cb(uint8_t rhport, uint8_t ep_addr, xfer_result_t event, uint32_t xferred_bytes) {"
    if text.count(old) != 1 or text.count(declaration) != 1:
        raise RuntimeError("unexpected TinyUSB data completion; timing patch refused")
    text = text.replace(
        declaration,
        marker
        + "\nTU_ATTR_WEAK void tud_msc_read_batch_complete_cb(uint32_t bytes) { (void)bytes; }\n\n"
        + declaration,
    )
    text = text.replace(old, new)
    path.write_text(text)


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
