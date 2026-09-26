"""Small FAT16 superfloppy backed by a directory and a durable local image.

The image is authoritative while attached. Export occurs only at an explicit
flush/eject boundary. Unclean sessions reuse the image, never re-import over it.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO

from .protocol import MAX_PAYLOAD, SECTOR_SIZE, SECTORS

FAT_SECTORS = 128
ROOT_SECTORS = 32
DATA_START = 1 + 2 * FAT_SECTORS + ROOT_SECTORS
CLUSTER_BYTES = 2048
CLUSTERS = (SECTORS - DATA_START) // 4
NAME = re.compile(r"[A-Z0-9_$~!#%&'()@^`{}-]{1,8}(\.[A-Z0-9_$~!#%&'()@^`{}-]{1,3})?\Z")
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
}


class StorageError(Exception):
    pass


def safe_name(name: str) -> str:
    upper = name.upper()
    if not NAME.fullmatch(upper) or upper.split(".")[0] in RESERVED:
        raise StorageError("unsupported or unsafe FAT 8.3 name")
    return upper


def no_link(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise StorageError("symlinks and reparse points are not supported")


def pack_name(name: str) -> bytes:
    base, _, ext = safe_name(name).partition(".")
    return (base.ljust(8) + ext.ljust(3)).encode("ascii")


def entry(name: str, cluster: int, size: int, directory: bool = False) -> bytes:
    raw = bytearray(32)
    raw[:11] = pack_name(name)
    raw[11] = 0x10 if directory else 0x20
    struct.pack_into("<H", raw, 24, 0x5821)  # 2024-01-01
    struct.pack_into("<HI", raw, 26, cluster, size)
    return bytes(raw)


class DirectoryDisk:
    """Exclusive directory-backed volume; synchronous methods run off the event loop."""

    def __init__(self, root: str | Path) -> None:
        source = Path(root).absolute()
        for parent in (source, *source.parents):
            no_link(parent)
        self.root = source.resolve(strict=True)
        if not self.root.is_dir():
            raise StorageError("backing root must be a directory")
        self.state = self.root / ".picousb"
        self.state.mkdir(exist_ok=True)
        no_link(self.state)
        self.lock = self.state / "lock"
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise StorageError(
                "volume locked; after a crash confirm no server is running before removing .picousb/lock"
            ) from None
        os.close(fd)
        self.file: BinaryIO | None = None
        try:
            if (self.state / "export-pending.json").exists():
                raise StorageError(
                    "interrupted export: preserve .picousb and recover before serving"
                )
            self.image = self.state / "disk.img"
            self.manifest = self.state / "manifest.json"
            if self.image.exists():
                no_link(self.image)
                no_link(self.manifest)
                if self.image.stat().st_size != SECTORS * SECTOR_SIZE:
                    raise StorageError("invalid recovery image size")
            else:
                self._create()
            self.file = self.image.open("r+b", buffering=0)
            self._baseline = json.loads(self.manifest.read_text())
            if not isinstance(self._baseline, dict):
                raise StorageError("invalid manifest")
            for name in self._baseline:
                self._path(name)
        except BaseException:
            self.close()
            raise

    def _path(self, relative: str) -> Path:
        parts = relative.split("/")
        if not parts or any(safe_name(p) != p.upper() for p in parts):
            raise StorageError("unsafe path")
        path = self.root
        for part in parts:
            path = path / part
            if path.exists() or path.is_symlink():
                no_link(path)
        return path

    def _scan(self) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        total_bytes = 0

        def visit(folder: Path, prefix: str, depth: int) -> None:
            nonlocal total_bytes
            if depth > 16:
                raise StorageError("directory nesting exceeds limit")
            seen: set[str] = set()
            for p in sorted(folder.iterdir()):
                if folder == self.root and p.name == ".picousb":
                    continue
                no_link(p)
                name = safe_name(p.name)
                if name in seen:
                    raise StorageError("case-insensitive filename collision")
                seen.add(name)
                rel = prefix + p.name
                if len(result) >= 8192:
                    raise StorageError("too many directory entries")
                if p.is_dir():
                    result[rel] = None
                    visit(p, rel + "/", depth + 1)
                elif p.is_file():
                    total_bytes += p.stat().st_size
                    if total_bytes > CLUSTERS * CLUSTER_BYTES:
                        raise StorageError("directory exceeds volume capacity")
                    with p.open("rb") as f:
                        result[rel] = hashlib.file_digest(f, "sha256").hexdigest()
                else:
                    raise StorageError("unsupported filesystem entry")

        visit(self.root, "", 0)
        return result

    def _create(self) -> None:
        baseline = self._scan()
        fat = bytearray(FAT_SECTORS * 512)
        struct.pack_into("<HH", fat, 0, 0xFFF8, 0xFFFF)
        next_cluster = 2
        temp = self.state / "initial.img"

        with temp.open("w+b") as f:
            f.truncate(SECTORS * 512)

            def allocate(length: int) -> list[int]:
                nonlocal next_cluster
                count = (length + CLUSTER_BYTES - 1) // CLUSTER_BYTES
                if next_cluster + count > CLUSTERS + 2:
                    raise StorageError("directory exceeds volume capacity")
                chain = list(range(next_cluster, next_cluster + count))
                next_cluster += count
                for i, c in enumerate(chain):
                    struct.pack_into("<H", fat, c * 2, chain[i + 1] if i + 1 < count else 0xFFFF)
                return chain

            def write_clusters(chain: list[int], data: bytes) -> None:
                for i, c in enumerate(chain):
                    f.seek(DATA_START * 512 + (c - 2) * CLUSTER_BYTES)
                    f.write(
                        data[i * CLUSTER_BYTES : (i + 1) * CLUSTER_BYTES].ljust(
                            CLUSTER_BYTES, b"\0"
                        )
                    )

            def build(folder: Path, parent: int, own: list[int] | None = None) -> bytes:
                records = bytearray()
                if own:
                    for name, c in ((b".          ", own[0]), (b"..         ", parent)):
                        dot = bytearray(32)
                        dot[:11] = name
                        dot[11] = 0x10
                        struct.pack_into("<H", dot, 26, c)
                        records += dot
                for p in sorted(folder.iterdir()):
                    if folder == self.root and p.name == ".picousb":
                        continue
                    no_link(p)
                    if p.is_dir():
                        chain = allocate((len(list(p.iterdir())) + 3) * 32)
                        records += entry(p.name, chain[0], 0, True)
                        write_clusters(chain, build(p, own[0] if own else 0, chain))
                    else:
                        size = p.stat().st_size
                        chain = allocate(size)
                        records += entry(p.name, chain[0] if chain else 0, size)
                        with p.open("rb") as src:
                            for c in chain:
                                write_clusters([c], src.read(CLUSTER_BYTES))
                return bytes(records) + bytes(32)

            root = build(self.root, 0)
            if len(root) > ROOT_SECTORS * 512:
                raise StorageError("FAT root directory full")
            boot = bytearray(512)
            boot[:11] = b"\xeb\x3c\x90PICOUSB "
            struct.pack_into(
                "<HBHBHHBHHHII",
                boot,
                11,
                512,
                4,
                1,
                2,
                512,
                0,
                0xF8,
                FAT_SECTORS,
                32,
                64,
                0,
                SECTORS,
            )
            boot[36:39] = b"\x80\0\x29"
            struct.pack_into("<I", boot, 39, 0x50444231)
            boot[43:54] = b"NO NAME    "
            boot[54:62] = b"FAT16   "
            boot[510:] = b"\x55\xaa"
            f.seek(0)
            f.write(boot + fat + fat + root)
            f.flush()
            os.fsync(f.fileno())
        if self._scan() != baseline:
            raise StorageError("backing directory changed during import")
        self.manifest.write_text(json.dumps(baseline))
        os.replace(temp, self.image)

    def _io(self) -> BinaryIO:
        if self.file is None:
            raise StorageError("volume closed")
        return self.file

    def read(self, lba: int, count: int = 1) -> bytes:
        if not 0 <= lba < SECTORS or not 1 <= count <= MAX_PAYLOAD // 512 or lba + count > SECTORS:
            raise StorageError("sector outside volume")
        f = self._io()
        f.seek(lba * 512)
        data = f.read(count * 512)
        if len(data) != count * 512:
            raise StorageError("short sector read")
        return data

    def write(self, lba: int, data: bytes) -> None:
        if (
            not 512 <= len(data) <= MAX_PAYLOAD
            or len(data) % 512
            or not 0 <= lba
            or lba + len(data) // 512 > SECTORS
        ):
            raise StorageError("invalid sector write")
        f = self._io()
        f.seek(lba * 512)
        if f.write(data) != len(data):
            raise StorageError("short sector write")
        os.fsync(f.fileno())

    def export(self) -> None:
        """Validate the entire graph before making ordinary file changes.

        A durable marker and staged files preserve evidence if publication is
        interrupted. Such a volume requires explicit recovery before reattach.
        """
        f = self._io()
        os.fsync(f.fileno())
        if self._scan() != self._baseline:
            raise StorageError("host directory changed while target owned volume")
        boot = self.read(0)
        if boot[510:] != b"\x55\xaa" or struct.unpack_from("<HBHBHHBHHHII", boot, 11) != (
            512,
            4,
            1,
            2,
            512,
            0,
            0xF8,
            FAT_SECTORS,
            32,
            64,
            0,
            SECTORS,
        ):
            raise StorageError("unsupported volume geometry")
        f.seek(512)
        fat = f.read(FAT_SECTORS * 512)
        if f.read(FAT_SECTORS * 512) != fat:
            raise StorageError("FAT copies disagree; incomplete metadata update")
        used: set[int] = set()
        files: dict[str, tuple[list[int], int] | None] = {}

        def chain(start: int) -> list[int]:
            result = []
            c = start
            while c < 0xFFF8:
                if c < 2 or c >= CLUSTERS + 2 or c in used:
                    raise StorageError("invalid, looping, or cross-linked FAT chain")
                used.add(c)
                result.append(c)
                c = struct.unpack_from("<H", fat, c * 2)[0]
            return result

        def blocks(cs: list[int]) -> bytes:
            result = bytearray()
            for c in cs:
                f.seek(DATA_START * 512 + (c - 2) * CLUSTER_BYTES)
                result += f.read(CLUSTER_BYTES)
            return bytes(result)

        def visit(data: bytes, prefix: str, depth: int) -> None:
            if depth > 16:
                raise StorageError("directory nesting exceeds limit")
            names: set[str] = set()
            lfn_remaining: int | None = None
            lfn_checksum = 0
            for offset in range(0, len(data), 32):
                record = data[offset : offset + 32]
                if not record[0]:
                    break
                if record[0] == 0xE5:
                    lfn_remaining = None
                    continue
                attr = record[11]
                if attr == 0x0F:
                    ordinal = record[0]
                    if ordinal & 0x40:
                        if lfn_remaining is not None or not 1 <= (ordinal & 0x1F) <= 20:
                            raise StorageError("invalid long-name record sequence")
                        lfn_remaining = ordinal & 0x1F
                        lfn_checksum = record[13]
                    if (
                        lfn_remaining is None
                        or (ordinal & 0x1F) != lfn_remaining
                        or record[13] != lfn_checksum
                        or record[12]
                        or record[26:28] != b"\0\0"
                    ):
                        raise StorageError("invalid long-name metadata")
                    lfn_remaining -= 1
                    # Names intentionally use the validated DOS alias in v1.
                    continue
                if lfn_remaining is not None:
                    checksum = 0
                    for byte in record[:11]:
                        checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 255
                    if lfn_remaining or checksum != lfn_checksum:
                        raise StorageError("incomplete long-name metadata")
                    lfn_remaining = None
                if attr & 8:
                    continue
                if record[:11] in (b".          ", b"..         "):
                    if not prefix:
                        raise StorageError("invalid root dot entry")
                    continue
                try:
                    base = record[:8].decode("ascii").rstrip()
                    ext = record[8:11].decode("ascii").rstrip()
                    name = safe_name(base + ("." + ext if ext else ""))
                except UnicodeError:
                    raise StorageError("unsupported FAT name encoding") from None
                if name in names or len(files) >= 8192:
                    raise StorageError("duplicate name or too many entries")
                names.add(name)
                rel = prefix + name
                start, size = struct.unpack_from("<HI", record, 26)
                if struct.unpack_from("<H", record, 20)[0]:
                    raise StorageError("invalid FAT16 high cluster")
                cs = chain(start) if start else []
                if attr & 0x10:
                    if not cs or len(cs) * CLUSTER_BYTES > 1024 * 1024:
                        raise StorageError("invalid or oversized directory")
                    files[rel] = None
                    visit(blocks(cs), rel + "/", depth + 1)
                else:
                    if size > len(cs) * CLUSTER_BYTES or (size == 0 and cs):
                        raise StorageError("file size inconsistent with allocation")
                    files[rel] = (cs, size)
            if lfn_remaining is not None:
                raise StorageError("unterminated long-name metadata")

        f.seek((1 + 2 * FAT_SECTORS) * 512)
        visit(f.read(ROOT_SECTORS * 512), "", 0)
        # Reject orphaned allocations rather than exporting an incomplete update.
        for c in range(2, CLUSTERS + 2):
            value = struct.unpack_from("<H", fat, c * 2)[0]
            if value and c not in used:
                raise StorageError("orphaned allocation; incomplete filesystem update")
        # Preserve original host spelling for existing entries (FAT is case-insensitive).
        spellings = {p.upper(): p for p in self._baseline}
        translated: dict[str, tuple[list[int], int] | None] = {}
        for name, value in sorted(files.items(), key=lambda item: item[0].count("/")):
            parent, _, leaf = name.rpartition("/")
            actual_parent = spellings.get(parent, parent)
            actual = spellings.get(name, (actual_parent + "/" if parent else "") + leaf)
            spellings[name] = actual
            self._path(actual)
            translated[actual] = value
        stage = Path(tempfile.mkdtemp(prefix="export-", dir=self.state))
        new_manifest: dict[str, str | None] = {}
        for name, value in translated.items():
            if value is None:
                new_manifest[name] = None
                continue
            cs, size = value
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with target.open("wb") as out:
                for c in cs:
                    f.seek(DATA_START * 512 + (c - 2) * CLUSTER_BYTES)
                    chunk = f.read(min(size, CLUSTER_BYTES))
                    size -= len(chunk)
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            new_manifest[name] = digest.hexdigest()
        backup = Path(tempfile.mkdtemp(prefix="backup-", dir=self.state))
        pending = self.state / "export-pending.json"
        with pending.open("w") as journal:
            json.dump(
                {
                    "stage": stage.name,
                    "backup": backup.name,
                    "before": self._baseline,
                    "after": new_manifest,
                },
                journal,
            )
            journal.flush()
            os.fsync(journal.fileno())
        # Keep old entries in the recovery directory until publication completes.
        for name in sorted(self._baseline, key=lambda s: s.count("/"), reverse=True):
            p = self._path(name)
            if p.is_file():
                destination = backup / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(p, destination)
            elif p.is_dir():
                p.rmdir()
        for name, value in sorted(translated.items(), key=lambda item: item[0].count("/")):
            p = self._path(name)
            if value is None:
                p.mkdir(parents=True, exist_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / name, p)
        manifest_temp = self.state / "manifest.tmp"
        with manifest_temp.open("w") as out_manifest:
            json.dump(new_manifest, out_manifest)
            out_manifest.flush()
            os.fsync(out_manifest.fileno())
        os.replace(manifest_temp, self.manifest)
        self._baseline = new_manifest
        pending.unlink()
        # Only these two freshly-created private staging directories are removed.
        # An interrupted export retains both plus its recovery marker.
        for scratch in (stage, backup):
            if scratch.parent != self.state or scratch.is_symlink():
                raise StorageError("invalid staging directory")
            shutil.rmtree(scratch)

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
        self.lock.unlink(missing_ok=True)
