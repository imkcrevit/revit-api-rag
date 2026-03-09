# revit-api-rag

Revit API RAG（检索增强生成）系统 — 帮助设计师用自然语言生成专业的 Revit C# 插件代码。

## 项目结构

```
revit-api-rag/
├── pipeline/          ← 离线数据准备（Colab 中运行）
│   ├── api_parser/    ← 解析 Revit API CHM 文档
│   ├── sdk_parser/    ← 解析 SDK 示例代码
│   ├── embedder/      ← 向量化 & 存入 ChromaDB
│   └── knowledge_builder/  ← 构建领域知识库
│
├── server/            ← 在线 RAG 服务（GCP 服务器运行）
│   ├── app/           ← FastAPI 后端
│   └── frontend/      ← Gradio 前端
│
├── config/            ← 配置文件
├── data/              ← 数据目录（向量库/SQLite/知识库）
└── docs/              ← 文档
```

## 快速开始

### 阶段一：数据准备（Colab）

1. 在 Google Colab 中打开 `pipeline/run_all.ipynb`
2. 上传 Revit API CHM 和 SDK 文件
3. 配置 `config/config.yaml` 中的 embedding 提供商
4. 运行全部 cell，生成向量库

### 阶段二：部署服务（GCP）

1. 创建 GCP e2-small 实例
2. 上传 `data/` 目录到服务器
3. `pip install -r requirements-server.txt`
4. `python -m server.app.main`

## 配置

复制 `config/config.example.yaml` 为 `config/config.yaml`，填入你的 API Key。

## License

MIT
