# 数模论文工作台前端

`frontend/` 是无构建链的原生 ES module 单页应用，只与本机 FastAPI 服务通信。

## 核心模块与概念问答

- `#/` 首页：显示工作流入口和后端健康状态。
- `#/paper` 数模论文：集中承载题面与附件、建模方案、求解脚本、论文骨架、一致性校验、交付流程和论文模型评测对照。
- `#/review` 文献综述：选择多篇 `.docx` 或 `.pdf`，调用 `/api/v1/review/generate`，生成综述预览、文献方法评测对照和可编辑 Word 文件。
- `#/ledger` 项目台账：只读查看产物、运行事件和网络审计记录。
- `#/concept` 概念问答：调用 `/api/v1/rag/ask`，先检索唯一 `artifacts/rag/index/` 知识库，再完成文本或图文回答。

顶栏渲染上述业务页和独立的概念问答页，首页通过品牌标识返回；旧的单独检索页面不再出现在导航中。

## 启动

在仓库根目录运行：

```powershell
.venv\Scripts\python.exe -m mmagent.cli serve
```

然后打开服务输出的本地地址。没有 Node、npm 或打包步骤。

也可以在仓库根目录执行 `python run.py`，它会幂等初始化数据库并启动同一个服务。
启动后先访问 `http://127.0.0.1:8000/api/v1/health`，返回 `{"ok":true}` 再进行页面验收。

## 文献综述输入

前端读取本地 `.docx` 或 `.pdf` 文件并编码为 JSON 后提交。后端使用 `python-docx` 和 `pypdf` 提取段落、表格或 PDF 文字层，按确定性规则归纳标题、主题词和摘要片段，不调用模型、不联网。结果写入 `artifacts/literature_reviews/`，并以 `literature_review` 类型登记到项目台账。

单次最多 20 篇，每篇最多 20 MB，总大小最多 80 MB；文件名不能包含路径片段或大小写重名；旧式二进制 `.doc` 暂不支持。扫描 PDF 没有文字层时仍可生成空正文综述，但需要人工核对和补充。

## 评测对照

论文页和文献综述页都提供相同格式的 JSON 评测数据输入。每行至少包含 `method`、`metric`、`value`，可用 `baseline: true` 标记基线，也可用 `higher_is_better` 明确指标方向。系统会按指标分组计算最佳方法、相对基线差值、改进比例和综合结论。

## 前端约定

- `assets/app.js` 是唯一路由入口。
- `assets/core/api.js` 是唯一 HTTP 出网封装。
- 页面只导出 `render(root)`，动态文本必须经过 `esc()`。
- 资源全部使用仓库内相对路径，不引入 CDN 或外部字体。
