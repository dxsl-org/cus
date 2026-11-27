"""Send files to the Windows Recycle Bin (undoable), never a hard delete."""
import ctypes
from ctypes import wintypes

_FO_DELETE = 3
_FOF_ALLOWUNDO, _FOF_NOCONFIRMATION, _FOF_SILENT, _FOF_NOERRORUI = 0x40, 0x10, 0x4, 0x400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def recycle(paths):
    """Recycle each path; returns the paths that failed (missing, locked)."""
    failed = []
    for path in paths:
        op = _SHFILEOPSTRUCTW(wFunc=_FO_DELETE, pFrom=str(path).replace("/", "\\") + "\0\0",
                              fFlags=_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0 or op.fAnyOperationsAborted:
            failed.append(path)
    return failed
