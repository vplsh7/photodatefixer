# Photo Date Fixer (Portable Windows App)

**Photo Date Fixer** is a simple, fast, user-friendly utility for fixing incorrect photo and file dates.  
It scans a chosen folder (non-recursive), compares timestamps against expected date ranges, highlights mismatches, and lets you repair:

- **EXIF Date Taken (DateTimeOriginal)**
- **File Modified time**
- **File Created time** (Windows only)

The app displays thumbnails, supports drag-and-drop folders, and works entirely offline.  
Designed for photographers, archivists, and anyone who has inherited messy photo libraries.

![Main Window](/images/main.png)

---

## ✨ Features

### 🖼️ Image Features
- Reads and writes **EXIF DateTimeOriginal** for JPEG & TIFF
- Displays **thumbnail previews** for supported image formats
- Non-destructive: uses a safe temp-file → atomic replace flow

### 📁 File & Folder Features
- Drag-and-drop entire folders into the app
- Browse and load any folder manually
- Non-recursive scan (only items directly in the folder)
- Shows:
  - Date Created
  - Date Modified
  - EXIF Date Taken (if available)

### 🔍 Smart Date Checking
- Specify expected **Year / Month / Day**
- Use `Any` for unknown components (e.g., Year = 2012, Month = Any)
- The app scans files and highlights mismatches in **red**
- "Select mismatches" button for quick bulk selection

### 🛠 Timestamp Editing
You can apply a corrected timestamp to selected files:
- ✔ Set EXIF DateTaken (JPEG/TIFF)
- ✔ Set File Modified Time
- ✔ Set File Creation Time (Windows only — uses Win32 API `SetFileTime`)
- Supports both individual and bulk editing

### 👌 Portable & Simple
- No installation needed when using the portable build
- No internet required
- Fully standalone EXE (PyInstaller)
- Works on Windows 10 and Windows 11

---

## 📦 Installation

### **Download Portable EXE**
1. Go to the **Releases** page  
2. Download the .exe
3. Run it — that's it!

---

## 🖱️ Usage

![Main Window](/images/edit.png)

1. Launch the app  
2. Drag a folder onto the window (or click *Browse*)  
3. Set expected **Year / Month / Day**  
4. Select which timestamp should be checked:
   - EXIF Date Taken  
   - File Modified  
   - File Created  
5. Click **Scan Folder**  
6. Review mismatches, select them  
7. Click **Set Date for Selected…**  
8. Apply the correct timestamp(s)

---

## 🧰 Building from Source

### Requirements
- Python 3.9+
- PySide6
- Pillow
- piexif
- PyInstaller (for builds)

Install dependencies:

```bash
pip install PySide6 Pillow piexif pyinstaller
