# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

「问数」(qadata)：对话式数据分析 Agent——自然语言问题 → SQL → 只读沙箱执行 → 可信答案。LangGraph 编排，BIRD 基准评测，求职作品项目，**评测驱动开发**（每个里程碑要有准确率数字）。权威设计文档在 `.scratch/qadata-agent/spec.md`，M1–M4 工作档案（spec/plan/复盘/ledger）按里程碑存于 `.scratch/qadata-mN/`（注意 `.scratch/` 与 `docs/` 均被 .gitignore，仅本地存在；里程碑移交清单见主设计 §11.1）。

## 常用命令

```bash
# 环境：.venv 为 editable 安装（pip install -e ".[dev]"），git bash 无需激活即可用：
.venv/Scripts/python -m pytest                 # 全部测试（当前 573 个，必须全绿）
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
# M7-rev2 web demo（票 02.5）：先 cd web && npm install && npm run build（产物 web/dist 由后端同源服务；未构建时 "/" 出诚实占位页），再：
qadata serve --port 8000   # 单进程起 API＋页面；智能体真空启动零预置，数据 data/agents/<uuid12>/（meta.yaml＋source.sqlite＋sessions/<sid12>.yaml，gitignore）；模型只读展示、真源 .env；票 03 起问数主通道＝POST /api/ask/stream（SSE 进度流）；票 04 起响应含可选 chart 图型字段（判定＝web/charts.py 纯函数）；票 05 起多轮会话落盘（owner 裁 2026-09-14，spec 原内存态作废——重启历史仍在；三层记忆组装＝web/sessions.py，图侧唯一新键 session_context 仅 understand/generate 消费）
# M8 票 02 值采样（默认关）：QADATA_VALUE_SAMPLING=1 在 schema 上下文为最终入选表附列取值样本块
#   （有界窗口 DISTINCT 防大表全扫；低基数全枚举、高基数给存储形态示例；窗口外如实漏；一切失败静默跳列；
#   关态零采样查询、schema 上下文逐字节一致有专测 tests/test_value_sampling.py）。
#   **判负成文（2026-09-15）**：A3 活靶单靶翻案＝机制有效；但 qwen＋BIRD 考面上值域病自愈/迁移
#   （447 迁列路径、326 迁 JOIN 语义、1330 自愈），20 题切片 Δ−3 全落 gold 清奇族噪声漂移、零翻案
#   → 默认保持关、代码测试留仓（生产库无 database_description 兜底时开闸即用）；判例链见 .scratch/qadata-m8/票02。
# M8 票 03 澄清回合（默认关；owner 改判 2026-09-16＝经典 HITL）：QADATA_CLARIFICATION=1 让「口径缺失到任何 SQL 都是猜」
#   的题在 understand 出口回一句澄清问：带会话（thread_id＋checkpointer 成对）＝节点内 interrupt() 暂停，下一条
#   消息原样发回即 Command(resume) 续答、服务端合成归档（compose_supplement 单源＝原问＋「补充说明：」＋澄清问＋答），
#   全链 4 call（understand 重放＝既定代价）；单轮/CLI/评测无 key 可续＝落 answer 直达 END（1 call 形态照旧）。
#   pending 指针住进程内（MemorySaver，重启丢在途澄清＝在册上限，升级路径 langgraph-checkpoint-sqlite）；
#   澄清轮不落盘不变，回放经 GET 会话详情 pending 字段恢复；标记＝防循环双闸（指令侧不加尾段＋节点侧机制版复闸）。
#   web 契约 14 字段形状不动（clarification＝暂停面）；请求位 discard_pending＝新话题弃续答；
#   CLI ask 黄字打印 exit 0；eval 记 error_class="clarified"（≈判负，判卷读误伤上界）。
#   关态逐字节/路由 map 无 END 分支/违令键不消费/参数不成对自动降级直 END 各有专测 tests/test_clarification.py。
# M8 票 04 反馈闭环（无开关，全程零 LLM）：POST /api/agents/{id}/feedback（body sid+ts+vote）写旁挂
#   <sid>.feedback.jsonl（append-only {ts,vote,question,sql,at}，模块级 threading.Lock 收口、与 ask 在途锁零交叠；
#   无会话档 404 写者不建会话、ts 回查不中 404、非 up/down 400）。回放 GET 轮条目加可选尾键 feedback（同 ts 末票）；
#   **不进 AskResponse 14 字段契约、不进 prompt/记忆/路由、不重开 PATCH（票 09 的 405 钉原样绿）**。
#   攒卷：qadata feedback-export --agents-dir --out → runs/feedback-<date>.jsonl 供人审手工成卷
#   （按 ts 回查轮、SQL 以轮 payload 为真源、回查不中如实计数；gold 必须人签、不自动进 tests/*_ids.json）。
# M8 票 06 任务控制台（无开关，零新增调用）：票 03 既有进度帧喂厚——结果帧加
#   ok/duration_ms/tokens_in/out（start 帧与末帧 14 字段契约零动），tools 层子步骤发
#   kind:"tool" 帧（explore list_tables/get_schema/select_tables/value_samples、execute execute_sql，
#   on_event 沿参透传、缺省 None 关态零发逐字节一致）；token 经 timed_invoke sink 逐调用流进结果帧。
#   web 右抽屉＝Console.tsx 概览四卡（步骤/成功率/输出/token·费用）＋追踪时间线（工具胶囊绿/红点）；
#   **token/费用只活在控制台不进答案报告（owner 裁 2026-09-15）**，费用卡标「影子折算」（锚价 ~¥3.9/M）；
#   回放态控制台为空（trail 现场观察不入档）。专测 tests/test_on_event.py 帧三型钉。
# M5 指标层（票 05，默认关）：QADATA_METRIC_LAYER=1 开命中路径（ask/eval 共用，注册表按库名寻址 metrics/<db>.yaml）；
# 关时与纯 Text-to-SQL 现状**在路由/调用数/账本/评测字段上**逐行为一致（评测 path 恒 fallback；
# 票 07 E1 三节展示形态与开关无关、两态共用，判分在结果集层不读 conclusion）。轨道① on/off 配对须同模型同时段。
```

- eval 逐题结果写 `runs/eval-last.jsonl`（每题带 run_id/llm_calls/input·output·total_tokens/latency_s，失败题也计——票 10），节点级明细写 `runs/traces.jsonl`（裸北京时间 ts＋latency_s 秒；历史行为 UTC/毫秒旧格式，不迁移）；轮末逐题汇总另落 `runs/tokens-<run_id>.json`（含 model），`--budget` 给定后自动向账本追加一行（成本/累计列留待填保持人审）；两者与 `data/` 均不入库。抽样跑分固定 seed=42 可复现。
- 评测参考值：M1 基线 BIRD dev-100 **63.0%**（qwen3.7-flash）；50 题配对 M2 **58.0%** → M3 **64.0%** → M4 终局运行 **60.0%**（deepseek-v4-flash-0731；M4 表观 Δ 被端点跨时段漂移淹没——同代码同日 32→36→29/30，证据链在 `.scratch/qadata-m4/retrospective.md`（本地），各线增量以受控探针/同时段配对为准，不以跨时段 Δ 计）。冒烟 10 题固定集回归门槛在 `tests/smoke_baseline.json`（M5 收尾重建 **10/10**，绑 f16fc7a＋glm-5.2；满分闸语义＝链路健康闸＋噪声带参考，数字回归判读归同时段配对）——**与模型绑定，换模型必须重建基线**。
- 轨道①（M5 指标考卷）固定题集 `tests/m5_financial_ids.json`：financial 106 题按 BIRD 难度分层 seed=42 抽 50（simple 29/moderate 18/challenging 3），一经冻结不得换题（换题＝历史数字作废，ADR-0001）；纯 SQL 对照分母 **27/50（54.0%）**，绑定 deepseek-v4-flash-0731（明细见 runs/m5-track1-baseline-report.md，本地）。**模型缺口警示**：.env 模型随订阅切换（deepseek-v4-flash-0731→09-09 kimi-k2.7-code→09-10 glm-5.2，端点同为百炼 coding plan）——轨道① on/off 配对须同模型同时段，跨模型数字不可比、各判卷绑实跑模型（票 06 定稿绑 kimi）。**票 06 判卷定稿**（09-09 kimi 同时段配对，同 commit `efea1cf`）：对照轮 28/50 vs 指标轮 29/50、**命中率 0/50**→①②「样本不足，无结论」、③判过→**metric_layer 默认保持 False**；零命中在体归因＝BIRD 题面普遍组合约束、与 18 原子口径交集≈0（L2 NONE 36 题基本判对、⑨闸在体拦 8 题零误伤、填槽拦 6 题），`.scratch/qadata-m5/retrospective.md`（本地）＋`runs/m5-track1-pair-report.md`；kimi **同码路径同时段逐题翻转可达 ±5**（29 vs 28 的 Δ+1 无统计含义）——此后 kimi 配对按该噪声带解读。
- 票 02 意图尾段注入（条款④）**判负成文**（2026-09-09，kimi 同日配对 32/50 vs 32/50、翻转 3/3、1466 分离探针证翻转不可复现＝注入信号≤载体 A 改写漂移噪声带）：软用途入墓地，载体 A（六字段意图）保留、prompt 消费者＝metric_match 填槽（票 07 起 respond 兜底口径说明增一展示消费者，零 prompt 注入），回炉裁决与全证据链见 `.scratch/qadata-m5/issues/02`。
- **M5 收尾（票 08，2026-09-10）**：票 01–08 与 10 全部落袋，复盘见 `.scratch/qadata-m5/retrospective.md`；账本闭合 ~¥5.8/¥10（影子折算口径——coding plan 订阅逐行无现金实付可计，价格锚 ~¥3.9/M，owner 可掌真实目录价平价替换）；单次 ≤¥3 红线全行满足、砍线规则全程未触发。票 09（B v2）**wontfix 成文**（2026-09-10，用户批准存在性探针 ¥0.08：三靶 0/3 命中「看不见取值」病灶——51 靶消失、326 迁至 JOIN 语义、407 实为 DISTINCT 判分形态；未进注入实现与配对，证据链在 09 票 Comments）。
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
- `graph/state.py`：`AgentState` TypedDict（total=False），**无 reducer，各键整值覆盖**；含 `original_question`（改写前原问题）、`verify_note`（可疑原因，None=通过）、`intent`（M5 载体 A：understand 同调六字段意图，None=解析失败回退，**勿再喂 prompt**——尾段注入④判负已拆，`test_generate_never_reads_intent` 钉死）、`matched_metric`（票 05 命中指标名，载荷兼作 respond 血缘/评测记录来源，兜底路径与降级后由 explore 清 None）、`metric_note`（票 05 模板降级原因，None=未降级；进失败历史与 respond 标注）、`session_context`（M7 票 05 唯一新状态键，precise_candidates 同款载荷纪律：{"turns": L2 行 ≤K=5·时间升序, "draft": L1|None}，仅 understand/generate 消费——AST 源扫描＋respond prompt 带/不带会话逐字节一致姊妹钉（tests/test_session_context.py），None/缺键＝关态逐字节一致；组装与三层记忆纪律见 web 段）。
- `graph/metrics.py`（M5 票 03，纯函数）：注册表六要素校验加载（`load_registry`/缺要素 `RegistryError` 拒绝、不带病运行）／L1 `match_metric`（归一化精确或包含，多候选歧义判 None）／`fill_slots`（时间/维度/过滤槽，任一填不出判未命中）／`render_sql`（命名占位符）／`parse_metric_review`（L2 严格解析，票 05）／`has_extreme_signal`（⑨ 闸中英极值·排名词形判定，票 06）／`lineage_text`（命中血缘一行文本，票 07——注册表字段展示契约归本模块，respond 不伸手进 Metric）。**不碰 LLM 不碰图**（AST 级 import 纪律测试钉死）。`metrics/financial.yaml` 为票 04 定稿注册表（按库名寻址 `metrics/<db>.yaml`）。
- `graph/intent.py`（M5 票 02）：`parse_understand_response` 纯函数（JSON 提取/宁空勿造归一/失败回退原文不烧预算）；意图 prompt 消费者唯一＝metric_match 填槽（票 05）；票 07 起 respond 兜底口径说明展示消费 evidence_terms（只进答案文本组装、零 prompt 注入）。M8 票 03 扩返**三元组**（第三元＝澄清问，仅 JSON 对象里非空字符串才采信——回退态/坏 JSON 恒 None，与 question/intent 判定正交）。
- `graph/nodes.py`：`make_nodes(llm, tracer, settings)` 节点工厂，闭包注入依赖以便测试替换假模型；respond 失败路径不调 LLM（永不编造）；**答案稳定性回退**：执行失败耗尽且最后尝试为执行失败形态时，respond 重执行 `_last_good_sql(attempts)`（从 attempts 派生，不新增状态键）作答并标注；**E1 三节答案（票 07）**：只有【结论】来自 LLM（respond prompt 只索要一句话），【数据依据】（行数/范围/所用表，表名＝sqlguard::used_tables 确定性派生）、【口径说明】（命中→注册表零 token 引用口径＋血缘；兜底/失败→evidence 命中项；失败态不走命中口径）、【校验标注】（verify_note/截断/回退/模板降级原样并入）全部代码组装（`compose_conclusion` 纯函数），空节省略不空转；判分在结果集层不读 conclusion，形态变更零评测风险。**票 03 进度帧**：`make_nodes` 加末位可选参 `on_event`——start 帧由节点包装层统一发，结果帧各节点出口显式发（execute 失败帧带 error_hints 中文修复建议＝页面所见即重试历史所读；attempt＝该时刻已入账尝试数、重试环上单调推进）；status 一行中文后端单源，execute/verify 两个纯码节点从此有事件（tracer 只挂 LLM 调用不够用的缺口在此补上）；M8 票 03 改判再加末位参 `hitl`——真时澄清走节点内 `interrupt()` 暂停（与 build 侧 checkpointer 装配成对），假＝直 END 形态照旧；M8 票 06 帧喂厚——结果帧（`_emit`）加 `ok/duration_ms/tokens_in/tokens_out`（start 帧三字段不动，ok 红＝硬失败形态：执行/提取失败、如实报失败；降级/可疑/未命中不算红），新增 `_tool` 发 kind:"tool" 子事件帧（execute 内 execute_sql/票决批量、respond 内回退复执行；explore 子步骤在 schema.py 内同源发射，on_event/sink 沿参透传），token 走 `timed_invoke` 末位可选参 sink（{"tin","tout"} 累计＝逐调用流进该步结果帧，图与 tools 每请求各建一份闭包无串扰）。
- `graph/verify.py`：规则校验器（空结果/聚合异常），确定性不烧 token；M3 精准化：「列出全部/所有/有哪些」类问题豁免空结果判可疑，截断不再触发重试（由 respond 标注）。
- `graph/error_hints.py`：SQL 错误分类→中文修复建议（规则版，零 token），进失败历史段。
- `graph/prompts.py`：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，动态内容只能追加尾部），含口径规则（禁格式化输出、只选问题需要的列、题面明示精度/百分比形态时从题面——M4-D 规则 5，228 探针实证）；`format_failure_history` 失败历史段（含修复建议）；`strip_conclusion_prefix` 防复读（剥多层）；`format_session_history`/`format_session_draft`（M7 票 05：L2→understand、L1→generate 尾草稿渲染，零 LLM 确定性拼装，空块＝关态逐字节一致）；M8 票 03 `understand_prompt` 尾追澄清指令段（`clarify` 参——开关开**且**题面不含「补充说明：」标记才加，含负清单收紧产出面；两态不满足时与参数加入前逐字节一致）；`compose_supplement`＝续轮合成公式单源（HITL 恢复态与 web 轮次归档同式，防两处字面漂移）。
- `graph/build.py`：`_route_after_execute`/`_route_after_verify` 为纯函数路由（独立测试）；`_route_after_understand` M8 票 03 首判 `answer→END`（直 END 形态，第三参 clarification 随开关启用——关态逐行为一致且路由 map 的 END 分支根本不装配，编译图出边集有结构直测）；`run_question` 是最外层守护——任何裸异常收敛为诚实失败的 `Answer`；改判（经典 HITL）＝末位成对参 `thread_id＋checkpointer`（成对时澄清走节点内 `interrupt()` 暂停，`__interrupt__` 面收成澄清 Answer；不成对自动降级直 END——配对纪律在守护闸口守死，调用方免配平）＋新出口 `resume_question`（`Command(resume)` 续跑原 thread，understand 重放＝既定代价，全程 4 call < 无状态往返 6 call）；票 03：末位可选参 `on_event`（节点级进度帧 `{node, attempt, status}`）沿 `build_graph→make_nodes` 闭包注入——缺省 None＝零包装逐行为一致（专测 `tests/test_on_event.py` 钉死：关态双跑、CLI/eval 调用面源码出现 on_event 即红、假模型完整帧序列含失败→重试→成功与预算耗尽形态）。
- 沙箱四层：① `tools/db.py::open_readonly` 只读连接唯一入口（URI 转义）② `tools/sqlguard.py` sqlglot 语句校验（解析→单语句→根白名单→表名校验；另供 `used_tables` 纯函数给 respond 数据依据节——显示辅助，认不出返回空表绝不抛，拒绝语义仍归 validate_sql，空名表节点的历史拒绝行为两侧语义各钉死）③ `tools/executor.py` 资源层（progress handler 5s 超时中断＋双行数上限：显示 `max_rows`/获取 `fetch_cap=1000`，撞顶用 COUNT(*) 报真值）④ sqlite 物理只读。`list_tables`/`get_schema` 认表也认视图（M3）。
- `tools/schema.py`：schema 上下文可附 `database_description/{table}.csv` 列注释（BIRD 官方，按选中表注入，`utf-8-sig + errors="replace"` 容错编码）；M8 票 02 值采样（`column_value_samples`，默认关）＝对最终入选表按列采样——`SELECT DISTINCT col FROM (SELECT … LIMIT 窗口) ORDER BY LIMIT 11`，文本亲和列、每表 ≤8 列×全局 ≤2000 字确定性裁尾，低基数全枚举（'gold' 大小写病灶）、高基数统一「存储形态示例」不冒充全量（日期分支废除中——真库 A4 区名列被日期正则误判注无意义范围，宁漏勿错），有界窗口把百万行表从 14s 压进毫秒带且双跑同文，一切失败静默跳列（锦上添花不连累本体，`_load_description` 先例）。
- `eval/`：`bird.py` 跑分器（逐题异常隔离，`--ids` 固定题集，`--out`/`--resume` 断点续跑，逐题 flush；记录含 `gold_sql`/`gold_failed`/`error_class`；M4：`--concurrency` 每题独立分片＋主线程单写者收口合并、非续跑先清遗留分片、`--skip-respond` 评测模式省调用）；`qtypes.py` 题型标签器（规则六类，词边界防误命中，题面优先）；`match.py` 判分 = 顺序无关多重集匹配，`_sort_key` 类型分层是为修 None/数值混排崩溃的假阴性 bug——勿动，回归测试钉死；`report.py` 两轮 diff/变体对比；`variants.py` 变体矩阵驱动（variants.yaml）。
- `llm/`：OpenAI 兼容网关（temperature=0；精准模式候选>1 时 `build_llm` 切 `precise_temperature`）＋ `invoke_with_backoff`（429/5xx/连接错指数退避重试，鉴权错立即失败）＋ `ratelimit.py` 全局限速器（`--qps`，多线程共享单实例）＋自建 JSONL tracing（不用 LangSmith）。注意：取回复用 `.content`（`timed_invoke` 语义），`str(AIMessage)` 的 repr 转义引号会炸 sqlglot 提取。
- `cli/main.py`：argparse+rich 薄壳，核心逻辑全在包内；`run_question` 顶层导入以便 monkeypatch；`ask --evidence` 透传业务口径；`serve` 只做参数转交（真逻辑在 `web/serve.py`：load_settings→build_llm 共享实例→create_app）。
- `web/`（M7 票 01/02 → **rev2 票 02.5 智能体化**，A 轨预置库/双轨制已拆——判卷以 spec 修订段＋票 02.5 为准）：`app.py::create_app(llm, settings, agents, static_dir, tracer)` 应用工厂（DI 接缝——契约测试 TestClient＋ScriptedLLM＋tmp AgentStore，不碰网/不碰真实目录）；面＝`GET/POST /api/agents`、`PATCH/DELETE /api/agents/{id}`、`POST /api/agents/{id}/datasource`（raw-body 字节流**上传唯一路**——spec 冻结依赖不走 multipart，固定落盘 source.sqlite、换库＝覆盖、端点全程零建连）、`GET /api/metrics-registries`、`GET /api/model`（**只读卡**：当前模型名、真源 .env、writable=false）、`POST /api/ask`（请求体 `db`→`agent_id`，**响应 12 字段冻结不动＋票 04 起新增可选 `chart`＋M8 票 03 起新增可选 `clarification` 共 14 字段**（澄清轮＝第 14 字段出真值、failed=False；**经典 HITL（owner 改判 2026-09-16，取代无状态前端合成裁决）**＝带会话时 understand 节点内 `interrupt()` 暂停、下一条消息原样发回即 `Command(resume)` 续答（服务端合成归档 4 call 全链；`_route_clarification` 两端点唯一分流闸口），单轮/无 checkpoint 配置＝直达 END 1 调用形态照旧；pending 指针住进程内（MemorySaver＋dict，重启丢在途澄清＝在册上限，升级路径 langgraph-checkpoint-sqlite）；**澄清轮不落盘不变**——回放经 GET 会话详情 `pending` 字段恢复（懒建档无档＋有 pending＝空档放行，无档无 pending 照旧 404），请求位 `discard_pending`＝弃续答按新话题问、归档成用户真话）；票 05 起请求可选 `session_id`——带＝装载三层记忆＋问完落盘一轮、响应 session_id 真值回显，单轮照旧 null；口径优先级照旧＝请求显式 > 智能体业务知识 > 空（会话级叠加框经 owner 裁 2026-09-15 撤销，口径活在配置面；spec 修订 M7-票05-1）；无数据源/引用注册表被删/非法会话 id 均拒在调模型前）、`POST /api/ask/stream`（票 03 进度流：SSE 帧三型（票 06 喂厚）＝start `node/attempt/status`／结果帧＋`ok/duration_ms/tokens_in/out`／`kind:"tool"` 子事件帧，末帧 `event: answer`＝契约本体与阻塞端点同源不漂移（票 06 末帧零动）（chart/session_id/轮次落盘一判双达同经 `answer_to_payload`＋`_finish_ask` 一个收口，两端点不漂移）；前置拒绝与 /api/ask 同序同文案、拒在起流前；**在途锁**两端点共用一把、忙 409——票 05 键升格 session_id（单轮维持 agent 级、`s:`/`a:` 前缀分域防同值撞锁；键与文案单收 `_acquire_ask_lock` 防双份字面量漂移））；票 05 会话面＝`GET /api/agents/{id}/sessions`（侧栏列表：title＝首问/updated/turn_count，懒建档故只认有轮次者）＋`GET .../sessions/{sid}`（重开回放＝问答本体，trail 属现场观察不入档）（会话 PATCH 面已整面撤除——票 09 owner 裁 2026-09-15 随 fresh_topic 人肉闸撤端点，话题连续性改模型隐式判、会话面无运行时写入口；无 DELETE＝票未划；旧档案残留 overlay/fresh_topic 键＝读取忽略有兼容专测，PATCH 405 有钉）；`charts.py::decide_chart`（票 04 图型判定**规则纯函数**：时间列→折线、类别＋数值→柱、一行一列纯数值→大数卡、其余→null＝表格；列**下标**寻址（列名可重复当不了键）、数值列 >6 不画半张图判表格、时间/数值要求列内全部非空同形态、bool 非数值；AST import 纪律测钉死不碰 LLM/图/库，并纳入 web 包禁 sqlite3 循环）；`sessions.py`（票 05）＝会话落盘＋三层记忆组装：一会话＝`data/agents/<agent>/<sid12>.yaml`（文件即数据库沿 AgentStore，删智能体连带清，坏文件整列报错；懒建档；sid 与 agent id 共用 `is_hex12` 焊穿越）——`build_session_context` 纯函数切 K=5 窗（L2＝问题/SQL/行数/标量头部→understand 消解；failed 轮剥 SQL 只留问题标失败）、L1＝最近成功轮完整 SQL＋头部摘要→generate 尾部草稿（failed 轮→不给草稿，"错误草稿不传染"最保守读法；票 09：人肉闸撤后草稿常给，"与上问无关则忽略"授权进节头、连续性模型隐式判——判错方向宁多带勿错切，多带的旧史由沙箱/verify 兜住）、L3 全史只落盘**永不进 prompt**；`result_head` 摘要（标量→值否则头部 3 行）；`append_turn` 落盘归档（闸复位语义随票 09 消亡）。图侧 `session_context` 唯一新键（precise_candidates 同款显式键纪律）仅 understand/generate 消费（AST＋respond prompt 带/不带会话逐字节一致姊妹钉，tests/test_session_context.py）；零新增 LLM 调用、每轮 attempts 独立入账、无会话关态逐字节一致各有专测；`agents.py::AgentStore` 文件即数据库（`data/agents/<uuid12>/{meta.yaml, source.sqlite, sessions/}`，**真空启动零预置**，删智能体＝删目录；名称可重复、uuid 是键；**业务知识双态互斥**：手动 evidence 或 `metrics_ref` 指标注册表引用——引用态**读取期派生**（`metric_evidence_text`，口径随注册表动、无第二份文本，双写 400 拒）；坏 meta 整列报错不静默吞）。构建产物 `web/dist` 同源服务（index＋/assets 挂载在 API 路由之后，未构建时 "/" 出占位页）。前端 `web/`（Vite＋React＋TS，状态路由不引新依赖；票 04 唯一新依赖＝recharts（owner 预裁，echarts 不议））三视图：首页（模型卡＋智能体卡片＋创建表单）／详情四节（基本信息/数据源/业务知识含引用切换/预设问题 ≤10）／对话页（票 05 会话制：sid 前端 crypto.randomUUID 截 hex12、进页/＋新建会话＝换新 sid 懒建档、换话题＝直接打字（票 09 撤 ↺ 按钮与闸，understand/generate 两节头显式授权"无关则忽略"）、侧栏历史会话真落点（点击回放＝问答本体无 trail 留档）、预设问题 chips 点击＝与手输同路零新通道；右对话＋票 03 进度流面板：逐帧直播自纠错、完成后折叠留档于答案下，在途锁前端侧＝锁输入与 chips 与侧栏条目，SSE 用 fetch 手解不引依赖；M8 票 06 任务控制台＝chatshell 右抽屉 `Console.tsx`（既有帧流的纯视图模型，零新依赖）：概览四卡（步骤/成功率/输出/token·费用——费用卡＝token 合计×影子锚价 ~¥3.9/M 如实标「影子折算」，且 token/费用只活在控制台不进答案报告，owner 裁 2026-09-15）＋追踪时间线（每步 N 工具·Xms、工具胶囊绿/红点）；聊天「工作过程」折叠与抽屉共用同一帧型标签映射（Console.tsx 单源，ProgressRow 升级）；帧类型联合 StepEvent|ToolEvent（忘判 kind 过不了 tsc＝帧型钉）；回放态控制台为空；M8 票 03 澄清＝「？ 待澄清」徽标（label 承载语义）＋**经典 HITL**（改判 2026-09-16：续答＝消息原样发回、服务端合成，前端拼串通道退役；pending 期间 composer 双按钮「补充/新话题」（`discard_pending`），会话号 sessionStorage 过刷新——不然服务端 pending 无人认领，新标签页照旧新会话）；票 04 结构化呈现（`Chart.tsx` 只管画、形状零自判——图型契约与后端 `decide_chart` 同源；配色＝已验证分类色板前六槽固定序不循环、单系列省图例；三节分节折叠：结论/校验标注常开、数据依据/口径说明可折、未知节头一律常开，有图时表格收进折叠＝图首读表查证；校验旗标徽标化 ✗失败/⚠模板降级/⚠已截断——label 承载语义不靠颜色单传；大数卡值原样呈现 UI 不格式化数字）；textContent 纪律（全文件禁 dangerouslySetInnerHTML）；不立前端测试（壳不判卷，CI 保持纯 Python）。**唯一入口三层钉测**＝AST 禁 web 包 import sqlite3＋上传阶段 connect 即炸行为测＋schema 摘要与问答链路均经 open_readonly。**落盘唯一写入口**＝`web/_fs.py::atomic_write`（M8 票 01：meta.yaml／会话 yaml／上传 source.sqlite 三写点收口 tmp+fsync+os.replace，crash 不出半档；存储撤除纪律＝端点删、残留键读忽略、新写不产）；**反馈面（M8 票 04）**＝`web/feedback.py`：`POST /api/agents/{id}/feedback`（sid+ts+vote）写**旁挂** `<sid>.feedback.jsonl`（append-only {ts,vote,question,sql,at}，非 atomic_write 覆盖语义——模块级 `threading.Lock` 收口并发 append、读侧同锁不撞半写窗，与 ask 在途锁**零交叠**不同文件不同锁域；坏档纪律＝末行 crash 尸体宽容只丢半行、中段坏行整档报错不静默吞），无会话档 404（写者不建会话）／ts 回查不中 404／非 up-down 400；回放 `GET .../sessions/{sid}` 轮条目加**可选尾键 `feedback`**（chart 单点先例、无票轮形状不动、同 ts 取末票）；**裁决旁挂维持**＝反馈不进 AskResponse 14 字段契约、不进 prompt/记忆/路由、不重开 PATCH（票 09 的 405 钉原样绿）；攒卷 `qadata feedback-export`→`runs/feedback-<date>.jsonl` 供人审手工成卷（按 ts 回查轮、SQL 以轮 payload 为真源、回查不中如实计数、gold 必须人签不自动进 `tests/*_ids.json`），前端 AnswerBubble meta 行后 👍/👎 双按钮投后禁用高亮（ts 锚点：回放轮自带、当场气泡回查会话档末轮补——契约 14 字段不带 ts 不改形状；单轮/澄清暂停/回查失败＝无 ts 不可投按钮不出现）。

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
