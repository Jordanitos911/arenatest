#!/usr/bin/env python3
"""
main.py - a tiny cleanup menu.

Run it, pick an option:

    1) Clean Recycle Bin   -> permanently empties the Recycle Bin / Trash
    2) Quit

On Windows it calls the real Shell API (SHEmptyRecycleBin), the same thing
that happens when you right-click the Recycle Bin and choose "Empty".
On macOS / Linux it empties the Trash folder instead, so the script still
works everywhere.

Usage:
    python main.py            # interactive menu
    python main.py --yes      # don't ask for confirmation
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

BIN_NAME = "Recycle Bin" if IS_WINDOWS else "Trash"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def human_size(num_bytes: float) -> str:
    """1536 -> '1.5 KB'"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num_bytes)} {unit}"
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def ask_yes_no(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


# --------------------------------------------------------------------------
# Windows implementation
# --------------------------------------------------------------------------
def _windows_bin_stats() -> tuple[int, int] | None:
    """Return (item_count, total_bytes) in the Recycle Bin, or None on failure."""
    import ctypes
    from ctypes import wintypes

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("i64Size", ctypes.c_int64),
            ("i64NumItems", ctypes.c_int64),
        ]

    shell32 = ctypes.windll.shell32
    shell32.SHQueryRecycleBinW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SHQUERYRBINFO)]
    shell32.SHQueryRecycleBinW.restype = ctypes.HRESULT

    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    try:
        # None = ask about every drive on the machine
        shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    except OSError:
        return None
    return int(info.i64NumItems), int(info.i64Size)


def _windows_empty_bin() -> tuple[bool, str]:
    """Empty the Recycle Bin on every drive. Returns (ok, message)."""
    import ctypes
    from ctypes import wintypes

    SHERB_NOCONFIRMATION = 0x00000001
    SHERB_NOPROGRESSUI = 0x00000002
    SHERB_NOSOUND = 0x00000004

    shell32 = ctypes.windll.shell32
    shell32.SHEmptyRecycleBinW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
    shell32.SHEmptyRecycleBinW.restype = ctypes.c_long  # HRESULT, checked by hand

    result = shell32.SHEmptyRecycleBinW(
        None,  # no parent window
        None,  # None = all drives
        SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND,
    )
    result &= 0xFFFFFFFF  # treat as unsigned

    if result == 0:  # S_OK
        return True, "Recycle Bin emptied."
    if result == 0x8000FFFF:  # E_UNEXPECTED - Windows says this when it's already empty
        return True, "Recycle Bin was already empty."
    return False, f"Windows refused to empty the Recycle Bin (HRESULT 0x{result:08X})."


# --------------------------------------------------------------------------
# macOS / Linux implementation
# --------------------------------------------------------------------------
def _trash_dirs() -> list[Path]:
    """Folders that hold trashed files on this OS."""
    home = Path.home()
    if IS_MAC:
        return [home / ".Trash"]

    # Linux / freedesktop.org spec
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    trash = data_home / "Trash"
    return [trash / "files", trash / "info"]


def _trash_stats() -> tuple[int, int]:
    """Return (item_count, total_bytes) sitting in the Trash."""
    count = 0
    total = 0
    for folder in _trash_dirs():
        if folder.name == "info":  # metadata, don't count it as user content
            continue
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            count += 1
            if entry.is_symlink():
                continue
            if entry.is_dir():
                for root, _dirs, files in os.walk(entry, onerror=lambda e: None):
                    for name in files:
                        try:
                            total += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass
            else:
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    return count, total


def _empty_trash() -> tuple[bool, str]:
    """Delete everything inside the Trash folders. Returns (ok, message)."""
    removed = 0
    failed: list[str] = []

    for folder in _trash_dirs():
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            try:
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
                else:
                    shutil.rmtree(entry)
                if folder.name != "info":
                    removed += 1
            except OSError as exc:
                failed.append(f"{entry.name}: {exc.strerror or exc}")

    if failed:
        detail = "\n   ".join(failed[:5])
        more = f"\n   ...and {len(failed) - 5} more" if len(failed) > 5 else ""
        return False, f"Removed {removed} item(s), but some could not be deleted:\n   {detail}{more}"
    if removed == 0:
        return True, "Trash was already empty."
    return True, f"Trash emptied - {removed} item(s) removed."


# --------------------------------------------------------------------------
# menu actions
# --------------------------------------------------------------------------
def clean_recycle_bin(skip_confirm: bool = False) -> None:
    print(f"\n--- Clean {BIN_NAME} ---")

    if IS_WINDOWS:
        stats = _windows_bin_stats()
    else:
        stats = _trash_stats()

    if stats is None:
        print(f"Could not read the {BIN_NAME} contents, will still try to empty it.")
    else:
        count, size = stats
        if count == 0:
            print(f"The {BIN_NAME} is already empty - nothing to do.")
            return
        print(f"Currently holding {count} item(s), about {human_size(size)}.")

    if not skip_confirm:
        print("This permanently deletes them. They cannot be restored.")
        if not ask_yes_no(f"Empty the {BIN_NAME} now?"):
            print("Cancelled - nothing was deleted.")
            return

    print("Working...")
    ok, message = _windows_empty_bin() if IS_WINDOWS else _empty_trash()
    print(("[OK] " if ok else "[!!] ") + message)


def print_menu() -> None:
    print()
    print("=" * 38)
    print("        CLEANUP TOOL")
    print("=" * 38)
    print(f"  1) Clean {BIN_NAME}")
    print("  2) Quit")
    print("-" * 38)


def main() -> int:
    skip_confirm = "--yes" in sys.argv or "-y" in sys.argv

    while True:
        print_menu()
        try:
            choice = input("Pick an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return 0

        if choice == "1":
            clean_recycle_bin(skip_confirm)
        elif choice in ("2", "q", "quit", "exit"):
            print("Bye!")
            return 0
        elif choice == "":
            continue
        else:
            print(f"'{choice}' is not an option - type 1 or 2.")


if __name__ == "__main__":
    raise SystemExit(main())
