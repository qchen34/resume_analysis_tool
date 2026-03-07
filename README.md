## Resume Analysis Tool

一个用于对比用户简历和目标岗位 JD，并进行匹配度分析与简历优化的工具（当前阶段为 CLI MVP）。

### 功能（当前阶段）

- 解析简历（md/txt）与 JD 文本，构建结构化 Profile。
- 计算简历与 JD 的匹配度评分，并输出差距分析。
- 调用 LLM 根据 JD 与差距点自动修改简历，并标注修改位置。
- 将原简历 / JD / 匹配结果 / 修改后简历等信息写入数据库。

### 目录结构（MVP）

```text
example_data/              示例简历 & JD（txt/md）
src/                       核心代码
  cli/                     命令行入口
  llm/                     LLM API 封装与 prompt 模板
  models/                  数据模型（ResumeProfile 等）
  parsers/                 简历 & JD 解析
  analysis/                匹配度 & 简历重写
  db/                      数据库相关
tests/                     单元测试
```

### 上传到 GitHub

若尚未在 GitHub 建仓，可参考 **[docs/upload_to_github.md](docs/upload_to_github.md)**：先在 GitHub 创建空仓库，再在本地执行 `git init`、`git add .`、`git commit`、`git remote add origin <URL>`、`git push -u origin main`。

---

### 快速开始

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 配置环境变量：

```bash
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY 等
```

3. 使用示例数据运行分析（实现 CLI 后）：

```bash
python -m src.cli.main \
  --resume example_data/resume_example_1.md \
  --jd example_data/jd_example_1.md \
  --company 字节 \
  --role 后端开发
```

