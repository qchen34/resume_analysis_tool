# Input 目录 — JD 与简历入口

将待分析的 **JD** 和 **简历** 文件放入本目录即可作为分析入口。

## 命名约定

- **JD**：文件名（不含后缀）中包含 `jd` 或 `job`，例如：`daotong_jd.jpeg`、`job_desc.pdf`
- **简历**：文件名中包含 `resume` 或 `cv`，例如：`my_resume.pdf`、`CV_example.pdf`

各类型只取**按文件名排序后的第一个**；支持格式：`.pdf`、`.png`、`.jpg`、`.jpeg`、`.bmp`、`.tiff`。

## 使用方式

- **命令行**：运行 `python main.py` 时，从本目录（或 .env 指定路径）读取 JD 与简历。可在 `.env` 中设置：
  - `INPUT_DIR`：JD/简历所在目录（默认 `input`，可为相对路径或绝对路径）
  - `INPUT_JD_PATH`：直接指定 JD 文件路径（如 `input/daotong_jd.jpeg` 或绝对路径）
  - `INPUT_RESUME_PATH`：直接指定简历文件路径
  不设置则从 `INPUT_DIR` 下按上述命名约定自动选取。
- **Web 前端**：默认使用上述同一入口；若在页面上传了文件，则以上传内容为准。
