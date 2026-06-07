import os
import sys
import time
import gc
import psutil
import logging
from pathlib import Path
from PyQt6 import QtWidgets, QtGui, QtCore

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from hushsnap.ocr_controller import OcrController
from hushsnap.system.debug_interface import DebugInterface
from hushsnap.system.memory_utils import get_working_set_mb, fmt_memory
from hushsnap.config import get_config_path, resolve_ui_lang, ui_text

# Configure logging to capture [ANCHOR] logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def get_private_bytes_mb():
    process = psutil.Process(os.getpid())
    return process.memory_info().private / (1024 * 1024)

class BenchmarkRunner:
    def __init__(self, image_path):
        # QApplication must be initialized to simulate UI and signal bridge
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.image_path = image_path

        # Initialize HushSnap core components
        config_path = get_config_path()
        lang = resolve_ui_lang(config_path)
        translate = lambda key, **kwargs: ui_text(lang, key, **kwargs)

        user_data_dir = Path(os.getenv("APPDATA")) / "HushSnap"

        # The Controller here is complete, containing all QTimers and signal connections
        self.controller = OcrController(
            app=self.app,
            translate=translate,
            config_path=config_path,
            user_data_dir=user_data_dir
        )

        self.finished = False
        self.start_time = 0
        self.end_time = 0
        self.last_text = ""

        # Listen for OCR finish signal (the moment user sees the result)
        self.controller.bridge.signal.connect(self._on_ocr_finished)

    def _on_ocr_finished(self, response):
        # Only stop when we get a real recognition result (which has the recognition object),
        # not the "Recognizing..." status update.
        if response.recognition is not None:
            self.end_time = time.perf_counter()
            self.last_text = response.text
            self.finished = True
            logger.debug("[Benchmark] Final result received. Text length: %d", len(self.last_text or ""))
        else:
            logger.debug("[Benchmark] Status update received: %s", response.text)

    def run_benchmark(self, iterations=10):
        print(f"\n{'='*70}")
        print(f" HUSHSNAP High-Fidelity OCR Full Workflow Benchmark (Sample: {Path(self.image_path).name})")
        print(f"{'='*70}")

        results = []
        texts = set()

        for i in range(iterations):
            print(f"\n[Iteration {i+1}/{iterations}] Preparing to simulate full lifecycle...")
            gc.collect()
            time.sleep(1.0)

            initial_pv = get_private_bytes_mb()
            initial_ws = get_working_set_mb()

            self.finished = False
            self.start_time = time.perf_counter()

            DebugInterface.simulate_manual_ocr(self.controller, self.image_path)

            peak_pv = initial_pv
            peak_ws = initial_ws

            while not self.finished:
                self.app.processEvents()
                peak_pv = max(peak_pv, get_private_bytes_mb())
                peak_ws = max(peak_ws, get_working_set_mb())
                time.sleep(0.01)

            # Give UI some time to repaint for visual observation
            for _ in range(5):
                self.app.processEvents()
                time.sleep(0.02)

            duration = (self.end_time - self.start_time) * 1000
            print(f"  > Wall Time: {duration:.1f} ms")
            print(f"  > Private Bytes Peak: {peak_pv:.2f} MB")
            print(f"  > Recognized characters: {len(self.last_text or '')}")

            results.append({
                'duration': duration,
                'peak_pv': peak_pv,
                'peak_ws': peak_ws
            })
            if self.last_text:
                texts.add(self.last_text)

        print(f"\n{'='*70}")
        avg_dur = sum(r['duration'] for r in results) / len(results)
        max_pv = max(r['peak_pv'] for r in results)
        print(f" Final Summary Report:")
        print(f" - Average End-to-End Latency: {avg_dur:.1f} ms")
        print(f" - Max Private Bytes: {max_pv:.2f} MB")
        print(f" - Result Consistency: {'Consistent' if len(texts) == 1 else f'Inconsistent ({len(texts)} variants)'}")
        if texts:
             print(f" - Result Summary: {list(texts)[0][:50].replace('\n', ' ')}...")
        print(f"{'='*70}")

if __name__ == "__main__":
    img = os.path.join(str(project_root), "tools", "0.png")
    if not os.path.exists(img):
        print(f"Error: Could not find test sample {img}")
        sys.exit(1)

    runner = BenchmarkRunner(img)
    runner.run_benchmark(iterations=5)
