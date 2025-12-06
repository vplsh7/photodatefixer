"""
Photo Date Fixer (Portable)
A single-file PySide6 GUI app for Windows (portable), with:
 - Folder drag-and-drop
 - Thumbnail preview
 - Scan non-recursive folder for files
 - Check EXIF / Modified / Created timestamps against expected Year/Month/Day
 - Select mismatches and edit EXIF / filesystem timestamps (including Windows creation time)

This version includes more robust EXIF saving (writes to temp file then atomic replace)
and improved Windows file-time setting error handling and path-existence checks.

Dependencies:
 - Python 3.9+
 - PySide6
 - Pillow
 - piexif

Packaging (recommended):
 - Use PyInstaller to create a one-file EXE or a portable folder.

Run (for development):
    python photo_date_fixer_portable.py

"""

import sys
import os
from pathlib import Path
import platform
from datetime import datetime
import io
import ctypes
from ctypes import wintypes
import tempfile
import shutil

from PIL import Image
import piexif

from PySide6 import QtCore, QtGui, QtWidgets

# ---------- Helpers: file times & windows creation time (robust) ----------

def get_file_times(path: Path):
    st = path.stat()
    created = datetime.fromtimestamp(st.st_ctime)
    modified = datetime.fromtimestamp(st.st_mtime)
    return created, modified


# Robust Windows file-time setter with clearer errors and checks
def set_file_times_windows(path: Path, modified_dt: datetime = None, created_dt: datetime = None):
    pstr = str(path)
    if not path.exists():
        return False, f"File not found: {pstr}"
    # first set modified time via os.utime
    try:
        if modified_dt:
            mod_ts = modified_dt.timestamp()
        else:
            mod_ts = path.stat().st_mtime
        atime = path.stat().st_atime
        os.utime(pstr, (atime, mod_ts))
    except Exception as e:
        return False, f"Failed to set modified time via os.utime: {e}"

    if created_dt is None:
        return True, "Modified time updated (creation unchanged)."

    # Windows SetFileTime: prepare and call
    CREATE_WRITE_ATTR = 0x0100
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    OPEN_EXISTING = 3

    # CreateFileW returns INVALID_HANDLE_VALUE (-1) on failure
    handle = ctypes.windll.kernel32.CreateFileW(pstr,
                                               CREATE_WRITE_ATTR,
                                               0, None,
                                               OPEN_EXISTING,
                                               FILE_FLAG_BACKUP_SEMANTICS, None)
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    if handle == INVALID_HANDLE_VALUE or handle is None:
        err = ctypes.windll.kernel32.GetLastError()
        return False, f"CreateFileW failed (error {err}). File may be locked or path invalid."

    # convert dt to FILETIME (100-ns intervals since 1601)
    def dt_to_filetime(dt):
        us = int(dt.timestamp() * 1e6)
        intervals = int(us * 10) + 116444736000000000
        low = intervals & 0xffffffff
        high = (intervals >> 32) & 0xffffffff
        return low, high

    low, high = dt_to_filetime(created_dt)
    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]
    c_ft = FILETIME(low, high)

    res = ctypes.windll.kernel32.SetFileTime(handle,
                                             ctypes.byref(c_ft),
                                             None,
                                             None)
    ctypes.windll.kernel32.CloseHandle(handle)
    if res == 0:
        err = ctypes.windll.kernel32.GetLastError()
        return False, f"SetFileTime failed (error {err})."
    return True, "Modified and Creation time updated (Windows)."


# Wrapper for other platforms
def set_file_times(path: Path, modified_dt: datetime = None, created_dt: datetime = None):
    if platform.system() == 'Windows':
        return set_file_times_windows(path, modified_dt, created_dt)
    else:
        try:
            if modified_dt:
                os.utime(str(path), (path.stat().st_atime, modified_dt.timestamp()))
            # creation change not supported
            if created_dt is not None:
                return False, "Creation time cannot be modified on this OS."
            return True, "Modified time updated."
        except Exception as e:
            return False, f"os.utime failed: {e}"


# ---- robust EXIF save (replace original via temp file) ----
def exif_set_datetime(path: Path, dt: datetime):
    pstr = str(path)
    if not path.exists():
        return False, f"File not found: {pstr}"
    try:
        # open read-only to inspect
        img = Image.open(pstr)
        img_format = img.format
        if img_format not in ('JPEG', 'TIFF'):
            return False, f"EXIF editing supported for JPEG/TIFF only (found {img_format})"
        exif_bytes = img.info.get('exif', b'')
        exif_dict = piexif.load(exif_bytes) if exif_bytes else {'0th':{}, 'Exif':{}, 'GPS':{}, '1st':{}, 'thumbnail': None}
        dt_str = dt.strftime('%Y:%m:%d %H:%M:%S')
        exif_dict.setdefault('Exif', {})[piexif.ExifIFD.DateTimeOriginal] = dt_str.encode()
        exif_dict.setdefault('0th', {})[piexif.ImageIFD.DateTime] = dt_str.encode()
        new_exif = piexif.dump(exif_dict)

        # Save to a temp file in same dir then atomically replace original (avoids partial writes)
        parent = path.parent
        with tempfile.NamedTemporaryFile(delete=False, dir=str(parent), suffix=path.suffix) as tmpf:
            tmpname = tmpf.name
        try:
            # reopen and save into tmp file
            img = Image.open(pstr)
            img.save(tmpname, exif=new_exif)
            # replace original with tmp atomically
            os.replace(tmpname, pstr)
            return True, "EXIF updated (saved via temp file replace)"
        finally:
            # cleanup leftover temp if any
            if os.path.exists(tmpname):
                try:
                    os.remove(tmpname)
                except:
                    pass
    except Exception as e:
        return False, f"EXIF update failed: {e}"


# ---------- Thumbnail helper (PIL -> QPixmap) ----------
def create_thumbnail_qpixmap(path: Path, size=(160, 120)):
    try:
        with Image.open(path) as img:
            img.thumbnail(size)
            bio = io.BytesIO()
            # convert to PNG in-memory
            img.convert('RGB').save(bio, format='PNG')
            bio.seek(0)
            qimg = QtGui.QImage.fromData(bio.read())
            pix = QtGui.QPixmap.fromImage(qimg)
            return pix
    except Exception:
        return None


# ---------- EXIF read helper (required for scanning) ----------
def exif_get_datetime(path: Path):
    """
    Returns the EXIF DateTimeOriginal as a datetime object, or None.
    Safe against missing EXIF, corrupt EXIF, or non-image formats.
    """
    try:
        with Image.open(path) as img:
            exif_bytes = img.info.get("exif", b"")
            if not exif_bytes:
                return None
            exif_dict = piexif.load(exif_bytes)
            dto = exif_dict.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
            if not dto:
                return None
            if isinstance(dto, bytes):
                dto = dto.decode("utf-8", errors="ignore")
            try:
                return datetime.strptime(dto, "%Y:%m:%d %H:%M:%S")
            except Exception:
                return None
    except Exception:
        return None


# ---------- File filtering ----------
IMAGE_EXTS = {'.jpg', '.jpeg', '.tiff', '.tif', '.png'}
OTHER_EXTS = {'.mp3', '.flac', '.wav', '.mp4', '.mov'}

def list_files_nonrecursive(folder: Path):
    return [p for p in sorted(folder.iterdir()) if p.is_file()]


# ---------- Main Qt App (unchanged structure) ----------
class FileItem:
    def __init__(self, path: Path, created, modified, exif_dt):
        self.path = path
        self.created = created
        self.modified = modified
        self.exif_dt = exif_dt
        self.checked_dt = None
        self.ok = False
        self.thumbnail = None


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Photo Date Fixer — Portable')
        self.resize(1100, 700)

        self.current_folder = Path.home() / 'Pictures'
        self.items = []  # list of FileItem

        self._build_ui()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        # Top controls
        top_h = QtWidgets.QHBoxLayout()
        v.addLayout(top_h)

        self.folder_edit = QtWidgets.QLineEdit(str(self.current_folder))
        self.folder_edit.setAcceptDrops(False)
        top_h.addWidget(self.folder_edit)
        btn_browse = QtWidgets.QPushButton('Browse')
        btn_browse.clicked.connect(self.browse_folder)
        top_h.addWidget(btn_browse)

        # Timestamp choices
        self.ts_combo = QtWidgets.QComboBox()
        self.ts_combo.addItems(['EXIF DateTimeOriginal', 'File Modified', 'File Created'])
        top_h.addWidget(self.ts_combo)

        top_h.addStretch()

        # Expected date
        top_h.addWidget(QtWidgets.QLabel('Year'))
        self.exp_year = QtWidgets.QLineEdit('Any')
        self.exp_year.setMaximumWidth(80)
        top_h.addWidget(self.exp_year)
        top_h.addWidget(QtWidgets.QLabel('Month'))
        self.exp_month = QtWidgets.QLineEdit('Any')
        self.exp_month.setMaximumWidth(80)
        top_h.addWidget(self.exp_month)
        top_h.addWidget(QtWidgets.QLabel('Day'))
        self.exp_day = QtWidgets.QLineEdit('Any')
        self.exp_day.setMaximumWidth(80)
        top_h.addWidget(self.exp_day)

        btn_scan = QtWidgets.QPushButton('Scan Folder')
        btn_scan.clicked.connect(self.scan_folder)
        top_h.addWidget(btn_scan)

        # Middle: List with thumbnails
        splitter = QtWidgets.QSplitter()
        v.addWidget(splitter)

        # Left: tree list
        left_w = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_w)
        splitter.addWidget(left_w)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(['Status', 'Filename', 'Created', 'Modified', 'EXIF'])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setIconSize(QtCore.QSize(160, 120))
        left_layout.addWidget(self.tree)

        # Buttons under tree
        hbtn = QtWidgets.QHBoxLayout()
        left_layout.addLayout(hbtn)
        btn_select_all = QtWidgets.QPushButton('Select All')
        btn_select_all.clicked.connect(self.select_all)
        hbtn.addWidget(btn_select_all)
        btn_select_m = QtWidgets.QPushButton('Select Mismatches')
        btn_select_m.clicked.connect(self.select_mismatches)
        hbtn.addWidget(btn_select_m)
        btn_clear = QtWidgets.QPushButton('Clear Selection')
        btn_clear.clicked.connect(lambda: self.tree.clearSelection())
        hbtn.addWidget(btn_clear)

        btn_set = QtWidgets.QPushButton('Set Date for Selected...')
        btn_set.clicked.connect(self.open_set_date_dialog)
        hbtn.addWidget(btn_set)

        # Right: thumbnail preview (single large preview)
        right_w = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_w)
        splitter.addWidget(right_w)

        self.preview_label = QtWidgets.QLabel('Thumbnail Preview')
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setMinimumSize(320, 240)
        right_layout.addWidget(self.preview_label)

        # drag and drop onto central widget
        central.setAcceptDrops(True)
        central.dragEnterEvent = self.drag_enter
        central.dropEvent = self.drop_event

        # status bar
        self.status = self.statusBar()

        # connect tree selection change to update preview
        self.tree.itemSelectionChanged.connect(self.update_preview)

    def browse_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, 'Select folder', str(self.current_folder))
        if d:
            self.current_folder = Path(d)
            self.folder_edit.setText(str(self.current_folder))

    def drag_enter(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def drop_event(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        # take first local file/folder
        p = Path(urls[0].toLocalFile())
        if p.is_dir():
            self.current_folder = p
            self.folder_edit.setText(str(self.current_folder))
            self.scan_folder()
        else:
            # if file, open its parent
            self.current_folder = p.parent
            self.folder_edit.setText(str(self.current_folder))
            self.scan_folder()

    def parse_expected(self):
        def to_none(s):
            s = (s or '').strip()
            if s.lower() in ('', 'any', '*'):
                return None
            try:
                return int(s)
            except:
                return None
        return to_none(self.exp_year.text()), to_none(self.exp_month.text()), to_none(self.exp_day.text())

    def scan_folder(self):
        folder = Path(self.folder_edit.text()).expanduser().resolve()
        if not folder.is_dir():
            QtWidgets.QMessageBox.critical(self, 'Error', f'Folder not found: {folder}')
            return
        self.status.showMessage(f'Scanning {folder} ...')
        QtWidgets.QApplication.processEvents()

        files = list_files_nonrecursive(folder)
        self.items = []
        self.tree.clear()
        expected = self.parse_expected()
        ts_choice = self.ts_combo.currentIndex()  # 0=exif,1=modified,2=created

        for p in files:
            try:
                created, modified = get_file_times(p)
            except Exception:
                created, modified = None, None
            exif_dt = None
            if ts_choice == 0 and p.suffix.lower() in IMAGE_EXTS:
                exif_dt = exif_get_datetime(p)
                dt = exif_dt
            elif ts_choice == 2:
                dt = created
            else:
                dt = modified

            item = FileItem(p, created, modified, exif_dt)
            item.checked_dt = dt
            item.ok = self.matches(dt, expected)
            # create thumbnail if image
            if p.suffix.lower() in IMAGE_EXTS:
                pix = create_thumbnail_qpixmap(p)
                item.thumbnail = pix

            self.items.append(item)

        # populate tree
        for idx, it in enumerate(self.items):
            status = '✓' if it.ok else '⚠'
            created_str = it.created.strftime('%Y-%m-%d %H:%M:%S') if it.created else '—'
            modified_str = it.modified.strftime('%Y-%m-%d %H:%M:%S') if it.modified else '—'
            exif_str = (it.exif_dt.strftime('%Y-%m-%d %H:%M:%S') if it.exif_dt else '—')
            node = QtWidgets.QTreeWidgetItem([status, it.path.name, created_str, modified_str, exif_str])
            node.setData(0, QtCore.Qt.UserRole, idx)
            if it.thumbnail:
                node.setIcon(1, QtGui.QIcon(it.thumbnail))
            if not it.ok:
                node.setForeground(0, QtGui.QBrush(QtGui.QColor('red')))
                node.setForeground(1, QtGui.QBrush(QtGui.QColor('red')))
            self.tree.addTopLevelItem(node)

        self.status.showMessage(f'Scan complete: {len(self.items)} files')

    def matches(self, dt, expected):
        if dt is None:
            return False
        y,m,d = expected
        if y is not None and dt.year != y:
            return False
        if m is not None and dt.month != m:
            return False
        if d is not None and dt.day != d:
            return False
        return True

    def select_all(self):
        self.tree.selectAll()

    def select_mismatches(self):
        sel = []
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            idx = child.data(0, QtCore.Qt.UserRole)
            if not self.items[idx].ok:
                sel.append(child)
        self.tree.clearSelection()
        for node in sel:
            node.setSelected(True)

    def update_preview(self):
        sel = self.tree.selectedItems()
        if not sel:
            self.preview_label.setText('Thumbnail Preview')
            self.preview_label.setPixmap(QtGui.QPixmap())
            return
        node = sel[0]
        idx = node.data(0, QtCore.Qt.UserRole)
        it = self.items[idx]
        if it.thumbnail:
            self.preview_label.setPixmap(it.thumbnail.scaled(640, 480, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        else:
            self.preview_label.setText('No preview available')

    def open_set_date_dialog(self):
        sel = self.tree.selectedItems()
        if not sel:
            QtWidgets.QMessageBox.information(self, 'No selection', 'No files selected.')
            return
        indices = [node.data(0, QtCore.Qt.UserRole) for node in sel]
        dlg = SetDateDialog(self, indices, self.ts_combo.currentIndex())
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            new_dt = dlg.get_datetime()
            applied = 0
            failed = []
            for idx in indices:
                fi = self.items[idx]
                # optionally set EXIF first
                if dlg.apply_exif and fi.path.suffix.lower() in IMAGE_EXTS:
                    ok2, msg = exif_set_datetime(fi.path, new_dt)
                    if not ok2:
                        failed.append((fi.path.name, msg))
                        continue
                # apply filesystem times
                created_dt = new_dt if dlg.apply_created else None
                mod_dt = new_dt if dlg.apply_modified else None
                ok2, msg = set_file_times(fi.path, modified_dt=mod_dt, created_dt=created_dt)
                if ok2:
                    applied += 1
                else:
                    failed.append((fi.path.name, msg))
            msg = f'Applied to {applied} files.'
            if failed:
                msg += f' {len(failed)} failures. First: {failed[0][0]}: {failed[0][1]}'
            QtWidgets.QMessageBox.information(self, 'Result', msg)
            self.scan_folder()


class SetDateDialog(QtWidgets.QDialog):
    def __init__(self, parent, indices, ts_choice):
        super().__init__(parent)
        self.indices = indices
        self.ts_choice = ts_choice
        self.setWindowTitle('Set date/time for selected files')
        self.setModal(True)
        self.resize(420, 220)

        layout = QtWidgets.QVBoxLayout(self)

        layout.addWidget(QtWidgets.QLabel(f'Files selected: {len(indices)}'))

        form = QtWidgets.QFormLayout()
        self.year_spin = QtWidgets.QSpinBox()
        self.year_spin.setRange(1970, 9999)
        self.year_spin.setValue(datetime.now().year)
        form.addRow('Year', self.year_spin)
        self.month_spin = QtWidgets.QSpinBox(); self.month_spin.setRange(1,12); self.month_spin.setValue(1)
        form.addRow('Month', self.month_spin)
        self.day_spin = QtWidgets.QSpinBox(); self.day_spin.setRange(1,31); self.day_spin.setValue(1)
        form.addRow('Day', self.day_spin)
        self.hour_spin = QtWidgets.QSpinBox(); self.hour_spin.setRange(0,23); self.hour_spin.setValue(12)
        form.addRow('Hour', self.hour_spin)
        self.min_spin = QtWidgets.QSpinBox(); self.min_spin.setRange(0,59); self.min_spin.setValue(0)
        form.addRow('Minute', self.min_spin)
        self.sec_spin = QtWidgets.QSpinBox(); self.sec_spin.setRange(0,59); self.sec_spin.setValue(0)
        form.addRow('Second', self.sec_spin)

        layout.addLayout(form)

        # options: EXIF, modified, created
        self.apply_exif_cb = QtWidgets.QCheckBox('Set EXIF DateTimeOriginal (JPEG/TIFF)')
        self.apply_exif_cb.setChecked(True)
        layout.addWidget(self.apply_exif_cb)
        self.apply_modified_cb = QtWidgets.QCheckBox('Set File Modified time')
        self.apply_modified_cb.setChecked(True)
        layout.addWidget(self.apply_modified_cb)
        self.apply_created_cb = QtWidgets.QCheckBox('Set File Created time (Windows only)')
        self.apply_created_cb.setChecked(True)
        if platform.system() != 'Windows':
            self.apply_created_cb.setToolTip('Creation time usually cannot be modified on Linux/macOS')
        layout.addWidget(self.apply_created_cb)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    @property
    def apply_exif(self):
        return self.apply_exif_cb.isChecked()

    @property
    def apply_modified(self):
        return self.apply_modified_cb.isChecked()

    @property
    def apply_created(self):
        return self.apply_created_cb.isChecked()

    def get_datetime(self):
        return datetime(self.year_spin.value(), self.month_spin.value(), self.day_spin.value(), self.hour_spin.value(), self.min_spin.value(), self.sec_spin.value())


# ---------- Run ----------
def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
