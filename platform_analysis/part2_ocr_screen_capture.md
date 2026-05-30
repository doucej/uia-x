# Part 2: OCR & Screen Capture Dependencies

## Function: `read_screen_text()` (lines 4612-4774)

This is the most Windows-dependent function in the Quicken skill.

---

## Windows-Only Dependencies

### 1. Screen Capture - PIL.ImageGrab
```python
from PIL import ImageGrab
img = ImageGrab.grab(bbox=(left, top, right, bottom))
```
- **Windows-only**: `ImageGrab.grab()` only works on Windows
- macOS equivalent: Use `screencapture` CLI tool, Quartz CGEvent, or PyObjC `CGWindowListCreateImage`

### 2. OCR Engine - Windows Native API
```python
from winsdk.windows.graphics.imaging import BitmapDecoder
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

engine = OcrEngine.try_create_from_user_profile_languages()
result = await engine.recognize_async(bitmap)
```
- **Windows-only**: `winsdk` is Microsoft's UWP SDK, not cross-platform
- macOS equivalent: Use Tesseract OCR (CLI), Apple Vision Framework, or cloud-based OCR

### 3. Async Event Loop Handling
```python
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = None
if loop and loop.is_running():
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = pool.submit(lambda: asyncio.run(_ocr(img))).result(timeout=10.0)
else:
    result = asyncio.run(_ocr(img))
```
- **Cross-platform issue**: Pattern works but needs macOS-compatible OCR implementation

### 4. Win32 Window Bounds Retrieval
```python
r = ctypes.wintypes.RECT()
user32.GetWindowRect(root_hwnd, ctypes.byref(r))
left, top, right, bottom = r.left, r.top, r.right, r.bottom
```
- **Windows-only**: Uses HWND-based coordinate system
- macOS equivalent: Use AX frame + window bounds conversion

### 5. Dollar Sign OCR Fix (Quicken-Specific)
```python
_dollar_re = re.compile(r"(?<![.\d])5(\d{1,3}(?:,\d{3})*\.\d{2})(?!\d)")
entry["text"] = _dollar_re.sub(r"$\1", entry["text"])
```
- **Windows-specific artifact**: Windows OCR reads Quicken's proprietary "$" glyph as "5"
- macOS may have different OCR artifacts needing different regex fixes

---

## Missing macOS Screen/OCR Infrastructure

### macOS Screen Capture Options

| Library | Platform | Status in Codebase |
|------|------|------|
| `PIL.ImageGrab` | Windows only | Used in bridge_ext |
| `screencapture` (CLI) | macOS | Not used |
| `pyobjc-core.Quartz.CGWindowListCreateImage` | macOS | Not used |
| `mss` | Cross-platform | Not used |
| `pyautogui` | Cross-platform | Not used |

### macOS OCR Options

| Library | Platform | Status in Codebase |
|------|------|------|
| `winsdk.windows.media.ocr` | Windows only | Used in bridge_ext |
| `pytesseract` | Cross-platform | Not used |
| `vision` (Apple Vision) | macOS | Not used |
| Google Vision API | Cloud | Not used |
| AWS Textract | Cloud | Not used |

---

## Code That Must Be Abstracted

1. **`read_screen_text()` function** - Entire function needs platform detection
2. **`ImageGrab.grab()` call** (line 4689) - Replace with platform-specific capture
3. **`winsdk.*` imports** (lines 4697-4699) - Replace with platform-specific OCR
4. **`OcrEngine` usage** (line 4709) - Replace with Tesseract or VisionFramework
5. **Win32 rectangle retrieval** (lines 4665-4675) - Replace with AX coordinate system

---

## Proposed macOS Implementation Pattern

```python
def read_screen_text_macos(bridge, *, region: str = "") -> dict[str, Any]:
    """Capture text using macOS Vision Framework or Tesseract."""
    import subprocess
    from CoreGraphics import CGWindowListCreateImage
    from Quartz import kCGWindowListOptionOnScreenOnly
    
    # Get region bounds (from AX frame)
    if region:
        left, top, right, bottom = map(int, region.split(","))
    else:
        # Use attached window's AX frame
        ax_frame = get_frame(_get_root())
        left, top = ax_frame.get("left", 0), ax_frame.get("top", 0)
        right, bottom = ax_frame.get("right", 0), ax_frame.get("bottom", 0)
    
    # Capture screenshot via Quartz
    img_data = CGWindowListCreateImage(
        (left, top, right-left, bottom-top),
        kCGWindowListOptionOnScreenOnly,
        None,
        kCGWindowImageDefault
    )
    
    # OCR via Tesseract (preferred) or Vision Framework
    try:
        from pytesseract import image_to_string
        lines = image_to_string(img_data, lang='eng')
    except ImportError:
        # Fallback to Vision Framework
        from Foundation import NSData, NSURL
        from Vision import VNRequest, VNRecognizeTextRequest
        
        lines = _vision_ocr(img_data)
    
    # Parse OCR output to structured format
    return _parse_ocr_output(lines)
```

---

## Summary: Must Fix Before macOS Port

| Issue | Severity | Fix Strategy |
|------|----------|---------------|
| `ImageGrab.grab()` on Windows only | **HIGH** | Bridge to `CGWindowListCreateImage` or CLI |
| `winsdk.*` imports Windows only | **HIGH** | Bridge to Tesseract or Vision Framework |
| `ctypes.windll.*` HWND references | **HIGH** | Bridge to AX element IDs |
| Dollar sign OCR fix hardcoded | MEDIUM | Make regex configurable per platform |
| `asyncio`/`concurrent` pattern | LOW | Keep, but use macOS-compatible OCR |
