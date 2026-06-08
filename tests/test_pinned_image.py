import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from PIL import Image
import numpy as np
from hushsnap.ui.pinned_image import PinnedImageWindow

@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app

def test_pinned_image_initial_size(qapp):
    # Create a 200x100 image
    img = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
    win = PinnedImageWindow(img)
    
    # Aspect ratio should be 2.0
    assert win.aspect_ratio == 2.0
    
    # Window size should be logical size (physical pixels / DPR)
    dpr = win.devicePixelRatio()
    assert win.width() == int(200 / dpr)
    assert win.height() == int(100 / dpr)
    win.close()

def test_pinned_image_aspect_ratio_maintained(qapp):
    img = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    win = PinnedImageWindow(img)
    win.show()
    
    # Simulate a resize from bottom-right
    # In the test env, we can just call the logic or simulate mouse events
    # Let's test the aspect ratio maintenance
    win.resize(200, 150) # Force a non-proportional size manually (simulating external resize or bug)
    # Note: paintEvent handles centering if the window size doesn't match AR.
    # But our mouseMoveEvent logic should keep it proportional.
    
    # Let's mock a resize drag
    win._is_resizing = True
    win._active_edge = QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.RightEdge
    win._drag_start_geometry = QtCore.QRect(100, 100, 100, 100)
    
    # Simulate mouse moving to (300, 300) -> dw=200, dh=200
    # Expected: new size 200x200 (since it's 1:1)
    
    # Create a fake mouse event
    # mouseMoveEvent uses globalPosition().toPoint()
    # We need to mock event.globalPosition().toPoint()
    
    class FakeEvent:
        def buttons(self): return QtCore.Qt.MouseButton.LeftButton
        def globalPosition(self):
            class Pos:
                def toPoint(self): return QtCore.QPoint(300, 300)
            return Pos()
        def accept(self): pass

    win.mouseMoveEvent(FakeEvent())
    
    assert win.width() == 200
    assert win.height() == 200
    win.close()
