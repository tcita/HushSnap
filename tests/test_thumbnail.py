import pytest
from PyQt6 import QtCore, QtWidgets
from PIL import Image

from hushsnap.ui.thumbnail import ThumbnailWindow
from hushsnap.constants import THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT

@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app

def test_thumbnail_fixed_size(qapp):
    """The thumbnail window must always have a fixed card size regardless of the input image size."""
    # Landscape image (wide)
    img_wide = Image.new("RGBA", (1000, 200), (255, 0, 0, 255))
    win_wide = ThumbnailWindow(img_wide)
    
    assert win_wide.card_width == THUMBNAIL_WIDTH
    assert win_wide.card_height == THUMBNAIL_HEIGHT
    assert win_wide.width() == THUMBNAIL_WIDTH + 2 * win_wide.shadow_padding
    assert win_wide.height() == THUMBNAIL_HEIGHT + 2 * win_wide.shadow_padding
    
    # Portrait image (tall)
    img_tall = Image.new("RGBA", (200, 1000), (0, 255, 0, 255))
    win_tall = ThumbnailWindow(img_tall)
    
    assert win_tall.card_width == THUMBNAIL_WIDTH
    assert win_tall.card_height == THUMBNAIL_HEIGHT
    
    # Tiny image
    img_tiny = Image.new("RGBA", (10, 10), (0, 0, 255, 255))
    win_tiny = ThumbnailWindow(img_tiny)
    
    assert win_tiny.card_width == THUMBNAIL_WIDTH
    assert win_tiny.card_height == THUMBNAIL_HEIGHT

def test_thumbnail_scaling_and_centering(qapp):
    """The screenshot must be scaled preserving aspect ratio and centered inside the fixed card_rect."""
    # Test with wide image (1000x500 -> aspect ratio 2:1)
    # Target size is 240x150.
    # Scaled width should be 240. Scaled height should be 120.
    img = Image.new("RGBA", (1000, 500), (255, 255, 255, 255))
    win = ThumbnailWindow(img)
    
    assert win.scaled_pixmap.width() == 240
    assert win.scaled_pixmap.height() == 120
    
    # Check centering coordinates (relative to window, including shadow_padding = 12):
    # px = 12 + (240 - 240) // 2 = 12
    # py = 12 + (150 - 120) // 2 = 27
    assert win.pixmap_rect.x() == 12
    assert win.pixmap_rect.y() == 27
    assert win.pixmap_rect.width() == 240
    assert win.pixmap_rect.height() == 120

    # Test with tall image (200x800 -> aspect ratio 1:4)
    # Target size is 240x150.
    # Scaled height should be 150. Scaled width should be 150 * 0.25 = 37.
    img_tall = Image.new("RGBA", (200, 800), (255, 255, 255, 255))
    win_tall = ThumbnailWindow(img_tall)
    
    assert win_tall.scaled_pixmap.height() == 150
    assert win_tall.scaled_pixmap.width() == 37
    
    # px = 12 + (240 - 37) // 2 = 113
    # py = 12 + (150 - 150) // 2 = 12
    assert win_tall.pixmap_rect.y() == 12
    assert win_tall.pixmap_rect.x() == 12 + (THUMBNAIL_WIDTH - win_tall.scaled_pixmap.width()) // 2

def test_thumbnail_manager_single_instance(qapp):
    """ThumbnailManager must close the previous thumbnail when showing a new one."""
    from hushsnap.ui.thumbnail import thumbnail_manager
    
    img1 = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    img2 = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    
    thumbnail_manager._do_show(img1)
    assert len(thumbnail_manager._windows) == 1
    win1 = thumbnail_manager._windows[0]
    
    thumbnail_manager._do_show(img2)
    assert len(thumbnail_manager._windows) == 1
    win2 = thumbnail_manager._windows[0]
    
    assert win1 != win2
