import logging
from PyQt6 import QtGui, QtCore
from ..ocr_controller import OcrController

logger = logging.getLogger(__name__)

class DebugInterface:
    """
    Internal interface for high-fidelity simulation of user workflows.
    Bypasses the actual CaptureWindow but feeds the resulting pixmap 
    into the exact same pipeline used during manual operation.
    """

    @staticmethod
    def simulate_manual_ocr(controller: OcrController, image_path: str):
        """
        Simulates: Hotkey -> Capture Completed -> Start OCR -> UI Popup.
        """
        pixmap = QtGui.QPixmap(image_path)
        if pixmap.isNull():
            raise FileNotFoundError(f"Simulation failed: Could not load {image_path}")

        logger.info("[DebugInterface] Simulating manual OCR workflow for: %s", image_path)

        # 1. Simulate hotkey trigger intent
        controller.schedule_ocr()

        # 2. Simulate capture window closing and returning Pixmap
        # This is the original entry point for OcrController to receive images,
        # which will trigger all subsequent signals.
        controller.handle_capture_completed(pixmap)
