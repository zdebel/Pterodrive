"""Locate a file: case-insensitive lookup and the OS's native open dialog.

No dependencies beyond the standard library:
  Windows  GetOpenFileNameW (comdlg32) through ctypes
  WSL      the Windows dialog through powershell.exe, paths mapped by wslpath
  macOS    'choose file' through osascript
  Linux    zenity or kdialog, whichever is installed
  fallback tkinter.filedialog, if tkinter is available
pick_file() returns None when no dialog could be shown or the user cancelled;
dialog_available() tells the two apart beforehand.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def find_file(directories, name: str) -> str | None:
    """First file called `name`, ignoring case, in the given directories."""
    want = name.lower()
    for d in directories:
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for e in entries:
            p = os.path.join(d, e)
            if e.lower() == want and os.path.isfile(p):
                return p
    return None


def pick_file(title: str, start_dir: str | None = None,
              patterns=('*.exe',), label: str = 'Programs') -> str | None:
    """Ask the user for an existing file with the native open dialog."""
    start_dir = start_dir or os.getcwd()
    for picker in _pickers():
        try:
            result = picker(title, start_dir, patterns, label)
        except Exception:
            continue
        if result is not False:                      # False = picker unavailable
            return result or None
    return None


def dialog_available() -> bool:
    """Whether pick_file() can show any dialog here."""
    for picker in _pickers():
        if picker in (_windows, _macos):
            return True
        if picker is _wsl and shutil.which('powershell.exe') and shutil.which('wslpath'):
            return True
        if picker in (_zenity, _kdialog) and shutil.which(picker.__name__[1:]):
            return True
        if picker is _tk:
            try:
                import tkinter                       # noqa: F401
                return True
            except ImportError:
                pass
    return False


def _is_wsl() -> bool:
    return 'microsoft' in os.uname().release.lower()


def _pickers():
    if sys.platform == 'win32':
        return (_windows,)
    if sys.platform == 'darwin':
        return (_macos, _tk)
    linux = (_zenity, _kdialog, _tk) if (os.environ.get('DISPLAY')
                                         or os.environ.get('WAYLAND_DISPLAY')) else ()
    return ((_wsl,) if _is_wsl() else ()) + linux


def _both_cases(patterns):
    return [p for pat in patterns for p in dict.fromkeys((pat.lower(), pat.upper()))]


def _windows(title, start_dir, patterns, label):
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND),
                    ('hInstance', wintypes.HINSTANCE), ('lpstrFilter', ctypes.c_void_p),
                    ('lpstrCustomFilter', wintypes.LPWSTR), ('nMaxCustFilter', wintypes.DWORD),
                    ('nFilterIndex', wintypes.DWORD), ('lpstrFile', ctypes.c_void_p),
                    ('nMaxFile', wintypes.DWORD), ('lpstrFileTitle', wintypes.LPWSTR),
                    ('nMaxFileTitle', wintypes.DWORD), ('lpstrInitialDir', wintypes.LPCWSTR),
                    ('lpstrTitle', wintypes.LPCWSTR), ('Flags', wintypes.DWORD),
                    ('nFileOffset', wintypes.WORD), ('nFileExtension', wintypes.WORD),
                    ('lpstrDefExt', wintypes.LPCWSTR), ('lCustData', wintypes.LPARAM),
                    ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', wintypes.LPCWSTR),
                    ('pvReserved', ctypes.c_void_p), ('dwReserved', wintypes.DWORD),
                    ('FlagsEx', wintypes.DWORD)]

    OFN_NOCHANGEDIR, OFN_PATHMUSTEXIST, OFN_FILEMUSTEXIST = 0x8, 0x800, 0x1000
    # filter = pairs of NUL-terminated strings, ending with an extra NUL
    filt = ctypes.create_unicode_buffer(
        f'{label} ({";".join(patterns)})\0{";".join(patterns)}\0All files (*.*)\0*.*\0\0')
    buf = ctypes.create_unicode_buffer(1024)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFilter = ctypes.addressof(filt)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.addressof(buf)
    ofn.nMaxFile = len(buf)
    ofn.lpstrInitialDir = start_dir
    ofn.lpstrTitle = title
    ofn.Flags = OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return buf.value
    return None                                      # cancelled


def _wsl(title, start_dir, patterns, label):
    if not (shutil.which('powershell.exe') and shutil.which('wslpath')):
        return False

    def ps(s):
        return "'" + s.replace("'", "''") + "'"
    win_dir = subprocess.run(['wslpath', '-w', start_dir], capture_output=True,
                             text=True).stdout.strip()
    script = (
        'Add-Type -AssemblyName System.Windows.Forms;'
        '[Console]::OutputEncoding = [Text.Encoding]::UTF8;'
        '$o = New-Object System.Windows.Forms.Form -Property @{TopMost=$true};'
        '$d = New-Object System.Windows.Forms.OpenFileDialog;'
        f'$d.Title = {ps(title)};'
        f'$d.Filter = {ps(f"{label} ({";".join(patterns)})|{";".join(patterns)}|All files (*.*)|*.*")};'
        f'$d.InitialDirectory = {ps(win_dir)};'
        "if ($d.ShowDialog($o) -eq 'OK') { [Console]::Out.Write($d.FileName) }")
    r = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-Command', script],
                       capture_output=True, text=True, encoding='utf-8')
    path = r.stdout.strip()
    if r.returncode != 0 or not path:
        return None
    return subprocess.run(['wslpath', '-u', path], capture_output=True,
                          text=True).stdout.strip() or None


def _macos(title, start_dir, patterns, label):
    def quote(s):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    script = (f'POSIX path of (choose file with prompt {quote(title)} '
              f'default location (POSIX file {quote(start_dir)}))')
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _zenity(title, start_dir, patterns, label):
    if not shutil.which('zenity'):
        return False
    r = subprocess.run(['zenity', '--file-selection', f'--title={title}',
                        f'--filename={os.path.join(start_dir, "")}',
                        f'--file-filter={label} | {" ".join(_both_cases(patterns))}',
                        '--file-filter=All files | *'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _kdialog(title, start_dir, patterns, label):
    if not shutil.which('kdialog'):
        return False
    r = subprocess.run(['kdialog', '--title', title, '--getopenfilename', start_dir,
                        f'{" ".join(_both_cases(patterns))}|{label}'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _tk(title, start_dir, patterns, label):
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return False
    root = tkinter.Tk()
    root.withdraw()
    try:
        return filedialog.askopenfilename(
            parent=root, title=title, initialdir=start_dir,
            filetypes=[(label, ' '.join(_both_cases(patterns))), ('All files', '*')])
    finally:
        root.destroy()
