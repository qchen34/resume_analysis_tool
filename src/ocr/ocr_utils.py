from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
PDF_EXTENSIONS = {".pdf"}
INPUT_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

# 使用 Flash 模型做图片/PDF 文字提取时的提示词
_EXTRACT_PROMPT = """请将本图片或文档中的全部文字，按原有顺序、段落和换行逐字提取，保留排版结构。
要求：
- 只输出提取出的纯文本，不要总结、不要改写、不要补全。
- 中英文、数字、标点均需完整保留。
- 若为多页文档，按页顺序输出，页与页之间可空一行。"""


def _get_client_and_model():
    """获取当前环境中的 Gemini 客户端与默认模型（Flash）。"""
    from src.llm.client import llm_client

    client = llm_client._client
    model = getattr(llm_client, "_default_model", "gemini-3-flash-preview")
    return client, model


def extract_text_with_flash(path: Path) -> str:
    """
    使用当前环境配置的 Flash 模型（GEMINI_MODEL）对图片或 PDF 进行文字提取。
    适用于 JD 截图、简历图片、扫描版 PDF 等，统一走多模态识别。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"OCR 文件不存在: {path}")

    client, model = _get_client_and_model()
    uploaded = client.files.upload(file=str(path))
    response = client.models.generate_content(
        model=model,
        contents=[_EXTRACT_PROMPT, uploaded],
    )
    text = (response.text or "").strip()
    return text


def extract_text_from_pdf(path: Path) -> str:
    """从 PDF 中提取文本，使用 Flash 模型做多模态识别（适用于扫描版/图片型 PDF）。"""
    return extract_text_with_flash(path)


def extract_text_from_image(path: Path) -> str:
    """从图片中提取文本，使用 Flash 模型做 OCR。"""
    return extract_text_with_flash(path)


def extract_text_auto(path: Path) -> str:
    """根据文件后缀自动选择：图片与 PDF 均使用 Flash 模型提取文本，作用于 JD 与简历解析。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS or suffix in IMAGE_EXTENSIONS:
        return extract_text_with_flash(path)
    raise ValueError(f"不支持的文件类型用于 OCR：{path}")


def get_input_dir(base_dir: Path) -> Path:
    """项目根下的 input 目录，作为 JD 与简历的入口目录。"""
    return Path(base_dir) / "input"


def get_jd_and_resume_from_input(base_dir: Path) -> Tuple[Path | None, Path | None]:
    """
    从 input 目录中按文件名约定发现 JD 与简历文件。
    - JD：文件名（不含后缀）包含 jd 或 job（不区分大小写），取按文件名排序的第一个。
    - 简历：文件名包含 resume 或 cv（不区分大小写），取按文件名排序的第一个。
    返回 (jd_path 或 None, resume_path 或 None)。
    """
    inp = get_input_dir(base_dir)
    if not inp.is_dir():
        return None, None
    jd_candidates: List[Path] = []
    resume_candidates: List[Path] = []
    for p in inp.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in INPUT_EXTENSIONS:
            continue
        stem = p.stem.lower()
        if "jd" in stem or "job" in stem:
            jd_candidates.append(p)
        if "resume" in stem or "cv" in stem:
            resume_candidates.append(p)
    jd_path = sorted(jd_candidates, key=lambda x: x.name)[0] if jd_candidates else None
    resume_path = sorted(resume_candidates, key=lambda x: x.name)[0] if resume_candidates else None
    return jd_path, resume_path


def find_ocr_sources(example_dir: Path) -> List[Path]:
    """
    在指定目录中查找可用于 OCR 的文件（图片/PDF）。
    返回按文件名排序的路径列表。
    """
    candidates: List[Path] = []
    for p in Path(example_dir).iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() in INPUT_EXTENSIONS:
            candidates.append(p)
    return sorted(candidates, key=lambda p: p.name)
