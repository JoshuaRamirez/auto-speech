"""CacheStore: lookup, promote, and list entries under config/cache/."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from cache_entry import CacheEntry


CACHE_PREFIX_LENGTH = 16  # hex chars; 64 bits — plenty for a single user
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class CachePromotionError(RuntimeError):
    """Raised when promoting a run's full.wav to the cache fails."""


class CacheStore:
    """Own the layout at <root>/<hash_prefix>/{full.wav, meta.json}."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, source_hash: str) -> Path:
        _validate_hash(source_hash)
        return self._root / source_hash[:CACHE_PREFIX_LENGTH]

    def lookup(self, source_hash: str) -> tuple[Path, CacheEntry] | None:
        _validate_hash(source_hash)
        entry_dir = self.path_for(source_hash)
        wav = entry_dir / "full.wav"
        meta = entry_dir / "meta.json"
        if not entry_dir.is_dir():
            return None
        if not wav.is_file() or wav.stat().st_size == 0:
            print(
                f"[cache] orphan: {entry_dir} has no valid full.wav",
                file=sys.stderr,
            )
            return None
        if not meta.is_file():
            print(
                f"[cache] orphan: {entry_dir} missing meta.json",
                file=sys.stderr,
            )
            return None
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            entry = CacheEntry.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(
                f"[cache] orphan: {entry_dir} meta.json unparseable: {exc}",
                file=sys.stderr,
            )
            return None
        if entry.source_hash != source_hash:
            print(
                f"[cache] warning: meta.json hash ({entry.source_hash}) "
                f"does not match lookup key ({source_hash}); treating as miss",
                file=sys.stderr,
            )
            return None
        return wav, entry

    def promote(
        self,
        source_hash: str,
        source_full_wav: Path,
        entry: CacheEntry,
    ) -> Path:
        _validate_hash(source_hash)
        if entry.source_hash != source_hash:
            raise CachePromotionError(
                "entry.source_hash does not match provided source_hash"
            )
        if not source_full_wav.is_file() or source_full_wav.stat().st_size == 0:
            raise CachePromotionError(
                f"source full.wav missing or empty: {source_full_wav}"
            )

        entry_dir = self.path_for(source_hash)
        entry_dir.mkdir(parents=True, exist_ok=True)
        dest_wav = entry_dir / "full.wav"
        dest_meta = entry_dir / "meta.json"
        meta_tmp = dest_meta.with_suffix(dest_meta.suffix + ".partial")

        try:
            # Move the WAV into the cache dir. os.replace is atomic within
            # the same filesystem; fall back to copy+unlink otherwise.
            try:
                os.replace(source_full_wav, dest_wav)
            except OSError:
                shutil.copy2(source_full_wav, dest_wav)
                source_full_wav.unlink(missing_ok=True)

            meta_tmp.write_text(
                json.dumps(entry.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(meta_tmp, dest_meta)
        except Exception as exc:
            # Best-effort cleanup: leave no half-written cache entry.
            meta_tmp.unlink(missing_ok=True)
            # If we moved the WAV but failed on meta, remove the orphan WAV
            # so future lookups don't see a half-entry.
            if dest_wav.exists() and not dest_meta.exists():
                dest_wav.unlink(missing_ok=True)
            try:
                if not any(entry_dir.iterdir()):
                    entry_dir.rmdir()
            except OSError:
                pass
            raise CachePromotionError(f"promotion failed: {exc}") from exc

        print(f"[cache] promoted {dest_wav}")
        return dest_wav

    def list_by_recency(self) -> list[tuple[Path, CacheEntry]]:
        if not self._root.is_dir():
            return []
        entries: list[tuple[Path, CacheEntry, str]] = []
        for child in self._root.iterdir():
            if not child.is_dir():
                continue
            wav = child / "full.wav"
            meta = child / "meta.json"
            if not (wav.is_file() and meta.is_file()):
                print(f"[cache] orphan (skipping): {child}", file=sys.stderr)
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                entry = CacheEntry.from_dict(data)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                print(
                    f"[cache] orphan (skipping) {child}: {exc}",
                    file=sys.stderr,
                )
                continue
            entries.append((wav, entry, entry.created_at))
        entries.sort(key=lambda t: t[2], reverse=True)
        return [(wav, entry) for wav, entry, _ in entries]


def _validate_hash(source_hash: str) -> None:
    if not _HEX_RE.match(source_hash):
        raise ValueError(
            f"source_hash must be 64 lowercase hex chars, got {source_hash!r}"
        )
