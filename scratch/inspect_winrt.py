import winrt.windows.graphics.imaging as imaging

print("--- imaging.SoftwareBitmap ---")
print(dir(imaging.SoftwareBitmap))

print("\n--- imaging.BitmapDecoder ---")
print(dir(imaging.BitmapDecoder))

try:
    # Check if there is a static convert method and its help
    print("\n--- Help for SoftwareBitmap.convert ---")
    print(help(imaging.SoftwareBitmap.convert))
except Exception as e:
    print(f"Could not get help for convert: {e}")
