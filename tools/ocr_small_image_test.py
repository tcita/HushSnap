
import sys
import logging
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap
from hushsnap.ocr.ocr_service import OcrService
from hushsnap.ocr.models import OcrRequest

# Setup logging to capture the fallback message
class FallbackDetector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.fallback_triggered = {}

    def emit(self, record):
        if "falling back to recognition-only" in record.getMessage():
            # We don't easily know which file it was from the record alone 
            # without more context, but we can assume the current file being processed.
            pass

def run_test():
    app = QApplication(sys.argv)
    service = OcrService()
    
    # Custom logger to catch fallback
    logger = logging.getLogger("hushsnap.ocr.rapidocr")
    logger.setLevel(logging.DEBUG)
    
    class CaptureHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []
        def emit(self, record):
            self.messages.append(record.getMessage())
    
    handler = CaptureHandler()
    logger.addHandler(handler)

    import glob
    test_files = sorted(glob.glob("test*.png"))
    
    for filename in test_files:
        print(f"\n--- Testing {filename} ---")
        pixmap = QPixmap(filename)
        if pixmap.isNull():
            print(f"Failed to load {filename}")
            continue
            
        handler.messages = []
        request = OcrRequest(pixmap=pixmap)
        response = service.recognize(request)
        
        # Access the underlying preprocess result if possible, 
        # but OcrResponse might not have it directly. 
        # Actually OcrResponse.recognition is from the engine.
        
        print(f"Result: '{response.text}'")
        
        # We can't easily get the summary from OcrResponse without changing OcrService.
        # But we can assume it happened if the dimensions were small.
        
        fallback_used = any("falling back to recognition-only" in msg for msg in handler.messages)
        if fallback_used:
            print("STATUS: Fallback TRIGGERED (Detection FAILED)")
        else:
            print("STATUS: Detection SUCCEEDED (Padding worked!)")
            
        # Also check if it mentioned "Safe Pad"
        # The OcrService uses run_minimal_pipeline which adds steps to OcrResponse if we can access them.
        # But here we just check if detection worked.

if __name__ == "__main__":
    run_test()
