# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

「问数」(qadata)：对话式数据分析 Agent——自然语言问题 → SQL → 只读沙箱执行 → 可信答案。LangGraph 编排，BIRD 基准评测，求职作品项目，**评测驱动开发**（每个里程碑要有准确率数字）。权威设计文档在 `.scratch/qadata-agent/spec.md`，M1–M4 工作档案（spec/plan/复盘/ledger）按里程碑存于 `.scratch/qadata-mN/`（注意 `.scratch/` 与 `docs/` 均被 .gitignore，仅本地存在；里程碑移交清单见主设计 §11.1）。

## 常用命令

```bash
# 环境：.venv 为 editable 安装（pip install -e ".[dev]"），git bash 无需激活即可用：
.venv/Scripts/python -m pytest                 # 全部测试（当前 601 个，必须全绿）
.venv/Scripts/python -m pytest tests/test_eval_match.py::test_xxx   # 单个测试
.venv/Scripts/python -m ruff check src tests   # lint（另有 ruff format）

# CLI（需仓库根目录有 .env，参照 .env.example：DEEPSEEK_API_KEY 必填；
# QADATA_BASE_URL / QADATA_MODEL 可切换任意 OpenAI 兼容端点（当前为百炼 coding plan，模型以 .env 的 QADATA_MODEL 为准）
qadata ask data/bird/dev/dev_databases/financial/financial.sqlite "去年销售额是多少"   # 口径通道＝字典（ADR-0008），ask 不再收 --evidence
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
#   web 契约字段形状不动（clarification＝暂停面；14 字段系当时口径，现 11，ADR-0007 注）；请求位 discard_pending＝新话题弃续答；
#   CLI ask 黄字打印 exit 0；eval 记 error_class="clarified"（≈判负，判卷读误伤上界）。
#   关态逐字节/路由 map 无 END 分支/违令键不消费/参数不成对自动降级直 END 各有专测 tests/test_clarification.py。
# M8 票 04 反馈闭环（无开关，全程零 LLM）：POST /api/agents/{id}/feedback（body sid+ts+vote）写旁挂
#   <sid>.feedback.jsonl（append-only {ts,vote,question,sql,at}，模块级 threading.Lock 收口、与 ask 在途锁零交叠；
#   无会话档 404 写者不建会话、ts 回查不中 404、非 up/down 400）。回放 GET 轮条目加可选尾键 feedback（同 ts 末票）；
#   **不进 AskResponse 契约、不进 prompt/记忆/路由、不重开 PATCH（票 09 的 405 钉原样绿）**。
#   攒卷：qadata feedback-export --agents-dir --out → runs/feedback-<date>.jsonl 供人审手工成卷
#   （按 ts 回查轮、SQL 以轮 payload 为真源、回查不中如实计数；gold 必须人签、不自动进 tests/*_ids.json）。
# M8 票 06 任务控制台（无开关，零新增调用）：票 03 既有进度帧喂厚——结果帧加
#   ok/duration_ms/tokens_in/out（start 帧与末帧字段契约零动），tools 层子步骤发
#   kind:"tool" 帧（explore list_tables/get_schema/select_tables/value_samples、execute execute_sql，
#   on_event 沿参透传、缺省 None 关态零发逐字节一致）；token 经 timed_invoke sink 逐调用流进结果帧。
#   web 右抽屉＝Console.tsx 概览四卡（步骤/成功率/输出/token·费用）＋追踪时间线（工具胶囊绿/红点）；
#   **token/费用只活在控制台不进答案报告（owner 裁 2026-09-15）**，费用卡标「影子折算」（锚价 ~¥3.9/M）；
#   回放态控制台复形（M9 票 02 改判：trail 精简入档为轮可选尾键，见下会话段）。专测 tests/test_on_event.py 帧三型钉。
# M8 票 07 答案报告化（无开关，零新增调用）：【结论】从「一句话」升级为自由成文的 markdown
#   小报告（指令只给「报告式总结、含量化发现」级别，不套固定骨架；owner 裁 2026-09-15：
#   「模型复述数据」旧防线让位——结果表并排可对照，数字漂移可查证）。三节其余与组装照旧；
#   报告正文不写 token/费用（票 06 边界）；超长不截断（透传有钉，中间截断切坏 markdown 形态）。
#   前端 web/src/Markdown.tsx＝手写 markdown 子集安全渲染器（标题/加粗/行内码/列表/表格/围栏
#   代码 → React 元素，零 raw HTML、零新依赖，子集外记号按普通文字诚实降级）；前端分节
#   SECTION_RE 白名单只认后端四节头（报告正文里的【…】不再当节头，单源一致钉＋纪律测
#   tests/test_web_markdown.py）。M7 票 06 报告导出＝挂起留痕（报告先行内化、导出缓做）。
# M8 票 08 思考流（无开关，零新增调用；仅流式端点生效）：understand/generate 那 12~30s 死寂转圈
#   变「看着它想」——模型 reasoning_content 增量实时推前端，与票 06 控制台合成完整故事。
#   管道裁决＝只加流式旁路不拆 invoke 收口：tracing 的 timed_invoke 旁增 timed_stream（llm.stream
#   逐 chunk 转新帧 {kind:"thinking", node, text}，单流累计截断 ≤2000 字防灌 DOM；token 经
#   _log_node 落成与 timed_invoke 同形的账本、零新增调用）。退避窄化＝仅首 token 前连接错走
#   gateway.backoff_delay 同一支曲线重开流（首 token 后断流如实炸＝帧不可回收，SSE 诚实中断先例）。
#   **仅 on_event 在场才流式**（nodes 的 _llm 助手分流）——CLI/eval 恒 timed_invoke 逐字节零变化
#   （关态姊妹钉 stream_used==0，on_event 纪律第三例）。**端点坑（探针实拍 2026-09-16）**：
#   langchain-openai 1.6 的 Chat Completions 流式路径**明确不提取** reasoning_content（docstring
#   亲荐 provider 子类），故 gateway 起 ReasoningChatOpenAI 覆写 _convert_chunk_to_generation_chunk
#   把 delta.reasoning_content 提进 additional_kwargs（invoke 不经过此钩子＝关态不变）＋build_llm
#   加 stream_usage=True（流式末 chunk 才带 usage_metadata＝token 聚合来源）。SSE 传输面 thinking
#   帧原样过流、末帧 answer 契约零动。前端 api.ts 帧联合加 ThinkingEvent（忘判 kind 过不了
#   tsc＝帧型钉）；App.tsx onProgress 把同节点相邻 thinking 并成一块（实渲 2 块而非数百帧灌 DOM）
#   渲进进度面板，stepCount 与 Console buildSteps 各排除 thinking（保票 06「N 步只数步帧」）。
#   专测 tests/test_thinking_stream.py（假模型 thinking/截断/关态不流/退避分流；故障注入
#   流模型 FlakyStreamLLM 住 fakes.py＝纪律 #5 有意例外，与 ScriptedLLM 职责正交）。
# M9 票 01 OTel 观测出口（默认关＝noop）：QADATA_OTEL_ENABLED=1 把 on_event 帧流镜像为 OTel span 树经
#   OTLP 上报（src/qadata/obs.py——埋点常驻、开关选出口，帧流是唯一埋点的第二消费者；traces.jsonl
#   审计账本与开关无关原地不动；一问＝一条 trace：web 挂 agent_id＋session_id＋轮 ts、eval 挂
#   run_id＋question_id；thinking 帧不入 span；关＝obs_for 返回 None 行为逐字节照旧，Langfuse 鉴权头
#   走标准 env OTEL_EXPORTER_OTLP_HEADERS 例见 .env.example；专测 tests/test_obs_otel.py 内存
#   exporter 断 span 形状零联网）。LangSmith 主干被否（ADR-0003），只留 OTLP-sink 插座（obs.py 模块头注释）。
# M9 票 04 词表硬资产＋预算保险丝（常开＝实测永不触发）：tiktoken 词表 src/qadata/assets/cl100k_base.tiktoken
#   入仓——缺失如实炸、无网络/len 兜底（gssc.check_vocab 装配位＋load_settings 启动双位点查）；
#   FUSE_TOKENS＝账本各场景实测最大 input_tokens×3（定阈判据落档 .scratch/qadata-m9/ticket04-threshold.md）；
#   超限才确定性淘汰（序见架构段 gssc），入账双出口＝账本 budget_fuse 行＋kind:"tool" 胶囊帧。专测 tests/test_budget_fuse.py。
# M9 票 05 摘要链（常开＝短会话零触发）：预算驱动记忆窗（MEMORY_TOKEN_BUDGET=560，K=5 降为默认换算结果）；
#   滑出轮懒补滚存摘要（挂点＝web 两端点锁内、组装前同步，无后台任务；失败入账不拦答题、游标不动下次再补；
#   会话档尾键 digest_lines/digest_upto，无尾键旧档逐字节现状；CLI/eval 单轮结构上零触发零花费）。专测 tests/test_digest_chain.py。
# M9 票 06 例题库召回（默认关）：QADATA_EMBED_MODEL 空＝未配置（端点与正文模型共用 base_url，OpenAI 兼容
#   /v1/embeddings）。唯一进料口＝qadata examples-sign --agents-dir --agent --file（每行 {q,sql,signed_by}
#   人签题对 → <智能体目录>/examples.yaml；无签拒收、会话成功轮永不自动吸收——错误自我强化被否在案）；
#   仅 serve 问数面挂接（CLI/eval 零触扫描钉），空池/未配置＝Select 恒等逐字节现状；hybrid 打分＋top-K=3
#   升序注入，失败＝降级不召回＋账本 recall 行入账照常作答（不烧生成调用数）。专测 tests/test_embedding_recall.py。
# M10 检索层——向量化通道（四路共用）：QADATA_EMBED_MODEL 空＝整层未配置（表卡/值索引/口径字典/例题召回
#   同挂这一把 embedder，端点与正文模型共用 base_url、OpenAI 兼容 /v1/embeddings，EmbeddingsClient.embed
#   自动切批 ≤20——百炼单请求条数上限实拍）。专测随各路（见下 retrieval 域条目）。
# M10 票 01/02 建索引（显式管理动作，问数路径永不建——ADR-0005）：qadata index-build <库文件>（须先配
#   QADATA_EMBED_MODEL）→ 一次建表卡＋值索引两档，落 data/indexes/<库指纹>/（库派生跟库走、serve 与 eval
#   共用一份——ADR-0004 双域；换库＝换目录＝天然作废）。逐条 embedding＝索引构建期唯一花钱处（离线、
#   几百发封顶、CLI 打印 embedded/reused/dropped 不静默）；两档各一 manifest＝四件套（model_id, content_hash,
#   built_at, db 全路径——库指纹目录是落位非字段），条目文本哈希没变不重 embed、换模型＝model_id 不合＝整档作废。
#   serve 启动逐智能体查档：有档且 model_id 对＝开，缺/坏/过期＝播报一行＋现读活库降级（绝不在请求路径建）。
#   专测 tests/test_table_cards.py（表卡粗召→精选→外键补漏＋小库逐字节现状）＋ tests/test_value_index.py（值采集封顶/出局/坏库）。
# M10 票 03 值链贴纸条（无开关＝产物即开关，spec §五「值链独立开关」被否不许上）：有值索引档＋有 embedder
#   即走——understand 现成 intent.filters 搭车抽词（零新增生成调用）＋题面 CJK 连续串（不分词）→ 一批 embed
#   → numpy 全扫 → 逐列 top-K＋相对边际（VALUE_LINK_TOP_K=5／MARGIN=0.85／MIN_SCORE=0.35，票 07 定标）→
#   值纸条块（`列 —— 库里实际这么存：…`，贴 schema 上下文最末，不改写题面）。intent=None/无关键词/缺档/
#   缺 embedder/过期/端点挂＝不贴、照常作答、value_link 行入账可见（不烧生成调用数）。专测 tests/test_value_link.py。
# M10 票 04/05 口径字典（口径唯一通道，ADR-0008）：qadata knowledge-feed --agent <hex12> --file <md/txt/csv>
#   （或 web 详情面进料门，双门共写入口 feed_knowledge）→ 条目级切块（空行分块/CSV 一行一条，否字数滑窗、
#   否 Word/PDF ETL）→ 整档一批 embed → <智能体目录>/knowledge.yaml（人进料跟智能体走、删智能体连带清）。
#   问数时按消解后题面检索 top-K（KNOWLEDGE_TOP_K=5／MIN_SCORE=0.45，票 07 定标）注入 generate 的 Select 格、
#   答案【口径说明】展示召回首行。缺 embedder＝可落盘但向量化挂账（装载闸拒读带病档、重喂补齐）。
#   专测 tests/test_knowledge_intake.py（进料/切块/挂账）＋ tests/test_knowledge_recall.py（检索/优先级/降级）。
# （M5 指标层已于 2026-09-21 退役删除——ADR-0007：QADATA_METRIC_LAYER/metrics/ 注册表/〔metric_match〕节点/
#   eval path 三字段/report --paths 全撤；轨道①历史数字见下方评参照段，票 09 段 A 已把 18 条口径并入字典料。）
```

- eval 逐题结果写 `runs/eval-last.jsonl`（每题带 run_id/llm_calls/input·output·total_tokens/latency_s，失败题也计——票 10），节点级明细写 `runs/traces.jsonl`（裸北京时间 ts＋latency_s 秒；历史行为 UTC/毫秒旧格式，不迁移）；轮末逐题汇总另落 `runs/tokens-<run_id>.json`（含 model），`--budget` 给定后自动向账本追加一行（成本/累计列留待填保持人审）；两者与 `data/` 均不入库。抽样跑分固定 seed=42 可复现。
- 评测参考值：M1 基线 BIRD dev-100 **63.0%**（qwen3.7-flash）；50 题配对 M2 **58.0%** → M3 **64.0%** → M4 终局运行 **60.0%**（deepseek-v4-flash-0731；M4 表观 Δ 被端点跨时段漂移淹没——同代码同日 32→36→29/30，证据链在 `.scratch/qadata-m4/retrospective.md`（本地），各线增量以受控探针/同时段配对为准，不以跨时段 Δ 计）。冒烟 10 题固定集回归门槛在 `tests/smoke_baseline.json`（M5 收尾重建 **10/10**，绑 f16fc7a＋glm-5.2；满分闸语义＝链路健康闸＋噪声带参考，数字回归判读归同时段配对）——**与模型绑定，换模型必须重建基线**。
- 轨道①（M5 指标考卷）固定题集 `tests/m5_financial_ids.json`：financial 106 题按 BIRD 难度分层 seed=42 抽 50（simple 29/moderate 18/challenging 3），一经冻结不得换题（换题＝历史数字作废，ADR-0001）；纯 SQL 对照分母 **27/50（54.0%）**，绑定 deepseek-v4-flash-0731（明细见 runs/m5-track1-baseline-report.md，本地）。**模型缺口警示**：.env 模型随订阅切换（deepseek-v4-flash-0731→09-09 kimi-k2.7-code→09-10 glm-5.2，端点同为百炼 coding plan）——轨道① on/off 配对须同模型同时段，跨模型数字不可比、各判卷绑实跑模型（票 06 定稿绑 kimi）。**票 06 判卷定稿**（09-09 kimi 同时段配对，同 commit `efea1cf`）：对照轮 28/50 vs 指标轮 29/50、**命中率 0/50**→①②「样本不足，无结论」、③判过→**metric_layer 默认保持 False**；零命中在体归因＝BIRD 题面普遍组合约束、与 18 原子口径交集≈0（L2 NONE 36 题基本判对、⑨闸在体拦 8 题零误伤、填槽拦 6 题），`.scratch/qadata-m5/retrospective.md`（本地）＋`runs/m5-track1-pair-report.md`；kimi **同码路径同时段逐题翻转可达 ±5**（29 vs 28 的 Δ+1 无统计含义）——此后 kimi 配对按该噪声带解读。
- 票 02 意图尾段注入（条款④）**判负成文**（2026-09-09，kimi 同日配对 32/50 vs 32/50、翻转 3/3、1466 分离探针证翻转不可复现＝注入信号≤载体 A 改写漂移噪声带）：软用途入墓地，载体 A 意图保留（现四字段，消费者＝值链搭车；metric_match 填槽与 evidence_terms 展示消费者已随 ADR-0007/0008 退役），回炉裁决与全证据链见 `.scratch/qadata-m5/issues/02`。
- **M5 收尾（票 08，2026-09-10）**：票 01–08 与 10 全部落袋，复盘见 `.scratch/qadata-m5/retrospective.md`；账本闭合 ~¥5.8/¥10（影子折算口径——coding plan 订阅逐行无现金实付可计，价格锚 ~¥3.9/M，owner 可掌真实目录价平价替换）；单次 ≤¥3 红线全行满足、砍线规则全程未触发。票 09（B v2）**wontfix 成文**（2026-09-10，用户批准存在性探针 ¥0.08：三靶 0/3 命中「看不见取值」病灶——51 靶消失、326 迁至 JOIN 语义、407 实为 DISTINCT 判分形态；未进注入实现与配对，证据链在 09 票 Comments）。
- 票 04 注册表**定稿入库**（2026-09-09 owner 拍板「全过」，提交 054388b，护栏①放行凭证在 YAML 文件头）：`metrics/financial.yaml` 18 条原子指标（六要素＋中英别名）。**库实际坑**：被测库五个日期列全为文本 YYYY-MM-DD，与 database_description 声称的 YYMMDD 不符——填槽字面量一律按库实际；槽标签以题面措辞为准（载体 A filters＝题面原样摘录，等值匹配容不得注释措辞）。裁决⑧⑨移交票 05：prompt 强调时间 filters 输出裸时间形；L2 指令「题面求具名极值/比较→NONE」防包含路径误命中户均条（票 06 设靶）。
- **M9 收尾（票 07，2026-09-20）**：票 01–07 全部落袋——OTel 出口／trail 入档／GSSC 字节等价收编／tiktoken 保险丝／摘要链懒补／embedding 召回例题库／文档收口（本条即移交锚）；全程零 eval 花费，新能力一律默认零触发形态。**embedding 召回配对 eval 留 owner 签题日**（启用即签库：`examples-sign` 人签首批 ＋ `QADATA_EMBED_MODEL`，无独立开关）；升级判据转正 README 明文（usearch 阈值／分层召回／LangSmith-OTLP-sink，代码锚点 obs.py·examples.py 模块头互指）；防回锅登记在 `.scratch/qadata-m9/spec.md` §五（骨架重排／服务级向量库／LangSmith 主干／滚动重压·多层归并／例题库自动吸收／LLM 压资料类／「演示·访客体验」回锅论证沿用 M7 裁定——各案重提前先读），词汇与决策固化 CONTEXT.md＋ADR-0002/0003；复盘与档案 `.scratch/qadata-m9/`。
- **M10 收尾（票 08，2026-09-22）**：票 01–08 落袋＋追加裁决（票 09 段 A/B＝M5 注册表退役 ADR-0007、evidence 直塞层退役 ADR-0008——2026-09-21 owner 直裁，字典＝口径唯一通道）——检索层三路通道（表卡粗召 `COARSE_TOP_K=120`／值链三旋钮 `VALUE_LINK_TOP_K=5`·`MARGIN=0.85`·`MIN_SCORE=0.35`／口径字典 `KNOWLEDGE_TOP_K=5`·`MIN_SCORE=0.45`）＋一归位（例题住 `retrieval/examples.py`）；三旋钮定标值＝票 07 免费全科回写（表卡逐表族口径 96.0% 过 ≥95% 线、值链入条率 66.7%→90.5%（n=42 薄样注记）、字典召回 7/10→8/10），专测 `test_table_cards`·`test_value_index`·`test_value_link`·`test_knowledge_intake`·`test_knowledge_recall`·`test_embedding_recall` 钉死。**判卷数字诚实（票 07 复盘 §5/§6）**：四轮付费考卷全部被端点事故污染（超时/403 占错题 38–79%、四模型同坑＝事故随端点不随模型），表观分只作参考读数——**「大库正确率平价」与「撤 evidence 门禁」两个裁决，数字面各欠一顿端点健康期的干净重跑（补跑协议在册 `.scratch/qadata-m10/retrospective.md` §6）**；确定性交付（夹具＋离线两率全科＋三旋钮定标回写＋判卷工具链）＝本票真判词。账本 ~¥3.1/¥6。防回锅登记在 `.scratch/qadata-m10/spec.md` §五（懒建／独立 LLM 抽词／题面改写／列级索引／MinHash-LSH／Word-PDF ETL／服务级向量库／M9 旧 ~5 万阈值／KB 替代 evidence／零触钉扩用／**值链独立开关——不许上**——各案重提前先读；「top-10 够用论」已被率一定标判负一次，重提前先读 §2 v1/v2 夹具判废）；词汇与决策固化 CONTEXT.md＋ADR-0004/0005/0006→0008；本票零代码（文档收口＝叙事资产转正）。
- 评测花费红线：单次对比 ≤ ¥3；测试不得依赖真实 API/网络。

## 架构

六节点＋双条件边的自纠错状态机（M5 可选指标层节点已退役，ADR-0007）：

```
question → understand → explore → generate → execute ──成功──→ verify ──通过──→ respond
                                                    ↑ 失败且有预算──┘  └──可疑且有预算──→重试
                    （预算耗尽：失败→respond 兜底；可疑→respond 带校验标注）
```
- ~~指标层~~（M5 票 05 立、**2026-09-21 退役删除 ADR-0007**：两级匹配/填槽渲染/⑨闸/path 三字段随 metric_match 节点整体下线，历史判词见下方 M5 评参照段）。
- 重试预算不新增状态键：`len(attempts)` 即账本（默认 3，`Settings.retry_budget`）；generate 提取失败也写 `SqlAttempt(sql="", error=…)` 入账。
- **精准模式（M4-C，默认关）**：`Settings.precise_candidates>1` 时 generate 同 prompt 连打 K 发＝**一轮账本**（`build_llm` 按候选数切 `precise_temperature`）；execute 票决（结果级多数派，纯函数 `graph/precise.py`），票决不能自证——并列无多数派取最大组代表转 verify 强判可疑进重试/标注；载荷走唯一新状态键 `precise_candidates`（非预算键）。**注意**：探针实证该端点运行内采样噪声≈0（temp=0.3 三发结果恒收敛），自一致性无收益前提，工作点决策留 M5。
- `graph/state.py`：`AgentState` TypedDict（total=False），**无 reducer，各键整值覆盖**；含 `original_question`（改写前原问题）、`verify_note`（可疑原因，None=通过）、`intent`（M5 载体 A：understand 同调**四字段**意图〔metric_mention/evidence_terms 已随 ADR-0007/0008 退役〕，None=解析失败回退，**勿再喂 prompt**——尾段注入④判负已拆，`test_generate_never_reads_intent` 钉死；消费者＝值链搭车抽词）。`evidence` 状态键与 `matched_metric`/`metric_note` 载荷键均已删除（ADR-0008/0007）、`session_context`（M7 票 05 唯一新状态键，precise_candidates 同款载荷纪律：{"turns": L2 窗口原文行·时间升序（M9 票 05 预算驱动，K=5＝默认换算结果）, "draft": L1|None，摘要链在场时加 "digest_lines"}，仅 understand/generate 消费——AST 源扫描＋respond prompt 带/不带会话逐字节一致姊妹钉（tests/test_session_context.py），None/缺键＝关态逐字节一致；可调用对象（recall/obs）不进状态键＝checkpointer 序列化不相宜，走 build 末位参；组装与三层记忆纪律见 web 段）。
- ~~`graph/metrics.py`~~（整文件已随 ADR-0007 退役删除；`metrics/financial.yaml` 定稿注册表的历史见 M5 评参照段，18 条口径经票 07 段 A 并入字典料）。
- `graph/intent.py`（M5 票 02）：`parse_understand_response` 纯函数（JSON 提取/宁空勿造归一/失败回退原文不烧预算）；意图消费者＝值链搭车抽词（M10 票 03）；metric_match 填槽与 evidence_terms 展示消费者已分别随 ADR-0007/0008 退役。M8 票 03 扩返**三元组**（第三元＝澄清问，仅 JSON 对象里非空字符串才采信——回退态/坏 JSON 恒 None，与 question/intent 判定正交）。
- `graph/nodes.py`：`make_nodes(llm, tracer, settings)` 节点工厂，闭包注入依赖以便测试替换假模型；respond 失败路径不调 LLM（永不编造）；**答案稳定性回退**：执行失败耗尽且最后尝试为执行失败形态时，respond 重执行 `_last_good_sql(attempts)`（从 attempts 派生，不新增状态键）作答并标注；**E1 三节答案（票 07）**：只有【结论】来自 LLM（M5 起索要一句话；**M8 票 07 升级为报告式总结**——markdown 行文、结构自定、含量化发现，不套固定骨架，SYSTEM_RULES 前缀与追加尾部纪律不变），【数据依据】（行数/范围/所用表，表名＝sqlguard::used_tables 确定性派生）、【口径说明】（ADR-0008 起＝本题字典召回块各条首行摘要，memo 共担零新调用；未挂字典/无召回＝空节省略〔注册表血缘与 evidence 命中项两分支已随 ADR-0007/0008 退役〕）、【校验标注】（verify_note/截断/回退/模板降级原样并入）全部代码组装（`compose_conclusion` 纯函数），空节省略不空转；判分在结果集层不读 conclusion，形态变更零评测风险。**票 03 进度帧**：`make_nodes` 加末位可选参 `on_event`——start 帧由节点包装层统一发，结果帧各节点出口显式发（execute 失败帧带 error_hints 中文修复建议＝页面所见即重试历史所读；attempt＝该时刻已入账尝试数、重试环上单调推进）；status 一行中文后端单源，execute/verify 两个纯码节点从此有事件（tracer 只挂 LLM 调用不够用的缺口在此补上）；M8 票 03 改判再加末位参 `hitl`——真时澄清走节点内 `interrupt()` 暂停（与 build 侧 checkpointer 装配成对），假＝直 END 形态照旧；M8 票 06 帧喂厚——结果帧（`_emit`）加 `ok/duration_ms/tokens_in/tokens_out`（start 帧三字段不动，ok 红＝硬失败形态：执行/提取失败、如实报失败；降级/可疑/未命中不算红），新增 `_tool` 发 kind:"tool" 子事件帧（execute 内 execute_sql/票决批量、respond 内回退复执行；explore 子步骤在 schema.py 内同源发射，on_event/sink 沿参透传），token 走 `timed_invoke` 末位可选参 sink（{"tin","tout"} 累计＝逐调用流进该步结果帧，图与 tools 每请求各建一份闭包无串扰）。**M8 票 08 思考流**：`make_nodes` 内加 `_llm(prompt, node)` 助手——on_event 在场时 understand/generate 走 `timed_stream`（逐 chunk 推 `{kind:"thinking", node, text}` 帧），关态恒 `timed_invoke` 逐字节一致；仅这两节点流式，其余照旧（metric_match 已退役 ADR-0007）。
- `graph/verify.py`：规则校验器（空结果/聚合异常），确定性不烧 token；M3 精准化：「列出全部/所有/有哪些」类问题豁免空结果判可疑，截断不再触发重试（由 respond 标注）。
- `graph/error_hints.py`：SQL 错误分类→中文修复建议（规则版，零 token），进失败历史段。
- `graph/prompts.py`：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，动态内容只能追加尾部），含口径规则（禁格式化输出、只选问题需要的列、题面明示精度/百分比形态时从题面——M4-D 规则 5，228 探针实证）；`format_failure_history` 失败历史段（含修复建议）；`strip_conclusion_prefix` 防复读（剥多层）；`format_session_history`/`format_session_draft`（M7 票 05：L2→understand、L1→generate 尾草稿渲染，零 LLM 确定性拼装，空块＝关态逐字节一致）；M8 票 03 `understand_prompt` 尾追澄清指令段（`clarify` 参——开关开**且**题面不含「补充说明：」标记才加，含负清单收紧产出面；两态不满足时与参数加入前逐字节一致）；`compose_supplement`＝续轮合成公式单源（HITL 恢复态与 web 轮次归档同式，防两处字面漂移）。M9 单源字面再扩：`EXAMPLES_HEADER`（参考例题节头——渲染 format_examples_block 与 gssc 淘汰共读，FAILURE_HISTORY_HEADER 先例）、`MEMORY_TURN_PREFIX/FAILED_TURN_SUFFIX`（记忆行前后缀——渲染、_turn_cost 预算计量、gssc 淘汰三处共读，措辞漂移不静默错价窗口）、`digest_turn_prefix/digest_para_prefix`（「第N轮：」「第a-b轮：」前缀语法——素材拼装、严格回读、折段指令三处共读）、digest_prompt/digest_fold（懒补指令单源）。
- `graph/build.py`：`_route_after_execute`/`_route_after_verify` 为纯函数路由（独立测试）；`_route_after_understand` M8 票 03 首判 `answer→END`（直 END 形态，第三参 clarification 随开关启用——关态逐行为一致且路由 map 的 END 分支根本不装配，编译图出边集有结构直测）；`run_question` 是最外层守护——任何裸异常收敛为诚实失败的 `Answer`；改判（经典 HITL）＝末位成对参 `thread_id＋checkpointer`（成对时澄清走节点内 `interrupt()` 暂停，`__interrupt__` 面收成澄清 Answer；不成对自动降级直 END——配对纪律在守护闸口守死，调用方免配平）＋新出口 `resume_question`（`Command(resume)` 续跑原 thread，understand 重放＝既定代价，全程 4 call < 无状态往返 6 call）；票 03：末位可选参 `on_event`（节点级进度帧 `{node, attempt, status}`）沿 `build_graph→make_nodes` 闭包注入——缺省 None＝零包装逐行为一致（专测 `tests/test_on_event.py` 钉死：关态双跑、CLI/eval 调用面源码出现 on_event 即红、假模型完整帧序列含失败→重试→成功与预算耗尽形态）；M9 末位再沿两参——`obs`（票 01，帧流→span 镜像器，缺省 None＝零挂接逐字节照旧，run_question 守护的 finally 收口 close 幂等）与 `recall`（票 06，例题库召回回调 question→注入块，缺省 None＝Select 恒等；沿参 run_question/resume_question→build_graph→make_nodes，on_event 末位纪律同款、不进状态键——可调用对象×checkpointer 序列化不相宜）。
- `graph/gssc.py`（M9 票 03–06，图内一切进模型内容的唯一出口，ADR-0002）：GSSC 流水线＝Gather 收料（gather_* 五路读状态＋格式化素材，节点侧不伸手拿料）→ Select 挑选（票 06 上岗：generate 场景经 recall 回调把召回块放进 "examples" 槽，缺省/非 generate/空块＝原对象恒等）→ Structure 分区落位（六分区 Zone 枚举＝装配器内部分类法、**非统一字节顺序**——五场景维持现状字节序、各区落位有钉；骨架重排被否勿回锅）→ Compress 预算保险丝（票 04：tiktoken cl100k `count_tokens` 前置计量、`FUSE_TOKENS` 实测最大×3，限内＝恒等拼接逐字节零变化；超限淘汰序＝丢最老窗口原文行→摘要行→纪要段→撤值纸条→撤字典块→撤参考例题→砍值采样→缩失败历史、任务/输出永不砍、无料可砍如实入账不硬砍（值纸条最可再生＝index-build 一键重得故排例题前，字典块撤了随时重召回同理、人签例题系 signed_by 稀缺资产让位在其后——M10 票 03/05 接线）；挂接面＝nodes×4＋schema 选表×1，on_compress 缺省 None＝纯函数面零变化）。`check_vocab`/`VOCAB_PATH`＝词表硬资产位点闸；`fuse_ledger_fields` 账本键名单源。验收＝tests/test_gssc.py 五场景「装配器出口 vs 旧路（prompts.py 旧装配函数原样保留＝oracle）」双跑逐字节对拍＋收编布线钉（直呼旧装配即红）。**图外唯一例外**＝票 05 web 侧懒补摘要 prompt（不在图装配路径、单次有界，注释在册）。
- `obs.py`（M9 票 01，trace 出口，ADR-0003）：on_event 帧流→OTel span 树镜像——`obs_for(settings, name, attrs)` 开关闸（关/无 settings＝None＝调用面零挂接；开＝`install` 一次装 OTLP 出口，resource 带 model/git sha），`Obs.mirror()` 包一层 on_event 帧先镜像再转原消费者（tool 帧＝孙 span、ok=False＝ERROR 红态、thinking 不入 span、串联键复制进每 span＝Langfuse 过滤要求）、`close()` 幂等兜 interrupt 暂停；`qadata_no_stream` 标记沿 M8 票 08 纪律（观测/入档链任何组合不启流式）。`shutdown()` 轮末冲刷批缓冲（CLI ask/eval 与 serve 收口位）。观测三角色分工见 README「可观测性」段。
- 沙箱四层：① `tools/db.py::open_readonly` 只读连接唯一入口（URI 转义）② `tools/sqlguard.py` sqlglot 语句校验（解析→单语句→根白名单→表名校验；另供 `used_tables` 纯函数给 respond 数据依据节——显示辅助，认不出返回空表绝不抛，拒绝语义仍归 validate_sql，空名表节点的历史拒绝行为两侧语义各钉死）③ `tools/executor.py` 资源层（progress handler 5s 超时中断＋双行数上限：显示 `max_rows`/获取 `fetch_cap=1000`，撞顶用 COUNT(*) 报真值）④ sqlite 物理只读。`list_tables`/`get_schema` 认表也认视图（M3）。
- `tools/schema.py`：schema 上下文可附 `database_description/{table}.csv` 列注释（BIRD 官方，按选中表注入，`utf-8-sig + errors="replace"` 容错编码）；M8 票 02 值采样（`column_value_samples`，默认关）＝对最终入选表按列采样——`SELECT DISTINCT col FROM (SELECT … LIMIT 窗口) ORDER BY LIMIT 11`，文本亲和列、每表 ≤8 列×全局 ≤2000 字确定性裁尾，低基数全枚举（'gold' 大小写病灶）、高基数统一「存储形态示例」不冒充全量（日期分支废除中——真库 A4 区名列被日期正则误判注无意义范围，宁漏勿错），有界窗口把百万行表从 14s 压进毫秒带且双跑同文，一切失败静默跳列（锦上添花不连累本体，`load_description` 先例；M10 票 01 起私有升公开，供表卡描述料共读）。
- `eval/`：`bird.py` 跑分器（逐题异常隔离，`--ids` 固定题集，`--out`/`--resume` 断点续跑，逐题 flush；记录含 `gold_sql`/`gold_failed`/`error_class`；M4：`--concurrency` 每题独立分片＋主线程单写者收口合并、非续跑先清遗留分片、`--skip-respond` 评测模式省调用）；`qtypes.py` 题型标签器（规则六类，词边界防误命中，题面优先）；`match.py` 判分 = 顺序无关多重集匹配，`_sort_key` 类型分层是为修 None/数值混排崩溃的假阴性 bug——勿动，回归测试钉死；`report.py` 两轮 diff/变体对比；`variants.py` 变体矩阵驱动（variants.yaml）。
- `llm/`：OpenAI 兼容网关（temperature=0；精准模式候选>1 时 `build_llm` 切 `precise_temperature`）＋ `invoke_with_backoff`（429/5xx/连接错指数退避重试，鉴权错立即失败；退避曲线单源 `backoff_delay`）＋ `ratelimit.py` 全局限速器（`--qps`，多线程共享单实例）＋自建 JSONL tracing（不用 LangSmith）。**M8 票 08**：`build_llm` 返 `ReasoningChatOpenAI`（ChatOpenAI 子类，覆写 `_convert_chunk_to_generation_chunk` 把流式 delta 的 `reasoning_content` 提进 additional_kwargs——langchain-openai 1.6 Chat Completions 不提取非标准字段；`invoke` 不经过此钩子＝关态逐字节不变）＋ `stream_usage=True`；`tracing::timed_stream` 流式旁路逐 chunk 推 thinking 帧、token 终帧经 `_log_node`（与 timed_invoke 共用收口）入账本零新增调用。**M9 票 06** 增 `EmbeddingsClient`/`build_embedder`：OpenAI 兼容 /v1/embeddings 与正文模型同端点，协议面只有 `embed(texts)→向量组`；自持一把 `QADATA_MAX_QPS` 限速（只限向量线，如实注）、不做退避重试（失败必降级，重试＝第二次花费不值）。注意：取回复用 `.content`（`timed_invoke`/`timed_stream` 同语义），`str(AIMessage)` 的 repr 转义引号会炸 sqlglot 提取。
- `retrieval/`（M10，三路通道一归位＋库域档面，ADR-0004/0005）：统一检索层住此包、全经 **GSSC Select 插座**消费——图不加新节点，检索只活在「进模型前」，任何一路炸＝降级现状路径＋账本入账、**永不因检索挂而拒答**。归属按料的寿命周期分双域（ADR-0004）：**`store.py`**＝库派生索引跟库走（`data/indexes/<库指纹>/` 两档 `table_cards.yaml`/`value_index.yaml`＋manifest 四件套，坏档如实炸 `RetrievalError`、缺档＝None＝默认关、`index_status`/`value_index_status` 供 serve 现读降级播报）；**人进料知识跟智能体走**（`examples.py` 例题库、`knowledge.py` 口径字典，删智能体连带清）。
  - **宽进窄出**（`cards.py`，票 01）：`build_table_recall`＝大库粗召回调（题面 embed→numpy 全扫余弦→候选表名 top-COARSE_TOP_K=120，票 07 定标：spec 起步值 top-10 在 391 表合成库逐表入率仅 63.2%＝结构不达线、K=120 逐表族 96.0% 过 ≥95%），只在大库分支（schema 超 `FULL_SCHEMA_LIMIT`）由 explore 消费——粗召宽进防漏表、既有 `_pick_tables_with_llm` 窄出防错用、`foreign_key_closure` 确定性补外键亲戚表（三段不合并＝漏料与错选分开记账、判卷分开归因）；**小库根本不读索引文件**（全量路在粗召闸之前，逐字节现状钉）。专测 `tests/test_table_cards.py`。
  - **值纸条**（`values.py`，票 02 采集＋票 03 查询）：`build_value_index`＝离线建值档（文本亲和列→CHESS 式黑名单子串跳过→有界 DISTINCT→`VALUE_INDEX_TOTAL_CHARS` 字符预算封顶、超预算整列如实出局）；`build_value_link`＝值链全程（intent.filters 搭车抽词＋题面 `extract_keywords(cjk=True)`→一批 embed→numpy 全扫→逐列 top-K＋相对边际 VALUE_LINK_TOP_K/MARGIN/MIN_SCORE 三旋钮→值纸条块贴 schema 上下文最末，`VALUE_STICKER_HEADER` 节头单源、与 gssc 淘汰共读）。恒返回回调（无开关、缺料入账可见）。专测 `tests/test_value_index.py`＋`tests/test_value_link.py`。
  - **口径字典**（`knowledge.py`，票 04 进料＋票 05 查询）：`feed_knowledge`＝唯一写入口（md/txt/csv 条目级切块→同文去重→整档一批 embed→`knowledge.yaml`，缺 embedder＝可落盘但挂账、装载闸拒读带病档）；`build_knowledge_recall`＝按消解后题面一次 embed→numpy 全扫→KNOWLEDGE_TOP_K/MIN_SCORE 截尾→`KNOWLEDGE_HEADER` 字典块（ADR-0008 起＝口径唯一通道），答案【口径说明】读其召回条目首行。专测 `tests/test_knowledge_intake.py`＋`tests/test_knowledge_recall.py`。
  - **例题库归位**（`examples.py`，M9 票 06→M10 票 06 平移入域，只搬家不重铸、档面仍住智能体目录原位零动）：`sign_examples` 人签唯一写入口（无签拒收＝自动吸收机制拒绝）、`build_recall` hybrid（关键词精确命中作第一排序键＋向量点积次键、RECALL_TOP_K=3 升序注入）。升级判据（ANN 单档：条目破几万或 numpy 全扫进秒级才议 usearch、仍是文件非服务；分层召回＝大库真瓶颈）注释在 `examples.py`/`__init__.py` 模块头＝README 明文互指锚。专测 `tests/test_embedding_recall.py`。
  - **现读降级**＝四路统一姿态：缺档/坏档/过期（model_id 不合）/端点挂→退回「现查活库、不贴纸条、不塞候选」现状路径并如实入账，绝不在问数路径建索引、绝不因检索挂拒答（`cards`/`values`/`knowledge`/`examples` 各闭包内自持降级/memo/过期闸，nodes 层零知情）。图侧唯一入口＝`build_graph`/`run_question`/`resume_question` 末位可选参 `table_recall`/`value_link`/`knowledge_recall`（＋M9 的 `recall`），缺省 None＝逐字节现状（on_event 末位纪律同族、可调用对象不进状态键）。
- `cli/main.py`：argparse+rich 薄壳，核心逻辑全在包内；`run_question` 顶层导入以便 monkeypatch；`serve` 只做参数转交（真逻辑在 `web/serve.py`：load_settings→build_llm 共享实例→embedder 仅 QADATA_EMBED_MODEL 非空时构造→create_app，开态出口/召回各播报一行）；`examples-sign`＝例题库唯一进料 CLI（调 retrieval/examples.sign_examples，零触扫描钉保证 CLI 外问数面零挂接）；`ask`/eval 轮末 obs shutdown 冲刷（票 01）。
- `web/`（M7 票 01/02 → **rev2 票 02.5 智能体化**，A 轨预置库/双轨制已拆——判卷以 spec 修订段＋票 02.5 为准）：`app.py::create_app(agents, llm, settings, static_dir, tracer, embedder)` 应用工厂（agents 必填、余皆注入——DI 接缝，契约测试 TestClient＋ScriptedLLM＋tmp AgentStore，不碰网/不碰真实目录；embedder＝票 06 向量化通道缝，生产 build_embedder/测试 FakeEmbedder、None＝召回未配置）；面＝`GET/POST /api/agents`、`PATCH/DELETE /api/agents/{id}`、`POST /api/agents/{id}/datasource`（raw-body 字节流**上传唯一路**——spec 冻结依赖不走 multipart，固定落盘 source.sqlite、换库＝覆盖、端点全程零建连）、`GET /api/model`（**只读卡**：当前模型名、真源 .env、writable=false）、`POST /api/ask`（请求体 `db`→`agent_id`，**响应 12 字段冻结不动＋票 04 可选 `chart`＋M8 票 03 可选 `clarification`＝曾 14 字段**（2026-09-21 注：M5 三键退役后重数＝11 字段，ADR-0007；澄清轮＝可选位出真值、failed=False；**经典 HITL（owner 改判 2026-09-16，取代无状态前端合成裁决）**＝带会话时 understand 节点内 `interrupt()` 暂停、下一条消息原样发回即 `Command(resume)` 续答（服务端合成归档 4 call 全链；`_route_clarification` 两端点唯一分流闸口），单轮/无 checkpoint 配置＝直达 END 1 调用形态照旧；pending 指针住进程内（MemorySaver＋dict，重启丢在途澄清＝在册上限，升级路径 langgraph-checkpoint-sqlite）；**澄清轮不落盘不变**——回放经 GET 会话详情 `pending` 字段恢复（懒建档无档＋有 pending＝空档放行，无档无 pending 照旧 404），请求位 `discard_pending`＝弃续答按新话题问、归档成用户真话）；票 05 起请求可选 `session_id`——带＝装载三层记忆＋问完落盘一轮、响应 session_id 真值回显，单轮照旧 null；口径通道＝字典单源（ADR-0008：请求 evidence/智能体 evidence 字段与引用派生全撤，老客户端残键＝忽略不报错有测钉；无数据源/非法会话 id 均拒在调模型前）、`POST /api/ask/stream`（票 03 进度流：SSE 帧三型（票 06 喂厚）＝start `node/attempt/status`／结果帧＋`ok/duration_ms/tokens_in/out`／`kind:"tool"` 子事件帧，末帧 `event: answer`＝契约本体与阻塞端点同源不漂移（票 06 末帧零动）（chart/session_id/轮次落盘一判双达同经 `answer_to_payload`＋`_finish_ask` 一个收口，两端点不漂移）；前置拒绝与 /api/ask 同序同文案、拒在起流前；**在途锁**两端点共用一把、忙 409——票 05 键升格 session_id（单轮维持 agent 级、`s:`/`a:` 前缀分域防同值撞锁；键与文案单收 `_acquire_ask_lock` 防双份字面量漂移））；票 05 会话面＝`GET /api/agents/{id}/sessions`（侧栏列表：title＝首问/updated/turn_count，懒建档故只认有轮次者）＋`GET .../sessions/{sid}`（重开回放＝问答本体＋**轮可选尾键 trail 透传**（M9 票 02：步骤帧＋tool 帧精简留痕、thinking 不入档、≈1KB/轮，trail_entry 帧→条目原样即帧＝形状单源；旧档无此键形状不动））（会话 PATCH 面已整面撤除——票 09 owner 裁 2026-09-15 随 fresh_topic 人肉闸撤端点，话题连续性改模型隐式判、会话面无运行时写入口；无 DELETE＝票未划；旧档案残留 overlay/fresh_topic 键＝读取忽略有兼容专测，PATCH 405 有钉）；`charts.py::decide_chart`（票 04 图型判定**规则纯函数**：时间列→折线、类别＋数值→柱、一行一列纯数值→大数卡、其余→null＝表格；列**下标**寻址（列名可重复当不了键）、数值列 >6 不画半张图判表格、时间/数值要求列内全部非空同形态、bool 非数值；AST import 纪律测钉死不碰 LLM/图/库，并纳入 web 包禁 sqlite3 循环）；`sessions.py`（票 05）＝会话落盘＋三层记忆组装：一会话＝`data/agents/<agent>/<sid12>.yaml`（文件即数据库沿 AgentStore，删智能体连带清，坏文件整列报错；懒建档；sid 与 agent id 共用 `is_hex12` 焊穿越）——`build_session_context` 纯函数组装（M9 票 05 换代）：**预算驱动窗口**＝`_window_count` 从最近往回收原文轮直至 tiktoken 预算（MEMORY_TOKEN_BUDGET=560＝票 04 现实最坏行实测换算；L2＝问题/SQL/行数/标量头部→understand 消解；failed 轮剥 SQL 只留问题标失败；K=5 降为默认换算结果——小行短会话全收＝零滑出零触发）、**滚存摘要链**＝滑出轮不丢失、`catch_up_digest` 懒补（挂点＝两端点锁内同源、组装前同步补齐、无后台任务；每滑出轮一行冻存摘要、满一批 DIGEST_BATCH=10 折一条标轮次范围段落行、段落溢出丢最老＝第二层归并不做有 ponytail 判据在册；严格回读不合不补——宁可不补不可补错；失败＝digest outcome 行入账＋本轮照常作答＋游标不动下次再补；压手＝正文 LLM 本体经 timed_invoke 真名入账＝真滑出才开真调用；注入形态段落行→行链→窗口原文远→近，淘汰接线先丢行后丢段与票 04 咬合）、L1＝最近成功轮完整 SQL＋头部摘要→generate 尾部草稿（failed 轮→不给草稿，"错误草稿不传染"最保守读法；票 09：人肉闸撤后草稿常给，"与上问无关则忽略"授权进节头、连续性模型隐式判——判错方向宁多带勿错切，多带的旧史由沙箱/verify 兜住）、L3 全史只落盘**永不进 prompt**；`result_head` 摘要（标量→值否则头部 3 行）；`append_turn` 落盘归档（闸复位语义随票 09 消亡；末位可选参 trail＝轮留痕尾键，缺省/空不写键逐字节照旧，采集单点 `_route_clarification` 两端点＋HITL 三路同源、无直播消费者打 qadata_no_stream 标守 timed_invoke，eval/CLI 不经收口零染指，M9 票 02）。**会话档可选尾键三件套**＝`trail`（轮步骤＋tool 帧精简留痕原样即帧，thinking 拒收，回放 Console 与直播同组件同形）＋`digest_lines`/`digest_upto` 游标（票 05，坏摘要链整档如实报错、无尾键旧档装载形状＝入档前逐字节现状）——观测三角色分工入册：会话档管「用户当时看见什么」（trail）、trace 管「机器内部怎么跑」（OTel→Langfuse）、审计账本 traces.jsonl 管判卷与预算真值。图侧 `session_context` 唯一新键（precise_candidates 同款显式键纪律）仅 understand/generate 消费（AST＋respond prompt 带/不带会话逐字节一致姊妹钉，tests/test_session_context.py）；零新增 LLM 调用、每轮 attempts 独立入账、无会话关态逐字节一致各有专测；`agents.py::AgentStore` 文件即数据库（`data/agents/<uuid12>/{meta.yaml, source.sqlite, sessions/}`，**真空启动零预置**，删智能体＝删目录；名称可重复、uuid 是键；口径管理面唯一＝`knowledge.yaml` 字典（knowledge-feed CLI＋web 双门，ADR-0004/0008；meta 的 evidence/metrics_ref 旧键＝读取忽略有兼容钉）；坏 meta 整列报错不静默吞）。构建产物 `web/dist` 同源服务（index＋/assets 挂载在 API 路由之后，未构建时 "/" 出占位页）。前端 `web/`（Vite＋React＋TS，状态路由不引新依赖；票 04 唯一新依赖＝recharts（owner 预裁，echarts 不议））三视图：首页（模型卡＋智能体卡片＋创建表单）／详情面（基本信息/数据源/口径字典进料/预设问题 ≤10；业务知识编辑区已随 ADR-0008 撤）／对话页（票 05 会话制：sid 前端 crypto.randomUUID 截 hex12、进页/＋新建会话＝换新 sid 懒建档、换话题＝直接打字（票 09 撤 ↺ 按钮与闸，understand/generate 两节头显式授权"无关则忽略"）、侧栏历史会话真落点（点击回放＝问答本体＋trail 留档（M9 票 02 起入档；旧档无＝空态照实））、预设问题 chips 点击＝与手输同路零新通道；右对话＋票 03 进度流面板：逐帧直播自纠错、完成后折叠留档于答案下，在途锁前端侧＝锁输入与 chips 与侧栏条目，SSE 用 fetch 手解不引依赖；M8 票 06 任务控制台＝chatshell 右抽屉 `Console.tsx`（既有帧流的纯视图模型，零新依赖）：概览四卡（步骤/成功率/输出/token·费用——费用卡＝token 合计×影子锚价 ~¥3.9/M 如实标「影子折算」，且 token/费用只活在控制台不进答案报告，owner 裁 2026-09-15）＋追踪时间线（每步 N 工具·Xms、工具胶囊绿/红点）；聊天「工作过程」折叠与抽屉共用同一帧型标签映射（Console.tsx 单源，ProgressRow 升级）；帧类型联合 StepEvent|ToolEvent（忘判 kind 过不了 tsc＝帧型钉）；回放态控制台复形（M9 票 02：档案 trail 与直播帧同源同形，SessionTurn.trail 可选尾键喂同一 buildSteps，旧档仍空态文案）；M8 票 08 思考流＝api.ts 联合再加 ThinkingEvent（`kind:"thinking"`）＋App.tsx onProgress 把同节点相邻 thinking 并成一块渲进进度面板（治死寂转圈、防数百帧灌 DOM），stepCount 与 Console.buildSteps 各排除 thinking（「N 步」语义与票 06 一致），styles.css `.thinking` muted 折行限高滚动；末帧 answer 契约零动；M8 票 07 答案报告化＝结论段走 `Markdown.tsx` 子集渲染器（标题/加粗/行内码/列表/表格/围栏代码→React 元素，纯函数 parseMarkdown＋零 raw HTML；分节白名单 SECTION_RE 只认后端 compose_conclusion 四节头——报告正文里的【】不被误切成节，机制闸不靠指令；表格复用 .result-table、代码块复用 .sql 零新样式面）；M8 票 03 澄清＝「？ 待澄清」徽标（label 承载语义）＋**经典 HITL**（改判 2026-09-16：续答＝消息原样发回、服务端合成，前端拼串通道退役；pending 期间 composer 双按钮「补充/新话题」（`discard_pending`），会话号 sessionStorage 过刷新——不然服务端 pending 无人认领，新标签页照旧新会话）；票 04 结构化呈现（`Chart.tsx` 只管画、形状零自判——图型契约与后端 `decide_chart` 同源；配色＝已验证分类色板前六槽固定序不循环、单系列省图例；三节分节折叠：结论/校验标注常开、数据依据/口径说明可折、未知节头一律常开，有图时表格收进折叠＝图首读表查证；校验旗标徽标化 ✗失败/⚠模板降级/⚠已截断——label 承载语义不靠颜色单传；大数卡值原样呈现 UI 不格式化数字）；textContent 纪律（全文件禁 dangerouslySetInnerHTML）；不立前端测试（壳不判卷，CI 保持纯 Python）。**唯一入口三层钉测**＝AST 禁 web 包 import sqlite3＋上传阶段 connect 即炸行为测＋schema 摘要与问答链路均经 open_readonly。**落盘唯一写入口**＝`web/_fs.py::atomic_write`（M8 票 01：meta.yaml／会话 yaml／上传 source.sqlite 三写点收口 tmp+fsync+os.replace，crash 不出半档；存储撤除纪律＝端点删、残留键读忽略、新写不产）；**反馈面（M8 票 04）**＝`web/feedback.py`：`POST /api/agents/{id}/feedback`（sid+ts+vote）写**旁挂** `<sid>.feedback.jsonl`（append-only {ts,vote,question,sql,at}，非 atomic_write 覆盖语义——模块级 `threading.Lock` 收口并发 append、读侧同锁不撞半写窗，与 ask 在途锁**零交叠**不同文件不同锁域；坏档纪律＝末行 crash 尸体宽容只丢半行、中段坏行整档报错不静默吞），无会话档 404（写者不建会话）／ts 回查不中 404／非 up-down 400；回放 `GET .../sessions/{sid}` 轮条目加**可选尾键 `feedback`**（chart 单点先例、无票轮形状不动、同 ts 取末票）；**裁决旁挂维持**＝反馈不进 AskResponse 字段契约（当时 14 键、现 11）、不进 prompt/记忆/路由、不重开 PATCH（票 09 的 405 钉原样绿）；攒卷 `qadata feedback-export`→`runs/feedback-<date>.jsonl` 供人审手工成卷（按 ts 回查轮、SQL 以轮 payload 为真源、回查不中如实计数、gold 必须人签不自动进 `tests/*_ids.json`），前端 AnswerBubble meta 行后 👍/👎 双按钮投后禁用高亮（ts 锚点：回放轮自带、当场气泡回查会话档末轮补——契约字段不带 ts 不改形状；单轮/澄清暂停/回查失败＝无 ts 不可投按钮不出现）；**例题库面（M9 票 06 → M10 票 06 归位 `retrieval/examples.py`，只搬家不重铸）**＝人签题对住 `<智能体目录>/examples.yaml`（文件即数据库沿 AgentStore，删智能体连带清），装载闸必验 `signed_by`（机器生成的条目永远不会带人签＝自动吸收被机制拒绝），`sign_examples` 唯一写入口（同题重签覆盖后签为真、孤儿目录拒建）、进料只经 examples-sign CLI（零触扫描钉＝app/sessions/feedback/nodes/build/eval 源码出现 sign_examples 即红），`build_recall` hybrid 打分＝关键词精确命中作**第一排序键**（字典序硬保底，治向量漏的表名列名取值字面量）＋向量点积次键、top-K=3 **升序注入**（DB-GPT 论文形态，渲染归 prompts.format_examples_block）；三路失败（坏档/未配置/端点挂）＝降级不召回＋账本 recall 行入账不静默（ok 行＝一次向量化调用本体、不烧生成调用数、题面 memo 重试环不翻倍）；挂接＝app `_recall_for` 逐请求现读（签入即生效）沿 build 参到 gssc.select，换 embedding 模型＝旧向量作废自动出局关键词面仍活；升级判据（usearch/分层召回）注释在 examples.py 模块头＝README 明文互指锚。

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
