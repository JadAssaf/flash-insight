import os
import sys
import time
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                            QPushButton, QLabel, QLineEdit, QTextEdit, QMessageBox,
                            QGroupBox, QGridLayout, QSpinBox, QComboBox, QHBoxLayout,
                            QDesktopWidget, QCheckBox, QSizePolicy)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QScreen
import threading
import mss
import pytesseract
from PIL import Image
import google.generativeai as genai
from pynput import keyboard
from dotenv import load_dotenv
import numpy as np
import io
import re
from config import (
    GEMINI_PROMPT,
    GENERATION_CONFIG,
    MODEL_NAME,
    MODEL_OPTIONS,
    MODEL_NOTES,
    MODEL_DAILY_LIMITS,
    DEFAULT_MODEL_LABEL,
)

# Load environment variables
load_dotenv()

REQUEST_LOG_PATH = os.path.join(os.path.dirname(__file__), "flash-insight-requests.log")
REQUEST_COOLDOWN_SECONDS = 4
MODEL_CACHE = {}


def log_api_event(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} pid={os.getpid()} {message}\n"
    print(line, end="", flush=True)
    try:
        with open(REQUEST_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(line)
    except OSError:
        pass


def get_model(model_name):
    if model_name not in MODEL_CACHE:
        MODEL_CACHE[model_name] = genai.GenerativeModel(model_name)
    return MODEL_CACHE[model_name]


def extract_response_text(response):
    try:
        text = response.text
        if text:
            return text
    except Exception:
        pass

    parts_text = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if text:
                parts_text.append(text)

    return "\n".join(parts_text).strip()

# Configure Gemini API
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    raise ValueError("Please set GOOGLE_API_KEY in .env file")

genai.configure(api_key=GOOGLE_API_KEY)

def pil_image_to_qimage(pil_image):
    """Convert PIL Image to QImage."""
    # Convert PIL image to RGB if it's not
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    
    # Get image data
    data = pil_image.tobytes("raw", "RGB")
    
    # Create QImage from data
    qimage = QImage(data, pil_image.size[0], pil_image.size[1], 
                   pil_image.size[0] * 3, QImage.Format_RGB888)
    return qimage

class ProcessingThread(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, capture_area, model_name):
        super().__init__()
        self.capture_area = capture_area
        self.monitor_index = 1  # Default to primary monitor
        self.model_name = model_name

    def image_to_bytes(self, img):
        """Convert PIL Image to bytes."""
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        return img_byte_arr

    def run(self):
        try:
            with mss.mss() as sct:
                # Capture the specified area with monitor index
                monitor = {
                    "top": self.capture_area.top(), 
                    "left": self.capture_area.left(),
                    "width": self.capture_area.width(),
                    "height": self.capture_area.height(),
                    "monitor": self.monitor_index
                }
                
                # Verify capture area is valid
                if monitor["width"] <= 0 or monitor["height"] <= 0:
                    raise ValueError("Invalid capture area dimensions")
                
                screenshot = sct.grab(monitor)
                
                # Convert to PIL Image
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                
                # Verify image content
                if img.size[0] == 0 or img.size[1] == 0:
                    raise ValueError("Captured image is empty")
                
                # Convert image to bytes
                img_bytes = self.image_to_bytes(img)
                
                # Process with Gemini
                request_id = f"{int(time.time() * 1000)}-{threading.get_ident()}"
                log_api_event(
                    f"START request_id={request_id} model={self.model_name} "
                    f"area={monitor['left']},{monitor['top']},{monitor['width']}x{monitor['height']} "
                    f"image={img.size[0]}x{img.size[1]} bytes={len(img_bytes)}"
                )
                response = get_model(self.model_name).generate_content(
                    contents=[
                        GEMINI_PROMPT,
                        {"mime_type": "image/png", "data": img_bytes}
                    ],
                    generation_config=GENERATION_CONFIG
                )
                log_api_event(f"SUCCESS request_id={request_id}")

                response_text = extract_response_text(response)
                if not response_text:
                    raise ValueError("Empty response from Gemini API")
                
                # Clean up and validate response
                answer = response_text.strip().upper()
                if not answer:
                    raise ValueError("Empty response from API")
                    
                self.finished.emit(answer)
        except Exception as e:
            print(f"Error in ProcessingThread: {str(e)}")
            log_api_event(f"ERROR error={repr(str(e))}")
            self.error.emit(str(e))

class SelectionOverlay(QWidget):
    def __init__(self, parent=None, screen_geometry=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        
        # Store screen geometry for coordinate translation
        self.screen_geometry = screen_geometry or QApplication.primaryScreen().geometry()
        self.setGeometry(self.screen_geometry)
        
        self.start_pos = None
        self.end_pos = None
        self.is_selecting = False
        self.parent = parent
        
        # Add centered help text overlay
        self.help_label = QLabel("Click and drag to select an area\nPress ESC to cancel", self)
        self.help_label.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 180);
                background-color: rgba(0, 0, 0, 80);
                padding: 15px 25px;
                border-radius: 8px;
                font-size: 15px;
                font-weight: 500;
            }
        """)
        self.help_label.adjustSize()
        
        # Center the help text
        self.center_help_label()
        self.help_label.show()

    def center_help_label(self):
        """Center the help label in the widget."""
        geometry = self.geometry()
        x = (geometry.width() - self.help_label.width()) // 2
        y = (geometry.height() - self.help_label.height()) // 2
        self.help_label.move(x, y)

    def resizeEvent(self, event):
        """Handle resize events to keep help text centered."""
        super().resizeEvent(event)
        self.center_help_label()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            if self.parent:
                self.parent.show()
                self.parent.activateWindow()

    def paintEvent(self, event):
        painter = QPainter(self)
        # Fill entire screen with very transparent overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 30))  # More transparent background
        
        if self.start_pos and self.end_pos:
            # Get selection rectangle
            x = min(self.start_pos.x(), self.end_pos.x())
            y = min(self.start_pos.y(), self.end_pos.y())
            width = abs(self.start_pos.x() - self.end_pos.x())
            height = abs(self.start_pos.y() - self.end_pos.y())
            
            # Draw semi-transparent white for the selection
            selection_color = QColor(255, 255, 255, 1)  # Almost fully transparent
            painter.fillRect(x, y, width, height, selection_color)
            
            # Draw blue border around selection
            pen = QPen(QColor(0, 120, 255, 200), 2)  # Semi-transparent blue
            painter.setPen(pen)
            painter.drawRect(x, y, width, height)
            
            # Draw selection dimensions
            text = f"{width} × {height}"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(x + 5, y - 5, text)
            
            # Hide help text when selecting
            self.help_label.hide()

    def mousePressEvent(self, event):
        self.start_pos = event.pos()
        self.end_pos = event.pos()
        self.is_selecting = True
        self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        self.is_selecting = False
        if self.parent and isinstance(self.parent, MainWindow):
            self.parent.capture_area = self.get_selection()
            self.parent.show()
            self.parent.activateWindow()  # Ensure main window comes to front
            self.parent.selection_complete()  # New method to handle completion
        self.close()

    def get_global_pos(self, local_pos):
        """Convert local coordinates to global screen coordinates."""
        return local_pos + self.screen_geometry.topLeft()

    def get_selection(self):
        if self.start_pos and self.end_pos:
            # Convert to global coordinates
            global_start = self.get_global_pos(self.start_pos)
            global_end = self.get_global_pos(self.end_pos)
            
            x = min(global_start.x(), global_end.x())
            y = min(global_start.y(), global_end.y())
            width = abs(global_start.x() - global_end.x())
            height = abs(global_start.y() - global_end.y())
            return QRect(x, y, width, height)
        return None

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flash Insight")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.window_width = 360
        
        # Initialize capture area to full screen size
        screen = QApplication.primaryScreen().geometry()
        self.capture_area = QRect(0, 0, screen.width(), screen.height())
        self.processing_thread = None
        self.last_request_started_at = 0
        self.active_model_label = next(
            (label for label, model_name in MODEL_OPTIONS.items() if model_name == MODEL_NAME),
            DEFAULT_MODEL_LABEL,
        )
        self.active_model_name = MODEL_OPTIONS[self.active_model_label]
        
        # Get the total virtual desktop size across all monitors
        total_rect = QRect()
        for screen in QApplication.screens():
            total_rect = total_rect.united(screen.geometry())
        
        # Update spin box ranges to accommodate all monitors
        self.max_width = total_rect.width()
        self.max_height = total_rect.height()
        self.max_x = total_rect.right()
        self.max_y = total_rect.bottom()
        
        # Position window on the right side of primary screen
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - self.window_width - 24, 84)
        
        # Set fixed size - window cannot be resized
        self.setFixedWidth(self.window_width)
        
        self.init_ui()
        self.updateWindowSize()
        self.start_preview_timer()

    def compact_primary_button_style(self, font_size=12):
        return f"""
            QPushButton {{
                background-color: #0a84ff;
                color: white;
                padding: 0 10px;
                border-radius: 8px;
                font-size: {font_size}px;
                font-weight: 700;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #1f8fff;
            }}
            QPushButton:pressed {{
                background-color: #0070df;
            }}
            QPushButton:disabled {{
                background-color: #404040;
                color: #808080;
            }}
        """

    def compact_toggle_style(self):
        return """
            QPushButton {
                background-color: #242428;
                color: #8d8d93;
                padding: 0 8px;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 600;
                border: 1px solid #3a3a40;
            }
            QPushButton:checked {
                background-color: rgba(10, 132, 255, 0.18);
                color: #4ca3ff;
                border: 1px solid rgba(10, 132, 255, 0.35);
            }
            QPushButton:hover {
                background-color: #2b2b30;
            }
        """

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setSpacing(8)
        self.main_layout.setContentsMargins(12, 12, 12, 10)

        self.left_spin = QSpinBox()
        self.top_spin = QSpinBox()
        self.width_spin = QSpinBox()
        self.height_spin = QSpinBox()

        title = QLabel("Flash Insight")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f7f7f8;")
        self.model_summary_label = QLabel(self.active_model_label)
        self.model_summary_label.setStyleSheet("color: #8d8d93; font-size: 12px; font-weight: 600;")

        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        title_layout.addWidget(title)
        title_layout.addStretch()
        title_layout.addWidget(self.model_summary_label)
        self.main_layout.addLayout(title_layout)

        controls_card = QWidget()
        controls_card.setObjectName("controlsCard")
        controls_card.setStyleSheet("""
            QWidget#controlsCard {
                background-color: #1c1c1f;
                border: 1px solid #2d2d31;
                border-radius: 8px;
            }
        """)
        controls_layout = QVBoxLayout(controls_card)
        controls_layout.setContentsMargins(10, 10, 10, 10)
        controls_layout.setSpacing(8)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)

        self.model_combo = QComboBox()
        self.model_combo.addItems(list(MODEL_OPTIONS.keys()))
        self.model_combo.setCurrentText(self.active_model_label)
        self.model_combo.setFixedHeight(32)
        self.model_combo.setStyleSheet("""
            QComboBox {
                background-color: #242428;
                color: #ffffff;
                border: 1px solid #3a3a40;
                border-radius: 8px;
                padding: 0 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox QAbstractItemView {
                background-color: #1c1c1e;
                color: #ffffff;
                selection-background-color: #0a84ff;
                outline: 0;
            }
        """)
        self.model_combo.setToolTip(
            "Sorted by published free daily request limits.\n"
            "Gemini 3.1 Lite: 500 RPD on this project\n"
            "Gemini 2.5/3 Flash: 20 RPD on this project\n"
            "Gemma is omitted because it is not supported by this app path"
        )
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        controls_row.addWidget(self.model_combo, 1)

        select_area_btn = QPushButton("Area")
        select_area_btn.setFixedSize(68, 32)
        select_area_btn.setStyleSheet(self.compact_primary_button_style(font_size=12))
        select_area_btn.clicked.connect(self.start_area_selection)
        controls_row.addWidget(select_area_btn)

        self.capture_btn = QPushButton("Run")
        self.capture_btn.setFixedSize(58, 32)
        self.capture_btn.setStyleSheet(self.compact_primary_button_style(font_size=12))
        self.capture_btn.clicked.connect(self.process_capture)
        controls_row.addWidget(self.capture_btn)
        controls_layout.addLayout(controls_row)

        toggles_row = QHBoxLayout()
        toggles_row.setContentsMargins(0, 0, 0, 0)
        toggles_row.setSpacing(6)

        self.preview_toggle_btn = QPushButton("Prev")
        self.preview_toggle_btn.setCheckable(True)
        self.preview_toggle_btn.setChecked(True)
        self.preview_toggle_btn.setFixedSize(58, 28)
        self.preview_toggle_btn.setStyleSheet(self.compact_toggle_style())
        self.preview_toggle_btn.clicked.connect(self.toggle_preview)
        toggles_row.addWidget(self.preview_toggle_btn)

        self.coords_toggle_btn = QPushButton("Bounds")
        self.coords_toggle_btn.setCheckable(True)
        self.coords_toggle_btn.setChecked(False)
        self.coords_toggle_btn.setFixedSize(70, 28)
        self.coords_toggle_btn.setStyleSheet(self.compact_toggle_style())
        self.coords_toggle_btn.clicked.connect(self.toggle_coordinates)
        toggles_row.addWidget(self.coords_toggle_btn)
        toggles_row.addStretch()
        controls_layout.addLayout(toggles_row)
        self.main_layout.addWidget(controls_card)

        self.coords_container = QWidget()
        self.coords_container.setObjectName("coordsCard")
        self.coords_container.setVisible(False)
        self.coords_container.setStyleSheet("""
            QWidget#coordsCard {
                background-color: #1c1c1f;
                border: 1px solid #2d2d31;
                border-radius: 8px;
            }
        """)
        coords_container_layout = QVBoxLayout(self.coords_container)
        coords_container_layout.setContentsMargins(10, 10, 10, 10)
        coords_container_layout.setSpacing(6)

        coords_widget = QWidget()
        coords_layout = QHBoxLayout(coords_widget)
        coords_layout.setSpacing(4)
        coords_layout.setContentsMargins(0, 0, 0, 0)

        coord_pairs = [
            ("X", self.left_spin, -self.max_x, self.max_x),
            ("Y", self.top_spin, -self.max_y, self.max_y),
            ("W", self.width_spin, 50, self.max_width),
            ("H", self.height_spin, 50, self.max_height)
        ]

        for label_text, spin_box, min_val, max_val in coord_pairs:
            container = QWidget()
            container_layout = QHBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(2)

            label = QLabel(label_text)
            label.setStyleSheet("color: #86868b; font-size: 11px; min-width: 10px;")

            spin_box.setRange(min_val, max_val)
            spin_box.setValue(getattr(self.capture_area,
                                   {"X": "left", "Y": "top",
                                    "W": "width", "H": "height"}[label_text])())
            spin_box.valueChanged.connect(self.update_capture_area)
            spin_box.setStyleSheet("""
                QSpinBox {
                    background-color: #242428;
                    border: 1px solid #3a3a40;
                    border-radius: 7px;
                    padding: 2px 4px;
                    min-width: 48px;
                    max-width: 54px;
                    color: #ffffff;
                    font-size: 11px;
                }
                QSpinBox::up-button, QSpinBox::down-button {
                    width: 0;
                    border: none;
                }
                QSpinBox::up-arrow, QSpinBox::down-arrow {
                    border: none;
                    width: 0;
                    height: 0;
                }
            """)

            container_layout.addWidget(label)
            container_layout.addWidget(spin_box)
            coords_layout.addWidget(container)

        coords_container_layout.addWidget(coords_widget)
        self.main_layout.addWidget(self.coords_container)

        self.preview_container = QWidget()
        self.preview_container.setObjectName("previewCard")
        self.preview_container.setStyleSheet("""
            QWidget#previewCard {
                background-color: #1c1c1f;
                border: 1px solid #2d2d31;
                border-radius: 8px;
            }
        """)
        preview_container_layout = QVBoxLayout(self.preview_container)
        preview_container_layout.setContentsMargins(10, 10, 10, 10)
        preview_container_layout.setSpacing(0)

        self.preview_label = QLabel()
        self.preview_label.setFixedSize(316, 96)
        self.preview_label.setStyleSheet("""
            QLabel {
                background-color: #111214;
                border-radius: 8px;
                padding: 0;
                border: 1px solid #2f3136;
                color: #6e6e73;
                font-size: 12px;
            }
        """)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setText("Preview")
        preview_container_layout.addWidget(self.preview_label, 0, Qt.AlignCenter)
        self.main_layout.addWidget(self.preview_container)

        results_card = QWidget()
        results_card.setObjectName("resultsCard")
        results_card.setStyleSheet("""
            QWidget#resultsCard {
                background-color: #1c1c1f;
                border: 1px solid #2d2d31;
                border-radius: 8px;
            }
        """)
        results_layout = QVBoxLayout(results_card)
        results_layout.setContentsMargins(10, 10, 10, 10)
        results_layout.setSpacing(6)

        results_header = QLabel("Result")
        results_header.setStyleSheet("color: #f7f7f8; font-size: 12px; font-weight: 600;")
        results_layout.addWidget(results_header)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFocusPolicy(Qt.NoFocus)
        self.result_text.setPlaceholderText("Answer appears here.")
        self.result_text.setStyleSheet("""
            QTextEdit {
                background-color: #111214;
                border: 1px solid #2f3136;
                border-radius: 8px;
                padding: 8px;
                font-size: 13px;
                font-weight: 600;
                color: #ffffff;
                selection-background-color: #0a84ff;
                selection-color: white;
            }
            QTextEdit:focus {
                border-color: #2f3136;
            }
            QScrollBar:vertical {
                border: none;
                background: #1c1c1e;
                width: 8px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #404040;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                border: none;
                background: none;
            }
        """)
        self.result_text.setFixedHeight(56)
        self.result_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        results_layout.addWidget(self.result_text)
        self.main_layout.addWidget(results_card)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #86868b; font-size: 11px; padding-left: 2px;")
        self.main_layout.addWidget(self.status_label)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #161618;
            }
            QWidget {
                color: #ffffff;
            }
        """)

    def start_preview_timer(self):
        self.preview_timer = QTimer()
        self.preview_timer.timeout.connect(self.update_preview)
        self.preview_timer.start(1000)  # Update every second

    def update_preview(self):
        try:
            with mss.mss() as sct:
                # Use only the primary monitor (index 1 in mss)
                monitor = {
                    "top": self.capture_area.top(),
                    "left": self.capture_area.left(),
                    "width": self.capture_area.width(),
                    "height": self.capture_area.height(),
                    "monitor": 1  # Primary monitor
                }
                
                screenshot = sct.grab(monitor)
                
                # Convert to QPixmap and display
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                qimg = pil_image_to_qimage(img)
                pixmap = QPixmap.fromImage(qimg)
                
                # Scale pixmap to fit preview label while maintaining aspect ratio
                scaled_pixmap = pixmap.scaled(
                    self.preview_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.preview_label.setPixmap(scaled_pixmap)
        except Exception as e:
            print(f"Preview error: {str(e)}")  # Debug print
            self.preview_label.setText(f"Preview error: {str(e)}")

    def update_capture_area(self):
        self.capture_area = QRect(
            self.left_spin.value(),
            self.top_spin.value(),
            self.width_spin.value(),
            self.height_spin.value()
        )
        self.update_preview()

    def process_capture(self):
        if self.processing_thread and self.processing_thread.isRunning():
            self.status_label.setText("Already processing...")
            return

        now = time.monotonic()
        seconds_since_last_request = now - self.last_request_started_at
        if seconds_since_last_request < REQUEST_COOLDOWN_SECONDS:
            wait_seconds = int(REQUEST_COOLDOWN_SECONDS - seconds_since_last_request) + 1
            self.status_label.setText(f"Wait {wait_seconds}s before retrying")
            self.status_label.setStyleSheet("color: #FFA500;")
            return

        self.last_request_started_at = now
        self.capture_btn.setEnabled(False)
        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet("color: #FFA500;")  # Orange for processing
        
        # Create capture area for primary monitor
        adjusted_area = QRect(
            self.capture_area.left(),
            self.capture_area.top(),
            self.capture_area.width(),
            self.capture_area.height()
        )
        
        self.processing_thread = ProcessingThread(adjusted_area, self.active_model_name)
        self.processing_thread.monitor_index = 1  # Always use primary monitor
        self.processing_thread.finished.connect(self.handle_result)
        self.processing_thread.error.connect(self.handle_error)
        self.processing_thread.start()

    def on_model_changed(self, model_label):
        self.active_model_label = model_label
        self.active_model_name = MODEL_OPTIONS[model_label]
        self.model_summary_label.setText(model_label)
        note = MODEL_NOTES.get(model_label, self.active_model_name)
        daily_limit = MODEL_DAILY_LIMITS.get(model_label, "limit varies")
        self.status_label.setText(f"{model_label} ({daily_limit}): {note}")
        self.status_label.setStyleSheet("color: #86868b;")

    def handle_result(self, result):
        self.result_text.setText(result)
        self.capture_btn.setEnabled(True)
        self.status_label.setText("✅ Processing complete")
        self.status_label.setStyleSheet("color: #4CAF50;")  # Green for success

    def handle_error(self, error_msg):
        self.result_text.setText(f"Error: {error_msg}")
        self.capture_btn.setEnabled(True)
        self.status_label.setText("❌ Error occurred")
        self.status_label.setStyleSheet("color: #f44336;")  # Red for error

    def closeEvent(self, event):
        if hasattr(self, 'preview_timer'):
            self.preview_timer.stop()
        event.accept()

    def start_area_selection(self):
        """Start the manual area selection process."""
        self.hide()  # Hide main window during selection
        
        # Create selection overlay only for primary screen
        primary_screen = QApplication.primaryScreen()
        self.selection_overlay = SelectionOverlay(self, primary_screen.geometry())
        self.selection_overlay.show()

    def selection_complete(self):
        """Handle completion of manual area selection."""
        if hasattr(self, 'selection_overlay'):
            self.selection_overlay.close()
            
            selection = self.selection_overlay.get_selection()
            if selection:
                self.left_spin.setValue(selection.x())
                self.top_spin.setValue(selection.y())
                self.width_spin.setValue(selection.width())
                self.height_spin.setValue(selection.height())
                self.update_capture_area()
            
        self.show()
        self.activateWindow()

    def toggle_coordinates(self):
        """Toggle coordinates visibility."""
        self.coords_container.setVisible(self.coords_toggle_btn.isChecked())
        self.updateWindowSize()
        
    def toggle_preview(self):
        """Toggle preview visibility."""
        self.preview_container.setVisible(self.preview_toggle_btn.isChecked())
        self.updateWindowSize()
        
    def updateWindowSize(self):
        """Update window size based on visible components."""
        self.layout().activate()
        content_height = self.centralWidget().sizeHint().height()
        self.setFixedSize(self.window_width, content_height)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    QTimer.singleShot(250, window.raise_)
    QTimer.singleShot(250, window.activateWindow)
    sys.exit(app.exec_()) 
