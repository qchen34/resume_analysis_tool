from __future__ import annotations

from pathlib import Path
from typing import List

import pdfplumber
from PIL import Image
import pytesseract


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
PDF_EXTENSIONS = {".pdf"}


def extract_text_from_pdf(path: Path) -> str:
    """从 PDF 文档中提取纯文本（优先用于非扫描型 PDF 简历/JD）。"""
    texts: List[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                texts.append(txt)
    return "\n\n".join(texts).strip()


def extract_text_from_image(path: Path, lang: str = "chi_sim+eng") -> str:
    """使用本地 Tesseract OCR 从图片中提取文本。"""
    image = Image.open(str(path))
    text = pytesseract.image_to_string(image, lang=lang)
    return text.strip()


def extract_text_auto(path: Path) -> str:
    """根据文件后缀自动选择 PDF 或图片 OCR。"""
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return extract_text_from_pdf(path)
    if suffix in IMAGE_EXTENSIONS:
        return extract_text_from_image(path)
    raise ValueError(f"不支持的文件类型用于 OCR：{path}")


def find_ocr_sources(example_dir: Path) -> List[Path]:
    """
    在 example_data 目录中查找可用于 OCR 的文件（图片/PDF）。
    返回按文件名排序的路径列表。
    """
    candidates: List[Path] = []
    for p in example_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() in IMAGE_EXTENSIONS | PDF_EXTENSIONS:
            candidates.append(p)
    return sorted(candidates, key=lambda p: p.name)

