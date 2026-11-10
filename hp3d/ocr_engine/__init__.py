"""Bundled RapidOCR (Apache-2.0) with PP-OCRv4 ONNX models.

The models live next to this package so a Windows install can read dimension
labels without downloading anything and without the Tesseract program.
OpenCV is the headless build already used by the app; this package is imported
from hp3d.ocr_dims and is not a second GUI dependency.
"""
