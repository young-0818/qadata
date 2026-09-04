# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

「问数」(qadata)：对话式数据分析 Agent——自然语言问题 → SQL → 只读沙箱执行 → 可信答案。LangGraph 编排，BIRD 基准评测，求职作品项目，**评测驱动开发**（每个里程碑要有准确率数字）。权威设计文档在 `docs/superpowers/specs/2026-09-02-qadata-agent-design.md`（注意 `docs/` 整体被 .gitignore，仅本地存在；里程碑移交清单见其 §11.1）。

## 常用命令

```bash
# 环境：.venv 为 editable 安装（pip install -e ".[dev]"），git bash 无需激活即可用：
.venv/Scripts/python -m pytest                 # 全部测试（当前 175 个，必须全绿）
.venv/Scripts/python -m pytest tests/test_eval_match.py::test_xxx   # 单个测试
.venv/Scripts/python -m ruff check src tests   # lint（另有 ruff format）

# CLI（需仓库根目录有 .env，参照 .env.example：DEEPSEEK_API_KEY 必填；
# QADATA_BASE_URL / QADATA_MODEL 可切换任意 OpenAI 兼容端点，当前 .env 为 deepseek-v4-flash-0731）
qadata ask data/bird/dev/dev_databases/financial/financial.sqlite "去年销售额是多少" --evidence "销售额 = …"
qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --sample 100
qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --ids tests/m2_compare_ids.json  # 固定题集
```

- eval 逐题结果写 `runs/eval-last.jsonl`，节点级 token/延迟写 `runs/traces.jsonl`（带 run_id/question_id 归属）；两者与 `data/` 均不入库。抽样跑分固定 seed=42 可复现。
- 评测参考值：M1 基线 BIRD dev-100 **63.0%**（qwen3.7-flash）；M2 50 题配对 **58.0%**（deepseek-v4-flash-0731，含模型切换混杂，README 有注明与归因）。冒烟 10 题固定集回归门槛在 `tests/smoke_baseline.json`——**与模型绑定，换模型必须重建基线**。
- 评测花费红线：单次对比 ≤ ¥3；测试不得依赖真实 API/网络。

## 架构

六节点＋双条件边的自纠错状态机（M2）：

```
question → understand → explore → generate → execute ──成功──→ verify ──通过──→ respond
                              ↑                  │                  │
                              └──失败且有预算─────┘   └──可疑且有预算──┘
                    （预算耗尽：失败→respond 兜底；可疑→respond 带校验标注）
```

- 重试预算不新增状态键：`len(attempts)` 即账本（默认 3，`Settings.retry_budget`）；generate 提取失败也写 `SqlAttempt(sql="", error=…)` 入账。
- `graph/state.py`：`AgentState` TypedDict（total=False），**无 reducer，各键整值覆盖**；含 `original_question`（改写前原问题）、`verify_note`（可疑原因，None=通过）。
- `graph/nodes.py`：`make_nodes(llm, tracer, settings)` 节点工厂，闭包注入依赖以便测试替换假模型；respond 失败路径不调 LLM（永不编造）；**答案稳定性回退**：执行失败耗尽且最后尝试为执行失败形态时，respond 重执行 `_last_good_sql(attempts)`（从 attempts 派生，不新增状态键）作答并标注。
- `graph/verify.py`：规则校验器（空结果/聚合异常），确定性不烧 token；M3 精准化：「列出全部/所有/有哪些」类问题豁免空结果判可疑，截断不再触发重试（由 respond 标注）。
- `graph/error_hints.py`：SQL 错误分类→中文修复建议（规则版，零 token），进失败历史段。
- `graph/prompts.py`：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，动态内容只能追加尾部），含口径规则（禁格式化输出、只选问题需要的列）；`format_failure_history` 失败历史段（含修复建议）；`strip_conclusion_prefix` 防复读（剥多层）。
- `graph/build.py`：`_route_after_execute`/`_route_after_verify` 为纯函数路由（独立测试）；`run_question` 是最外层守护——任何裸异常收敛为诚实失败的 `Answer`。
- 沙箱四层：① `tools/db.py::open_readonly` 只读连接唯一入口（URI 转义）② `tools/sqlguard.py` sqlglot 语句校验（解析→单语句→根白名单→表名校验）③ `tools/executor.py` 资源层（progress handler 5s 超时中断＋双行数上限：显示 `max_rows`/获取 `fetch_cap=1000`，撞顶用 COUNT(*) 报真值）④ sqlite 物理只读。`list_tables`/`get_schema` 认表也认视图（M3）。
- `tools/schema.py`：schema 上下文可附 `database_description/{table}.csv` 列注释（BIRD 官方，按选中表注入，`utf-8-sig + errors="replace"` 容错编码）。
- `eval/`：`bird.py` 跑分器（逐题异常隔离，`--ids` 固定题集，`--out`/`--resume` 断点续跑，逐题 flush；记录含 `gold_sql`/`gold_failed`/`error_class`）；`match.py` 判分 = 顺序无关多重集匹配，`_sort_key` 类型分层是为修 None/数值混排崩溃的假阴性 bug——勿动，回归测试钉死；`report.py` 两轮 diff/变体对比；`variants.py` 变体矩阵驱动（variants.yaml）。
- `llm/`：OpenAI 兼容网关（temperature=0）＋ `invoke_with_backoff`（429/5xx/连接错指数退避重试，鉴权错立即失败）＋自建 JSONL tracing（不用 LangSmith）。
- `cli/main.py`：argparse+rich 薄壳，核心逻辑全在包内；`run_question` 顶层导入以便 monkeypatch；`ask --evidence` 透传业务口径。

## 项目纪律（非一般性建议，均源自设计文档与 M2 实践）

1. **永不编造**：失败路径不调 LLM，输出规则化的诚实失败说明；「一个错数字的代价远大于一次我不知道」。
2. 自设计 StateGraph，禁用 `create_react_agent` 预制件。
3. **归属明确**：每类错误先想「该谁处理」，不写万能 try-except（现存的有意宽捕获均带 `# noqa: BLE001` 与理由）。
4. **评测驱动**：优化前先分析 `runs/eval-last.jsonl` 错题定位失败模式，不盲改；改完跑 eval 对比；配对对比必须同题集（`tests/*_ids.json`），有混杂因素（换模型/审查题）须注明。
5. **测试语义（M2 起）**：新测试一律用 `tests/fakes.py::ScriptedLLM`＋`calls` 计数断言；禁止依赖 `FakeListChatModel` 的循环行为（超出脚本必须显式报错）。
6. 数字诚实：准确率入 README 时混杂与限制如实标注（M2 教训：配额审查/模型切换都会污染表观数字）。

## 约定

- 注释、docstring、文档全用中文；提交信息用 conventional 前缀＋中文描述（用户裁决，如 `feat: sqlglot 语句层校验`）。
- 测试用 `conftest.py` 的 `RecorderLLM`（M1 遗留，捕获 prompt 断言链路）与新标准的 `ScriptedLLM`。
- `tests/test_config.py` 的 autouse 夹具隔离了 `load_dotenv` 与环境变量——防止开发者本机 `.env` 污染测试；新增 config 变量必须同步进夹具的 delenv 列表。
- CI（GitHub Actions）门禁 ruff＋pytest；ruff 用当前版默认规则，升级 ruff 后若变红先分辨真问题还是规则漂移。
