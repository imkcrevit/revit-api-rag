# 致谢与源代码归属 (Acknowledgements and Source Attribution)

本 RAG 项目的部分代码和文件实现，参考或直接使用了来自 Jeremy Tammik 的 [RevitSdkSamples](https://github.com/jeremytammik/RevitSdkSamples) 项目。

该项目是基于 **MIT License** 授权的优秀资源，为本项目提供了巨大的帮助。

原始项目的版权和授权详情，请参阅本项目根目录下的 `NOTICE` 和 `LICENSE` 文件。

# Acknowledgements and Source Attribution

Portions of this RAG project are based on code and files from Jeremy Tammik's [RevitSdkSamples](https://github.com/jeremytammik/RevitSdkSamples).

The original project is an invaluable resource licensed under the **MIT License**.

The original copyright notice and full license text can be found in the `NOTICE` and `LICENSE` files in this project's root directory.

# Revit API RAG

基于 Revit API 文档和 SDK 示例代码的 RAG（检索增强生成）系统，帮助开发者快速生成 Revit C# 插件代码。

## 2025.03 重大更新

### 项目重构
- 旧版代码（AutoDL 本地训练）已迁移至 [`legacy/`](./legacy/) 目录保留 — **[查看旧版完整文档及逻辑图](./legacy/README.md)**
- 新版 Pipeline 迁移至 **Google Colab** 运行，利用免费 GPU + Google Drive 存储
- 项目结构重新组织，分离 `pipeline/`（训练）和 `server/`（部署）

### 架构概览

```
revit-api-rag/
├── pipeline/              # 数据处理 & 训练（Colab 运行）
│   ├── run_all.ipynb      # 主训练 Notebook
│   ├── api_parser/        # API 文档解析
│   │   ├── parse_chm.py   # CHM HTML → 结构化数据
│   │   └── quality_agent.py  # LLM 质量剪枝
│   ├── sdk_parser/        # SDK 代码解析
│   │   ├── extract.py     # .cs 源码提取
│   │   └── quality_agent.py  # Golden code 生成
│   ├── embedder/          # 向量化
│   │   └── embed.py       # SQLite → ChromaDB
│   ├── retriever.py       # 两层检索器
│   └── llm_client.py      # LLM 客户端（OpenRouter）
├── config/                # 配置
│   └── config.yaml
├── data/                  # 生成的数据库文件
│   ├── sqlite/            # SQLite（API + SDK 结构化数据）
│   └── legacy_db/         # 旧版数据库（保留参考）
├── server/                # Web 服务（部署用）
├── legacy/                # 旧版代码（AutoDL 时期）
└── docs/                  # 文档
```

## 数据处理 Pipeline

> V1 旧版 Pipeline 的详细逻辑图和 Prompt 设计请参考 [legacy/README.md](./legacy/README.md)

### API 文档处理

![RevitAPI 解析流程](./docs/images/RevitAPI.png)

```
Revit 2026 API CHM
    ↓ 7z 解压
HTML 文件 (~4000+ 页)
    ↓ parse_chm.py 解析
结构化数据 (name, summary, syntax, parameters, remarks...)
    ↓ quality_agent.py (Gemini Flash 剪枝)
revit_api.db (SQLite)
    ↓ embed.py
ChromaDB 向量库 (API)
```

- **解析**: 从 CHM 解压的 HTML 中提取类名、方法签名、参数、备注等结构化信息
- **剪枝**: 使用 Gemini Flash 对低质量/冗余条目进行清洗，保留高质量 API 参考
- **存储**: SQLite 存储全量结构化数据，ChromaDB 存储 `name + summary` 的语义向量

![数据库结构](./docs/images/RevitEembeddingDatabse.png)

### SDK 代码处理

![RevitSDK 解析流程](./docs/images/RevitSDK.png)

```
Revit SDK Samples (~200+ 项目)
    ↓ extract.py (tree-sitter 解析)
.cs 源码 → 类/方法提取
    ↓ quality_agent.py (Gemini 生成 Golden Code)
SDK golden code (JSON)
    ↓ SQLite 存储
revit_api.db (sdk_code 表)
    ↓ embed.py
ChromaDB 向量库 (Code)
```

- **提取**: 使用 tree-sitter 解析 C# 源码，提取类定义、方法签名、关键代码块
- **Golden Code**: LLM 阅读项目 README + 源码，生成精炼的示例代码
- **存储**: SQLite 存储 golden code + 元数据，ChromaDB 存储代码语义向量

### Embedding 策略
- **API 向量化**: `name + summary` → OpenRouter Embedding API → ChromaDB
- **Code 向量化**: `golden_code summary` → OpenRouter Embedding API → ChromaDB
- **检索**: 查询 → ChromaDB 语义搜索 (top_k) → SQLite 回查全文内容
- **重排序**: 可选 rerank 模型对结果精排

## RAG 检索 & 代码生成

![RAG 主流程](./docs/images/workflow.png)

```
用户查询: "创建结构柱"
    ↓ Query Rewriting (LLM 提取 API 关键词)
改写: "structural column, NewFamilyInstance, FamilySymbol, Level"
    ↓ ChromaDB 语义搜索
API 结果: 15 条  |  SDK 结果: 5 条
    ↓ 上下文组装
Prompt = API Reference + SDK Code + User Query
    ↓ LLM 流式生成 (Gemini Flash / Claude)
输出: C# 插件代码（简洁模式 / 完整模式）
```

![V2 完整工作流](./docs/images/RAG-Workflow-Update.jpg)

## 训练产出文件

训练完成后产出 4 个数据库文件，已上传至 [GitHub Release (v2.0-data)](https://github.com/imkcrevit/revit-api-rag/releases/tag/v2.0-data)：

| 文件 | 大小 | 说明 |
|------|------|------|
| `revit_api.db` | 33 MB | API 结构化数据（name, summary, syntax, parameters, remarks...） |
| `revit_sdk.db` | 932 KB | SDK Golden Code + 元数据 |
| `chromadb_api.tar.gz` | 286 MB | API 语义向量库（27596 条 embedding） |
| `chromadb_code.tar.gz` | 1.8 MB | SDK 代码语义向量库（153 条 embedding） |

下载后放置到对应目录：
```
data/
├── sqlite/
│   ├── revit_api.db          # API 结构化数据
│   └── revit_sdk.db          # SDK golden code
├── chromadb_api/              # 解压 chromadb_api.tar.gz
└── chromadb_code/             # 解压 chromadb_code.tar.gz
```

## 环境要求

- **训练环境**: Google Colab (免费版即可)
- **Revit 版本**: 2026
- **LLM**: OpenRouter (Gemini Flash / Claude / GPT)
- **Embedding**: OpenRouter Embedding API
- **存储**: Google Drive (ChromaDB + SQLite ~100MB)

## 快速开始

### 训练（Colab）
1. 打开 `pipeline/run_all.ipynb` in Google Colab
2. 按顺序运行所有 Cell
3. 生成的数据库文件自动保存到 Google Drive

### 部署（本地/GCP）
```bash
# 安装依赖
pip install -r requirements-server.txt

# 启动服务
python -m server.main
```

---

## 更新日志

### 2025.03 — V2 重大重构
- 旧版代码迁移至 [`legacy/`](./legacy/) — [查看旧版文档](./legacy/README.md)
- Pipeline 迁移至 Google Colab
- 新增 SDK Pipeline V2（tree-sitter + LLM golden code）
- 新增 Quality Agent（API + SDK 数据剪枝）
- 新增 Query Rewriting（查询改写）
- 新增流式代码生成（简洁模式 / 完整模式）

### 2025.10.23 — SDK Embedding ([详细文档](./legacy/README.md#2025-10-23-update))
- 使用 tree-sitter 提取 SDK 代码块
- LLM 生成 clean code JSON
- 数据存储至 SQLite

### 初始版本 — API RAG ([详细文档](./legacy/README.md#revit-api-rag-v1))
- Revit API CHM 解析 → SQLite + ChromaDB
- SDK 代码解析 → ChromaDB
- 基础 RAG 检索 + 代码生成
