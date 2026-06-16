import sys
import threading
import time
import re
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
import cv2
from PIL import Image, ImageTk
import easyocr

base_dir = Path("Solar_Cells")

back_camera_default = 1
front_camera_default = 2

# preview sizes
preview_width = 1000
preview_height = 1000

# capture resolution
capture_width = 1920
capture_height = 1080


def get_opencv_backend():
    # choose the right backend depending on the os
    if sys.platform.startswith("win"):
        return cv2.CAP_DSHOW
    if sys.platform.startswith("darwin"):
        return cv2.CAP_AVFOUNDATION
    if sys.platform.startswith("linux"):
        return cv2.CAP_V4L2
    return 0


def open_camera(index: int):
    # open a camera at the given index using the backend
    backend = get_opencv_backend()
    capture = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
    if not capture.isOpened():
        return None

    # set resolution
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, capture_width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_height)
    return capture


def find_serial_roi(frame):
    # try to find an area in the frame that looks like where the serial number is
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    h, w = frame.shape[:2]
    candidates = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        # ignore tiny shapes
        if cw < 140 or ch < 40:
            continue
        aspect = cw / float(ch)
        # ignore near‑square shapes
        if 0.8 <= aspect <= 1.2:
            continue
        # check if it's more on the right side
        center_x = x + cw / 2.0
        right_bias = center_x > w * 0.45
        candidates.append((x, y, cw, ch, right_bias, cw * ch))

    if not candidates:
        return None

    # sort so the best match ends up last
    candidates.sort(key=lambda item: (item[4], item[5]))
    x, y, cw, ch, _, _ = candidates[-1]
    return x, y, cw, ch


def extract_serial(text):
    # pull out numbers from the recognized text
    tokens = re.findall(r"\d+", text)
    for token in tokens:
        # check if a standalone token is 11 digits
        candidate = token.strip()
        if len(candidate) == 11:
            return candidate

    # fallback: maybe all digits are separated
    joined = "".join(tokens)
    if len(joined) == 11:
        return joined

    return None


class CameraCaptureApp:
    def __init__(self, root: tk.Tk):
        # setup main window stuff
        self.root = root
        self.root.title("Back + Front Camera Capture")
        self.root.update_idletasks()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        win_w = int(screen_w * 0.75)
        win_h = int(screen_h * 0.75)
        win_w = min(max(win_w, 800), 2200)
        win_h = min(max(win_h, 600), 1400)
        self.win_w = win_w
        self.win_h = win_h
        self.root.geometry(f"{self.win_w}x{self.win_h}")
        self.root.minsize(700, 500)
        self.root.resizable(False, False)

        # make sure save folder exists
        base_dir.mkdir(parents=True, exist_ok=True)

        # load easyocr reader
        self.reader = easyocr.Reader(["en"], gpu=False)

        # open both cameras
        self.back_cam = open_camera(back_camera_default)
        self.front_cam = open_camera(front_camera_default)
        self.back_frame = None
        self.front_frame = None
        self.previews_ready = False
        self.is_capturing = False

        # build layout
        self.build_interface()
        self.initialize_camera_status()

        # start preview updating
        if self.back_cam and self.front_cam:
            self.root.after(50, self.update_preview)

    def build_interface(self):
        # build the gui components
        title = tk.Label(self.root, text="Back + Front Camera Capture", font=("Segoe UI", 18, "bold"))
        title.pack(pady=(16, 8))

        subtitle = tk.Label(
            self.root,
            text="Focus both cameras before capture. Back image serial is used as the save key.",
            font=("Segoe UI", 11),
            fg="#333333",
        )
        subtitle.pack(pady=(0, 12))

        previews_frame = ttk.Frame(self.root)
        previews_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        previews_frame.grid_columnconfigure(0, weight=1)
        previews_frame.grid_columnconfigure(1, weight=1)

        # calculate preview box sizes
        header_footer_space = 220
        avail_w = self.win_w - 48
        avail_h = max(200, self.win_h - header_footer_space)
        preview_w = min(preview_width, int(avail_w / 2) - 10)
        preview_h = min(preview_height, int(avail_h))
        self.preview_w = preview_w
        self.preview_h = preview_h

        # back preview canvas
        self.back_preview = tk.Canvas(
            previews_frame,
            width=self.preview_w,
            height=self.preview_h,
            bg="#111111",
            highlightthickness=0,
        )
        self.back_preview.create_text(
            int(self.preview_w / 2),
            int(self.preview_h / 2),
            text="Back camera preview",
            fill="white",
            font=("Segoe UI", 16, "bold"),
            tags="placeholder",
        )
        self.back_preview.grid(row=0, column=0, padx=(0, 10), pady=4)

        # front preview canvas
        self.front_preview = tk.Canvas(
            previews_frame,
            width=self.preview_w,
            height=self.preview_h,
            bg="#111111",
            highlightthickness=0,
        )
        self.front_preview.create_text(
            int(self.preview_w / 2),
            int(self.preview_h / 2),
            text="Front camera preview",
            fill="white",
            font=("Segoe UI", 16, "bold"),
            tags="placeholder",
        )
        self.front_preview.grid(row=0, column=1, padx=(10, 0), pady=4)

        # capture button
        self.capture_button = tk.Button(
            self.root,
            text="Capture Back + Front",
            font=("Segoe UI", 13, "bold"),
            bg="#007ACC",
            fg="white",
            activebackground="#005A9E",
            activeforeground="white",
            padx=20,
            pady=12,
            command=self.on_capture_pressed,
            state="disabled",
        )
        self.capture_button.pack(pady=(16, 8))

        # status text
        self.status_label = tk.Label(self.root, text="Initializing cameras...", font=("Segoe UI", 11), fg="#555555")
        self.status_label.pack(pady=(0, 6))

        # footer
        footer_frame = ttk.Frame(self.root)
        footer_frame.pack(fill="x", padx=16, pady=(6, 12))

        self.camera_info_label = tk.Label(
            footer_frame,
            text=f"Back index: {back_camera_default} · Front index: {front_camera_default}",
            font=("Segoe UI", 9),
            fg="#555555",
        )
        self.camera_info_label.pack(side="left")

    def initialize_camera_status(self):
        # check if cameras opened properly
        missing = []
        if self.back_cam is None:
            missing.append("back")
        if self.front_cam is None:
            missing.append("front")

        if missing:
            self.capture_button.config(state="disabled")
            self.status_label.config(
                text=f"Unable to open {', '.join(missing)} camera(s). Check device connections and permissions.",
                fg="#B22222",
            )
        else:
            self.capture_button.config(state="disabled")
            self.status_label.config(text="Cameras ready. Waiting for preview frames...", fg="#333333")

    def update_preview(self):
        # refresh camera previews
        updated = False

        if self.back_cam:
            success, frame = self.back_cam.read()
            if success and frame is not None:
                self.back_frame = frame
                self.show_frame(frame, self.back_preview)
                updated = True

        if self.front_cam:
            success, frame = self.front_cam.read()
            if success and frame is not None:
                self.front_frame = frame
                self.show_frame(frame, self.front_preview)
                updated = True

        # enable capture only when both previews working
        if updated and self.back_frame is not None and self.front_frame is not None and not self.previews_ready:
            self.previews_ready = True
            if self.back_cam and self.front_cam:
                self.capture_button.config(state="normal")
            self.status_label.config(text="Previews ready. Press capture when aligned.", fg="#333333")

        self.root.after(30, self.update_preview)

    def show_frame(self, frame, container):
        # convert and show frame on gui canvas
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        scale = min(preview_width / width, preview_height / height)
        resized = cv2.resize(rgb, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
        image = Image.fromarray(resized)
        photo = ImageTk.PhotoImage(image)
        container.delete("IMG")
        container.create_image(0, 0, anchor="nw", image=photo, tags="IMG")
        container.image = photo

    def on_capture_pressed(self):
        # start the capture process
        if self.is_capturing:
            return

        if self.back_frame is None or self.front_frame is None:
            self.update_status("Camera previews are not ready yet.", error=True)
            return

        self.is_capturing = True
        self.capture_button.config(state="disabled")
        self.update_status("Capturing back camera image...", error=False)
        threading.Thread(target=self.capture_sequence, daemon=True).start()

    def capture_sequence(self):
        # threaded capture workflow
        try:
            back_frame = self.back_frame.copy()
            front_frame = self.front_frame.copy()

            # read serial from back image
            serial = self.read_serial_from_back(back_frame)
            if serial is None:
                raise RuntimeError(
                    "Unable to detect serial number from the back camera image. Focus the serial number and try again."
                )

            # prepare save folder
            output_folder = base_dir / serial
            output_folder.mkdir(parents=True, exist_ok=True)
            front_path = output_folder / f"{serial}_front.png"

            self.update_status("Capturing front camera image...", error=False)
            time.sleep(0.2)
            if not cv2.imwrite(str(front_path), front_frame):
                raise RuntimeError("Failed to save the front camera image.")

            self.update_status(f"Saved images under Solar_Cells/{serial}", error=False)
            messagebox.showinfo(
                "Capture Complete",
                f"Saved image to: \n{front_path}",
            )
        except Exception as exc:
            self.update_status(str(exc), error=True)
            messagebox.showerror("Capture Failed", str(exc))
        finally:
            self.is_capturing = False
            self.capture_button.config(state="normal")

    def update_status(self, message: str, error: bool = False):
        # update status line with red/warning or normal text
        self.status_label.config(text=message, fg="#B22222" if error else "#333333")

    def read_serial_from_back(self, frame):
        # find region of serial number and run ocr
        roi = find_serial_roi(frame)
        if roi is None:
            return None

        x, y, w, h = roi
        cropped = frame[y:y + h, x:x + w]
        rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
        results = self.reader.readtext(rgb, detail=1)
        text = " ".join(item[1] for item in results)
        return extract_serial(text)

    def close(self):
        # cleanup before window closes
        if self.back_cam:
            self.back_cam.release()
        if self.front_cam:
            self.front_cam.release()
        self.root.destroy()


def main():
    # main entrypoint
    root = tk.Tk()
    app = CameraCaptureApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
