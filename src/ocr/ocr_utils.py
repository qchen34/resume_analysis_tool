from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import List, Optional, Tuple

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


def _get_ocr_cache_dir() -> Path:
    """
    OCR 文本缓存目录：
    - 统一放在 <项目根>/test_outputs/cache_ocr
    - 便于 CLI 与 Streamlit 共用，避免重复消耗 Flash OCR token
    """
    # 当前文件位于 src/ocr/，向上两级即为项目根
    base_dir = Path(__file__).resolve().parents[2]
    cache_root = base_dir / "test_outputs" / "cache_ocr"
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root


def _hash_file(path: Path) -> str:
    """对文件内容做 sha256，作为 OCR 缓存 key。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text_with_flash(path: Path) -> str:
    """
    使用当前环境配置的 Flash 模型（GEMINI_MODEL）对图片或 PDF 进行文字提取。
    适用于 JD 截图、简历图片、扫描版 PDF 等，统一走多模态识别。

    为节省 OCR token，本函数增加一层文件级缓存：
    - 以文件内容哈希为 key，将纯文本结果缓存到 test_outputs/cache_ocr 下；
    - 同一份文件（内容未变）多次调用时直接返回缓存结果，不再调用 Gemini。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"OCR 文件不存在: {path}")

    cache_dir = _get_ocr_cache_dir()
    file_key = _hash_file(path)
    cache_path = cache_dir / f"{file_key}.txt"
    if cache_path.is_file():
        # 命中 OCR 文本缓存：避免再次调用 Flash 模型
        print(f"[OCR] 命中文本缓存（{path.name} → {cache_path.name}）")
        return cache_path.read_text(encoding="utf-8")

    client, model = _get_client_and_model()
    uploaded = client.files.upload(file=str(path))
    response = client.models.generate_content(
        model=model,
        contents=[_EXTRACT_PROMPT, uploaded],
    )
    text = (response.text or "").strip()
    # 写入缓存（失败时忽略错误，不影响主流程）
    try:
        cache_path.write_text(text, encoding="utf-8")
        print(f"[OCR] 已写入文本缓存（{path.name} → {cache_path.name}）")
    except Exception:
        pass
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


def extract_jd_structured(path: Path) -> Tuple[str, "JobProfile", str]:
    """
    OCR 提取 JD 文本后立即用 LLM 结构化，保证公司名、岗位等字段准确，供 Tavily 等下游使用。
    返回 (原始文本, JobProfile, 结构化 JSON 字符串)。
    """
    from src.models.schemas import JobProfile
    from src.parsers.jd_parser import parse_jd_with_llm

    path = Path(path)
    raw_text = extract_text_auto(path)
    job_profile, json_str = parse_jd_with_llm(raw_text)
    return raw_text, job_profile, json_str


def extract_resume_structured(path: Path) -> Tuple[str, "ResumeProfile", str]:
    """
    OCR 提取简历文本后立即用 LLM 结构化。
    返回 (原始文本, ResumeProfile, 结构化 JSON 字符串)。
    """
    from src.models.schemas import ResumeProfile
    from src.parsers.resume_parser import parse_resume_with_llm

    path = Path(path)
    raw_text = extract_text_auto(path)
    resume_profile, json_str = parse_resume_with_llm(raw_text)
    return raw_text, resume_profile, json_str


def get_input_dir(base_dir: Path) -> Path:
    """
    获取 JD/简历的入口目录。优先使用 .env 中的 INPUT_DIR：
    - 未设置时：项目根目录下的 input/
    - 设置为相对路径（如 input 或 my_files）：相对于项目根
    - 设置为绝对路径（如 /Users/yourname/jobs）：直接使用
    """
    base_dir = Path(base_dir)
    raw = (os.getenv("INPUT_DIR") or "").strip()
    if not raw:
        return base_dir / "input"
    p = Path(raw)
    return p if p.is_absolute() else (base_dir / raw)


def _resolve_path(base_dir: Path, raw: str) -> Optional[Path]:
    """将 .env 中的路径解析为绝对 Path：绝对路径直接用，相对路径相对于 base_dir。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (Path(base_dir) / raw)


def get_jd_and_resume_from_input(base_dir: Path) -> Tuple[Path | None, Path | None]:
    """
    解析 JD 与简历文件路径，优先使用 .env 中的 INPUT_JD_PATH、INPUT_RESUME_PATH；
    未设置时从 input 目录（见 get_input_dir）按文件名约定发现。
    - 若同时设置了 INPUT_JD_PATH 与 INPUT_RESUME_PATH：使用这两条路径（可为相对项目根或绝对路径）。
    - 否则：在 input 目录下找文件名含 jd/job 的作为 JD、含 resume/cv 的作为简历。
    返回 (jd_path 或 None, resume_path 或 None)。
    """
    base_dir = Path(base_dir)
    jd_env = os.getenv("INPUT_JD_PATH")
    resume_env = os.getenv("INPUT_RESUME_PATH")
    if jd_env and resume_env:
        jd_path = _resolve_path(base_dir, jd_env)
        resume_path = _resolve_path(base_dir, resume_env)
        if jd_path and jd_path.is_file() and resume_path and resume_path.is_file():
            return jd_path, resume_path
        # 若指定了但文件不存在，仍回退到目录发现

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
