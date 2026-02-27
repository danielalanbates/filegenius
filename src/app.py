#!/usr/bin/env python3
"""
FileGenius - Smart File Organization Tool
v4.0 - Open Source Release

Copyright (c) 2025 Daniel Alan Bates
License: Polyform Noncommercial 1.0.0 - See LICENSE file
Contact: daniel@batesai.org

Workflow:
1. Welcome screen - Choose what you want to do
2. Select folder
3. Execute operation
"""

__version__ = "4.1.0"
__author__ = "Daniel"

# Build distribution target. Set FILEGENIUS_MAS_BUILD=1 for Mac App Store builds.
# When True, sandbox restrictions apply: no AppleScript automation, no rumps menu bar,
# no arbitrary path access outside user-selected folders.
import os as _os
MAS_BUILD = _os.environ.get("FILEGENIUS_MAS_BUILD", "0") == "1"
del _os

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import shutil
import threading
import time
from datetime import datetime, timedelta
import heapq
import webbrowser
import requests
import logging
import os
import sys
import tempfile

# Sandbox-friendly temp directory setup
if 'HOME' in os.environ:
    _sandbox_tmp = os.path.join(os.environ['HOME'], 'Library/Containers/org.batesai.filegenius/Data/tmp')
    if not os.path.exists(_sandbox_tmp):
        try:
            os.makedirs(_sandbox_tmp, exist_ok=True)
        except OSError:
            _sandbox_tmp = tempfile.gettempdir()

    os.environ['TMPDIR'] = _sandbox_tmp
    os.environ['TEMP'] = _sandbox_tmp
    os.environ['TMP'] = _sandbox_tmp
    tempfile.tempdir = _sandbox_tmp


# OpenAI API key - users must provide their own via environment variable or settings
# No embedded keys in open source version

# Import license manager
try:
    from license_manager import LicenseManager
    HAS_LICENSE_MANAGER = True
except ImportError:
    HAS_LICENSE_MANAGER = False
    print("Warning: license_manager.py not found - running without license checks")

# Import system monitoring
try:
    from system_monitor import SystemMonitor
    HAS_SYSTEM_MONITOR = True
except ImportError:
    HAS_SYSTEM_MONITOR = False
    print("Warning: system_monitor.py not found - system monitoring disabled")

if not MAS_BUILD:
    try:
        import rumps
        HAS_RUMPS = True
    except ImportError:
        HAS_RUMPS = False
else:
    HAS_RUMPS = False


# ============================================================================
# CUSTOM BUTTON WIDGET
# ============================================================================

class ColorButton(tk.Label):
    """Custom button that properly displays colors on macOS with pop-up effect"""

    def __init__(self, parent, text, command, bg, fg='white', font=None, padx=20, pady=10, **kwargs):
        super().__init__(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            relief=tk.RAISED,
            borderwidth=2,
            highlightthickness=2,
            highlightbackground='#1D4ED8',
            highlightcolor='#1D4ED8',
            padx=padx,
            pady=pady,
            **kwargs,
        )
        self.command = command
        self.default_bg = bg
        self.hover_bg = self._darken_color(bg)

        # Bind events
        self.bind('<Button-1>', self._on_click)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)

    def _darken_color(self, hex_color):
        """Darken a hex color by 20% for hover effect"""
        hex_color = hex_color.lstrip('#')
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        r, g, b = int(r * 0.8), int(g * 0.8), int(b * 0.8)
        return f'#{r:02x}{g:02x}{b:02x}'

    def _on_click(self, event):
        # Pop down effect - darken background
        self.config(bg=self.hover_bg)

    def _on_release(self, event):
        # Pop up effect - restore background
        self.config(bg=self.default_bg)
        if self.command:
            self.command()

    def _on_enter(self, event):
        # On hover: darken background and add subtle shadow
        self.config(bg=self.hover_bg)

    def _on_leave(self, event):
        # On leave: restore default appearance
        self.config(bg=self.default_bg)

# ============================================================================
# SAFETY CHECKER
# ============================================================================

class SafetyChecker:
    """Validates operations to prevent system damage"""

    SAFE_FOLDERS = [
        '~/Downloads', '~/Desktop', '~/Documents',
        '~/Pictures', '~/Music', '~/Movies', '~/Public',
        '~/Library/Mobile Documents/com~apple~CloudDocs',  # iCloud Drive
    ]

    DANGEROUS_PATHS = [
        '/', '/System', '/Library', '/Applications',
        '/usr', '/bin', '/sbin', '/etc', '/private', '/var', '/dev'
    ]

    DANGEROUS_HOME_PATHS = [
        '~/Library', '~/.ssh', '~/.config', '~/.local',
    ]

    SYSTEM_FILE_EXTENSIONS = [
        '.dylib', '.framework', '.kext', '.app', '.bundle',
        '.plist', '.prefPane', '.dmg', '.pkg', '.mpkg',
    ]

    @classmethod
    def is_safe_folder(cls, path: str) -> Tuple[bool, str]:
        """Check if folder is safe to scan - Unrestricted version with warnings"""
        if not path:
            return False, "No folder selected"

        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(path):
            return False, "Folder doesn't exist"

        # Only warn if they explicitly select actual system-critical directories
        # Skip root '/' since it would match everything
        system_critical_paths = ['/System', '/Library', '/Applications',
                                 '/usr', '/bin', '/sbin', '/etc', '/private', '/var', '/dev']

        # Check if path exactly IS a system path or IS inside a system path (but not root)
        for dangerous in system_critical_paths:
            if path == dangerous or path.startswith(dangerous + os.sep):
                return True, "⚠️ WARNING: DANGEROUS SYSTEM FOLDER. Unrestricted access enabled - use extreme caution!"

        # Warn only for critical home folders like ~/Library, ~/.ssh, ~/.config
        # but NOT for user-friendly folders like ~/Downloads, ~/Documents, ~/Desktop
        for dangerous in cls.DANGEROUS_HOME_PATHS:
            dangerous_full = os.path.expanduser(dangerous)
            if path == dangerous_full or path.startswith(dangerous_full + os.sep):
                return True, "⚠️ WARNING: CRITICAL HOME FOLDER. Unrestricted access enabled - proceed with care!"

        if MAS_BUILD:
            return True, "Folder access granted"
        return True, "Unrestricted scan allowed"

    @classmethod
    def assess_file_safety(cls, filepath: str) -> Tuple[str, str, str, str]:
        """
        Assess file safety with descriptive warnings.
        Returns: (safety_level, warning_message, recommendation, age_status)
        """
        try:
            mod_time = os.path.getmtime(filepath)
            file_age_days = (time.time() - mod_time) / (24 * 3600)
            age_status = 'OLD' if file_age_days > 180 else 'RECENT'
            age_desc = f'{int(file_age_days)} days old'
        except:
            age_status = 'UNKNOWN'
            age_desc = 'unknown age'

        filepath_abs = os.path.abspath(os.path.expanduser(filepath))
        ext = os.path.splitext(filepath)[1].lower()

        for path in cls.DANGEROUS_PATHS:
            if filepath_abs.startswith(path):
                return 'CAUTION', '⚠️ SYSTEM FILE', 'Critical for OS stability - proceed with extreme caution', age_status

        for dangerous in cls.DANGEROUS_HOME_PATHS:
            dangerous_full = os.path.expanduser(dangerous)
            if filepath_abs.startswith(dangerous_full):
                return 'CAUTION', '⚠️ SYSTEM FILE', 'Critical system folder - proceed with extreme caution', age_status

        if ext in cls.SYSTEM_FILE_EXTENSIONS:
            return 'CAUTION', '⚠️ SYSTEM COMPONENT', 'Needed for system stability - proceed with caution', age_status

        if MAS_BUILD:
            return 'SAFE', 'ALLOWED', 'Access granted', age_status
        return 'SAFE', 'UNRESTRICTED', 'Unrestricted access enabled', age_status

    @classmethod
    def is_safe_file(cls, filepath: str) -> Tuple[bool, str]:
        """Legacy method for backwards compatibility"""
        safety_level, warning, recommendation, age_status = cls.assess_file_safety(filepath)
        if safety_level == 'DANGEROUS':
            return False, f"{warning} - {recommendation}"
        return True, ""


# ============================================================================
# FILE SIZE ANALYZER
# ============================================================================

class FileSizeAnalyzer:
    """Analyze files by size"""

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """Format bytes to human readable"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    @classmethod
    def analyze_folder(cls, folder: str, max_results: int = 5000, exclude_icloud: bool = True, progress_callback=None, wait_for_all: bool = False) -> Dict:
        """
        Analyze folder and return largest files using heap for memory efficiency.
        Only keeps top max_results files in memory at any time.

        Args:
            progress_callback: Optional function(files_scanned, estimated_total) to report progress
            wait_for_all: If False, skip files that take longer than 10 seconds to access
        """
        # Initialize counters
        results = {
            'total_files': 0,
            'total_size': 0,
            'largest_files': [],
            'incomplete': False  # Flag to indicate if scan was incomplete
        }
        last_callback_size = 0

        try:
            # Use a min-heap to keep only the largest files
            # Heap stores tuples of (size, counter, file_info_dict)
            # Counter ensures unique comparison when sizes are equal
            largest_heap = []
            counter = 0

            stopped = False
            for root, dirs, files in os.walk(folder, followlinks=False):
                # Unrestricted edition: Scan everything
                if stopped:
                    break

                for filename in files:
                    # Check if progress callback signals to stop
                    if progress_callback:
                        if not progress_callback(results['total_files'], results['total_size']):
                            results['incomplete'] = True
                            stopped = True
                            break

                    # Scan ALL files - no limit on scanning
                    # Include hidden files - they can be large too!

                    filepath = os.path.join(root, filename)

                    # Skip symlinks to avoid counting files twice
                    if os.path.islink(filepath):
                        continue

                    # Skip files that take too long if not waiting for all
                    file_start_time = time.time()
                    
                    try:
                        # Get actual disk usage for sparse files (like Docker.raw)
                        # os.path.getsize() returns logical size, stat().st_blocks gives actual disk usage
                        stat_info = os.stat(filepath)
                        # st_blocks is in 512-byte blocks, multiply by 512 to get bytes
                        actual_size = stat_info.st_blocks * 512
                        logical_size = stat_info.st_size

                        # Use actual size if file is sparse (actual < logical)
                        # Otherwise use logical size
                        size = actual_size if actual_size < logical_size else logical_size

                        # Check if file access took too long
                        access_time = time.time() - file_start_time
                        if not wait_for_all and access_time > 10:
                            results['incomplete'] = True
                            continue

                        safety_level, warning, recommendation, age_status = SafetyChecker.assess_file_safety(filepath)

                        # Always count file in totals
                        results['total_files'] += 1
                        results['total_size'] += size

                                        # Call progress callback every 100 files or 50MB, whichever comes first
                        if progress_callback and (results['total_files'] % 100 == 0 or
                                               results['total_size'] - last_callback_size > 50 * 1024 * 1024):
                            if not progress_callback(results['total_files'], results['total_size']):
                                results['incomplete'] = True
                                stopped = True
                                break
                            last_callback_size = results['total_size']

                        # Build file info dict
                        file_info = {
                            'path': filepath,
                            'size': size,
                            'size_formatted': cls.format_size(size),
                            'name': filename,
                            'safety_level': safety_level,
                            'warning': warning,
                            'recommendation': recommendation,
                            'age_status': age_status,
                            'is_safe': safety_level != 'DANGEROUS'
                        }

                        # Use heap to keep only top max_results largest files
                        if len(largest_heap) < max_results:
                            # Heap not full yet, just add (size, counter, file_info)
                            heapq.heappush(largest_heap, (size, counter, file_info))
                            counter += 1
                        elif size > largest_heap[0][0]:
                            # This file is larger than smallest in heap
                            heapq.heapreplace(largest_heap, (size, counter, file_info))
                            counter += 1

                    except (OSError, PermissionError):
                        continue

            # Convert heap to sorted list (largest first)
            results['largest_files'] = [file_info for size, cnt, file_info in sorted(largest_heap, reverse=True)]

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

        return results


# ============================================================================
# SIMPLE ORGANIZER
# ============================================================================

class SimpleOrganizer:
    """Organize files by type"""

    CATEGORIES = {
        'Images': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.heic', '.svg', '.raw'],
        'Documents': ['.pdf', '.doc', '.docx', '.txt', '.rtf', '.pages', '.md'],
        'Spreadsheets': ['.xls', '.xlsx', '.csv', '.numbers'],
        'Videos': ['.mp4', '.mov', '.avi', '.mkv', '.wmv', '.m4v'],
        'Audio': ['.mp3', '.wav', '.aac', '.flac', '.m4a', '.ogg'],
        'Archives': ['.zip', '.rar', '.7z', '.tar', '.gz', '.dmg'],
        'Code': ['.py', '.js', '.java', '.cpp', '.c', '.cs', '.rb', '.go', '.php', '.swift'],
        'Web': ['.html', '.css', '.jsx', '.tsx', '.vue'],
        'Config': ['.json', '.xml', '.yaml', '.yml', '.toml', '.ini'],
    }

    @classmethod
    def preview_organize_by_type(cls, folder: str) -> Dict:
        """Preview organization"""
        preview = {category: [] for category in cls.CATEGORIES}
        preview['Other'] = []
        preview['No Extension'] = []

        try:
            for filename in os.listdir(folder):
                filepath = os.path.join(folder, filename)

                if not os.path.isfile(filepath):
                    continue

                _, ext = os.path.splitext(filename)
                ext = ext.lower()

                found_category = None
                for category, extensions in cls.CATEGORIES.items():
                    if ext in extensions:
                        found_category = category
                        break

                if not ext:
                    preview['No Extension'].append(filename)
                elif found_category:
                    preview[found_category].append(filename)
                else:
                    preview['Other'].append(filename)

        except Exception as e:
            print(f"Error: {e}")

        return {k: v for k, v in preview.items() if v}

    @classmethod
    def organize_by_type(cls, folder: str, move_callback=None) -> Tuple[bool, str, int]:
        """Execute organization. Optional move_callback(original, dest) for undo logging."""
        files_moved = 0
        files_skipped = 0

        try:
            for filename in os.listdir(folder):
                filepath = os.path.join(folder, filename)

                if not os.path.isfile(filepath):
                    continue

                # Unrestricted version: allow CAUTION files but show warning
                try:
                    safety_level, _, _, _ = SafetyChecker.assess_file_safety(filepath)
                    if safety_level == 'DANGEROUS':
                        files_skipped += 1
                        continue
                    # CAUTION files are now allowed in unrestricted version
                except Exception:
                    # If safety check fails, leave file in place
                    files_skipped += 1
                    continue

                _, ext = os.path.splitext(filename)
                ext = ext.lower()

                target_folder = None
                for category, extensions in cls.CATEGORIES.items():
                    if ext in extensions:
                        target_folder = category
                        break

                if not target_folder:
                    target_folder = 'Other' if ext else 'No Extension'

                target_path = os.path.join(folder, target_folder)
                os.makedirs(target_path, exist_ok=True)

                dest = os.path.join(target_path, filename)
                shutil.move(filepath, dest)
                files_moved += 1
                
                # Log move for undo if callback provided
                if move_callback:
                    move_callback(filepath, dest)

            return True, f" Success! Organized {files_moved} files", files_moved

        except PermissionError as e:
            if MAS_BUILD:
                return False, "Permission denied. Please re-select the folder using the folder picker.", files_moved
            return False, f" Error: {str(e)}", files_moved
        except Exception as e:
            return False, f" Error: {str(e)}", files_moved


# ============================================================================
# MAIN GUI
# ============================================================================

class FileGenius:
    """Main application"""

    def __init__(self, root: tk.Tk = None):
        super().__init__()
        self.root = root or tk.Tk()
        self.root.title("FileGenius 4.0 - Smart Organizer")
        self.root.geometry("1100x800")  # Default size
        self.root.minsize(1000, 700)  # Enforce larger minimum to prevent collapsing

        # Initialize license manager
        self.license_manager = LicenseManager("FileGenius") if HAS_LICENSE_MANAGER else None

        # Initialize system monitor
        self.root.option_add('*Font', 'Helvetica 13')
        self.root.configure(bg='#F3F4F6')

        self.current_folder: Optional[str] = None
        self.selected_operation: Optional[str] = None
        self.analyze_results = None
        self.file_data = {}
        self.scanning = False
        self._permissions_notice_shown = False
        self.scan_dots = 0  # Animation counter
        self.estimated_files = 0  # Estimated file count for progress
        self.files_scanned = 0  # Files scanned so far
        self.scan_start_time = None  # When scan started
        self.undo_log: List[Tuple[str, str]] = []  # List of (original_path, new_path) for undo
        self.scan_stopped = False  # Flag to indicate scan was stopped
        self.scan_incomplete = False  # Flag to indicate scan didn't complete all files
        self.stop_scan_thread = None  # Thread for stopping scan

        # Check license on startup
        if not self._check_license_on_startup():
            # Trial expired - don't show main UI, just show trial dialog and quit
            return

        self.show_welcome()

    def _check_license_on_startup(self) -> bool:
        """Check license status and show appropriate dialog

        Returns:
            True if app can continue, False if trial expired
        """
        if not self.license_manager:
            return True  # No license manager, allow app to run

        # Freemium model: app always opens. Free-vs-Pro limits are enforced
        # when the user actually runs a cleanup operation (organize/delete/move).
        # This keeps the UI usable even after the free quota is reached.
        return True

    def _can_perform_cleaning(self) -> bool:
        """Check whether a cleanup action is allowed.

        Basic organize / move / delete should always work now with no trial limit.
        AI features remain gated separately by license in their own handlers.
        """
        return True

    def _register_cleaning(self):
        """Register a completed cleanup for the free plan."""
        logging.info(f'Registering cleaning use for period {self.license_manager._get_current_period()} if self.license_manager else "No license manager"')
        if not self.license_manager or self.license_manager.is_licensed():
            return
        self.license_manager.register_use()

    def _open_url(self, url):
        """Open URL in default browser"""
        import webbrowser
        webbrowser.open(url)

    @staticmethod
    def _move_to_trash_sandbox(filepath: str) -> bool:
        """Move file to Trash using NSFileManager (sandbox-safe, no AppleScript needed)."""
        try:
            import ctypes
            import ctypes.util

            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

            # Get NSFileManager.defaultManager
            NSFileManager = objc.objc_getClass(b'NSFileManager')
            sel_default = objc.sel_registerName(b'defaultManager')
            fm = objc.objc_msgSend(NSFileManager, sel_default)

            # Create NSURL from file path
            NSString = objc.objc_getClass(b'NSString')
            NSURL = objc.objc_getClass(b'NSURL')

            sel_str = objc.sel_registerName(b'stringWithUTF8String:')
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
            ns_path = objc.objc_msgSend(NSString, sel_str, filepath.encode('utf-8'))

            sel_url = objc.sel_registerName(b'fileURLWithPath:')
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            file_url = objc.objc_msgSend(NSURL, sel_url, ns_path)

            # Call trashItemAtURL:resultingItemURL:error:
            sel_trash = objc.sel_registerName(b'trashItemAtURL:resultingItemURL:error:')
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            result = objc.objc_msgSend(fm, sel_trash, file_url, None, None)
            return bool(result)
        except Exception as e:
            logging.warning(f"Sandbox trash failed for {filepath}: {e}")
            return False

    def clear_screen(self):
        """Clear all widgets"""
        for widget in self.root.winfo_children():
            widget.destroy()

    def show_welcome(self):
        """Welcome screen with colorful design"""
        self.clear_screen()

        # Modern gradient header
        title_frame = tk.Frame(self.root, bg='#2563EB', pady=45)  # Modern blue
        title_frame.pack(fill=tk.X)

        # Logo and title together
        title_row = tk.Frame(title_frame, bg='#2563EB')
        title_row.pack()

        # Teal/blue background square for the icon
        logo_outer = tk.Frame(title_row, bg='#2563EB', highlightthickness=0)
        logo_outer.pack(side=tk.LEFT, padx=(0, 20))

        # Teal/blue background square for the icon
        logo_frame = tk.Frame(
            logo_outer,
            bg='#2563EB',
            width=85,
            height=85,
            highlightthickness=0,
            bd=0,
        )
        logo_frame.pack(padx=7, pady=7)
        logo_frame.pack_propagate(False)  # Maintain fixed size

        # Resolve asset path (works in py2app bundle)
        import sys
        app_dir = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))

        # Load and display the icon
        try:
            from PIL import Image, ImageTk
            icon_path = app_dir / 'filegenius_1024.png'

            if icon_path.exists():
                img = Image.open(icon_path)
                img = img.resize((80, 80), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                logo_icon = tk.Label(logo_frame, image=photo, bg='#2563EB', bd=0)
                logo_icon.image = photo  # Keep reference
                logo_icon.place(relx=0.5, rely=0.5, anchor='center')
            else:
                # Fallback to emoji if icon not found
                logo_icon = tk.Label(
                    logo_frame,
                    text="🗂️",
                    font=('Helvetica', 55),
                    bg='#2563EB',
                    fg='#FBBF24',
                    bd=0,
                )
                logo_icon.place(relx=0.5, rely=0.5, anchor='center')
        except Exception as e:
            # Fallback to emoji on error
            print(f"Logo error: {e}")
            logo_icon = tk.Label(
                logo_frame,
                text="🗂️",
                font=('Helvetica', 55),
                bg='#2563EB',
                fg='#FBBF24',
                bd=0,
            )
            logo_icon.place(relx=0.5, rely=0.5, anchor='center')

        tk.Label(
            title_row,
            text="FileGenius",
            font=('Helvetica', 42, 'bold'),
            bg='#2563EB',
            fg='white',
        ).pack(side=tk.LEFT)

        tk.Label(
            title_frame,
            text="Organize your files safely and easily",
            font=('Helvetica', 19),
            fg='#BFDBFE',
            bg='#2563EB',
        ).pack(pady=(12, 0))

        # Content with lighter background (original spacing)
        content = tk.Frame(self.root, bg='#F8F9FA', padx=40, pady=30)
        content.pack(fill=tk.BOTH, expand=True)

        # Cards container - use grid for side-by-side layout
        cards_container = tk.Frame(content, bg='#F8F9FA')
        cards_container.pack(fill=tk.BOTH, expand=True, pady=10)

        # Match original layout: two columns sharing space
        cards_container.grid_columnconfigure(0, weight=1)
        cards_container.grid_columnconfigure(1, weight=1)

        # Colorful cards with different accent colors
        self.create_card(
            cards_container,
            "📂 Organize by File Type",
            "Sort files into folders\n(Images, Documents, Code, etc.)",
            "organize",
            0,
            accent_color='#FF6B6B',
        )  # Coral red

        self.create_card(
            cards_container,
            "📊 Find Large Files",
            "See which files take up\nthe most space",
            "analyze",
            1,
            accent_color='#4ECDC4',
        )  # Turquoise

        # Footer with repositioned elements - bottom layout
        footer = tk.Frame(self.root, bg='#2563EB', pady=18)
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        # Bottom left: Bates AI logo + version
        footer_left = tk.Frame(footer, bg='#2563EB')
        footer_left.pack(side=tk.LEFT, padx=(10, 0))
        try:
            from PIL import Image, ImageTk
            import os
            # Search for logo in known locations (no bundled placeholder)
            if MAS_BUILD:
                possible_paths = [
                    app_dir / 'batesai-logo.jpg',
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai-logo.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.png'),
                ]
            else:
                possible_paths = [
                    app_dir / 'batesai-logo.jpg',
                    '~/batesai.jpg',
                    '~/batesai.png',
                    '~/batesai-logo.jpg',
                    '~/batesai-logo.png',
                    '~/Downloads/AIcode-10-Archive/Experimental/meetpad-fresh-premium-experiment/batesai-logo.jpg',
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.png'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'batesai.png'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'batesai.png'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'batesai.png'),
                ]

            logo_path = None
            for path in possible_paths:
                expanded = os.path.expanduser(path)
                if os.path.exists(expanded):
                    logo_path = expanded
                    break

            if logo_path:
                img = Image.open(logo_path)
                img = img.resize((60, 60), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                bates_logo = tk.Label(
                    footer_left,
                    image=photo,
                    bg='#2563EB',
                    relief=tk.SOLID,
                    borderwidth=2,
                    bd=2,
                    highlightthickness=2,
                    highlightbackground='#60A5FA',
                )
                bates_logo.image = photo
                bates_logo.pack(side=tk.LEFT, padx=(0, 10))
                bates_logo.bind('<Button-1>', lambda e: self._open_url('https://batesai.org'))
                bates_logo.bind('<Enter>', lambda e: bates_logo.config(highlightbackground='white', bg='#1E40AF'))
                bates_logo.bind('<Leave>', lambda e: bates_logo.config(highlightbackground='#60A5FA', bg='#2563EB'))
            else:
                bates_logo = tk.Label(
                    footer_left,
                    text="B",
                    font=('Helvetica', 24, 'bold'),
                    bg='#2563EB',
                    fg='#FBBF24',
                    bd=0,
                )
                bates_logo.pack(side=tk.LEFT, padx=(0, 10))
                bates_logo.bind('<Button-1>', lambda e: self._open_url('https://batesai.org'))
        except Exception as e:
            print(f"Bates AI logo error: {e}")

        # Feedback link next to logo
        feedback_link = tk.Label(
            footer_left,
            text="Give feedback",
            font=('Helvetica', 13, 'underline'),
            fg='#BFDBFE',
            bg='#2563EB',
        )
        feedback_link.pack(side=tk.LEFT, padx=(0, 12))
        feedback_link.bind('<Button-1>', lambda e: self._open_url('mailto:help@BatesAI.org'))
        feedback_link.bind('<Enter>', lambda e: feedback_link.config(fg='white'))
        feedback_link.bind('<Leave>', lambda e: feedback_link.config(fg='#BFDBFE'))

        tk.Label(
            footer_left,
            text=f"v{__version__}",
            font=('Helvetica', 13),
            fg='#93C5FD',
            bg='#2563EB',
        ).pack(side=tk.LEFT, padx=(0, 10))

        # Center area: prominent AI upgrade/status area
        footer_center = tk.Frame(footer, bg='#2563EB')
        footer_center.pack(side=tk.LEFT, expand=True)

        # Bottom right: Credits and link
        footer_right = tk.Frame(footer, bg='#2563EB')
        footer_right.pack(side=tk.RIGHT, padx=(0, 10))
        tk.Label(
            footer_right,
            text="Built with organize by Thomas Feldmann",
            font=('Helvetica', 15),
            fg='#BFDBFE',
            bg='#2563EB',
        ).pack(side=tk.LEFT)
        credits_link = tk.Label(
            footer_right,
            text="github.com/tfeldmann/organize",
            font=('Helvetica', 14, 'underline'),
            fg='#93C5FD',
            bg='#2563EB',
        )
        credits_link.pack(side=tk.LEFT, padx=5)
        credits_link.bind('<Button-1>', lambda e: self._open_url('https://github.com/tfeldmann/organize'))
        credits_link.bind('<Enter>', lambda e: credits_link.config(fg='white'))
        credits_link.bind('<Leave>', lambda e: credits_link.config(fg='#93C5FD'))

    def _ensure_delete_permissions(self):
        """Prompt user to grant macOS permissions for Finder automation."""
        if self._permissions_notice_shown:
            return

        if MAS_BUILD:
            # Sandbox handles permissions through entitlements; no Finder automation needed.
            self._permissions_notice_shown = True
            return

        # Check if permissions are already properly set by attempting a test operation
        try:
            import subprocess
            result = subprocess.run(
                ['osascript', '-e', 'tell application "Finder" to activate'],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                # Permissions already set, skip the dialog
                self._permissions_notice_shown = True
                return
        except Exception:
            pass

        self._permissions_notice_shown = True
        message = (
            "macOS needs permission before FileGenius can move files to Trash via Finder.\n\n"
            "1. Open System Settings → Privacy & Security.\n"
            "2. Grant Automation permission for Python (or FileGenius) to control Finder.\n"
            "3. Also grant Full Disk Access if prompted.\n\n"
            "After granting access, rerun the cleanup action."
        )
        if messagebox.askyesno(
            "macOS Permission Required",
            message + "\n\nOpen System Settings now?",
        ):
            import subprocess
            subprocess.run(['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_Automation'])

        # Teal/blue background square for the icon
        logo_outer = tk.Frame(title_row, bg='#2563EB', highlightthickness=0)
        logo_outer.pack(side=tk.LEFT, padx=(0, 20))

        # Teal/blue background square for the icon
        logo_frame = tk.Frame(logo_outer, bg='#2563EB', width=85, height=85,
                             highlightthickness=0, bd=0)
        logo_frame.pack(padx=7, pady=7)
        logo_frame.pack_propagate(False)  # Maintain fixed size

        # Load and display the icon
        try:
            from PIL import Image, ImageTk
            import os
            import sys

            # Get the directory where the executable/script is located
            if getattr(sys, 'frozen', False):
                # Running as compiled app
                app_dir = sys._MEIPASS
            else:
                # Running as script
                app_dir = os.path.dirname(os.path.abspath(__file__))

            # Load the FileGenius logo
            icon_path = os.path.join(app_dir, 'filegenius_1024.png')

            if os.path.exists(icon_path):
                img = Image.open(icon_path)
                img = img.resize((80, 80), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                logo_icon = tk.Label(logo_frame, image=photo, bg='#2563EB', bd=0)
                logo_icon.image = photo  # Keep reference
                logo_icon.place(relx=0.5, rely=0.5, anchor='center')
            else:
                # Fallback to emoji if icon not found
                logo_icon = tk.Label(logo_frame, text="🗂️", font=('Helvetica', 55),
                                    bg='#2563EB', fg='#FBBF24', bd=0)
                logo_icon.place(relx=0.5, rely=0.5, anchor='center')
        except Exception as e:
            # Fallback to emoji on error
            print(f"Logo error: {e}")
            logo_icon = tk.Label(logo_frame, text="🗂️", font=('Helvetica', 55),
                                bg='#2563EB', fg='#FBBF24', bd=0)
            logo_icon.place(relx=0.5, rely=0.5, anchor='center')

        tk.Label(title_row, text="FileGenius",
                font=('Helvetica', 42, 'bold'), bg='#2563EB', fg='white').pack(side=tk.LEFT)

        tk.Label(title_frame, text="Organize your files safely and easily",
                font=('Helvetica', 19), fg='#BFDBFE', bg='#2563EB').pack(pady=(12, 0))

        # Content with lighter background (slightly reduced padding to avoid excess whitespace)
        content = tk.Frame(self.root, bg='#F8F9FA', padx=30, pady=20)
        content.pack(fill=tk.BOTH, expand=True)

        # Cards container - use grid for side-by-side layout
        cards_container = tk.Frame(content, bg='#F8F9FA')
        cards_container.pack(fill=tk.BOTH, expand=True, pady=10)

        # Configure grid columns to be equal width (2 columns - cleaner, focused layout)
        cards_container.grid_columnconfigure(0, weight=1)
        cards_container.grid_columnconfigure(1, weight=1)

        # Colorful cards with different accent colors
        self.create_card(cards_container, "📂 Organize by File Type",
                        "Sort files into folders\n(Images, Documents, Code, etc.)",
                        "organize", 0, accent_color='#FF6B6B')  # Coral red

        self.create_card(cards_container, "📊 Find Large Files",
                        "See which files take up\nthe most space",
                        "analyze", 1, accent_color='#4ECDC4')  # Turquoise

        # Footer with repositioned elements - bottom layout
        footer = tk.Frame(self.root, bg='#2563EB', pady=18)
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        # Bottom left: Bates AI logo + version
        footer_left = tk.Frame(footer, bg='#2563EB')
        footer_left.pack(side=tk.LEFT, padx=(10, 0))
        try:
            from PIL import Image, ImageTk
            import os
            # Search for logo in multiple locations
            if MAS_BUILD:
                possible_paths = [
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai-logo.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.png'),
                ]
            else:
                possible_paths = [
                    '~/batesai.jpg',
                    '~/batesai.png',
                    '~/batesai-logo.jpg',
                    '~/batesai-logo.png',
                    '~/Downloads/AIcode-10-Archive/Experimental/meetpad-fresh-premium-experiment/batesai-logo.jpg',
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batesai.png'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'batesai.png'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'batesai.png'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'batesai.jpg'),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'batesai.png'),
                ]

            logo_path = None
            for path in possible_paths:
                expanded = os.path.expanduser(path)
                if os.path.exists(expanded):
                    logo_path = expanded
                    break

            if logo_path:
                img = Image.open(logo_path)
                img = img.resize((60, 60), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                bates_logo = tk.Label(footer_left, image=photo, bg='#2563EB',
                                      relief=tk.SOLID, borderwidth=2, bd=2,
                                      highlightthickness=2, highlightbackground='#60A5FA')
                bates_logo.image = photo
                bates_logo.pack(side=tk.LEFT, padx=(0, 10))
                bates_logo.bind('<Button-1>', lambda e: self._open_url('https://batesai.org'))
                bates_logo.bind('<Enter>', lambda e: bates_logo.config(highlightbackground='white', bg='#1E40AF'))
                bates_logo.bind('<Leave>', lambda e: bates_logo.config(highlightbackground='#60A5FA', bg='#2563EB'))
            else:
                bates_logo = tk.Label(footer_left, text="B", font=('Helvetica', 24, 'bold'), bg='#2563EB', fg='#FBBF24', bd=0)
                bates_logo.pack(side=tk.LEFT, padx=(0, 10))
                bates_logo.bind('<Button-1>', lambda e: self._open_url('https://batesai.org'))
        except Exception as e:
            print(f"Bates AI logo error: {e}")

        tk.Label(footer_left, text=f"v{__version__}", font=('Helvetica', 13),
                fg='#93C5FD', bg='#2563EB').pack(side=tk.LEFT, padx=(0, 10))

        # Center area: prominent AI upgrade/status area
        footer_center = tk.Frame(footer, bg='#2563EB')
        footer_center.pack(side=tk.LEFT, expand=True)

        # Bottom right: Credits and link
        footer_right = tk.Frame(footer, bg='#2563EB')
        footer_right.pack(side=tk.RIGHT, padx=(0, 10))
        tk.Label(footer_right, text="Built with organize by Thomas Feldmann", font=('Helvetica', 15), fg='#BFDBFE', bg='#2563EB').pack(side=tk.LEFT)
        credits_link = tk.Label(footer_right, text="github.com/tfeldmann/organize", font=('Helvetica', 14, 'underline'), fg='#93C5FD', bg='#2563EB')
        credits_link.pack(side=tk.LEFT, padx=5)
        credits_link.bind('<Button-1>', lambda e: self._open_url('https://github.com/tfeldmann/organize'))
        credits_link.bind('<Enter>', lambda e: credits_link.config(fg='white'))
        credits_link.bind('<Leave>', lambda e: credits_link.config(fg='#93C5FD'))

        # Footer center placeholder (empty)

    def create_card(self, parent, title, desc, operation, col, accent_color='#007AFF'):
        """Create colorful operation card"""
        # Shadow container for depth (original sizing)
        shadow = tk.Frame(parent, bg='#D0D0D0', height=184)
        shadow.grid(row=0, column=col, sticky='nsew', padx=15, pady=15)
        shadow.grid_propagate(False)

        # Outer container with white background
        container = tk.Frame(shadow, bg='white', relief=tk.RAISED, borderwidth=0, height=180)
        container.place(x=0, y=0, relwidth=1, relheight=1)
        container.propagate(False)

        # Click handler
        def click(e=None):
            self.current_operation = operation
            if operation == 'system_health':
                self.show_system_health()
            else:
                self.show_folder_select()

        # Colorful accent bar at top
        accent_bar = tk.Frame(container, bg=accent_color, height=6)
        accent_bar.pack(fill=tk.X)

        # Icon/emoji at top with colorful background circle
        icon_frame = tk.Frame(container, bg='white')
        icon_frame.pack(pady=(20, 5))

        icon_bg = tk.Frame(icon_frame, bg=accent_color, width=70, height=70)
        icon_bg.pack()
        icon_bg.pack_propagate(False)

        # Use text/symbol instead of emoji for better color display
        icon_text = title.split()[0]

        # Replace emojis with colored symbols that work in tkinter
        if '📂' in icon_text or '📁' in icon_text:
            # Use folder symbol
            icon_text = '◧'  # Folder-like box symbol
            icon_font = ('Helvetica', 54, 'bold')
        elif '📊' in icon_text:
            # Use stacked bars for chart
            icon_text = '▤'  # Grid/chart pattern
            icon_font = ('Helvetica', 54, 'bold')
        else:
            icon_font = ('Helvetica', 42, 'bold')

        icon_label = tk.Label(icon_bg, text=icon_text, font=icon_font,
                             bg=accent_color, fg='white')
        icon_label.pack(expand=True)

        # Title without emoji
        title_text = ' '.join(title.split()[1:])
        title_label = tk.Label(container, text=title_text, font=('Helvetica', 17, 'bold'),
                              bg='white', fg='#2C3E50')
        title_label.pack(pady=(10, 5))

        # Description
        desc_label = tk.Label(container, text=desc, font=('Helvetica', 13),
                             fg='#7F8C8D', bg='white', justify='center')
        desc_label.pack(pady=(0, 15))

        # Hover effects for card - smooth without layout shift
        def on_hover_enter(e):
            # Only change colors, no size changes to prevent layout shift
            container.config(bg='#F8F9FA')  # Slight background change
            shadow.config(bg='#A0A0A0')  # Darker shadow on hover
            desc_label.config(bg='#F8F9FA')  # Match container background
            title_label.config(bg='#F8F9FA')  # Match container background
            icon_frame.config(bg='#F8F9FA')  # Match container background

        def on_hover_leave(e):
            container.config(bg='white')  # Original background
            shadow.config(bg='#D0D0D0')  # Original shadow
            desc_label.config(bg='white')  # Restore background
            title_label.config(bg='white')  # Restore background
            icon_frame.config(bg='white')  # Restore background

        # Bind click and hover to all widgets
        for widget in [container, icon_frame, icon_bg, icon_label, title_label, desc_label, shadow, accent_bar]:
            widget.bind('<Button-1>', click)
            widget.bind('<Enter>', on_hover_enter)
            widget.bind('<Leave>', on_hover_leave)

    def show_folder_select(self):
        """Folder selection screen with modern design"""
        self.clear_screen()

        # Modern header
        header = tk.Frame(self.root, bg='#2563EB', pady=12)
        header.pack(fill=tk.X)

        # Back button and title on same line
        header_content = tk.Frame(header, bg='#2563EB')
        header_content.pack(fill=tk.X, padx=20)

        back_btn = tk.Label(
            header_content,
            text="← Back",
            font=('Helvetica', 13, 'bold'),
            fg='white',
            bg='#14B8A6',
            relief=tk.RAISED,
            padx=15,
            pady=8,
            borderwidth=2,
            highlightthickness=0,
        )
        back_btn.pack(side=tk.LEFT)
        back_btn.bind('<Button-1>', lambda e: back_btn.config(relief=tk.SUNKEN))
        back_btn.bind('<ButtonRelease-1>', lambda e: (back_btn.config(relief=tk.RAISED), self.show_welcome()))

        # Logo and title
        try:
            from PIL import Image, ImageTk
            import sys
            app_dir = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
            icon_path = app_dir / 'filegenius_1024.png'
            if icon_path.exists():
                img = Image.open(icon_path)
                img = img.resize((40, 40), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                logo = tk.Label(header_content, image=photo, bg='#2563EB', bd=0)
                logo.image = photo
                logo.pack(side=tk.LEFT, padx=(20, 10))
        except:
            # Fallback to emoji
            logo = tk.Label(header_content, text="🗂️", font=('Helvetica', 28),
                           bg='#2563EB', fg='#FBBF24')
            logo.pack(side=tk.LEFT, padx=(20, 10))

        operation_titles = {
            'organize': 'Organize by File Type',
            'analyze': 'Find Large Files'
        }

        tk.Label(header_content, text=f"FileGenius - {operation_titles[self.current_operation]}",
                font=('Helvetica', 20, 'bold'), fg='white', bg='#2563EB').pack(side=tk.LEFT)

        # Content with light background - REDUCED PADDING
        content = tk.Frame(self.root, bg='#F8F9FA', padx=30, pady=15)
        content.pack(fill=tk.BOTH, expand=True)

        tk.Label(content, text="Select a folder:",
                font=('Helvetica', 20, 'bold'), fg='#2C3E50', bg='#F8F9FA').pack(pady=(0, 15))

        # Quick buttons - compact, with emojis for clarity
        quick_frame = tk.Frame(content, bg='#F8F9FA', pady=8)
        quick_frame.pack()

        ColorButton(quick_frame, text="📥 Downloads",
                   command=lambda: self.select_folder('~/Downloads'),
                   font=('Helvetica', 15, 'bold'), bg='#2563EB', fg='white',
                   padx=22, pady=12).grid(row=0, column=0, padx=10)

        ColorButton(quick_frame, text="🖥️ Desktop",
                   command=lambda: self.select_folder('~/Desktop'),
                   font=('Helvetica', 15, 'bold'), bg='#2563EB', fg='white',
                   padx=22, pady=12).grid(row=0, column=1, padx=10)

        ColorButton(quick_frame, text="📄 Documents",
                   command=lambda: self.select_folder('~/Documents'),
                   font=('Helvetica', 15, 'bold'), bg='#2563EB', fg='white',
                   padx=22, pady=12).grid(row=0, column=2, padx=10)

        tk.Label(content, text="or", font=('Helvetica', 15), fg='#95A5A6', bg='#F8F9FA').pack(pady=10)

        ColorButton(content, text="📂 Browse for Folder...",
                   command=self.browse_folder,
                   font=('Helvetica', 15, 'bold'), bg='#4A90E2', fg='white',
                   padx=30, pady=12).pack(pady=20)

        # Result area - scrollable
        self.result_frame = tk.Frame(content, bg='#F8F9FA')
        self.result_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 5))

    def select_folder(self, folder_path):
        """Quick select folder"""
        folder = os.path.expanduser(folder_path)
        if os.path.exists(folder):
            self.set_folder(folder)
        else:
            messagebox.showwarning("Not Found", f"{folder} doesn't exist")

    def browse_folder(self):
        """Browse for folder"""
        folder = filedialog.askdirectory(
            title="Select Folder",
            initialdir=os.path.expanduser('~/Downloads')
        )
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder):
        """Set folder and show continue button"""
        self.current_folder = folder
        is_safe, message = SafetyChecker.is_safe_folder(folder)

        # Clear result area
        for widget in self.result_frame.winfo_children():
            widget.destroy()

        # In unrestricted version, show warnings for dangerous folders but allow proceeding
        if "WARNING" in message or "DANGEROUS" in message:
            # Show warning but still allow proceeding (unrestricted version)
            self.show_warning_but_allow_proceed(folder, message)
            return

        # Check if this is an iCloud location
        is_icloud = 'iCloud' in folder or 'Mobile Documents' in folder

        # Show result - use yellow background for iCloud, green for normal folders
        result_bg = '#FFF3CD' if is_icloud else '#E8F5E9'
        result = tk.Frame(self.result_frame, bg=result_bg, relief=tk.SOLID, borderwidth=2, pady=15, padx=20)
        result.pack(fill=tk.X, pady=5)

        tk.Label(
            result,
            text=" Selected folder:",
            font=('Helvetica', 13),
            bg=result_bg,
            fg='#666'
        ).pack(anchor=tk.W)
        tk.Label(
            result,
            text=folder,
            font=('Helvetica', 14, 'bold'),
            fg='#000000',
            bg=result_bg
        ).pack(anchor=tk.W, pady=3)

        # Show iCloud warning inline if it's an iCloud location
        if is_icloud:
            warning_frame = tk.Frame(result, bg=result_bg, pady=10)
            warning_frame.pack(fill=tk.X, pady=8)

            tk.Label(warning_frame,
                    text=" iCloud Drive Location",
                    font=('Helvetica', 14, 'bold'),
                    bg=result_bg, fg='#856404').pack(anchor=tk.W)

            tk.Label(warning_frame,
                    text=" Files in iCloud may appear to take up space but are stored online.\n"
                         "Deleting them may NOT free up local disk space if 'Optimize Mac Storage' is enabled.",
                    font=('Helvetica', 12),
                    bg=result_bg, fg='#856404',
                    justify=tk.LEFT,
                    wraplength=700).pack(anchor=tk.W, pady=(5, 0))

        tk.Label(result, text=f" {message}", font=('Helvetica', 13, 'bold'),
                fg='#2E7D32' if not is_icloud else '#856404', bg=result_bg).pack(anchor=tk.W, pady=8)

        # Blue button with white text - using ColorButton
        continue_btn = ColorButton(result, text=" Continue ",
                 font=('Helvetica', 14, 'bold'),
                 bg='#007AFF', fg='white',
                 padx=25, pady=10,
                 command=self.execute_operation)
        continue_btn.pack(pady=10)

    def show_warning_but_allow_proceed(self, folder: str, message: str):
        """Show warning for dangerous folder but allow proceeding (unrestricted version)"""
        # Warning box with orange background
        warning_bg = '#FFF3CD'
        warning = tk.Frame(self.result_frame, bg=warning_bg, relief=tk.SOLID, borderwidth=3, pady=15, padx=20)
        warning.pack(fill=tk.X, pady=5)

        # Warning icon and title
        title_frame = tk.Frame(warning, bg=warning_bg)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(title_frame, text="⚠️ WARNING: DANGEROUS FOLDER",
                font=('Helvetica', 16, 'bold'), fg='#856404', bg=warning_bg).pack(side=tk.LEFT)

        # Selected folder
        tk.Label(warning, text=folder, font=('Helvetica', 12, 'bold'), 
                bg=warning_bg, fg='#000000', wraplength=700).pack(pady=(0, 8))

        # Warning message
        tk.Label(warning, text=message, font=('Helvetica', 12, 'bold'),
                fg='#C62828', bg=warning_bg, wraplength=700).pack(pady=(0, 10))

        # Note about unrestricted access
        tk.Label(warning, text="Unrestricted version: You can proceed, but be extremely careful.",
                font=('Helvetica', 11), fg='#856404', bg=warning_bg, wraplength=700).pack(pady=(0, 15))

        # Continue button
        continue_btn = tk.Button(warning, text="⚠️ Continue Anyway",
                               font=('Helvetica', 14, 'bold'),
                               bg='#FF6B6B', fg='black',
                               padx=20, pady=8,
                               command=self.proceed_with_dangerous_folder)
        continue_btn.pack()

    def proceed_with_dangerous_folder(self):
        """User chose to proceed with dangerous folder"""
        # Clear the warning and show normal result
        for widget in self.result_frame.winfo_children():
            widget.destroy()

        # Show normal result with red background to indicate danger
        result = tk.Frame(self.result_frame, bg='#FFEBEE', relief=tk.SOLID, borderwidth=2, pady=15, padx=20)
        result.pack(fill=tk.X, pady=5)

        tk.Label(
            result,
            text=f"✓ Selected: {self.current_folder}",
            font=('Helvetica', 14, 'bold'),
            fg='#C62828',
            bg='#FFEBEE'
        ).pack()

        tk.Label(
            result,
            text="⚠️ DANGEROUS FOLDER - Proceed with extreme caution",
            font=('Helvetica', 12, 'bold'),
            fg='#C62828',
            bg='#FFEBEE'
        ).pack(pady=(5, 0))

        # Blue button with white text - using ColorButton
        continue_btn = ColorButton(result, text=" Continue ",
                 font=('Helvetica', 14, 'bold'),
                 bg='#007AFF', fg='white',
                 padx=25, pady=10,
                 command=self.execute_operation)
        continue_btn.pack(pady=10)

    def show_unsafe_folder_warning(self, folder: str, message: str):
        """Show large warning for unsafe folder selection"""
        # Clear any selected folder to block execution
        self.current_folder = None

        # Compact warning box
        warning_bg = '#FFF3CD'
        warning = tk.Frame(self.result_frame, bg=warning_bg, relief=tk.SOLID, borderwidth=3, pady=20, padx=25)
        warning.pack(fill=tk.BOTH, expand=True, pady=5)

        # Warning icon and title - COMPACT
        tk.Label(warning, text=" DANGEROUS FOLDER",
                font=('Helvetica', 18, 'bold'), fg='#856404', bg=warning_bg).pack(pady=(0, 12))

        # Selected folder
        tk.Label(warning, text=folder, font=('Helvetica', 13, 'bold'), bg=warning_bg, fg='#000',
                wraplength=700).pack(pady=(0, 12))

        # Warning message
        tk.Label(warning, text=message, font=('Helvetica', 14, 'bold'),
                fg='#C62828', bg=warning_bg).pack(pady=(0, 12))

        # Explanation - COMPACT, no folder hints
        explanation = tk.Label(warning,
                              text="Scanning/moving/deleting files here could:\n"
                                   " Damage your operating system\n"
                                   " Break installed applications\n"
                                   " Make your Mac unstable",
                              font=('Helvetica', 14), fg='#856404', bg=warning_bg,
                              justify=tk.LEFT)
        explanation.pack(pady=(0, 15))

        # Buttons - Two options
        btn_frame = tk.Frame(warning, bg=warning_bg)
        btn_frame.pack(pady=(0, 5))

        ColorButton(btn_frame, text=" Go Back",
                   font=('Helvetica', 14, 'bold'), bg='#6C757D', fg='white',
                   padx=25, pady=10,
                   command=lambda: (self.result_frame.winfo_children()[0].destroy() if self.result_frame.winfo_children() else None)).pack(side=tk.LEFT, padx=8)

    def execute_operation(self):
        """Execute the chosen operation"""
        if self.current_operation == 'organize':
            self.show_organize()
        elif self.current_operation == 'analyze':
            self.show_analyze()

    def show_system_health(self):
        """System health monitoring screen"""
        self.clear_screen()

        # Modern header
        header = tk.Frame(self.root, bg='#2563EB', pady=12)
        header.pack(fill=tk.X)

        # Back button and title
        header_content = tk.Frame(header, bg='#2563EB')
        header_content.pack(fill=tk.X, padx=20)

        back_btn = tk.Label(
            header_content,
            text="← Back",
            font=('Helvetica', 12, 'bold'),
            fg='white',
            bg='#14B8A6',
            relief=tk.RAISED,
            padx=15,
            pady=8,
            borderwidth=2,
            highlightthickness=0,
        )
        back_btn.pack(side=tk.LEFT)
        back_btn.bind('<Button-1>', lambda e: back_btn.config(relief=tk.SUNKEN))
        back_btn.bind('<ButtonRelease-1>', lambda e: (back_btn.config(relief=tk.RAISED), self.show_welcome()))

        tk.Label(header_content, text="FileGenius - System Health",
                font=('Helvetica', 20, 'bold'), fg='white', bg='#2563EB').pack(side=tk.LEFT, padx=(20, 0))

        # Content (slightly reduced padding to avoid excess whitespace)
        content = tk.Frame(self.root, bg='#F8F9FA', padx=30, pady=20)
        content.pack(fill=tk.BOTH, expand=True)

        if not self.system_monitor:
            tk.Label(content, text=" System monitoring not available",
                    font=('Helvetica', 18, 'bold'), fg='#C62828', bg='#F8F9FA').pack(pady=50)
            tk.Label(content, text="Install psutil: pip install psutil",
                    font=('Helvetica', 14), fg='#666', bg='#F8F9FA').pack()
            return

        # Get system status
        status = self.system_monitor.get_detailed_status()

        # Overall status banner
        health_emoji = "✅" if status['overall_healthy'] else "⚠️"
        health_text = "System Healthy" if status['overall_healthy'] else "Action Needed"
        health_color = "#4CAF50" if status['overall_healthy'] else "#FF9800"

        banner = tk.Frame(content, bg=health_color, pady=20)
        banner.pack(fill=tk.X, pady=(0, 30))

        tk.Label(banner, text=f"{health_emoji} {health_text}",
                font=('Helvetica', 28, 'bold'), fg='white', bg=health_color).pack()

        # Stats cards
        stats_container = tk.Frame(content, bg='#F8F9FA')
        stats_container.pack(fill=tk.BOTH, expand=True)

        # Configure 3 columns
        stats_container.grid_columnconfigure(0, weight=1)
        stats_container.grid_columnconfigure(1, weight=1)
        stats_container.grid_columnconfigure(2, weight=1)

        # Disk Space Card
        self._create_health_card(
            stats_container, 0,
            icon="💽",
            title="Disk Space",
            value=f"{status['disk_free_percent']:.1f}%",
            subtitle=f"{status['disk_free_gb']:.1f} GB free",
            is_healthy=status['disk_healthy'],
            color="#2196F3"
        )

        # CPU Usage Card
        self._create_health_card(
            stats_container, 1,
            icon="💻",
            title="CPU Usage",
            value=f"{status['cpu_percent']:.1f}%",
            subtitle=f"{'Normal' if status['cpu_healthy'] else 'High load'}",
            is_healthy=status['cpu_healthy'],
            color="#9C27B0"
        )

        # Memory Card
        self._create_health_card(
            stats_container, 2,
            icon="💾",
            title="Memory",
            value=f"{status['memory_percent']:.1f}%",
            subtitle=f"{status['memory_available_gb']:.1f} GB available",
            is_healthy=status['memory_available_gb'] > 1,
            color="#FF5722"
        )

        # Action buttons
        action_frame = tk.Frame(content, bg='#F8F9FA')
        action_frame.pack(pady=30)

        ColorButton(action_frame, text=" Find Large Files",
                   command=lambda: (setattr(self, 'current_operation', 'analyze'), self.show_folder_select()),
                   font=('Helvetica', 14, 'bold'), bg='#2563EB', fg='white',
                   padx=25, pady=12).pack(side=tk.LEFT, padx=10)

        ColorButton(action_frame, text=" Refresh",
                   command=self.show_system_health,
                   font=('Helvetica', 14, 'bold'), bg='#6C757D', fg='white',
                   padx=25, pady=12).pack(side=tk.LEFT, padx=10)

    def _create_health_card(self, parent, col, icon, title, value, subtitle, is_healthy, color):
        """Create a health status card"""
        card = tk.Frame(parent, bg='white', relief=tk.RAISED, borderwidth=1)
        card.grid(row=0, column=col, sticky='nsew', padx=15, pady=15)

        # Icon with colored background
        icon_frame = tk.Frame(card, bg=color, pady=20)
        icon_frame.pack(fill=tk.X)

        tk.Label(icon_frame, text=icon, font=('Helvetica', 40),
                bg=color, fg='white').pack()

        # Content
        content = tk.Frame(card, bg='white', pady=20)
        content.pack(fill=tk.BOTH, expand=True)

        tk.Label(content, text=title, font=('Helvetica', 16, 'bold'),
                fg='#666', bg='white').pack()

        tk.Label(content, text=value, font=('Helvetica', 32, 'bold'),
                fg=color, bg='white').pack(pady=5)

        status_icon = "✅" if is_healthy else "⚠️"
        tk.Label(content, text=f"{status_icon} {subtitle}",
                font=('Helvetica', 12), fg='#999', bg='white').pack()

    def show_organize(self):
        """Organize screen with modern design"""
        self.clear_screen()

        # Modern header
        header = tk.Frame(self.root, bg='#2563EB', pady=12)
        header.pack(fill=tk.X)

        # Back button and title on same line
        header_content = tk.Frame(header, bg='#2563EB')
        header_content.pack(fill=tk.X, padx=20)

        back_btn = tk.Label(
            header_content,
            text="← Back",
            font=('Helvetica', 12, 'bold'),
            fg='white',
            bg='#14B8A6',
            relief=tk.RAISED,
            padx=15,
            pady=8,
            borderwidth=2,
            highlightthickness=0,
        )
        back_btn.pack(side=tk.LEFT)
        back_btn.bind('<Button-1>', lambda e: back_btn.config(relief=tk.SUNKEN))
        back_btn.bind('<ButtonRelease-1>', lambda e: (back_btn.config(relief=tk.RAISED), self.show_folder_select()))

        # Logo and title
        try:
            from PIL import Image, ImageTk
            import os, sys
            app_dir = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(app_dir, 'filegenius_1024.png')
            if os.path.exists(icon_path):
                img = Image.open(icon_path)
                img = img.resize((40, 40), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                logo = tk.Label(header_content, image=photo, bg='#2563EB', bd=0)
                logo.image = photo
                logo.pack(side=tk.LEFT, padx=(20, 10))
        except:
            # Fallback to emoji
            logo = tk.Label(header_content, text=" ", font=('Helvetica', 28),
                           bg='#2563EB', fg='#FBBF24')
            logo.pack(side=tk.LEFT, padx=(20, 10))

        tk.Label(header_content, text="FileGenius - Organize by File Type",
                font=('Helvetica', 20, 'bold'), fg='white', bg='#2563EB').pack(side=tk.LEFT)

        # Content with light background - REDUCED PADDING
        content = tk.Frame(self.root, bg='#F8F9FA', padx=20, pady=10)
        content.pack(fill=tk.BOTH, expand=True)

        # Folder info
        tk.Label(content, text=f" {self.current_folder}",
                font=('Helvetica', 13), fg='#000000', bg='#F8F9FA').pack(anchor=tk.W, pady=(0, 8))

        # Preview with minimal padding
        preview_frame = tk.Frame(content, bg='white', relief=tk.SOLID, borderwidth=1, padx=15, pady=10)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Use system default font size (typically 13-15pt)
        self.preview_text = scrolledtext.ScrolledText(
            preview_frame,
            wrap=tk.WORD,
            font=('Helvetica', 14),
            relief=tk.FLAT,
            borderwidth=0,
            bg='#FFFFFF',
            fg='#000000',
            insertbackground='#000000',
            selectbackground='#007AFF',
            selectforeground='#FFFFFF',
            highlightthickness=1,
            highlightbackground='#E5E7EB',
        )
        self.preview_text.configure(
            bg='#FFFFFF',
            background='#FFFFFF',
            fg='#000000',
            selectbackground='#007AFF',
            inactiveselectbackground='#E0E7FF',
        )
        self.preview_text.insert(tk.END, "Preparing preview...\n\n")
        self.preview_text.pack(fill=tk.BOTH, expand=True)

        # Buttons - compact
        btn_frame = tk.Frame(content, bg='#F8F9FA', pady=8)
        btn_frame.pack()

        ColorButton(btn_frame, text=" Refresh", command=self.do_preview,
                   font=('Helvetica', 13, 'bold'), bg='#6C757D', fg='white',
                   padx=20, pady=8).pack(side=tk.LEFT, padx=8)

        # Green button with white text - using ColorButton
        ColorButton(btn_frame, text=" Basic Organize Files as Listed",
                 font=('Helvetica', 14, 'bold'),
                 bg='#34C759', fg='white',
                 padx=25, pady=10,
                 command=self.do_organize).pack(side=tk.LEFT, padx=8)

        # AI-driven organize button
        ColorButton(btn_frame, text=" ✨ Organize by AI",
                   font=('Helvetica', 13, 'bold'), bg='#6C5CE7', fg='white',
                   padx=22, pady=8,
                   command=self._organize_by_ai).pack(side=tk.LEFT, padx=8)

        # AI settings for organize view (same dialog as Analyze) - far right
        ColorButton(btn_frame, text=" ⚙️ AI Settings",
                   font=('Helvetica', 14, 'bold'), bg='#374151', fg='white',
                   padx=22, pady=10,
                   command=self._show_ai_settings_dialog).pack(side=tk.RIGHT, padx=8)

        # Auto preview
        self.do_preview()

    def do_preview(self):
        """Preview organization"""
        # Force white background even in dark mode
        self.preview_text.configure(bg='#FFFFFF', background='#FFFFFF', fg='#000000', insertbackground='#000000')
        self.preview_text.delete('1.0', tk.END)
        self.preview_text.insert(tk.END, "Analyzing...\n\n")
        self.preview_text.update_idletasks()
        self.root.update()

        preview = SimpleOrganizer.preview_organize_by_type(self.current_folder)
        total = sum(len(files) for files in preview.values())
        
        # Get existing folders in the directory
        existing_folders = []
        try:
            for item in os.listdir(self.current_folder):
                item_path = os.path.join(self.current_folder, item)
                if os.path.isdir(item_path) and not item.startswith('.'):
                    existing_folders.append(item)
        except Exception:
            pass

        self.preview_text.delete('1.0', tk.END)

        # Header
        self.preview_text.insert(tk.END, f"Found {total} files to organize\n", 'header')
        self.preview_text.insert(tk.END, "\n")
        
        # Show existing folders first
        if existing_folders:
            self.preview_text.insert(tk.END, "📂 Existing Folders", 'folder_header')
            self.preview_text.insert(tk.END, f" ({len(existing_folders)} folders)\n", 'count')
            for folder in sorted(existing_folders):
                self.preview_text.insert(tk.END, f"  📁 {folder}/\n", 'folder')
            self.preview_text.insert(tk.END, "\n")

        icon_map = {
            'Images': '🖼️',
            'Documents': '📄',
            'Spreadsheets': '📊',
            'Videos': '🎬',
            'Audio': '🎵',
            'Archives': '📚',
            'Code': '💻',
            'Web': '🌐',
            'Config': '⚙️',
            'Other': '📁',
            'No Extension': '📦',
        }

        for category, files in sorted(preview.items()):
            if files:
                icon = icon_map.get(category, '📁')
                # Category name
                self.preview_text.insert(tk.END, f"{icon} {category}", 'category')
                self.preview_text.insert(tk.END, f" ({len(files)} files)\n", 'count')

                # File list
                for f in files[:5]:
                    self.preview_text.insert(tk.END, f"  {f}\n")
                if len(files) > 5:
                    self.preview_text.insert(tk.END, f"  ... and {len(files) - 5} more\n", 'more')
                self.preview_text.insert(tk.END, "\n")

        # Configure tags with readable fonts
        self.preview_text.tag_config('header', font=('Helvetica', 15, 'bold'), foreground='#000')
        self.preview_text.tag_config('folder_header', font=('Helvetica', 14, 'bold'), foreground='#14B8A6')
        self.preview_text.tag_config('folder', font=('Helvetica', 13), foreground='#000000')
        self.preview_text.tag_config('category', font=('Helvetica', 14, 'bold'), foreground='#007AFF')
        self.preview_text.tag_config('count', font=('Helvetica', 13), foreground='#666')
        self.preview_text.tag_config('more', font=('Helvetica', 13, 'italic'), foreground='#999')

        if total > 0:
            self.preview_text.insert(tk.END, "\n Ready to organize!", 'success')
        else:
            self.preview_text.insert(tk.END, "\n No files to organize", 'warning')

        self.preview_text.tag_config('success', font=('Helvetica', 14, 'bold'), foreground='#34C759')
        self.preview_text.tag_config('warning', font=('Helvetica', 14, 'bold'), foreground='#FF9500')

    def do_organize(self):
        """Perform actual organization"""
        if not self._can_perform_cleaning():
            return

        if not messagebox.askyesno(
            "Confirm",
            "Move files into subfolders exactly as listed in the preview?\n\n"
            "You can undo this operation from the AI Organize dialog.",
        ):
            return

        # Clear previous undo log and start fresh
        self._clear_undo_log()
        
        success, message, count = SimpleOrganizer.organize_by_type(
            self.current_folder, 
            move_callback=self._log_file_move
        )
        
        if count > 0:
            message += f"\n\n↩️ You can undo this from AI Settings > Undo"
        
        messagebox.showinfo("Done", message)

        if success:
            self._register_cleaning()
            self.do_preview()

    def _execute_basic_organize(self, suppress_notification: bool = False):
        """Execute basic organize without confirmation (called from AI Accept)."""
        if not self._can_perform_cleaning():
            return

        # Clear previous undo log and start fresh
        self._clear_undo_log()
        
        success, message, count = SimpleOrganizer.organize_by_type(
            self.current_folder, 
            move_callback=self._log_file_move
        )
        
        if count > 0:
            message += f"\n\n↩️ You can undo this from the AI Organize dialog"
        
        if not suppress_notification:
            messagebox.showinfo("Organization Complete", message)

        if success:
            self._register_cleaning()
            self.do_preview()

    def _show_ai_confirm_dialog(self, file_count: int, folder_count: int, folder_summary: str) -> bool:
        """Show custom confirmation dialog with highlighted question. Returns True if user confirms."""
        result = [False]
        
        dialog = tk.Toplevel(self.root)
        dialog.title("AI Organize")
        dialog.geometry("600x500")
        dialog.minsize(560, 420)
        dialog.configure(bg="#FFFFFF")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Header
        header = tk.Frame(dialog, bg="#6C5CE7", pady=12)
        header.pack(fill=tk.X)
        tk.Label(header, text="🤖 AI Organization", font=("Helvetica", 16, "bold"),
                fg="white", bg="#6C5CE7").pack()
        
        # Body with scrollable content
        body = tk.Frame(dialog, bg="#FFFFFF", padx=20, pady=15)
        body.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        
        # Summary
        summary = f"📁 Found {file_count} files to organize"
        if folder_count > 0:
            summary += f" across {folder_count} subfolders"
        
        tk.Label(body, text=summary, font=("Helvetica", 14), fg="#1F2937", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 10))
        
        # Folder summary in scrollable text if present
        if folder_summary:
            folder_frame = tk.Frame(body, bg="#FFFFFF", relief=tk.GROOVE, borderwidth=1)
            folder_frame.pack(fill=tk.BOTH, expand=True, pady=10)
            folder_text = tk.Text(
                folder_frame,
                font=("Helvetica", 12),
                height=6,
                wrap=tk.WORD,
                bg="#FFFFFF",
                fg="#111111",
                relief=tk.FLAT,
                insertbackground="#111111",
                highlightthickness=0,
            )
            folder_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            folder_text.insert("1.0", folder_summary)
            folder_text.config(state="disabled", cursor="arrow")
        
        # Highlighted question
        question_badge = tk.Label(
            body,
            text="Ready to proceed with AI organization?",
            font=("Helvetica", 13, "bold"),
            fg="#065F46",
            bg="#DCFCE7",
            padx=12,
            pady=6,
            borderwidth=1,
            relief=tk.SOLID,
        )
        question_badge.pack(anchor=tk.W, pady=(15, 8))
        
        # Buttons frame at bottom of dialog (outside body)
        btn_frame = tk.Frame(dialog, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X, padx=20, pady=15)
        
        def on_yes():
            result[0] = True
            dialog.destroy()
        
        def on_no():
            dialog.destroy()
        
        button_kwargs = {
            "bg": "white",
            "fg": "#1F2937",
            "activebackground": "#E5E7EB",
            "activeforeground": "#111111",
            "borderwidth": 1,
            "relief": tk.RAISED,
            "highlightthickness": 0,
            "padx": 20,
            "pady": 8,
        }
        tk.Button(
            btn_frame,
            text="Yes, Proceed",
            command=on_yes,
            font=("Helvetica", 13, "bold"),
            **button_kwargs,
        ).pack(side=tk.LEFT, padx=10)
        tk.Button(
            btn_frame,
            text="Cancel",
            command=on_no,
            font=("Helvetica", 13),
            **button_kwargs,
        ).pack(side=tk.LEFT, padx=10)
        
        dialog.wait_window()
        return result[0]

    def _organize_by_ai(self):
        """Use AI to suggest how to organize files in the selected folder."""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please select a folder first.")
            return

        # Get AI provider and key
        provider, api_key = self._get_ai_provider_and_key()
        if not api_key:
            # Show which paths were checked for debugging
            env_paths = self._get_env_file_candidates()
            paths_str = "\n".join(f"  • {p}" for p in env_paths[:5])
            messagebox.showerror(
                "AI Organize",
                f"No API key found for {provider}.\n\n"
                f"Checked .env files:\n{paths_str}\n\n"
                "Please set up your AI provider in Settings or add CEREBRAS_API_KEY to your .env file."
            )
            return

        # Get file list recursively including subfolders
        try:
            files_info = []
            folders_found = set()
            
            for root, dirs, files in os.walk(self.current_folder):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                # Get relative path for this folder
                rel_root = os.path.relpath(root, self.current_folder)
                if rel_root != '.':
                    folders_found.add(rel_root)
                
                for filename in files:
                    if filename.startswith('.'):
                        continue
                    filepath = os.path.join(root, filename)
                    try:
                        ext = os.path.splitext(filename)[1].lower()
                        size = os.path.getsize(filepath)
                        rel_path = os.path.relpath(filepath, self.current_folder)
                        files_info.append(f"- {rel_path} ({ext or 'no ext'}, {FileSizeAnalyzer.format_size(size)})")
                    except (OSError, PermissionError):
                        continue
            
            if not files_info:
                messagebox.showinfo("AI Organize", "No files found in this folder.")
                return
            
            # Build folder summary
            folder_summary = ""
            if folders_found:
                folder_list = sorted(folders_found)[:20]
                folder_summary = f"\nExisting subfolders ({len(folders_found)} total):\n"
                folder_summary += "\n".join(f"  📁 {f}" for f in folder_list)
                if len(folders_found) > 20:
                    folder_summary += f"\n  ... and {len(folders_found) - 20} more folders"
            
            # Limit files for API efficiency
            files_sample = files_info[:50]
            if len(files_info) > 50:
                files_sample.append(f"... and {len(files_info) - 50} more files")
            
            files_block = "\n".join(files_sample)
            
            # Show custom confirmation dialog with highlighted question
            if not self._show_ai_confirm_dialog(len(files_info), len(folders_found), folder_summary):
                return
            
            # Extra warning for large operations
            if len(files_info) > 50:
                warning_msg = (
                    f"⚠️ IMPORTANT: You're about to organize {len(files_info)} files.\n\n"
                    "This is a significant operation that will move many files.\n"
                    "While FileGenius keeps an undo log, please review the AI suggestions carefully before applying.\n\n"
                    "Are you sure you want to continue?"
                )
                if not messagebox.askyesno("Large Operation Warning", warning_msg, icon='warning'):
                    return
                
        except Exception as e:
            messagebox.showerror("AI Organize", f"Could not read folder:\n{e}")
            return

        # Build prompt
        prompt = (
            "You are a file organization expert. Analyze these files and suggest how to organize them.\n\n"
            f"Folder: {self.current_folder}\n"
            f"{folder_summary}\n"
            f"Files ({len(files_info)} total):\n{files_block}\n\n"
            "Suggest:\n"
            "1. Which category folders to create or use (e.g., Images, Documents, Code, etc.)\n"
            "2. Which files should go in each folder\n"
            "3. Any files that should NOT be moved (config files, important system files)\n"
            "4. If subfolders already exist, suggest if files should be moved into them\n\n"
            "Be concise. Use bullet points. Output plain text only."
        )

        # Show loading dialog
        loading = tk.Toplevel(self.root)
        loading.title("AI Organizing...")
        loading.geometry("300x100")
        loading.transient(self.root)
        tk.Label(loading, text="🤖 AI is analyzing your files...", font=("Helvetica", 14)).pack(expand=True)
        loading.update()

        def worker():
            try:
                suggestions = self._call_ai_api(provider, api_key, prompt)
                self.root.after(0, lambda: self._apply_ai_strategy(suggestions, loading))
            except Exception as e:
                import traceback
                error_msg = f"Provider: {provider}\nError: {str(e)}\n\nDetails:\n{traceback.format_exc()[:500]}"
                self.root.after(0, lambda: (loading.destroy(), messagebox.showerror("AI Organize Error", error_msg)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_ai_strategy(self, suggestions: str, loading_dialog):
        """Apply AI organization based on actual AI suggestions."""
        loading_dialog.destroy()

        # Store last AI summary for debugging/support
        self.last_ai_strategy = suggestions

        # Apply without extra popups or navigation
        # Show quick working indicator to avoid bounce-back or spinner confusion
        working = tk.Toplevel(self.root)
        working.title("Applying…")
        working.geometry("260x120")
        working.configure(bg="#FFFFFF")
        working.transient(self.root)
        tk.Label(working, text="Working on your files…", font=("Helvetica", 13), bg="#FFFFFF", fg="#111111").pack(expand=True, pady=20)
        working.update()

        self._execute_ai_organize(suggestions, suppress_notification=True)
        working.destroy()

    def _execute_ai_organize(self, suggestions: str, suppress_notification: bool = False):
        """Execute AI-suggested organization by parsing suggestions and moving files."""
        if not self._can_perform_cleaning():
            return

        # Clear previous undo log and start fresh
        self._clear_undo_log()

        files_moved = 0
        files_skipped = 0

        try:
            # Parse AI suggestions to extract folder assignments
            # The AI provides text like:
            # - Move file.jpg to Images
            # - Move document.pdf to Documents
            # We need to extract these and execute them

            folder_assignments = self._parse_ai_suggestions(suggestions)

            if not folder_assignments:
                # If parsing fails, fall back to basic organize
                success, message, count = SimpleOrganizer.organize_by_type(
                    self.current_folder,
                    move_callback=self._log_file_move
                )
                if count > 0:
                    message += f"\n\n↩️ You can undo this from the AI Organize dialog"
                if not suppress_notification:
                    messagebox.showinfo("Organization Complete", message)
                if success:
                    self._register_cleaning()
                    self.do_preview()
                return

            # Execute the AI suggestions
            for filename, target_folder in folder_assignments.items():
                filepath = os.path.join(self.current_folder, filename)

                # Skip if file doesn't exist
                if not os.path.exists(filepath):
                    files_skipped += 1
                    continue

                # Skip directories
                if os.path.isdir(filepath):
                    continue

                # Check safety
                try:
                    safety_level, _, _, _ = SafetyChecker.assess_file_safety(filepath)
                    if safety_level == 'DANGEROUS':
                        files_skipped += 1
                        continue
                except Exception:
                    files_skipped += 1
                    continue

                # Create target folder and move file
                try:
                    target_path = os.path.join(self.current_folder, target_folder)
                    os.makedirs(target_path, exist_ok=True)

                    dest = os.path.join(target_path, filename)
                    shutil.move(filepath, dest)
                    files_moved += 1

                    # Log move for undo
                    self._log_file_move(filepath, dest)
                except Exception as e:
                    logging.warning(f"Failed to move {filename}: {e}")
                    files_skipped += 1

            # Show result
            message = f"✅ AI organization complete!\n\n"
            if files_moved > 0:
                message += f"📁 Moved {files_moved} files"
            if files_skipped > 0:
                message += f"\n⊘ Skipped {files_skipped} files"

            if files_moved > 0:
                message += f"\n\n↩️ You can undo this from the AI Organize dialog"

            if not suppress_notification:
                messagebox.showinfo("Organization Complete", message)

            if files_moved > 0:
                self._register_cleaning()
                self.do_preview()

        except Exception as e:
            logging.error(f"Error during AI organization: {e}")
            if not suppress_notification:
                messagebox.showerror("Organization Error", f"Error: {e}")

    def _parse_ai_suggestions(self, suggestions: str) -> dict:
        """Parse AI suggestions text and extract folder -> files mappings.

        Returns a dict like: {'file.jpg': 'Images', 'doc.pdf': 'Documents', ...}
        """
        assignments = {}

        try:
            # Get all files in the current folder
            all_files = set()
            for item in os.listdir(self.current_folder):
                filepath = os.path.join(self.current_folder, item)
                if os.path.isfile(filepath):
                    all_files.add(item)

            if not all_files:
                return assignments

            # Parse suggestions line by line
            lines = suggestions.split('\n')
            current_folder = None

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Look for folder headers (e.g., "Images:", "Documents:", etc.)
                if line.endswith(':') and not line.startswith('-'):
                    potential_folder = line.rstrip(':').strip()
                    # Common folder names that AI suggests
                    if potential_folder in ['Images', 'Documents', 'Videos', 'Audio',
                                           'Code', 'Archives', 'Config', 'Web', 'Spreadsheets',
                                           'Projects', 'Work', 'Personal', 'Important',
                                           'Old', 'Temporary', 'Misc', 'Other']:
                        current_folder = potential_folder
                        continue

                # Look for file mentions in format "- filename" or "- filename.ext"
                if current_folder and (line.startswith('-') or line.startswith('•')):
                    # Extract filename
                    filename = line.lstrip('-•').strip()

                    # Clean up common prefixes
                    if filename.startswith('`') or filename.startswith("'"):
                        filename = filename[1:]
                    if filename.endswith('`') or filename.endswith("'"):
                        filename = filename[:-1]

                    # Check if this file exists in the folder
                    if filename in all_files:
                        assignments[filename] = current_folder
                    else:
                        # Try fuzzy matching for partial filenames
                        for actual_file in all_files:
                            if filename.lower() in actual_file.lower() or actual_file.lower() in filename.lower():
                                assignments[actual_file] = current_folder
                                break

            return assignments

        except Exception as e:
            logging.error(f"Error parsing AI suggestions: {e}")
            return {}

    def _log_file_move(self, original_path: str, new_path: str):
        """Log a file move for potential undo."""
        self.undo_log.append((original_path, new_path))

    def _clear_undo_log(self):
        """Clear the undo log (called before new organize operation)."""
        self.undo_log = []

    def _undo_last_organize(self, parent_dialog=None):
        """Undo the last organize operation by moving files back."""
        if not self.undo_log:
            messagebox.showinfo("Undo", "Nothing to undo.")
            return

        count = len(self.undo_log)
        if not messagebox.askyesno(
            "Undo Organize",
            f"This will move {count} files back to their original locations.\n\nProceed with undo?"
        ):
            return

        success = 0
        failed = 0
        for original_path, new_path in reversed(self.undo_log):
            try:
                if os.path.exists(new_path):
                    # Ensure original directory exists
                    os.makedirs(os.path.dirname(original_path), exist_ok=True)
                    shutil.move(new_path, original_path)
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                logging.warning(f"Failed to undo move {new_path} -> {original_path}: {e}")
                failed += 1

        self.undo_log = []
        
        msg = f"Undo complete!\n\n✅ {success} files restored"
        if failed > 0:
            msg += f"\n❌ {failed} files could not be restored"
        
        messagebox.showinfo("Undo Complete", msg)
        
        if parent_dialog:
            parent_dialog.destroy()

    def show_analyze(self):
        """Size analysis screen with modern design"""
        self.clear_screen()

        # Modern header
        header = tk.Frame(self.root, bg='#2563EB', pady=12)
        header.pack(fill=tk.X)

        # Back button and title on same line
        header_content = tk.Frame(header, bg='#2563EB')
        header_content.pack(fill=tk.X, padx=20)

        back_btn = tk.Label(
            header_content,
            text="← Back",
            font=('Helvetica', 12, 'bold'),
            fg='white',
            bg='#14B8A6',
            relief=tk.RAISED,
            padx=15,
            pady=8,
            borderwidth=2,
            highlightthickness=0,
        )
        back_btn.pack(side=tk.LEFT)
        back_btn.bind('<Button-1>', lambda e: back_btn.config(relief=tk.SUNKEN))
        back_btn.bind('<ButtonRelease-1>', lambda e: (back_btn.config(relief=tk.RAISED), self.show_folder_select()))

        # Logo and title
        try:
            from PIL import Image, ImageTk
            import os, sys
            app_dir = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(app_dir, 'filegenius_1024.png')
            if os.path.exists(icon_path):
                img = Image.open(icon_path)
                img = img.resize((40, 40), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                logo = tk.Label(header_content, image=photo, bg='#2563EB', bd=0)
                logo.image = photo
                logo.pack(side=tk.LEFT, padx=(20, 10))
        except:
            # Fallback to emoji
            logo = tk.Label(header_content, text=" ", font=('Helvetica', 28),
                           bg='#2563EB', fg='#FBBF24')
            logo.pack(side=tk.LEFT, padx=(20, 10))

        tk.Label(header_content, text="FileGenius - Find Large Files",
                font=('Helvetica', 20, 'bold'), fg='white', bg='#2563EB').pack(side=tk.LEFT)

        # Content with light background - REDUCED PADDING
        content = tk.Frame(self.root, bg='#F8F9FA', padx=20, pady=10)
        content.pack(fill=tk.BOTH, expand=True)

        # Folder info
        tk.Label(content, text=f" {self.current_folder}",
                font=('Helvetica', 13), fg='#000000', bg='#F8F9FA').pack(anchor=tk.W, pady=(0, 8))

        # Stats area - SEPARATE ROW for better visibility
        stats_frame = tk.Frame(content, bg='#E3F2FD', relief=tk.SOLID, borderwidth=2, pady=10)
        stats_frame.pack(fill=tk.X, pady=(0, 8))

        # Create container for left and right aligned text
        stats_container = tk.Frame(stats_frame, bg='#E3F2FD')
        stats_container.pack(fill=tk.X, padx=10)

        # Left-aligned scanning status
        self.stats_label = tk.Label(stats_container, text="Ready to scan", font=('Helvetica', 15, 'bold'),
                                    bg='#E3F2FD', fg='#2C3E50', anchor='w')
        self.stats_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Legend for safety colors
        legend_frame = tk.Frame(content, bg='#F8F9FA', pady=5)
        legend_frame.pack(fill=tk.X)

        tk.Label(legend_frame, text="Legend:", font=('Helvetica', 12, 'bold'),
                bg='#F8F9FA', fg='#666').pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(legend_frame, text="🟢 SAFE (green)", font=('Helvetica', 12),
                bg='#F8F9FA', fg='#2E7D32').pack(side=tk.LEFT, padx=5)
        tk.Label(legend_frame, text="🟠 CAUTION (orange)", font=('Helvetica', 12),
                bg='#F8F9FA', fg='#F57C00').pack(side=tk.LEFT, padx=5)
        tk.Label(legend_frame, text="🔴 DANGEROUS (red)", font=('Helvetica', 12),
                bg='#F8F9FA', fg='#C62828').pack(side=tk.LEFT, padx=5)

        # Size filter controls
        filter_frame = tk.Frame(content, bg='#F8F9FA', pady=5)
        filter_frame.pack(fill=tk.X)

        # Wait for all files checkbox
        wait_frame = tk.Frame(content, bg='#FFF3CD', relief=tk.SOLID, borderwidth=1, pady=8)
        wait_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.wait_for_all_var = tk.BooleanVar(value=False)
        wait_checkbox = tk.Checkbutton(
            wait_frame,
            text="Wait for all files to be scanned?",
            variable=self.wait_for_all_var,
            font=('Helvetica', 12, 'bold'),
            bg='#FFF3CD',
            fg='#856404',
            selectcolor='#FFF3CD',
            activebackground='#FFF3CD',
            activeforeground='#856404'
        )
        wait_checkbox.pack(side=tk.LEFT, padx=(10, 5))
        
        tk.Label(
            wait_frame,
            text="Warning: This may take much longer on large directories with many files.",
            font=('Helvetica', 11),
            bg='#FFF3CD',
            fg='#856404'
        ).pack(side=tk.LEFT, padx=5)

        # Incomplete scan warning (hidden initially)
        self.incomplete_warning = tk.Label(
            content,
            text="⚠️ Scanning was unable to access all files. Some files may be missing from results.",
            font=('Helvetica', 12, 'bold'),
            bg='#FFEBEE',
            fg='#C62828',
            relief=tk.SOLID,
            borderwidth=1,
            padx=10,
            pady=8,
            wraplength=900,
            justify=tk.LEFT
        )
        self.incomplete_warning.pack(fill=tk.X, pady=(0, 5))
        self.incomplete_warning.pack_forget()  # Hidden until needed

        tk.Label(filter_frame, text="Minimum file size:", font=('Helvetica', 12, 'bold'),
                bg='#F8F9FA', fg='#2C3E50').pack(side=tk.LEFT, padx=(0, 8))

        def _update_size_filter_label(value):
            try:
                kb = int(float(value))
            except Exception:
                kb = 10
            if kb < 1024:
                text = f"≥ {kb} KB"
            else:
                mb = kb / 1024.0
                text = f"≥ {mb:.1f} MB"
            self.size_filter_value_label.config(text=text)

        self.size_filter_scale = tk.Scale(
            filter_frame,
            from_=10,
            to=102400,
            orient=tk.HORIZONTAL,
            length=220,
            resolution=10,
            showvalue=False,
            bg='#F8F9FA',
            highlightthickness=0,
            troughcolor='#E5E7EB',
        )
        self.size_filter_scale.set(10)
        self.size_filter_scale.config(command=_update_size_filter_label)
        self.size_filter_scale.pack(side=tk.LEFT, padx=4)

        self.size_filter_value_label = tk.Label(
            filter_frame,
            text="≥ 10 KB",
            font=('Helvetica', 12),
            bg='#F8F9FA',
            fg='#2C3E50',
        )
        self.size_filter_value_label.pack(side=tk.LEFT, padx=4)

        ColorButton(
            filter_frame,
            text=" Apply Size Filter",
            command=self.apply_size_filter,
            font=('Helvetica', 12, 'bold'),
            bg='#007AFF',
            fg='white',
            padx=14,
            pady=6,
        ).pack(side=tk.LEFT, padx=8)

        # Recommendation display area
        self.recommendation_label = tk.Label(content, text="", font=('Helvetica', 12),
                                           bg='#FFF3CD', fg='#856404', relief=tk.SOLID,
                                           borderwidth=1, padx=10, pady=8, wraplength=900,
                                           justify=tk.LEFT)
        self.recommendation_label.pack(fill=tk.X, pady=(5, 5))
        self.recommendation_label.pack_forget()  # Hidden until file selected

        # Results frame - white box with minimal padding
        results_container = tk.Frame(content, bg='white', relief=tk.SOLID, borderwidth=1, padx=8, pady=8)
        results_container.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        results_frame = tk.Frame(results_container, bg='white')
        results_frame.pack(fill=tk.BOTH, expand=True)

        # Configure Treeview style - compact rows with visible text
        style = ttk.Style()
        style.configure('Large.Treeview',
                       rowheight=26,
                       font=('Helvetica', 12),
                       foreground='black',  # Ensure text is always visible
                       background='white')
        style.configure('Large.Treeview.Heading',
                       font=('Helvetica', 13, 'bold'),
                       foreground='black',
                       background='#E3F2FD')

        # Create Treeview for file list - no height limit, let it expand
        columns = ('Size', 'Type', 'Filename', 'Path')
        self.file_tree = ttk.Treeview(results_frame, columns=columns, show='tree headings',
                                      style='Large.Treeview')

        # Configure columns with proper widths
        self.file_tree.column('#0', width=32, minwidth=32, stretch=False)  # Checkboxes
        self.file_tree.column('Size', width=120, minwidth=100, stretch=False)
        self.file_tree.column('Type', width=70, minwidth=60, stretch=False)
        self.file_tree.column('Filename', width=320, minwidth=250, stretch=True)
        self.file_tree.column('Path', width=430, minwidth=250, stretch=True)

        # Configure headings
        self.file_tree.heading('#0', text='Select')
        self.file_tree.heading('Size', text='Size')
        self.file_tree.heading('Type', text='Type')
        self.file_tree.heading('Filename', text='Filename')
        self.file_tree.heading('Path', text='Location')

        # Scrollbars
        vsb = ttk.Scrollbar(results_frame, orient="vertical", command=self.file_tree.yview)
        hsb = ttk.Scrollbar(results_frame, orient="horizontal", command=self.file_tree.xview)
        self.file_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Grid layout
        self.file_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        results_frame.grid_rowconfigure(0, weight=1)
        results_frame.grid_columnconfigure(0, weight=1)

        # Select All toolbar - add above the tree
        select_toolbar = tk.Frame(content, bg='#F0F0F0', pady=5, padx=10)
        select_toolbar.pack(fill=tk.X, before=results_frame)

        tk.Label(select_toolbar, text="Selection:", font=('Helvetica', 11, 'bold'),
                bg='#F0F0F0', fg='#2C3E50').pack(side=tk.LEFT, padx=(0, 10))

        ColorButton(select_toolbar, text="✅ Select All",
                   font=('Helvetica', 11, 'bold'),
                   bg='#34C759', fg='white',
                   padx=12, pady=6,
                   command=self.select_all_files).pack(side=tk.LEFT, padx=3)

        ColorButton(select_toolbar, text="◻️  Deselect All",
                   font=('Helvetica', 11, 'bold'),
                   bg='#A9A9A9', fg='white',
                   padx=12, pady=6,
                   command=self.deselect_all_files).pack(side=tk.LEFT, padx=3)

        self.selection_info_label = tk.Label(select_toolbar, text="0 selected",
                                            font=('Helvetica', 11),
                                            bg='#F0F0F0', fg='#666')
        self.selection_info_label.pack(side=tk.LEFT, padx=20)

        # Track selection state for range select
        self.last_selected_item = None

        # Actions frame - compact layout
        actions_frame = tk.Frame(content, bg='#F8F9FA', pady=8)
        actions_frame.pack(fill=tk.X)

        tk.Label(actions_frame, text="Move to:",
                font=('Helvetica', 12, 'bold'), bg='#F8F9FA', fg='#2C3E50').pack(side=tk.LEFT, padx=(0, 8))

        self.move_dest_var = tk.StringVar()
        self.move_entry = tk.Entry(
            actions_frame,
            textvariable=self.move_dest_var,
            width=35,
            font=('Helvetica', 12),
            relief=tk.SOLID,
            borderwidth=1,
            bg='white',
            fg='#111111',
            insertbackground='#111111',
            highlightthickness=1,
            highlightbackground='#D1D5DB',
        )
        self.move_entry.pack(side=tk.LEFT, padx=5)

        # Store browse button reference for flashing effect
        self.browse_folder_btn = ColorButton(actions_frame, text="Browse...", command=self.browse_move_dest,
                   font=('Helvetica', 12, 'bold'), bg='#FFA500', fg='white',
                   padx=15, pady=8)
        self.browse_folder_btn.pack(side=tk.LEFT, padx=5)

        # Stop scanning button (hidden initially)
        self.stop_scan_btn = ColorButton(actions_frame, text=" Stop Scanning",
                 font=('Helvetica', 13, 'bold'),
                 bg='#FF6B6B', fg='white',
                 padx=20, pady=8,
                 command=self.stop_scanning)
        self.stop_scan_btn.pack(side=tk.LEFT, padx=5)
        self.stop_scan_btn.config(state=tk.DISABLED)  # Disabled until scanning starts

        # Buttons on same row as move destination
        ColorButton(actions_frame, text=" Move to Trash",
                 font=('Helvetica', 13, 'bold'),
                 bg='#FF3B30', fg='white',
                 padx=20, pady=8,
                 command=self.move_to_trash_selected_files).pack(side=tk.LEFT, padx=8)

        ColorButton(actions_frame, text=" Move",
                 font=('Helvetica', 13, 'bold'),
                 bg='#007AFF', fg='white',
                 padx=20, pady=8,
                 command=self.move_selected_files).pack(side=tk.LEFT, padx=5)

        # AI cleanup button
        ColorButton(actions_frame, text=" ✨ AI Suggested Cleanup",
                 font=('Helvetica', 13, 'bold'),
                 bg='#6C5CE7', fg='white',
                 padx=20, pady=8,
                 command=self.ai_suggest_cleanup).pack(side=tk.LEFT, padx=5)

        # AI settings button to let user enter their own OpenAI API key (far right)
        ColorButton(actions_frame, text=" ⚙️ AI Settings",
                 font=('Helvetica', 14, 'bold'),
                 bg='#374151', fg='white',
                 padx=22, pady=10,
                 command=self._show_ai_settings_dialog).pack(side=tk.RIGHT, padx=5)

        self.file_data = {}  # Store file info by tree item id

        # Auto-analyze
        self.root.after(100, self.do_analyze)

    def get_directory_size(self, path):
        """Calculate total size of a directory in bytes"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    # Skip if it is symbolic link
                    if not os.path.islink(fp):
                        try:
                            total_size += os.path.getsize(fp)
                        except (OSError, PermissionError):
                            continue
        except Exception as e:
            print(f"Error calculating directory size: {e}")
        return total_size

    def do_analyze(self):
        """Analyze sizes and display in Treeview - with threading to prevent UI freeze"""
        # Clear tree AND file data
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        self.file_data = {}  # Clear cached file data

        # Hide recommendation box during scan
        if hasattr(self, 'recommendation_label'):
            self.recommendation_label.pack_forget()

        # Show animated scanning indicator IMMEDIATELY and CLEAR previous stats
        self.scanning = True
        self.scan_stopped = False
        self.scan_incomplete = False
        self.scan_dots = 0
        self.files_scanned = 0
        self.scan_start_time = None  # When scan started
        
        # Show stop button and hide incomplete warning
        self.stop_scan_btn.pack(side=tk.LEFT, padx=5)
        self.stop_scan_btn.config(state=tk.NORMAL, text="⏹ Stop Scanning")
        self.incomplete_warning.pack_forget()

        # Make stats label VERY visible before starting - bright background
        self.stats_label.config(
            text=" SCANNING... PLEASE WAIT...",
            fg='#FFFFFF',
            bg='#007AFF',
            font=('Helvetica', 17, 'bold')
        )

        # Force multiple updates to ensure visibility
        self.root.update_idletasks()
        self.root.update()

        # Initialize progress tracking
        self.bytes_processed = 0
        self.last_update_time = time.time()
        self.last_bytes_processed = 0
        
        # Estimate total size in a separate thread to avoid UI freeze
        def estimate_total_size():
            try:
                self.estimated_total_size = self.get_directory_size(self.current_folder)
                print(f"Estimated total size: {self.estimated_total_size / (1024*1024):.2f} MB")
            except Exception as e:
                print(f"Error estimating directory size: {e}")
                self.estimated_total_size = 0
        
        # Start size estimation in a separate thread
        threading.Thread(target=estimate_total_size, daemon=True).start()
        
        # Start animation
        self.update_scanning_indicator()

        # Run analysis in background thread - with small delay to ensure UI updates
        def analyze_thread():
            try:
                # Progress callback to update scan progress
                def progress_update(files_count, total_size=0):
                    self.files_scanned = files_count
                    # Check if scan should stop
                    if self.scan_stopped:
                        return False  # Signal to stop scanning
                    return True  # Continue scanning

                # Only exclude iCloud if user DIDN'T explicitly select an iCloud folder
                is_icloud_folder = 'iCloud' in self.current_folder or 'Mobile Documents' in self.current_folder
                results = FileSizeAnalyzer.analyze_folder(
                    self.current_folder,
                    exclude_icloud=not is_icloud_folder,  # If they selected iCloud, don't exclude it
                    progress_callback=progress_update,
                    wait_for_all=self.wait_for_all_var.get()
                )

                # Check if scan was stopped or incomplete
                if self.scan_stopped:
                    self.scan_incomplete = True
                elif hasattr(results, 'incomplete') and results.get('incomplete', False):
                    self.scan_incomplete = True

                # Update UI from main thread
                self.root.after(0, lambda: self.display_results(results))
            except Exception as e:
                self.root.after(0, lambda: self.stats_label.config(text=f"Error: {e}"))
                self.scanning = False

        # Small delay before starting thread to let UI update show
        self.root.after(50, lambda: threading.Thread(target=analyze_thread, daemon=True).start())

    def update_scanning_indicator(self):
        """Animate scanning indicator with scan rate and time remaining"""
        if self.scanning and hasattr(self, 'stats_label') and self.stats_label.winfo_exists():
            dots = "." * (self.scan_dots % 4)

            # Update left side - scanning text with file count
            if self.files_scanned > 0:
                status_text = f" SCANNING{dots} ({self.files_scanned:,} files)"
                if self.scan_stopped:
                    status_text = f" STOPPING{dots} ({self.files_scanned:,} files)"
                self.stats_label.config(
                    text=status_text,
                    fg='#FFFFFF',
                    bg='#007AFF',
                    font=('Helvetica', 17, 'bold')
                )
            else:
                self.stats_label.config(
                    text=f" SCANNING{dots} PLEASE WAIT{dots}",
                    fg='#FFFFFF',
                    bg='#007AFF',
                    font=('Helvetica', 17, 'bold')
                )

            self.scan_dots += 1
            self.root.update_idletasks()  # Force UI refresh during animation
            self.root.after(300, self.update_scanning_indicator)

    def display_results(self, results):
        """Display analysis results in UI - called from main thread"""
        try:
            self.scanning = False
            # Disable stop button when scan is complete
            self.stop_scan_btn.config(state=tk.DISABLED, text="⏹ Stop Scanning")
            
            # Show incomplete warning if needed
            if self.scan_incomplete:
                self.incomplete_warning.pack(fill=tk.X, pady=(0, 5))
            
            # Keep a copy of results so we can re-filter without re-scanning
            self.analyze_results = results

            # Update stats to reflect completion
            total_size = FileSizeAnalyzer.format_size(results['total_size'])
            scan_status = ""
            if self.scan_stopped:
                scan_status = " (Stopped)"
            elif self.scan_incomplete:
                scan_status = " (Incomplete)"
                
            self.stats_label.config(
                text=f"{results['total_files']} files • {total_size} total{scan_status}",
                fg='#2C3E50',  # Dark grey
                bg='#E3F2FD',  # Light blue background (match container)
                font=('Helvetica', 15, 'bold')
            )

            # Check if we have files to display
            if not results['largest_files']:
                self.stats_label.config(text="No files found in this folder")
                return

            # Configure tags for color coding safety levels
            # Use explicit white background; tag foreground colors carry safety meaning
            self.file_tree.tag_configure('SAFE', foreground='#2E7D32', background='white')      # Green text
            self.file_tree.tag_configure('CAUTION', foreground='#F57C00', background='white')   # Orange text
            self.file_tree.tag_configure('DANGEROUS', foreground='#C62828', background='white') # Red text

            # Apply style but do NOT override tag colors for normal rows
            style = ttk.Style()
            style.map(
                'Large.Treeview',
                foreground=[('selected', 'white')],
                background=[('selected', '#2563EB'), ('!selected', 'white')],
            )

            # Unbind previous click handlers to prevent duplicate events
            self.file_tree.unbind('<Button-1>')
            self.file_tree.unbind('<Button-2>')
            self.file_tree.unbind('<Control-Button-1>')

            # Populate tree - show ALL files with safety and age indicators

            # Simple file-type emoji map based on extension
            type_icon_map = {
                '.jpg': '🖼️', '.jpeg': '🖼️', '.png': '🖼️', '.gif': '🖼️', '.webp': '🖼️',
                '.heic': '🖼️', '.heif': '🖼️',
                '.pdf': '📄', '.doc': '📄', '.docx': '📄', '.txt': '📄', '.rtf': '📄',
                '.xls': '📊', '.xlsx': '📊', '.csv': '📊',
                '.mp4': '🎬', '.mov': '🎬', '.m4v': '🎬', '.avi': '🎬', '.mkv': '🎬',
                '.mp3': '🎵', '.wav': '🎵', '.aiff': '🎵', '.flac': '🎵',
                '.zip': '📚', '.rar': '📚', '.7z': '📚', '.tar': '📚', '.gz': '📚',
                '.py': '💻', '.js': '💻', '.ts': '💻', '.html': '🌐', '.css': '💻', '.json': '💻',
            }

            for i, file_info in enumerate(results['largest_files']):
                filepath = file_info['path']
                filename = file_info['name']
                size_str = file_info['size_formatted']
                safety_level = file_info['safety_level']
                warning = file_info['warning']
                recommendation = file_info['recommendation']
                age_status = file_info['age_status']

                # Build display name with safety indicator
                display_name = f"{warning} {filename}"

                # Derive file-type emoji for the dedicated Type column
                ext = os.path.splitext(filename)[1].lower()
                type_icon = type_icon_map.get(ext, '📁')

                # Insert into tree with larger checkbox emoji for better visibility
                # Column order: Size, Type, Filename, Path
                item_id = self.file_tree.insert(
                    '',
                    'end',
                    text='◻️',
                    values=(size_str, type_icon, display_name, filepath),
                    tags=(safety_level,),
                )

                # Store file data including safety and age info
                self.file_data[item_id] = {
                    'path': filepath,
                    'size': file_info['size'],
                    'safety_level': safety_level,
                    'warning': warning,
                    'recommendation': recommendation,
                    'age_status': age_status,
                    'is_safe': file_info['is_safe'],
                    'checked': False,
                }

            # Bind click events ONCE after the loop completes
            self.file_tree.bind('<Button-1>', self.on_tree_click)
            self.file_tree.bind('<Button-2>', self.show_context_menu)  # Right-click on Mac
            self.file_tree.bind('<Control-Button-1>', self.show_context_menu)  # Ctrl+click on Mac

        except Exception as e:
            print(f"ERROR in display_results: {e}")
            import traceback
            traceback.print_exc()
            self.scanning = False
            self.stats_label.config(text=f"Error displaying results: {e}")

    def apply_size_filter(self):
        """Filter analyzed files by minimum size using the slider."""
        if not hasattr(self, 'analyze_results') or not self.analyze_results:
            messagebox.showinfo(
                "Filter Files",
                "Run 'Find Large Files' first, then adjust the size filter.",
            )
            return

        try:
            min_kb = float(self.size_filter_scale.get())
        except Exception:
            min_kb = 10.0

        min_bytes = int(min_kb * 1024)

        # Clear existing rows
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        self.file_data = {}

        type_icon_map = {
            '.jpg': '🖼️', '.jpeg': '🖼️', '.png': '🖼️', '.gif': '🖼️', '.webp': '🖼️',
            '.heic': '🖼️', '.heif': '🖼️',
            '.pdf': '📄', '.doc': '📄', '.docx': '📄', '.txt': '📄', '.rtf': '📄',
            '.xls': '📊', '.xlsx': '📊', '.csv': '📊',
            '.mp4': '🎬', '.mov': '🎬', '.m4v': '🎬', '.avi': '🎬', '.mkv': '🎬',
            '.mp3': '🎵', '.wav': '🎵', '.aiff': '🎵', '.flac': '🎵',
            '.zip': '📚', '.rar': '📚', '.7z': '📚', '.tar': '📚', '.gz': '📚',
            '.py': '💻', '.js': '💻', '.ts': '💻', '.html': '🌐', '.css': '💻', '.json': '💻',
        }

        filtered_count = 0
        filtered_bytes = 0

        for file_info in self.analyze_results['largest_files']:
            size = file_info['size']
            if size < min_bytes:
                continue

            filepath = file_info['path']
            filename = file_info['name']
            size_str = file_info['size_formatted']
            safety_level = file_info['safety_level']
            warning = file_info['warning']
            recommendation = file_info['recommendation']
            age_status = file_info['age_status']

            display_name = f"{warning} {filename}"

            ext = os.path.splitext(filename)[1].lower()
            type_icon = type_icon_map.get(ext, '📁')

            item_id = self.file_tree.insert(
                '',
                'end',
                text='◻️',
                values=(size_str, type_icon, display_name, filepath),
                tags=(safety_level,),
            )

            self.file_data[item_id] = {
                'path': filepath,
                'size': size,
                'safety_level': safety_level,
                'warning': warning,
                'recommendation': recommendation,
                'age_status': age_status,
                'is_safe': file_info['is_safe'],
                'checked': False,
            }

            filtered_count += 1
            filtered_bytes += size

        # Update stats label to reflect filtered set
        self.stats_label.config(
            text=f"{filtered_count} files • {FileSizeAnalyzer.format_size(filtered_bytes)} ",
            fg='#2C3E50',
            bg='#E3F2FD',
            font=('Helvetica', 15, 'bold'),
        )

    def on_tree_click(self, event):
        """Handle tree click for checkbox toggle with Shift/Cmd multi-select"""
        # Identify which column was clicked
        region = self.file_tree.identify_region(event.x, event.y)
        column = self.file_tree.identify_column(event.x)
        item = self.file_tree.identify_row(event.y)

        if not item or item not in self.file_data:
            return

        # Get all items in order
        all_items = self.file_tree.get_children()

        # Check modifier keys
        shift_pressed = (event.state & 0x1) != 0  # Shift key
        cmd_pressed = (event.state & 0x8) != 0   # Cmd/Alt key
        ctrl_pressed = (event.state & 0x4) != 0  # Ctrl key (same as Cmd on macOS)

        # Handle different click types
        if shift_pressed and self.last_selected_item and self.last_selected_item in all_items:
            # Shift+click: select range
            self._select_range(self.last_selected_item, item)
        elif cmd_pressed or ctrl_pressed:
            # Cmd/Ctrl+click: toggle individual item while keeping others
            self.file_data[item]['checked'] = not self.file_data[item]['checked']
            checkbox = '✅' if self.file_data[item]['checked'] else '◻️'
            self.file_tree.item(item, text=checkbox)
            self.last_selected_item = item
        else:
            # Regular click: toggle single checkbox
            self.file_data[item]['checked'] = not self.file_data[item]['checked']
            checkbox = '✅' if self.file_data[item]['checked'] else '◻️'
            self.file_tree.item(item, text=checkbox)
            self.last_selected_item = item

        # Update selection info
        self._update_selection_info()

        # Show recommendation when clicking anywhere on the row
        recommendation = self.file_data[item]['recommendation']
        safety_level = self.file_data[item]['safety_level']

        # Color code the recommendation box
        if safety_level == 'DANGEROUS':
            bg_color = '#FFEBEE'
            fg_color = '#C62828'
        elif safety_level == 'CAUTION':
            bg_color = '#FFF3CD'
            fg_color = '#856404'
        else:
            bg_color = '#E8F5E9'
            fg_color = '#2E7D32'

        if safety_level == 'DANGEROUS':
            level_emoji = '🔴'
        elif safety_level == 'CAUTION':
            level_emoji = '🟠'
        else:
            level_emoji = '🟢'

        self.recommendation_label.config(
            text=f"{level_emoji} 💡 Recommendation: {recommendation}",
            bg=bg_color,
            fg=fg_color,
        )
        self.recommendation_label.pack(fill=tk.X, pady=(5, 5))

    def _select_range(self, start_item, end_item):
        """Select all items between start and end items (inclusive)"""
        all_items = self.file_tree.get_children()
        try:
            start_idx = all_items.index(start_item)
            end_idx = all_items.index(end_item)
        except ValueError:
            return

        # Ensure start_idx is before end_idx
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx

        # Select all items in range
        for i in range(start_idx, end_idx + 1):
            item = all_items[i]
            if item in self.file_data:
                self.file_data[item]['checked'] = True
                self.file_tree.item(item, text='✅')

    def select_all_files(self):
        """Select all files"""
        for item in self.file_tree.get_children():
            if item in self.file_data:
                self.file_data[item]['checked'] = True
                self.file_tree.item(item, text='✅')
        self._update_selection_info()

    def deselect_all_files(self):
        """Deselect all files"""
        for item in self.file_tree.get_children():
            if item in self.file_data:
                self.file_data[item]['checked'] = False
                self.file_tree.item(item, text='◻️')
        self._update_selection_info()

    def _update_selection_info(self):
        """Update the selection counter at the top"""
        if hasattr(self, 'selection_info_label'):
            selected_count = sum(1 for item in self.file_tree.get_children()
                               if item in self.file_data and self.file_data[item]['checked'])
            total_count = len([item for item in self.file_tree.get_children() if item in self.file_data])
            self.selection_info_label.config(text=f"{selected_count} of {total_count} selected")

    def show_context_menu(self, event):
        """Show context menu on right-click"""
        item = self.file_tree.identify_row(event.y)
        if item and item in self.file_data:
            # Select the item
            self.file_tree.selection_set(item)

            # Create context menu
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Reveal in Finder",
                           command=lambda: self.reveal_in_finder(self.file_data[item]['path']))
            menu.add_separator()
            menu.add_command(label="Copy Path",
                           command=lambda: self.copy_path_to_clipboard(self.file_data[item]['path']))

            # Show menu at cursor position
            menu.post(event.x_root, event.y_root)

    def reveal_in_finder(self, filepath):
        """Reveal file in Finder"""
        import subprocess
        subprocess.run(['open', '-R', filepath])

    def copy_path_to_clipboard(self, filepath):
        """Copy file path to clipboard"""
        self.root.clipboard_clear()
        self.root.clipboard_append(filepath)
        self.root.update()  # Required for clipboard to work

    def ai_suggest_cleanup(self):
        """Use AI to suggest safe cleanup actions for current scan."""
        # Get selected provider and API key
        provider, api_key = self._get_ai_provider_and_key()

        if not api_key:
            env_paths = self._get_env_file_candidates()
            paths_str = "\n".join(f"  • {p}" for p in env_paths[:5])
            messagebox.showerror(
                "AI Suggestions",
                f"No API key found for {provider}.\n\n"
                f"Checked .env files:\n{paths_str}\n\n"
                "Please set up your AI provider in Settings."
            )
            return

        if not self.file_data:
            messagebox.showinfo(
                "AI Suggestions",
                "No scan data available yet.\n\nRun 'Find Large Files' first.",
            )
            return

        prompt = self._build_ai_cleanup_prompt()
        if not prompt:
            messagebox.showinfo(
                "AI Suggestions",
                f"Not enough data to build AI suggestions.\n\nFiles found: {len(self.file_data)}\n\nTry scanning a larger folder.",
            )
            return

        # Show loading indicator
        loading = tk.Toplevel(self.root)
        loading.title("AI Analyzing...")
        loading.geometry("300x100")
        loading.transient(self.root)
        tk.Label(loading, text="🤖 AI is analyzing your files...", font=("Helvetica", 14)).pack(expand=True)
        loading.update()

        def worker():
            try:
                suggestions = self._call_ai_api(provider, api_key, prompt)
                self.root.after(0, lambda: (loading.destroy(), self._show_ai_suggestions_dialog(suggestions)))
            except Exception as e:
                import traceback
                error_msg = f"Provider: {provider}\nError: {str(e)}\n\nDetails:\n{traceback.format_exc()[:500]}"
                self.root.after(
                    0,
                    lambda: (loading.destroy(), messagebox.showerror("AI Suggestions Error", error_msg)),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _prompt_for_api_key(self):
        """Show popup to prompt user for OpenAI API key."""
        dialog = tk.Toplevel(self.root)
        dialog.title("API Key Required")
        dialog.geometry("500x280")
        dialog.minsize(450, 250)
        dialog.transient(self.root)
        dialog.grab_set()

        body = tk.Frame(dialog, bg="#F8F9FA", padx=25, pady=25)
        body.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            body,
            text="🔑 OpenAI API Key Required",
            font=("Helvetica", 16, "bold"),
            fg="#111827",
            bg="#F8F9FA",
        ).pack(anchor=tk.W)

        tk.Label(
            body,
            text=(
                "To use AI-powered cleanup suggestions, you need an OpenAI API key.\n\n"
                "Get your key at: https://platform.openai.com/api-keys\n\n"
                "Your key is stored locally on this Mac only."
            ),
            font=("Helvetica", 12),
            fg="#4B5563",
            bg="#F8F9FA",
            justify=tk.LEFT,
            wraplength=440,
        ).pack(anchor=tk.W, pady=(10, 15))

        entry = tk.Entry(body, show="*", font=("Helvetica", 13), width=50)
        entry.pack(fill=tk.X, pady=(0, 15))
        entry.focus_set()

        button_frame = tk.Frame(body, bg="#F8F9FA")
        button_frame.pack(fill=tk.X)

        def save_and_retry():
            key = entry.get().strip()
            if not key:
                messagebox.showwarning("API Key", "Please enter an API key.")
                return
            if self.license_manager and hasattr(self.license_manager, "set_openai_api_key"):
                self.license_manager.set_openai_api_key(key)
            dialog.destroy()
            messagebox.showinfo("API Key Saved", "API key saved! Click 'AI Suggested Cleanup' again to use it.")

        tk.Button(
            button_frame,
            text="Save API Key",
            command=save_and_retry,
            font=("Helvetica", 12, "bold"),
            bg="#10B981",
            fg="white",
            padx=20,
            pady=8,
        ).pack(side=tk.RIGHT)

        tk.Button(
            button_frame,
            text="Cancel",
            command=dialog.destroy,
            font=("Helvetica", 12),
            padx=15,
            pady=8,
        ).pack(side=tk.RIGHT, padx=(0, 10))

    def _get_env_file_candidates(self) -> List[str]:
        if MAS_BUILD:
            # In sandbox, only check the app's container directory
            container = os.path.join(
                os.path.expanduser('~'),
                'Library', 'Containers', 'org.batesai.filegenius',
                'Data', 'Library', 'Application Support', 'FileGenius'
            )
            return [os.path.join(container, '.env')]

        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(base_dir, ".env"),
            os.path.join(os.path.dirname(base_dir), ".env"),
            os.path.join(os.path.dirname(os.path.dirname(base_dir)), ".env"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(base_dir))), ".env"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(base_dir)))), ".env"),
            os.path.join(base_dir, "..", "docs", ".env"),
        ]
        return [os.path.abspath(os.path.expanduser(path)) for path in candidates]

    def _load_value_from_env_files(self, *keys: str) -> Optional[str]:
        if not keys:
            return None
        normalized = {k.strip() for k in keys if k}
        if not normalized:
            return None
        for path in self._get_env_file_candidates():
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip("'\"")
                        if key in normalized and value:
                            return value
            except Exception:
                continue
        return None

    def _load_openai_key_from_env_file(self) -> Optional[str]:
        return self._load_value_from_env_files("OPENAI_API_KEY")

    @staticmethod
    def _deobfuscate_builtin_key() -> str:
        """Decode the built-in fallback API key."""
        import base64
        # XOR-obfuscated key (not true security, but avoids plaintext in binary)
        _d = base64.b64decode(b'FAQcWk8BE0EDGRQHAUMdEwUFEgMOBQMUQhkRDxEUAQEcQg8dAE4BBRRCAwUZREFOAxxETg==')
        _k = 0x77
        return bytes(b ^ _k for b in _d).decode('ascii')

    def _load_cerebras_key(self) -> Optional[str]:
        """Load Cerebras API key from environment or parent .env files."""
        # Check environment variable first
        key = os.environ.get("CEREBRAS_API_KEY")
        if key:
            return key
        # Check .env files (including parent directories)
        key = self._load_value_from_env_files("CEREBRAS_API_KEY")
        if key:
            return key
        # Fallback to built-in key (obfuscated for App Store)
        return self._deobfuscate_builtin_key()

    def _get_ai_provider_and_key(self) -> Tuple[str, Optional[str]]:
        """Get the selected AI provider and its API key."""
        # Get selected provider (default to Cerebras)
        provider = "Cerebras (Free)"
        try:
            if self.license_manager and hasattr(self.license_manager, "get_ai_provider"):
                provider = self.license_manager.get_ai_provider() or "Cerebras (Free)"
        except Exception:
            pass

        config = self.AI_PROVIDERS.get(provider, self.AI_PROVIDERS["Cerebras (Free)"])
        
        # Get API key
        if config.get("builtin"):
            # Use built-in Cerebras key
            api_key = self._load_cerebras_key()
        else:
            # Try to get custom key from license manager
            api_key = None
            try:
                if self.license_manager and hasattr(self.license_manager, "get_custom_api_key"):
                    api_key = self.license_manager.get_custom_api_key(provider)
            except Exception:
                pass
            # Fallback to environment variable
            if not api_key:
                api_key = os.environ.get(config.get("key_env", ""))
            if not api_key:
                api_key = self._load_value_from_env_files(config.get("key_env", ""))
        
        return provider, api_key

    def _build_ai_cleanup_prompt(self) -> str:
        """Build a compact summary of current scan results for AI."""
        try:
            items = sorted(
                self.file_data.values(),
                key=lambda d: d.get("size", 0),
                reverse=True,
            )
        except Exception:
            items = list(self.file_data.values())

        if not items:
            return ""

        top = items[:25]
        total_bytes = sum(d.get("size", 0) for d in items)
        total_str = FileSizeAnalyzer.format_size(total_bytes) if total_bytes else "0 B"

        lines = []
        for d in top:
            size_str = FileSizeAnalyzer.format_size(d.get("size", 0))
            path = d.get("path", "")
            warning = d.get("warning", "")
            safety = d.get("safety_level", "")
            age = d.get("age_status", "")
            lines.append(
                f"- {size_str} | safety={safety} | age={age} | {warning} | {path}"
            )

        files_block = "\n".join(lines)
        folder = self.current_folder or "<unknown>"

        prompt = (
            "You are an expert Mac disk cleanup assistant.\n"
            "The user just scanned a folder with FileGenius (a macOS file organizer).\n"
            "FileGenius has already classified files by safety level and age.\n\n"
            f"Scanned folder: {folder}\n"
            f"Total size (all files considered): {total_str}\n"
            "Here are the largest and most relevant files:\n"
            f"{files_block}\n\n"
            "Using this information, propose a concise, prioritized list of cleanup actions.\n"
            "- Be conservative with files marked DANGEROUS or CAUTION: usually recommend keeping them unless clearly safe.\n"
            "- Prefer deleting or moving SAFE and OLD files (e.g., videos, downloads, archives).\n"
            "- Group actions into clear bullets (e.g., 'Delete X–Y', 'Move Z to external drive').\n"
            "- Never claim you have actually deleted anything; you are only suggesting.\n"
            "- Output plain text, no code.\n"
        )
        return prompt

    def _call_ai_api(self, provider: str, api_key: str, prompt: str) -> str:
        """Call the selected AI provider's API and return the text reply."""
        config = self.AI_PROVIDERS.get(provider, self.AI_PROVIDERS["Cerebras (Free)"])
        url = config["url"]
        model = config["model"]
        
        system_msg = "You are a cautious macOS disk cleanup assistant helping a user decide what to delete or move."
        
        # Handle Anthropic's different API format
        if config.get("anthropic"):
            headers = {
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": model,
                "max_tokens": 700,
                "system": system_msg,
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            try:
                return data["content"][0]["text"]
            except (KeyError, IndexError) as e:
                raise RuntimeError("Unexpected response format from Anthropic API") from e
        
        # Handle Google Gemini's different API format
        elif config.get("google"):
            url_with_key = f"{url}?key={api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": f"{system_msg}\n\n{prompt}"}]}],
                "generationConfig": {"maxOutputTokens": 700},
            }
            resp = requests.post(url_with_key, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as e:
                raise RuntimeError("Unexpected response format from Google API") from e
        
        # Standard OpenAI-compatible format (OpenAI, Grok, Groq)
        else:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 700,
            }
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                error_detail = ""
                try:
                    error_detail = resp.text[:500]
                except Exception:
                    pass
                # Better error message for Cerebras rate limit issues
                if provider == "Cerebras (Free)" and resp.status_code in (429, 402):
                    raise RuntimeError(
                        "Cerebras API rate limit reached.\n\n"
                        "Please wait a moment and try again, or configure a different AI provider in Settings."
                    ) from e
                raise RuntimeError(f"{provider} API error ({resp.status_code}): {error_detail}") from e
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"{provider} connection error: {e}") from e
            
            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise RuntimeError(f"Unexpected response format from {provider} API: {data}") from e

    def _show_ai_suggestions_dialog(self, suggestions: str):
        """Show AI suggestions in a scrollable dialog with action buttons."""
        dialog = tk.Toplevel(self.root)
        dialog.configure(bg="white")
        dialog.title("AI Cleanup Suggestions")
        dialog.geometry("850x650")
        dialog.minsize(700, 500)
        dialog.update_idletasks()
        try:
            screen_w = dialog.winfo_screenwidth()
            screen_h = dialog.winfo_screenheight()
            win_w = dialog.winfo_width()
            win_h = dialog.winfo_height()
            x = max(20, int((screen_w - win_w) / 2))
            y = max(20, int((screen_h - win_h) / 2))
            dialog.geometry(f"{win_w}x{win_h}+{x}+{y}")
        except Exception:
            pass

        header = tk.Frame(dialog, bg="#2563EB", pady=12)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="✨ AI Cleanup Suggestions",
            font=("Helvetica", 18, "bold"),
            fg="white",
            bg="#2563EB",
        ).pack()

        body = tk.Frame(dialog, bg="white", padx=15, pady=15)
        body.pack(fill=tk.BOTH, expand=True)

        # Two-column layout: AI suggestions on left, file preview on right
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        suggestions_frame = tk.Frame(body, bg="white")
        suggestions_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        text = scrolledtext.ScrolledText(
            suggestions_frame,
            wrap=tk.WORD,
            font=("Helvetica", 13),
            relief=tk.FLAT,
            borderwidth=0,
            bg="white",
            fg="#111111",
            insertbackground="#111111",
            highlightthickness=0,
        )
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, suggestions or "No suggestions returned.")
        text.config(state=tk.DISABLED)

        preview_frame = tk.Frame(body, bg="white")
        preview_frame.grid(row=0, column=1, sticky="nsew")

        tk.Label(
            preview_frame,
            text="Scanned Files Preview",
            font=("Helvetica", 13, "bold"),
            fg="#0F172A",
            bg="white",
        ).pack(anchor=tk.W, pady=(0, 6))

        preview_box = scrolledtext.ScrolledText(
            preview_frame,
            wrap=tk.WORD,
            font=("Helvetica", 12),
            relief=tk.FLAT,
            borderwidth=1,
            bg="white",
            fg="#111111",
            highlightthickness=1,
            highlightbackground="#D1D5DB",
        )
        preview_box.pack(fill=tk.BOTH, expand=True)

        preview_lines = []
        if self.file_data:
            top_files = sorted(
                self.file_data.values(),
                key=lambda d: d.get("size", 0),
                reverse=True,
            )[:20]
            for idx, f in enumerate(top_files, start=1):
                size_str = FileSizeAnalyzer.format_size(f.get("size", 0))
                preview_lines.append(
                    f"{idx}. {size_str} • {f.get('warning', '').strip()} • {os.path.basename(f.get('path', ''))}"
                )
            preview_box.insert(tk.END, "\n".join(preview_lines))
        else:
            preview_box.insert(tk.END, "No scan data available.\nRun Find Large Files first.")
        preview_box.config(state=tk.DISABLED)

        # Action buttons frame
        action_frame = tk.Frame(dialog, bg="white", pady=12)
        action_frame.pack(fill=tk.X)
        
        tk.Label(
            action_frame,
            text="Quick Actions:",
            font=("Helvetica", 12, "bold"),
            fg="#374151",
            bg="white",
        ).pack(side=tk.LEFT, padx=15)

        def confirm_bulk_action(action_title: str, files: List[Dict], extra_note: str = "") -> bool:
            """Show a single confirmation dialog summarizing file count/size."""
            if not files:
                return False

            total_size = sum(f.get("size", 0) for f in files)
            size_str = FileSizeAnalyzer.format_size(total_size)
            count = len(files)

            confirm = tk.Toplevel(self.root)
            confirm.title(action_title)
            confirm.configure(bg="white")
            confirm.geometry("420x250")
            confirm.minsize(380, 230)
            confirm.transient(dialog)
            confirm.grab_set()

            body = tk.Frame(confirm, bg="white", padx=20, pady=20)
            body.pack(fill=tk.BOTH, expand=True)

            tk.Label(
                body,
                text=f"⚠️ You're about to {action_title.lower()}",
                font=("Helvetica", 15, "bold"),
                fg="#000000",
                bg="white",
            ).pack(anchor=tk.W)

            tk.Label(
                body,
                text=f"{count} files • {size_str}",
                font=("Helvetica", 13),
                fg="#000000",
                bg="white",
            ).pack(anchor=tk.W, pady=(8, 12))

            if extra_note:
                tk.Label(
                    body,
                    text=extra_note,
                    font=("Helvetica", 12),
                    fg="#4B5563",
                    bg="white",
                    wraplength=360,
                    justify=tk.LEFT,
                ).pack(anchor=tk.W, pady=(0, 12))

            button_bar = tk.Frame(body, bg="white")
            button_bar.pack(fill=tk.X, pady=(10, 0))

            result = {"confirmed": False}

            def on_confirm():
                result["confirmed"] = True
                confirm.destroy()

            tk.Button(
                button_bar,
                text="Cancel",
                command=confirm.destroy,
                font=("Helvetica", 12),
                fg="#000000",
                padx=16,
                pady=6,
            ).pack(side=tk.RIGHT, padx=(10, 0))

            tk.Button(
                button_bar,
                text="Continue",
                command=on_confirm,
                font=("Helvetica", 12, "bold"),
                bg="#DC2626",
                fg="#000000",
                padx=16,
                pady=6,
            ).pack(side=tk.RIGHT)

            confirm.wait_window()
            return result["confirmed"]

        def move_safe_to_trash():
            """Move files marked as SAFE to trash."""
            if not self.file_data:
                messagebox.showinfo("No Files", "No scanned files available.")
                return
            
            safe_files = [f for f in self.file_data.values() if f.get("safety_level") == "SAFE"]
            if not safe_files:
                messagebox.showinfo("No Safe Files", "No files marked as SAFE found.")
                return
            
            count = len(safe_files)
            total_size = sum(f.get("size", 0) for f in safe_files)
            size_str = FileSizeAnalyzer.format_size(total_size)

            self._ensure_delete_permissions()

            extra = "Files marked SAFE can typically be deleted. They will be moved to macOS Trash."
            if not confirm_bulk_action("Move SAFE Files to Trash", safe_files, extra):
                return
            
            moved = 0
            for f in safe_files:
                try:
                    path = f.get("path")
                    if path and os.path.exists(path):
                        if MAS_BUILD:
                            if self._move_to_trash_sandbox(path):
                                moved += 1
                        else:
                            os.system(f'osascript -e \'tell application "Finder" to delete POSIX file "{path}"\'')
                            moved += 1
                except Exception:
                    pass

            messagebox.showinfo("Done", f"Moved {moved} files to Trash.")
            dialog.destroy()

        def delete_old_files():
            """Delete files marked as OLD."""
            if not self.file_data:
                messagebox.showinfo("No Files", "No scanned files available.")
                return

            old_files = [f for f in self.file_data.values() if "old" in f.get("age_status", "").lower()]
            if not old_files:
                messagebox.showinfo("No Old Files", "No old files found.")
                return

            count = len(old_files)
            total_size = sum(f.get("size", 0) for f in old_files)
            size_str = FileSizeAnalyzer.format_size(total_size)

            self._ensure_delete_permissions()

            extra = "These files have not been accessed recently. They will be moved to macOS Trash."
            if not confirm_bulk_action("Move OLD Files to Trash", old_files, extra):
                return

            moved = 0
            for f in old_files:
                try:
                    path = f.get("path")
                    if path and os.path.exists(path):
                        if MAS_BUILD:
                            if self._move_to_trash_sandbox(path):
                                moved += 1
                        else:
                            os.system(f'osascript -e \'tell application "Finder" to delete POSIX file "{path}"\'')
                            moved += 1
                except Exception:
                    pass

            messagebox.showinfo("Done", f"Moved {moved} old files to Trash.")
            dialog.destroy()

        def open_in_finder():
            """Open current folder in Finder."""
            if self.current_folder:
                import subprocess
                subprocess.run(['open', self.current_folder])

        button_kwargs = {
            "font": ("Helvetica", 12, "bold"),
            "bg": "white",
            "fg": "#111111",
            "activebackground": "#E5E7EB",
            "activeforeground": "#111111",
            "relief": tk.SOLID,
            "borderwidth": 1,
            "highlightthickness": 0,
            "padx": 14,
            "pady": 8,
        }

        tk.Button(
            action_frame,
            text="🗑️ Trash SAFE Files",
            command=move_safe_to_trash,
            **button_kwargs,
        ).pack(side=tk.LEFT, padx=6)

        tk.Button(
            action_frame,
            text="🕐 Trash OLD Files",
            command=delete_old_files,
            **button_kwargs,
        ).pack(side=tk.LEFT, padx=6)

        tk.Button(
            action_frame,
            text="📂 Open in Finder",
            command=open_in_finder,
            **button_kwargs,
        ).pack(side=tk.LEFT, padx=6)

        footer = tk.Frame(dialog, bg="white", pady=10)
        footer.pack(fill=tk.X)

        tk.Label(
            footer,
            text="⚠️ Review suggestions carefully before taking action.",
            font=("Helvetica", 11, "bold"),
            fg="#DC2626",
            bg="white",
        ).pack(side=tk.LEFT, padx=10)

        tk.Button(
            footer,
            text="Close",
            command=dialog.destroy,
            font=("Helvetica", 12),
            padx=18,
            pady=6,
        ).pack(side=tk.RIGHT, padx=10)

    # AI Provider configurations
    AI_PROVIDERS = {
        "OpenAI (ChatGPT)": {
            "url": "https://api.openai.com/v1/chat/completions",
            "model": "gpt-4o-mini",
            "key_env": "OPENAI_API_KEY",
            "key_url": "https://platform.openai.com/api-keys",
            "builtin": False,
        },
        "Anthropic (Claude)": {
            "url": "https://api.anthropic.com/v1/messages",
            "model": "claude-3-haiku-20240307",
            "key_env": "ANTHROPIC_API_KEY",
            "key_url": "https://console.anthropic.com/",
            "builtin": False,
            "anthropic": True,
        },
        "Grok (xAI)": {
            "url": "https://api.x.ai/v1/chat/completions",
            "model": "grok-beta",
            "key_env": "XAI_API_KEY",
            "key_url": "https://console.x.ai/",
            "builtin": False,
        },
        "Cerebras (Free)": {
            "url": "https://api.cerebras.ai/v1/chat/completions",
            "model": "llama-3.3-70b",
            "key_env": "CEREBRAS_API_KEY",
            "key_url": "https://cloud.cerebras.ai/",
            "builtin": True,
        },
        "Groq": {
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "model": "llama-3.3-70b-versatile",
            "key_env": "GROQ_API_KEY",
            "key_url": "https://console.groq.com/",
            "builtin": False,
        },
        "Google (Gemini)": {
            "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
            "model": "gemini-1.5-flash",
            "key_env": "GOOGLE_API_KEY",
            "key_url": "https://aistudio.google.com/apikey",
            "builtin": False,
            "google": True,
        },
    }

    def _show_ai_settings_dialog(self):
        """Allow the user to select AI provider and enter API key."""
        dialog = tk.Toplevel(self.root)
        dialog.title("AI Settings")
        dialog.geometry("560x380")
        dialog.minsize(560, 380)
        dialog.transient(self.root)
        dialog.grab_set()

        body = tk.Frame(dialog, bg="#F8F9FA", padx=25, pady=20)
        body.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            body,
            text="🤖 AI Provider Settings",
            font=("Helvetica", 16, "bold"),
            fg="#111827",
            bg="#F8F9FA",
        ).pack(anchor=tk.W)

        tk.Label(
            body,
            text="Choose your AI provider. Cerebras is free and built-in.",
            font=("Helvetica", 12),
            fg="#4B5563",
            bg="#F8F9FA",
        ).pack(anchor=tk.W, pady=(4, 15))

        # Provider selection
        provider_frame = tk.Frame(body, bg="#F8F9FA")
        provider_frame.pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            provider_frame,
            text="Provider:",
            font=("Helvetica", 13, "bold"),
            fg="#111827",
            bg="#F8F9FA",
        ).pack(side=tk.LEFT)

        # Get current provider
        current_provider = "Cerebras (Free)"
        try:
            if self.license_manager and hasattr(self.license_manager, "get_ai_provider"):
                current_provider = self.license_manager.get_ai_provider() or "Cerebras (Free)"
        except Exception:
            pass

        provider_var = tk.StringVar(value=current_provider)
        provider_menu = ttk.Combobox(
            provider_frame,
            textvariable=provider_var,
            values=list(self.AI_PROVIDERS.keys()),
            state="readonly",
            font=("Helvetica", 12),
            width=25,
        )
        provider_menu.pack(side=tk.LEFT, padx=(10, 0))

        # API Key section
        key_frame = tk.Frame(body, bg="#F8F9FA")
        key_frame.pack(fill=tk.X, pady=(10, 0))

        key_label = tk.Label(
            key_frame,
            text="API Key:",
            font=("Helvetica", 13, "bold"),
            fg="#111827",
            bg="#F8F9FA",
        )
        key_label.pack(anchor=tk.W)

        info_label = tk.Label(
            key_frame,
            text="Using built-in free API key",
            font=("Helvetica", 11),
            fg="#059669",
            bg="#F8F9FA",
        )
        info_label.pack(anchor=tk.W, pady=(2, 6))

        entry = tk.Entry(key_frame, show="*", font=("Helvetica", 13))
        entry.pack(fill=tk.X, pady=(0, 5))

        # Load current key if exists
        def load_current_key():
            provider = provider_var.get()
            config = self.AI_PROVIDERS.get(provider, {})
            if config.get("builtin"):
                entry.delete(0, tk.END)
                entry.config(state="disabled")
                info_label.config(text="Using built-in free API key", fg="#059669")
            else:
                entry.config(state="normal")
                key_url = config.get("key_url", "")
                info_label.config(text=f"Get your key at: {key_url}", fg="#4B5563")
                # Try to load saved key
                try:
                    if self.license_manager and hasattr(self.license_manager, "get_custom_api_key"):
                        saved_key = self.license_manager.get_custom_api_key(provider) or ""
                        entry.delete(0, tk.END)
                        entry.insert(0, saved_key)
                except Exception:
                    pass

        load_current_key()
        provider_menu.bind("<<ComboboxSelected>>", lambda e: load_current_key())

        # Buttons
        button_frame = tk.Frame(body, bg="#F8F9FA")
        button_frame.pack(fill=tk.X, pady=(20, 0))

        def save_settings():
            provider = provider_var.get()
            key = entry.get().strip()
            try:
                if self.license_manager:
                    if hasattr(self.license_manager, "set_ai_provider"):
                        self.license_manager.set_ai_provider(provider)
                    if not self.AI_PROVIDERS.get(provider, {}).get("builtin") and key:
                        if hasattr(self.license_manager, "set_custom_api_key"):
                            self.license_manager.set_custom_api_key(provider, key)
                messagebox.showinfo("AI Settings", f"Settings saved!\nProvider: {provider}")
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("AI Settings", f"Could not save settings:\n{e}")

        tk.Button(
            button_frame,
            text="Cancel",
            command=dialog.destroy,
            font=("Helvetica", 12),
            padx=16,
            pady=6,
        ).pack(side=tk.RIGHT, padx=(10, 0))

        tk.Button(
            button_frame,
            text="Save",
            command=save_settings,
            font=("Helvetica", 12, "bold"),
            bg="#10B981",
            fg="black",
            padx=20,
            pady=6,
        ).pack(side=tk.RIGHT)

    def _show_trial_expired_dialog(self):
        """Legacy trial-expired dialog (disabled).

        Kept as a no-op for backwards compatibility, but Basic Organize and
        other cleanups are now always allowed without a trial limit.
        """
        logging.info("Trial-expired dialog is disabled; basic organize is always allowed.")

    def _show_pro_required_dialog(self):
        """Inform user that AI suggestions require a Pro license."""
        url = None
        if self.license_manager:
            try:
                url = self.license_manager.get_purchase_url()
            except Exception:
                url = None

        if url:
            if messagebox.askyesno(
                "FileGenius Pro Required",
                "AI Suggested Cleanup is a Pro feature.\n\n"
                "Upgrade now for $5 to unlock ChatGPT-powered cleanup suggestions?",
            ):
                self._open_url(url)
        else:
            messagebox.showinfo(
                "FileGenius Pro Required",
                "AI cleanup suggestions are a Pro feature.\n\n"
                "Purchase a Pro license to unlock unlimited use and AI-powered cleanup.",
            )

    def browse_move_dest(self):
        """Browse for move destination"""
        folder = filedialog.askdirectory(title="Select destination folder")
        if folder:
            self.move_dest_var.set(folder)

    def stop_scanning(self):
        """Stop the scanning process and show current results"""
        if self.scanning:
            self.scan_stopped = True
            # Update button to show stopping
            self.stop_scan_btn.config(state=tk.DISABLED, text="⏹ Stopping...")
            
    def move_to_trash_selected_files(self):
        """Move selected files to trash instead of deleting them"""
        import subprocess
        
        if not self._can_perform_cleaning():
            return

        # Get checked items
        to_move = []
        unsafe = []

        for item_id, data in self.file_data.items():
            if data['checked']:
                to_move.append(data['path'])
                if not data['is_safe']:
                    unsafe.append(data['path'])

        if not to_move:
            messagebox.showinfo("No Selection", "No files selected for moving to trash.\n\nClick the checkbox next to files to select them.")
            return

        # Warn about unsafe files
        if unsafe:
            if not messagebox.askyesno("Warning",
                f"You selected {len(unsafe)} system file(s) for moving to trash.\n\n"
                "This could affect your system! You can recover them from Trash later.\n\nContinue anyway?"):
                return

        # Confirm move to trash
        total_size = sum(os.path.getsize(p) for p in to_move if os.path.exists(p))
        size_str = FileSizeAnalyzer.format_size(total_size)

        if not messagebox.askyesno("Move to Trash",
            f"Move {len(to_move)} files ({size_str}) to Trash?\n\n"
            "This will move them to the macOS Trash bin where they can be recovered."):
            return

        # Move files to trash and remove from tree
        moved = 0
        moved_size = 0
        items_to_remove = []
        failed_files = []

        for item_id, data in self.file_data.items():
            if data['checked']:
                filepath = data['path']
                try:
                    if os.path.exists(filepath):
                        file_size = os.path.getsize(filepath)
                        if MAS_BUILD:
                            if self._move_to_trash_sandbox(filepath):
                                moved += 1
                                moved_size += file_size
                                items_to_remove.append(item_id)
                            else:
                                failed_files.append(f"{os.path.basename(filepath)}: Sandbox trash failed")
                        else:
                            script = f'''
tell application "Finder"
    move (POSIX file "{filepath}") to trash
end tell
'''
                            subprocess.run(['osascript', '-e', script], check=True, capture_output=True)
                            moved += 1
                            moved_size += file_size
                            items_to_remove.append(item_id)
                except Exception as e:
                    failed_files.append(f"{os.path.basename(filepath)}: {str(e)}")

        # Remove moved items from tree and file_data
        for item_id in items_to_remove:
            self.file_tree.delete(item_id)
            del self.file_data[item_id]

        # Update stats to reflect move
        remaining_files = len(self.file_data)
        remaining_size = sum(data['size'] for data in self.file_data.values())
        self.stats_label.config(
            text=f"{remaining_files:,} files • {FileSizeAnalyzer.format_size(remaining_size)} total",
            fg='#2C3E50',
            bg='#E3F2FD',
            font=('Helvetica', 15, 'bold')
        )

        # Show results
        if moved > 0:
            messagebox.showinfo("Move to Trash Complete", 
                f"Successfully moved {moved} file(s) ({FileSizeAnalyzer.format_size(moved_size)}) to the Trash bin.")
        
        if failed_files:
            error_msg = "Could not move these files to trash:\n\n" + "\n".join(failed_files[:10])
            if len(failed_files) > 10:
                error_msg += f"\n... and {len(failed_files) - 10} more"
            messagebox.showerror("Move Errors", error_msg)
            
        if moved > 0:
            self._register_cleaning()

    def _flash_warning(self, *widgets):
        """Flash widgets yellow to indicate they need attention"""
        original_colors = []

        # Store original colors and set to yellow
        for widget in widgets:
            if isinstance(widget, ColorButton):
                original_colors.append(widget.default_bg)
                widget.config(bg='#FFEB3B')  # Bright yellow
                widget.default_bg = '#FFEB3B'
            else:
                original_colors.append(widget.cget('bg'))
                widget.config(bg='#FFEB3B')  # Bright yellow

        # Flash 3 times
        def restore_colors(flash_count=0):
            if flash_count < 3:
                # Toggle between yellow and white
                for i, widget in enumerate(widgets):
                    if flash_count % 2 == 0:
                        widget.config(bg='white')
                    else:
                        widget.config(bg='#FFEB3B')

                self.root.after(200, lambda: restore_colors(flash_count + 1))
            else:
                # Restore original colors
                for i, widget in enumerate(widgets):
                    if isinstance(widget, ColorButton):
                        widget.config(bg=original_colors[i])
                        widget.default_bg = original_colors[i]
                    else:
                        widget.config(bg=original_colors[i])

        self.root.after(200, restore_colors)

    def move_selected_files(self):
        """Move files to selected destinations"""
        if not self._can_perform_cleaning():
            return

        destination = self.move_dest_var.get()

        if not destination:
            # Flash the folder button and entry yellow to indicate missing selection
            self._flash_warning(self.browse_folder_btn, self.move_entry)
            messagebox.showinfo("No Destination", "Please select a destination folder first.")
            return

        if not os.path.exists(destination):
            messagebox.showerror("Error", f"Destination folder doesn't exist:\n{destination}")
            return

        # Get checked items
        to_move = []
        for item_id, data in self.file_data.items():
            if data['checked']:
                to_move.append(data['path'])

        if not to_move:
            messagebox.showinfo("No Selection", "No files selected for moving.\n\nClick the checkbox next to files to select them.")
            return

        # Confirm move
        if not messagebox.askyesno("Confirm Move",
            f"Move {len(to_move)} file(s) to:\n{destination}?"):
            return

        # Move files
        moved = 0
        for filepath in to_move:
            try:
                if os.path.exists(filepath):
                    filename = os.path.basename(filepath)
                    dest_path = os.path.join(destination, filename)

                    # Handle duplicates
                    if os.path.exists(dest_path):
                        base, ext = os.path.splitext(filename)
                        counter = 1
                        while os.path.exists(dest_path):
                            dest_path = os.path.join(destination, f"{base}_{counter}{ext}")
                            counter += 1

                    shutil.move(filepath, dest_path)
                    moved += 1
            except Exception as e:
                messagebox.showerror("Error", f"Failed to move {filepath}:\n{e}")

        messagebox.showinfo("Done", f"Moved {moved} file(s)")
        self.do_analyze()  # Refresh list
        if moved > 0:
            self._register_cleaning()


def main():
    root = tk.Tk()
    
    # Set Dock icon
    try:
        from PIL import Image, ImageTk
        import sys
        app_dir = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, 'frozen', False):
            app_dir = sys._MEIPASS
        icon_path = os.path.join(app_dir, 'filegenius_1024.png')
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
            photo = ImageTk.PhotoImage(img)
            root.iconphoto(True, photo)
    except Exception as e:
        print(f"Could not set Dock icon: {e}")
    
    app = FileGenius(root)
    root.mainloop()


# ============================================================================
# MENU BAR APP (OPTIONAL - not available in Mac App Store builds)
# ============================================================================

if HAS_RUMPS:
    class FileGeniusMenuBar(rumps.App):
        """Menu bar application for FileGenius"""

        def __init__(self):
            app_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(app_dir, 'filegenius_menubar.png')

            if os.path.exists(icon_path):
                super().__init__("FileGenius", icon=icon_path, quit_button=None)
            else:
                super().__init__("FileGenius", quit_button=None)

            self.menu = [
                "Open FileGenius",
                rumps.separator,
                "Organize Downloads",
                "Organize Desktop",
                "Find Large Files",
                rumps.separator,
                "Quit"
            ]

        @rumps.clicked("Open FileGenius")
        def open_app(self, _):
            """Open main GUI"""
            import subprocess
            subprocess.Popen(['python3', __file__])

        @rumps.clicked("Organize Downloads")
        def organize_downloads(self, _):
            """Quick organize Downloads folder"""
            folder = os.path.expanduser('~/Downloads')
            if os.path.exists(folder):
                success, message, count = SimpleOrganizer.organize_by_type(folder)
                rumps.notification("FileGenius", "Organize Downloads", message)

        @rumps.clicked("Organize Desktop")
        def organize_desktop(self, _):
            """Quick organize Desktop folder"""
            folder = os.path.expanduser('~/Desktop')
            if os.path.exists(folder):
                success, message, count = SimpleOrganizer.organize_by_type(folder)
                rumps.notification("FileGenius", "Organize Desktop", message)

        @rumps.clicked("Find Large Files")
        def find_large_files(self, _):
            """Open app to Large Files page"""
            import subprocess
            subprocess.Popen(['python3', __file__])

        @rumps.clicked("Quit")
        def quit_app(self, _):
            """Quit application"""
            rumps.quit_application()


def main_menubar():
    """Run as menu bar app"""
    if not HAS_RUMPS:
        print("rumps not installed. Run: pip install rumps")
        print("Falling back to regular GUI...")
        main()
        return

    FileGeniusMenuBar().run()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--menubar':
        main_menubar()
    else:
        main()