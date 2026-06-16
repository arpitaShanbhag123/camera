import sys
import threading
import time
import re
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk


# base folder for image saving
base_dir = Path("Solar_Cells")

camera_index = 1

# preview sizing
preview_width = 1000
preview_height = 1000

# actual camera resolution
capture_width = 1920
capture_height = 1080


def get_opencv_backend():
    if sys.platform.startswith("win"):
        return cv2.CAP_DSHOW
    if sys.platform.startswith("darwin"):
        return cv2.CAP_AVFOUNDATION
    if sys.platform.startswith("linux"):
        return cv2.CAP_V4L2
    return 0


def open_camera(index: int):
    backend = get_opencv_backend()
    cam = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)

    if not cam.isOpened():
        return None

    cam.set(cv2.CAP_PROP_FRAME_WIDTH, capture_width)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_height)
    return cam


def extract_serial(text: str):
    # Accept either a contiguous 11-digit group, or digit groups that sum to 11 when joined.
    digits_groups = re.findall(r"\d+", text)
    for d in digits_groups:
        if len(d) == 11:
            return d

    # Join all digit characters (handles cases like '81131 1381 25')
    all_digits = ''.join(re.findall(r"\d", text))
    if len(all_digits) == 11:
        return all_digits

    # Fallback: if there are more than 11 digits, return the last 11 (common in prefixed data)
    if len(all_digits) > 11:
        return all_digits[-11:]

    return None


class CameraCaptureApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Solar Cell Capture (Front Camera + QR Scanner)")
        self.root.update_idletasks()

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        win_w = int(screen_w * 0.75)
        win_h = int(screen_h * 0.75)
        win_w = min(max(win_w, 800), 2200)
        win_h = min(max(win_h, 600), 1400)

        self.root.geometry(f"{win_w}x{win_h}")
        self.root.resizable(False, False)

        base_dir.mkdir(parents=True, exist_ok=True)

        # open single camera
        self.cam = open_camera(camera_index)
        self.frame = None
        self.is_capturing = False
        # debounce timer id for auto-processing scanner input
        self._scan_after_id = None
        self._last_processed_scan = None

        # build the gui
        self.build_interface()

        # start preview loop
        if self.cam:
            self.root.after(50, self.update_preview)

    def build_interface(self):
        title = tk.Label(self.root, text="Solar Cell Capture", font=("Segoe UI", 18, "bold"))
        title.pack(pady=(16, 8))

        subtitle = tk.Label(
            self.root,
            text="",
            font=("Segoe UI", 11),
            fg="#333333",
        )
        subtitle.pack(pady=(0, 10))

        # serial number label
        self.serial_label = tk.Label(
            self.root,
            text="serial: ---",
            font=("Segoe UI", 14),
            fg="#004080"
        )
        self.serial_label.pack(pady=(0, 10))

        # scanner Entry for debugging
        self.scan_entry = tk.Entry(self.root, font=("Segoe UI", 12))
        self.scan_entry.place(x=16, y=120, width=400, height=28)
        self.scan_entry.focus_set()
        self.scan_entry.bind("<Return>", self.on_scanner_enter)
        self.scan_entry.bind("<KeyRelease>", self.on_scanner_key)
        # forward global keyboard events to the scanner entry so it works without clicking
        self.root.bind_all('<Key>', self.on_global_key, add='+')

        # preview canvas
        self.preview_canvas = tk.Canvas(
            self.root,
            width=preview_width,
            height=preview_height,
            bg="#111111",
            highlightthickness=0,
        )
        self.preview_canvas.pack(pady=10)

        # status label
        self.status_label = tk.Label(self.root, text="initializing camera...", font=("Segoe UI", 11), fg="#555555")
        self.status_label.pack(pady=(0, 6))

    def update_preview(self):
        if self.cam:
            ok, frame = self.cam.read()
            if ok:
                self.frame = frame
                self.show_frame(frame)
                self.status_label.config(text="ready for scans.", fg="#333333")

        self.root.after(30, self.update_preview)

    def show_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]

        scale = min(preview_width / w, preview_height / h)
        resized = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        img = Image.fromarray(resized)
        photo = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("IMG")
        self.preview_canvas.create_image(0, 0, anchor="nw", image=photo, tags="IMG")
        self.preview_canvas.image = photo

    def on_scanner_enter(self, event):
        if self.is_capturing:
            return  # ignore scans during capture

        scanned = self.scan_entry.get().strip()
        print("raw scan:", repr(scanned))
        digits = re.findall(r"\d+", scanned)
        print("digit groups:", digits)

        serial = extract_serial(scanned)
        print("extracted serial:", serial)

        if not serial:
            self.status_label.config(text=f"invalid QR data: {scanned}", fg="#B22222")
            self.scan_entry.delete(0, tk.END)
            self.scan_entry.focus_set()
            return

        # update serial label
        self.serial_label.config(text=f"serial: {serial}")

        # clear hidden entry for next scan
        self.scan_entry.delete(0, tk.END)
        self.scan_entry.focus_set()

        # start capture
        self.start_capture(serial)

    def on_scanner_key(self, event):
        if event.keysym == "Return":
            return
        current = self.scan_entry.get()
        print("scanner key input:", repr(current))

    def _schedule_process_scan(self, delay_ms: int = 200):
        try:
            if self._scan_after_id:
                self.root.after_cancel(self._scan_after_id)
        except Exception:
            pass

        try:
            self._scan_after_id = self.root.after(delay_ms, self._process_scanned_input)
        except Exception:
            self._scan_after_id = None

    def _process_scanned_input(self):
        self._scan_after_id = None
        if self.is_capturing:
            return

        scanned = self.scan_entry.get().strip()
        if not scanned:
            return
        # avoid re-processing the same content repeatedly
        if scanned == self._last_processed_scan:
            return

        print("raw scan:", repr(scanned))
        digits = re.findall(r"\d+", scanned)
        print("digit groups:", digits)

        serial = extract_serial(scanned)
        print("extracted serial:", serial)

        if not serial:
            self.status_label.config(text=f"invalid QR data: {scanned}", fg="#B22222")
            self.scan_entry.delete(0, tk.END)
            try:
                self.scan_entry.focus_set()
            except Exception:
                pass
            return

        self._last_processed_scan = scanned

        # update serial label
        self.serial_label.config(text=f"serial: {serial}")

        # clear entry and restore focus
        self.scan_entry.delete(0, tk.END)
        try:
            self.scan_entry.focus_set()
        except Exception:
            pass

        # start capture
        self.start_capture(serial)

    def on_global_key(self, event):
        # If the scanner entry already has focus, let normal handling occur
        try:
            if self.root.focus_get() == self.scan_entry:
                return
        except Exception:
            pass

        # If it's the Return key, trigger the scanner handler
        if event.keysym == 'Return':
            # The scanner entry contains the accumulated characters we inserted below
            self._process_scanned_input()
            return 'break'

        # Insert printable characters into the hidden entry so scanner input is captured
        ch = event.char
        if ch and ch.isprintable():
            try:
                self.scan_entry.insert(tk.END, ch)
            except Exception:
                pass
            # schedule auto-processing after short idle so scanners without Return still work
            self._schedule_process_scan(200)
            return 'break'

    def start_capture(self, serial):
        if self.frame is None:
            self.status_label.config(text="camera not ready", fg="#B22222")
            return

        self.is_capturing = True
        self.status_label.config(text=f"capturing image for {serial}...", fg="#333333")

        threading.Thread(target=self.capture_sequence, args=(serial,), daemon=True).start()

    def capture_sequence(self, serial):
        try:
            frame = self.frame.copy()

            output_folder = base_dir / serial
            output_folder.mkdir(parents=True, exist_ok=True)

            filename = output_folder / f"{serial}.png"
            if filename.exists():
                suffix = 1
                while True:
                    candidate = output_folder / f"{serial}_{suffix:03d}.png"
                    if not candidate.exists():
                        filename = candidate
                        break
                    suffix += 1

            cv2.imwrite(str(filename), frame)

            # Update UI from main thread to avoid tkinter thread-safety issues
            def _on_success():
                try:
                    self.status_label.config(text=f"saved: {str(filename)}", fg="#333333")
                finally:
                    self.is_capturing = False
                    try:
                        self.scan_entry.focus_set()
                    except Exception:
                        pass

            self.root.after(0, _on_success)

        except Exception as exc:
            def _on_error():
                try:
                    self.status_label.config(text=str(exc), fg="#B22222")
                finally:
                    self.is_capturing = False
                    try:
                        self.scan_entry.focus_set()
                    except Exception:
                        pass

            self.root.after(0, _on_error)

    def close(self):
        if self.cam:
            self.cam.release()
        self.root.destroy()

def main():
    root = tk.Tk()
    app = CameraCaptureApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()

if __name__ == "__main__":
    main()
