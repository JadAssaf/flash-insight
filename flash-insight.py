import os
import sys
import time
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                            QPushButton, QLabel, QLineEdit, QTextEdit, QMessageBox,
                            QGroupBox, QGridLayout, QSpinBox, QComboBox, QHBoxLayout,
                            QDesktopWidget, QCheckBox)
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
from config import GEMINI_PROMPT, GENERATION_CONFIG, MODEL_NAME

# Load environment variables
load_dotenv()

# Configure Gemini API
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    raise ValueError("Please set GOOGLE_API_KEY in .env file")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel(MODEL_NAME)
# model = genai.GenerativeModel('gemini-2.0-pro-exp-02-05')

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

    def __init__(self, capture_area):
        super().__init__()
        self.capture_area = capture_area
        self.monitor_index = 1  # Default to primary monitor

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
                response = model.generate_content(
                    contents=[
                        GEMINI_PROMPT,
                        {"mime_type": "image/png", "data": img_bytes}
                    ],
                    generation_config=GENERATION_CONFIG
                )
                
                if not response.text:
                    raise ValueError("Empty response from Gemini API")
                
                # Clean up and validate response
                answer = response.text.strip().upper()
                if not answer:
                    raise ValueError("Empty response from API")
                    
                self.finished.emit(answer)
        except Exception as e:
            print(f"Error in ProcessingThread: {str(e)}")
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
        
        # Initialize capture area to full screen size
        screen = QApplication.primaryScreen().geometry()
        self.capture_area = QRect(0, 0, screen.width(), screen.height())
        self.processing_thread = None
        
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
        self.move(screen.width() - 600, 100)
        
        # Set minimum size but allow resizing
        self.setMinimumSize(500, 700)
        self.resize(600, 800)
        
        self.init_ui()
        self.start_preview_timer()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)
        
        # Initialize spin boxes first
        self.left_spin = QSpinBox()
        self.top_spin = QSpinBox()
        self.width_spin = QSpinBox()
        self.height_spin = QSpinBox()
        
        # Header section with title and select area button
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        
        title = QLabel("Flash Insight")
        title.setStyleSheet("""
            QLabel {
                font-size: 18px;
                font-weight: 500;
                color: #ffffff;
            }
        """)
        
        select_area_btn = QPushButton("⌘ Select Area")
        select_area_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #0a84ff;
                padding: 4px 12px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                border: 1px solid #404040;
                height: 24px;
            }
            QPushButton:hover {
                background-color: #353535;
                border-color: #454545;
            }
            QPushButton:pressed {
                background-color: #404040;
                color: #47a2ff;
            }
        """)
        select_area_btn.clicked.connect(self.start_area_selection)
        
        # Add preview toggle button
        self.preview_toggle_btn = QPushButton("👁")
        self.preview_toggle_btn.setCheckable(True)
        self.preview_toggle_btn.setChecked(True)
        self.preview_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #0a84ff;
                padding: 4px 8px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                border: 1px solid #404040;
                height: 24px;
                min-width: 24px;
            }
            QPushButton:checked {
                background-color: #404040;
                color: #86868b;
            }
            QPushButton:hover {
                background-color: #353535;
                border-color: #454545;
            }
        """)
        self.preview_toggle_btn.clicked.connect(self.toggle_preview)
        
        # Add coordinates toggle button
        self.coords_toggle_btn = QPushButton("⌘")
        self.coords_toggle_btn.setCheckable(True)
        self.coords_toggle_btn.setChecked(False)  # Start with coordinates hidden
        self.coords_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #0a84ff;
                padding: 4px 8px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                border: 1px solid #404040;
                height: 24px;
                min-width: 24px;
            }
            QPushButton:checked {
                background-color: #404040;
                color: #86868b;
            }
            QPushButton:hover {
                background-color: #353535;
                border-color: #454545;
            }
        """)
        self.coords_toggle_btn.clicked.connect(self.toggle_coordinates)
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.coords_toggle_btn)
        header_layout.addWidget(self.preview_toggle_btn)
        header_layout.addWidget(select_area_btn)
        layout.addWidget(header_widget)
        
        # Coordinates section with compact layout
        self.coords_container = QWidget()
        self.coords_container.setVisible(False)  # Start hidden
        coords_container_layout = QVBoxLayout(self.coords_container)
        coords_container_layout.setContentsMargins(0, 0, 0, 0)
        coords_container_layout.setSpacing(0)
        
        coords_widget = QWidget()
        coords_layout = QHBoxLayout(coords_widget)
        coords_layout.setSpacing(4)
        coords_layout.setContentsMargins(8, 4, 8, 4)
        
        # Create coordinate pairs with labels
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
            label.setStyleSheet("color: #86868b; font-size: 12px; min-width: 16px;")
            
            spin_box.setRange(min_val, max_val)
            spin_box.setValue(getattr(self.capture_area, 
                                   {"X": "left", "Y": "top", 
                                    "W": "width", "H": "height"}[label_text])())
            spin_box.valueChanged.connect(self.update_capture_area)
            spin_box.setStyleSheet("""
                QSpinBox {
                    background-color: #2c2c2e;
                    border: none;
                    padding: 2px 2px;
                    min-width: 50px;
                    max-width: 60px;
                    color: #ffffff;
                    font-size: 12px;
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
        
        layout.addWidget(self.coords_container)
        
        # Preview with enhanced styling
        self.preview_container = QWidget()
        preview_container_layout = QVBoxLayout(self.preview_container)
        preview_container_layout.setContentsMargins(0, 0, 0, 0)
        preview_container_layout.setSpacing(0)
        
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(300, 160)
        self.preview_label.setStyleSheet("""
            QLabel {
                background-color: #1c1c1e;
                border-radius: 8px;
                padding: 1px;
                border: 1px solid #2c2c2e;
            }
        """)
        self.preview_label.setAlignment(Qt.AlignCenter)
        preview_container_layout.addWidget(self.preview_label)
        layout.addWidget(self.preview_container)
        
        # Process button with enhanced styling
        self.capture_btn = QPushButton("⌘ Process")
        self.capture_btn.setStyleSheet("""
            QPushButton {
                background-color: #0a84ff;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                margin: 4px 0;
            }
            QPushButton:hover {
                background-color: #1f8fff;
            }
            QPushButton:pressed {
                background-color: #0070df;
            }
            QPushButton:disabled {
                background-color: #404040;
                color: #808080;
            }
        """)
        self.capture_btn.clicked.connect(self.process_capture)
        layout.addWidget(self.capture_btn)
        
        # Result area with enhanced styling
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Results will appear here")
        self.result_text.setStyleSheet("""
            QTextEdit {
                background-color: #1c1c1e;
                border: 1px solid #2c2c2e;
                border-radius: 8px;
                padding: 8px;
                font-size: 13px;
                color: #ffffff;
                selection-background-color: #0a84ff;
                selection-color: white;
            }
            QTextEdit:focus {
                border-color: #0a84ff;
            }
        """)
        self.result_text.setMinimumHeight(40)
        layout.addWidget(self.result_text)
        
        # Status label with enhanced styling
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #86868b;
                font-size: 11px;
                margin-top: 2px;
            }
        """)
        layout.addWidget(self.status_label)
        
        # Global styling
        self.setStyleSheet("""
            QMainWindow {
                background-color: #161618;
            }
            QWidget {
                color: #ffffff;
            }
        """)
        
        # Set window properties for an even more compact look
        self.setMinimumSize(360, 280)
        self.resize(380, 300)

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
        
        self.processing_thread = ProcessingThread(adjusted_area)
        self.processing_thread.monitor_index = 1  # Always use primary monitor
        self.processing_thread.finished.connect(self.handle_result)
        self.processing_thread.error.connect(self.handle_error)
        self.processing_thread.start()

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
        base_height = 280  # Base height with just title, process button, and results
        
        if self.coords_toggle_btn.isChecked():
            base_height += 50  # Height of coordinates section
            
        if self.preview_toggle_btn.isChecked():
            base_height += 180  # Height of preview section
            
        self.setMinimumSize(360, base_height)
        self.resize(380, base_height + 20)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_()) 