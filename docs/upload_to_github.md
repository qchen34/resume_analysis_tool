# 将项目上传到 GitHub

## 第一步：在 GitHub 上创建新仓库

1. 打开 [GitHub](https://github.com/new)。
2. **Repository name**：例如 `resume_analysis_tool`。
3. **Description**（可选）：如「简历与 JD 匹配分析、简历优化工具」。
4. 选择 **Public**。
5. **不要**勾选 "Add a README file"、"Add .gitignore"、"Choose a license"（本地已有）。
6. 点击 **Create repository**。
7. 创建完成后，记下仓库地址，例如：  
   `https://github.com/你的用户名/resume_analysis_tool.git`

---

## 第二步：在本地提交并推送

在项目根目录（`resume_analysis_tool`）下执行：

```bash
# 1. 初始化仓库（若尚未初始化）
git init

# 2. 添加所有文件（.gitignore 会排除 .env、data/、venv、test_outputs 等）
git add .

# 3. 首次提交
git commit -m "Initial commit: resume & JD analysis, matching, rewrite, DB"

# 4. 可选：将默认分支改为 main（与 GitHub 默认一致）
git branch -M main

# 5. 添加远程仓库（把下面的 URL 换成你在第一步得到的地址）
git remote add origin https://github.com/你的用户名/resume_analysis_tool.git

# 6. 推送到 GitHub
git push -u origin main
```

若 GitHub 仓库已存在 README 等文件，先执行 `git pull origin main --rebase` 再 `git push -u origin main`。

---

## 当前 .gitignore 已排除的内容

- `.env`（敏感配置）
- `venv/`、`.venv/`、`env/`
- `data/`、`*.db`、`*.sqlite`
- `test_outputs/*`（报告产出，保留目录及 `.gitkeep`）
- `example_data/resume_example_1.md`、`example_data/jd_example_1.md`（若含真实信息）
- `__pycache__/`、`.pytest_cache/`、`build/`、`dist/`

上传前请确认没有把 API Key 或真实简历/JD 提交进仓库。
