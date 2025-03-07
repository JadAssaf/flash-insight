import os
import sys
import time
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                            QPushButton, QLabel, QLineEdit, QTextEdit, QMessageBox,
                            QGroupBox, QGridLayout, QSpinBox)
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

    def image_to_bytes(self, img):
        """Convert PIL Image to bytes."""
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        return img_byte_arr

    def run(self):
        try:
            with mss.mss() as sct:
                # Capture the specified area
                monitor = {"top": self.capture_area.top(), 
                         "left": self.capture_area.left(),
                         "width": self.capture_area.width(),
                         "height": self.capture_area.height()}
                
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
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set window flags to stay on top and be frameless
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)  # Change cursor to crosshair
        self.showFullScreen()
        
        self.start_pos = None
        self.end_pos = None
        self.is_selecting = False
        self.parent = parent

    def paintEvent(self, event):
        painter = QPainter(self)
        # Fill entire screen with very transparent overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 60))  # Reduced opacity to 60/255
        
        if self.start_pos and self.end_pos:
            # Clear the selected rectangle
            x = min(self.start_pos.x(), self.end_pos.x())
            y = min(self.start_pos.y(), self.end_pos.y())
            width = abs(self.start_pos.x() - self.end_pos.x())
            height = abs(self.start_pos.y() - self.end_pos.y())
            painter.eraseRect(x, y, width, height)
            
            # Draw blue border around selection
            pen = QPen(QColor(0, 120, 255), 2)  # Changed to blue
            painter.setPen(pen)
            painter.drawRect(x, y, width, height)

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

    def get_selection(self):
        if self.start_pos and self.end_pos:
            x = min(self.start_pos.x(), self.end_pos.x())
            y = min(self.start_pos.y(), self.end_pos.y())
            width = abs(self.start_pos.x() - self.end_pos.x())
            height = abs(self.start_pos.y() - self.end_pos.y())
            return QRect(x, y, width, height)
        return None

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flash Insight")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        
        # Initialize capture area
        self.capture_area = QRect(0, 110, 340, 670)
        self.processing_thread = None
        
        # Position window on the right side of screen
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
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title = QLabel("📸 Flash Insight")
        title.setStyleSheet("font-size: 24px; font-weight: bold; margin: 10px;")
        layout.addWidget(title)
        
        # Instructions
        instructions = QLabel("Adjust the capture area below to match your content.\nThe preview will update automatically.")
        instructions.setStyleSheet("font-size: 14px; margin: 10px;")
        layout.addWidget(instructions)
        
        # Capture Area Controls
        capture_group = QGroupBox("Capture Area")
        capture_layout = QGridLayout()
        
        # Left position
        left_label = QLabel("Left:")
        self.left_spin = QSpinBox()
        self.left_spin.setRange(0, 3000)
        self.left_spin.setValue(self.capture_area.left())
        self.left_spin.valueChanged.connect(self.update_capture_area)
        
        # Top position
        top_label = QLabel("Top:")
        self.top_spin = QSpinBox()
        self.top_spin.setRange(0, 3000)
        self.top_spin.setValue(self.capture_area.top())
        self.top_spin.valueChanged.connect(self.update_capture_area)
        
        # Width
        width_label = QLabel("Width:")
        self.width_spin = QSpinBox()
        self.width_spin.setRange(50, 1000)
        self.width_spin.setValue(self.capture_area.width())
        self.width_spin.valueChanged.connect(self.update_capture_area)
        
        # Height
        height_label = QLabel("Height:")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(50, 1000)
        self.height_spin.setValue(self.capture_area.height())
        self.height_spin.valueChanged.connect(self.update_capture_area)
        
        # Add to grid layout
        capture_layout.addWidget(left_label, 0, 0)
        capture_layout.addWidget(self.left_spin, 0, 1)
        capture_layout.addWidget(top_label, 0, 2)
        capture_layout.addWidget(self.top_spin, 0, 3)
        capture_layout.addWidget(width_label, 1, 0)
        capture_layout.addWidget(self.width_spin, 1, 1)
        capture_layout.addWidget(height_label, 1, 2)
        capture_layout.addWidget(self.height_spin, 1, 3)
        
        capture_group.setLayout(capture_layout)
        layout.addWidget(capture_group)
        
        # Preview Area
        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout()
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(300, 200)
        self.preview_label.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self.preview_label)
        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)
        
        # Capture Button
        self.capture_btn = QPushButton("📸 Process")
        self.capture_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.capture_btn.clicked.connect(self.process_capture)
        layout.addWidget(self.capture_btn)
        
        # Result Area
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Answer will appear here...")
        self.result_text.setMinimumHeight(100)
        layout.addWidget(self.result_text)
        
        # Status Label
        self.status_label = QLabel("Ready to process")
        self.status_label.setStyleSheet("color: #666; margin: 5px;")
        layout.addWidget(self.status_label)
        
        # Set window style
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f6fa;
            }
            QLabel {
                color: #2c3e50;
            }
            QSpinBox {
                padding: 5px;
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                min-width: 80px;
            }
        """)

    def start_preview_timer(self):
        self.preview_timer = QTimer()
        self.preview_timer.timeout.connect(self.update_preview)
        self.preview_timer.start(1000)  # Update every second

    def update_preview(self):
        try:
            with mss.mss() as sct:
                # Capture the specified area
                monitor = {"top": self.capture_area.top(), 
                         "left": self.capture_area.left(),
                         "width": self.capture_area.width(),
                         "height": self.capture_area.height()}
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
        
        self.processing_thread = ProcessingThread(self.capture_area)
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

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_()) 