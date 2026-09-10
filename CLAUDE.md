# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

「问数」(qadata)：对话式数据分析 Agent——自然语言问题 → SQL → 只读沙箱执行 → 可信答案。LangGraph 编排，BIRD 基准评测，求职作品项目，**评测驱动开发**（每个里程碑要有准确率数字）。权威设计文档在 `.scratch/qadata-agent/spec.md`，M1–M4 工作档案（spec/plan/复盘/ledger）按里程碑存于 `.scratch/qadata-mN/`（注意 `.scratch/` 与 `docs/` 均被 .gitignore，仅本地存在；里程碑移交清单见主设计 §11.1）。

## 常用命令

```bash
# 环境：.venv 为 editable 安装（pip install -e ".[dev]"），git bash 无需激活即可用：
.venv/Scripts/python -m pytest                 # 全部测试（当前 389 个，必须全绿）
.venv/Scripts/python -m pytest tests/test_eval_match.py::test_xxx   # 单个测试
.venv/Scripts/python -m ruff check src tests   # lint（另有 ruff format）

# CLI（需仓库根目录有 .env，参照 .env.example：DEEPSEEK_API_KEY 必填；
# QADATA_BASE_URL / QADATA_MODEL 可切换任意 OpenAI 兼容端点（当前为百炼 coding plan，模型以 .env 的 QADATA_MODEL 为准）
qadata ask data/bird/dev/dev_databases/financial/financial.sqlite "去年销售额是多少" --evidence "销售额 = …"
qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --sample 100
qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --ids tests/m2_compare_ids.json  # 固定题集
# M4 真跑统一参数（并发＋限速＋省 1 次调用/题；模型一律用 .env 的 QADATA_MODEL——2026-09-09 用户裁决，废弃 M4 脚本内嵌 export 锁模型做法；跑分记录须注明实跑模型）：
#   --concurrency 5 --qps 8 --skip-respond --budget runs/m5-budget.md
qadata report --baseline runs/A.jsonl --current runs/B.jsonl --types runs/m4-attribution.jsonl  # 两轮 diff＋题型切片
# M5 指标层（票 05，默认关）：QADATA_METRIC_LAYER=1 开命中路径（ask/eval 共用，注册表按库名寻址 metrics/<db>.yaml）；
# 关时与纯 Text-to-SQL 现状**在路由/调用数/账本/评测字段上**逐行为一致（评测 path 恒 fallback；
# 票 07 E1 三节展示形态与开关无关、两态共用，判分在结果集层不读 conclusion）。轨道① on/off 配对须同模型同时段。
```

- eval 逐题结果写 `runs/eval-last.jsonl`（每题带 run_id/llm_calls/input·output·total_tokens/latency_s，失败题也计——票 10），节点级明细写 `runs/traces.jsonl`（裸北京时间 ts＋latency_s 秒；历史行为 UTC/毫秒旧格式，不迁移）；轮末逐题汇总另落 `runs/tokens-<run_id>.json`（含 model），`--budget` 给定后自动向账本追加一行（成本/累计列留待填保持人审）；两者与 `data/` 均不入库。抽样跑分固定 seed=42 可复现。
- 评测参考值：M1 基线 BIRD dev-100 **63.0%**（qwen3.7-flash）；50 题配对 M2 **58.0%** → M3 **64.0%** → M4 终局运行 **60.0%**（deepseek-v4-flash-0731；M4 表观 Δ 被端点跨时段漂移淹没——同代码同日 32→36→29/30，README M4 段有证据链，各线增量以受控探针/同时段配对为准，不以跨时段 Δ 计）。冒烟 10 题固定集回归门槛在 `tests/smoke_baseline.json`（M4 重建后 9/10）——**与模型绑定，换模型必须重建基线**。
- 轨道①（M5 指标考卷）固定题集 `tests/m5_financial_ids.json`：financial 106 题按 BIRD 难度分层 seed=42 抽 50（simple 29/moderate 18/challenging 3），一经冻结不得换题（换题＝历史数字作废，ADR-0001）；纯 SQL 对照分母 **27/50（54.0%）**，绑定 deepseek-v4-flash-0731（明细见 runs/m5-track1-baseline-report.md，本地）。**模型缺口警示**：.env 现为 kimi-k2.7-code——轨道① on/off 配对须同模型同时段，若以 kimi 跑则 27/50 深基线跨模型不可比，需按纪律处理（票 06 已标注）。**票 06 判卷定稿**（09-09 kimi 同时段配对，同 commit `efea1cf`）：对照轮 28/50 vs 指标轮 29/50、**命中率 0/50**→①②「样本不足，无结论」、③判过→**metric_layer 默认保持 False**；零命中在体归因＝BIRD 题面普遍组合约束、与 18 原子口径交集≈0（L2 NONE 36 题基本判对、⑨闸在体拦 8 题零误伤、填槽拦 6 题），README M5 段＋`runs/m5-track1-pair-report.md`；kimi **同码路径同时段逐题翻转可达 ±5**（29 vs 28 的 Δ+1 无统计含义）——此后 kimi 配对按该噪声带解读。
- 票 02 意图尾段注入（条款④）**判负成文**（2026-09-09，kimi 同日配对 32/50 vs 32/50、翻转 3/3、1466 分离探针证翻转不可复现＝注入信号≤载体 A 改写漂移噪声带）：软用途入墓地，载体 A（六字段意图）保留、prompt 消费者＝metric_match 填槽（票 07 起 respond 兜底口径说明增一展示消费者，零 prompt 注入），回炉裁决与全证据链见 `.scratch/qadata-m5/issues/02`。
- 票 04 注册表**定稿入库**（2026-09-09 owner 拍板「全过」，提交 054388b，护栏①放行凭证在 YAML 文件头）：`metrics/financial.yaml` 18 条原子指标（六要素＋中英别名）。**库实际坑**：被测库五个日期列全为文本 YYYY-MM-DD，与 database_description 声称的 YYMMDD 不符——填槽字面量一律按库实际；槽标签以题面措辞为准（载体 A filters＝题面原样摘录，等值匹配容不得注释措辞）。裁决⑧⑨移交票 05：prompt 强调时间 filters 输出裸时间形；L2 指令「题面求具名极值/比较→NONE」防包含路径误命中户均条（票 06 设靶）。
- 评测花费红线：单次对比 ≤ ¥3；测试不得依赖真实 API/网络。

## 架构

六节点（＋M5 可选指标层节点）＋双条件边的自纠错状态机：

```
question → understand →〔metric_match〕→ explore → generate → execute ──成功──→ verify ──通过──→ respond
                            命中↓未命中→explore        ↑          │                  │
                                                    └──失败且有预算─┘   └──可疑且有预算──┘
                    （预算耗尽：失败→respond 兜底；可疑→respond 带校验标注）
```
- **指标层（M5 票 05，`Settings.metric_layer` 默认关＝上图〔〕节点不进、与纯 SQL 现状在路由/调用数/账本/评测字段上逐行为一致；票 07 三节展示属两态共用的展示层）**：understand 后插 metric_match——两级匹配（L1 归一化别名确定性别名 → L2 LLM 复核整表 ≤18 条，只输出指标名或 NONE，解析失败＝未命中，宁漏勿错）；**⑨ 闸·机制版（票 06，owner 批准补闸）**：任一级命中后、填槽前，题面/原文/mention/output_form 含具名极值·排名词形即撤销命中走兜底（`has_extreme_signal`，零新增调用——探针在体证实 L2 指令守不住 L1 包含穿透，指令降级为纵深防御）；闸后→填槽→渲染→走既有 execute（沙箱四层无旁路）→verify；未命中/该库无注册表文件→整节点跳过零调用走兜底。模板执行失败或结果可疑→记一条模板 attempt→降级兜底恰好一次（`matched_metric` 由 explore 清 None 作二次降级闸；`metric_note` 留原因进失败历史与 respond 标注）。评测逐题记录加 `path`（metric/fallback）/`metric_name`/`template_fell_back`。

- 重试预算不新增状态键：`len(attempts)` 即账本（默认 3，`Settings.retry_budget`）；generate 提取失败也写 `SqlAttempt(sql="", error=…)` 入账。
- **精准模式（M4-C，默认关）**：`Settings.precise_candidates>1` 时 generate 同 prompt 连打 K 发＝**一轮账本**（`build_llm` 按候选数切 `precise_temperature`）；execute 票决（结果级多数派，纯函数 `graph/precise.py`），票决不能自证——并列无多数派取最大组代表转 verify 强判可疑进重试/标注；载荷走唯一新状态键 `precise_candidates`（非预算键）。**注意**：探针实证该端点运行内采样噪声≈0（temp=0.3 三发结果恒收敛），自一致性无收益前提，工作点决策留 M5。
- `graph/state.py`：`AgentState` TypedDict（total=False），**无 reducer，各键整值覆盖**；含 `original_question`（改写前原问题）、`verify_note`（可疑原因，None=通过）、`intent`（M5 载体 A：understand 同调六字段意图，None=解析失败回退，**勿再喂 prompt**——尾段注入④判负已拆，`test_generate_never_reads_intent` 钉死）、`matched_metric`（票 05 命中指标名，载荷兼作 respond 血缘/评测记录来源，兜底路径与降级后由 explore 清 None）、`metric_note`（票 05 模板降级原因，None=未降级；进失败历史与 respond 标注）。
- `graph/metrics.py`（M5 票 03，纯函数）：注册表六要素校验加载（`load_registry`/缺要素 `RegistryError` 拒绝、不带病运行）／L1 `match_metric`（归一化精确或包含，多候选歧义判 None）／`fill_slots`（时间/维度/过滤槽，任一填不出判未命中）／`render_sql`（命名占位符）／`parse_metric_review`（L2 严格解析，票 05）／`has_extreme_signal`（⑨ 闸中英极值·排名词形判定，票 06）／`lineage_text`（命中血缘一行文本，票 07——注册表字段展示契约归本模块，respond 不伸手进 Metric）。**不碰 LLM 不碰图**（AST 级 import 纪律测试钉死）。`metrics/financial.yaml` 为票 04 定稿注册表（按库名寻址 `metrics/<db>.yaml`）。
- `graph/intent.py`（M5 票 02）：`parse_understand_response` 纯函数（JSON 提取/宁空勿造归一/失败回退原文不烧预算）；意图 prompt 消费者唯一＝metric_match 填槽（票 05）；票 07 起 respond 兜底口径说明展示消费 evidence_terms（只进答案文本组装、零 prompt 注入）。
- `graph/nodes.py`：`make_nodes(llm, tracer, settings)` 节点工厂，闭包注入依赖以便测试替换假模型；respond 失败路径不调 LLM（永不编造）；**答案稳定性回退**：执行失败耗尽且最后尝试为执行失败形态时，respond 重执行 `_last_good_sql(attempts)`（从 attempts 派生，不新增状态键）作答并标注；**E1 三节答案（票 07）**：只有【结论】来自 LLM（respond prompt 只索要一句话），【数据依据】（行数/范围/所用表，表名＝sqlguard::used_tables 确定性派生）、【口径说明】（命中→注册表零 token 引用口径＋血缘；兜底/失败→evidence 命中项；失败态不走命中口径）、【校验标注】（verify_note/截断/回退/模板降级原样并入）全部代码组装（`compose_conclusion` 纯函数），空节省略不空转；判分在结果集层不读 conclusion，形态变更零评测风险。
- `graph/verify.py`：规则校验器（空结果/聚合异常），确定性不烧 token；M3 精准化：「列出全部/所有/有哪些」类问题豁免空结果判可疑，截断不再触发重试（由 respond 标注）。
- `graph/error_hints.py`：SQL 错误分类→中文修复建议（规则版，零 token），进失败历史段。
- `graph/prompts.py`：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，动态内容只能追加尾部），含口径规则（禁格式化输出、只选问题需要的列、题面明示精度/百分比形态时从题面——M4-D 规则 5，228 探针实证）；`format_failure_history` 失败历史段（含修复建议）；`strip_conclusion_prefix` 防复读（剥多层）。
- `graph/build.py`：`_route_after_execute`/`_route_after_verify` 为纯函数路由（独立测试）；`run_question` 是最外层守护——任何裸异常收敛为诚实失败的 `Answer`。
- 沙箱四层：① `tools/db.py::open_readonly` 只读连接唯一入口（URI 转义）② `tools/sqlguard.py` sqlglot 语句校验（解析→单语句→根白名单→表名校验；另供 `used_tables` 纯函数给 respond 数据依据节——显示辅助，认不出返回空表绝不抛，拒绝语义仍归 validate_sql，空名表节点的历史拒绝行为两侧语义各钉死）③ `tools/executor.py` 资源层（progress handler 5s 超时中断＋双行数上限：显示 `max_rows`/获取 `fetch_cap=1000`，撞顶用 COUNT(*) 报真值）④ sqlite 物理只读。`list_tables`/`get_schema` 认表也认视图（M3）。
- `tools/schema.py`：schema 上下文可附 `database_description/{table}.csv` 列注释（BIRD 官方，按选中表注入，`utf-8-sig + errors="replace"` 容错编码）。
- `eval/`：`bird.py` 跑分器（逐题异常隔离，`--ids` 固定题集，`--out`/`--resume` 断点续跑，逐题 flush；记录含 `gold_sql`/`gold_failed`/`error_class`；M4：`--concurrency` 每题独立分片＋主线程单写者收口合并、非续跑先清遗留分片、`--skip-respond` 评测模式省调用）；`qtypes.py` 题型标签器（规则六类，词边界防误命中，题面优先）；`match.py` 判分 = 顺序无关多重集匹配，`_sort_key` 类型分层是为修 None/数值混排崩溃的假阴性 bug——勿动，回归测试钉死；`report.py` 两轮 diff/变体对比；`variants.py` 变体矩阵驱动（variants.yaml）。
- `llm/`：OpenAI 兼容网关（temperature=0；精准模式候选>1 时 `build_llm` 切 `precise_temperature`）＋ `invoke_with_backoff`（429/5xx/连接错指数退避重试，鉴权错立即失败）＋ `ratelimit.py` 全局限速器（`--qps`，多线程共享单实例）＋自建 JSONL tracing（不用 LangSmith）。注意：取回复用 `.content`（`timed_invoke` 语义），`str(AIMessage)` 的 repr 转义引号会炸 sqlglot 提取。
- `cli/main.py`：argparse+rich 薄壳，核心逻辑全在包内；`run_question` 顶层导入以便 monkeypatch；`ask --evidence` 透传业务口径。

## 项目纪律（非一般性建议，均源自设计文档与 M2 实践）

1. **永不编造**：失败路径不调 LLM，输出规则化的诚实失败说明；「一个错数字的代价远大于一次我不知道」。
2. 自设计 StateGraph，禁用 `create_react_agent` 预制件。
3. **归属明确**：每类错误先想「该谁处理」，不写万能 try-except（现存的有意宽捕获均带 `# noqa: BLE001` 与理由）。
4. **评测驱动**：优化前先分析 `runs/eval-last.jsonl` 错题定位失败模式，不盲改；改完跑 eval 对比；配对对比必须同题集（`tests/*_ids.json`）**且同时段**（M4 实证：端点跨时段漂移 ±4~7 题 ≫ 噪声带，跨日 Δ 不可解读，线级增量用同时段对照或受控探针），有混杂因素（换模型/审查题）须注明；**新线先探针后上线**（M4-C：¥0.3 探针拦下 ¥3.5 无效帕累托）。
5. **测试语义（M2 起）**：新测试一律用 `tests/fakes.py::ScriptedLLM`＋`calls` 计数断言；禁止依赖 `FakeListChatModel` 的循环行为（超出脚本必须显式报错）。
6. 数字诚实：准确率入 README 时混杂与限制如实标注（M2 教训：配额审查/模型切换都会污染表观数字）。

## 约定

- 注释、docstring、文档全用中文；提交信息用 conventional 前缀＋中文描述（用户裁决，如 `feat: sqlglot 语句层校验`）。
- 测试用 `conftest.py` 的 `RecorderLLM`（M1 遗留，捕获 prompt 断言链路）与新标准的 `ScriptedLLM`。
- `tests/test_config.py` 的 autouse 夹具隔离了 `load_dotenv` 与环境变量——防止开发者本机 `.env` 污染测试；新增 config 变量必须同步进夹具的 delenv 列表。
- CI（GitHub Actions）门禁 ruff＋pytest；ruff 用当前版默认规则，升级 ruff 后若变红先分辨真问题还是规则漂移。

## Agent skills

### Issue tracker

议题与规格以 markdown 形式保存在本地 `.scratch/<feature>/`（不入库）。见 `docs/agents/issue-tracker.md`。

### Triage labels

保留五个默认分诊角色（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。见 `docs/agents/domain.md`。
