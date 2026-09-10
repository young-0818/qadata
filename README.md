# 问数 (qadata)

对话式数据分析 Agent：自然语言问题 →（人审指标口径模板 ｜ LLM 生成 SQL）→ 沙箱执行 → 自纠错 → 可信答案。

- 设计文档：`.scratch/qadata-agent/spec.md`（本地）

## 架构

六节点＋一个可选指标层节点的自纠错状态机（LangGraph 编排，双条件边）：

```
question → understand →〔metric_match〕→ explore → generate → execute ──成功──→ verify ──通过──→ respond
                            命中↓未命中→explore        ↑          │                  │
                                                    └──失败且有预算─┘   └──可疑且有预算──┘
                    （预算耗尽：失败→respond 兜底；可疑→respond 带校验标注）
```

- **命中路径（M5 指标层，默认关）**：两级匹配（确定性别名归一化 → LLM 整表复核，解析失败＝未命中——宁漏勿错）→ ⑨ 极值闸（具名极值/排名词形撤销命中走兜底，纯函数零调用）→ 意图填槽 → 人审 SQL 模板渲染——每级失效都跌回兜底；模板执行失败入账恰好降级一次。
- **兜底路径（M1–M4 管线）**：schema 探索 → LLM 生成 SQL → 重试环（预算 3，`len(attempts)` 即账本）→ 规则校验器。
- **沙箱四层**：只读连接唯一入口 / sqlglot 单语句白名单＋表名校验 / 5s 超时＋双行数上限 / sqlite 物理只读。全路径无旁路（模板产出的 SQL 照过四层）。
- **答案形态（E1，两态共用）**：【结论】（唯一 LLM 产出）＋【数据依据】【口径说明】【校验标注】全部代码组装；命中路径口径节零 token 引用注册表＋血缘。失败路径不调 LLM——永不编造。
- 评测：BIRD 双轨小样本（ADR-0001：轨道① financial 冻结 50 题＝指标考卷，轨道② 跨库 50 题＝回归闸），`qadata eval`/`qadata report --paths` 分路径报告，逐题 token/延迟统计。

## M1 基线（2026-09-02）

- 模型：qwen3.7-flash（OpenAI 兼容接口），BIRD dev 随机 100 题（seed=42，分层抽样前的朴素随机）
- 总执行准确率：**63.0%**（63/100）
  - simple 69.4%（43/62）｜ moderate 53.6%（15/28）｜ challenging 50.0%（5/10）
- 系统状态：M1 最小闭环（线性图、无自纠错、临时沙箱），后续里程碑按评测驱动迭代
- 成本：输出 ~0.51M tokens（推理模型 thinking 开销为主），单题均 ~5K 输出 tokens
- 复现：`qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --sample 100`（在仓库根目录运行）

## M2 自纠错与沙箱（2026-09-03）

- 图结构：线性图 → 带自纠错反馈环（预算 3 次：执行失败重试＋校验可疑重试）；
  沙箱补齐语句层（sqlglot 白名单/单语句/表名校验）与资源层（5s 超时中断/双行数上限）
- 冒烟基线（10 题固定集，`tests/smoke_ids.json`）：**50%**（deepseek-v4-flash-0731，见 `tests/smoke_baseline.json`）
- 50 题配对对比（与 M1 同题，`tests/m2_compare_ids.json`）：同题子集 **62.0% → 58.0%**（救回 6 / 改坏 8）
  - ⚠️ 混杂说明：M1 基线用 qwen3.7-flash；M2 评测中途该模型免费配额耗尽，
    换 deepseek-v4-flash-0731 重跑，Δ 含模型切换成分
  - 翻转归因：自纠错环净效应 ≈ 救回 2 / 改坏 3（改坏均为 verify 触发重试后答案漂移）；
    其余翻转归因模型差异。样本小，两者均在噪声量级
  - 沙箱零误伤：21 道错题全部为判分不匹配，零超时、零 sqlguard 误拒；
    BIRD gold SQL 1534 题 sqlglot 解析失败 0 例
- 成本变化：单题均输出 tokens ~5K → ~4.1K（重试使调用次数升至 3.3 次/题，新模型单次更省）
- 复现：`qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --ids tests/m2_compare_ids.json`

## M3 自纠错深化与评测完备（2026-09-04）

- 靶子三件套（M2 评测数据直接指向）：verify 精准化（「列出全部」类空结果豁免、截断不再触发重试）、
  答案稳定性（执行失败耗尽时回退最近成功候选作答）、generate 口径规则（禁格式化输出、只选问题需要的列）
- 评测工具链：断点续跑（`--out`/`--resume`，逐题 flush 进度可见）、记录增强（`gold_sql`/`gold_failed`/`error_class`）、
  失败样本库（任意 run 文件派生）、`qadata report` 两轮 diff、变体矩阵最小版（`--variants`）
- 其余：错误分类修复建议（规则版进失败历史）、`database_description/` 列注释按选中表进 schema 上下文、
  视图入列、LLM 客户端超时、ruff 版本锁定
- 冒烟（10 题固定集）：**60%** ≥ 基线 50%
- 50 题配对对比（与 M2 同题同模型 deepseek-v4-flash-0731）：**58.0% → 64.0%**（救回 4 / 改坏 1，净 +3）
  - M2 归因的 3 道「verify 假阳性 → 重试改坏」题（457/1309/1330）全部救回，重试改坏归零（靶子直接命中）
  - ⚠️ 噪声说明：同一代码多轮运行中个别题（如 228、440）正误翻转，接口在 temp=0 下仍有非确定性；
    50 题样本 ±1-2 题属噪声量级，净效应以翻转归因为准
  - 开发过程验证跑逮住 1 个真 bug：BIRD 部分 `database_description` CSV 非 UTF-8（如 formula_1），
    曾致 7 题 explore 崩溃；已修（`utf-8-sig + errors="replace"`）并加回归测试
  - 全量 dev（1534 题）按裁决推迟至项目收尾；M3 口径为 50 题固定配对集
- 成本：验证/归因/终局共 ~5 次运行 ≈ ¥6.5（超出原估算 ¥4——验证跑揪出编码 bug 后的修复-重跑循环，如实记录）
- 复现：`qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --ids tests/m2_compare_ids.json --out runs/eval-m3-50.jsonl`
  对比报告：`qadata report --baseline runs/eval-m2-50.jsonl --current runs/eval-m3-50.jsonl`

## M4 速度与正确率双优化（2026-09-05）

- **方法学发现（本里程碑最重要产出）：端点跨时段漂移 ≫ ±1-2 题噪声带**。同一代码同一题集，
  24 小时内三个档位：32/50（M3 前晚）→ 36/50（M4 今晨 A 复现跑）→ 29-30/50（当晚 B/D 跑）；
  B/D 两晚各自「改坏」的 {1436, 1466, 1508} 与对应运行的代码改动无关（共同受害者）。
  **结论入纪律：配对对比必须同时段**；单题差异在跨时段比较中不可解读。
- 50 题终局数字（如实标注）：M3 64.0% → 终局运行（D 态，晚间档位）**60.0%**（30/50；
  救回 228/440/689，改坏 189/285/1330/1436/1508，后三题疑漂移）。**M4 未获得可验证的准确率净提升**
  ——表观数字被 ±4~7 题的时段漂移淹没，各线增量一律以受控证据计（见下），不以跨时段 Δ 计。
  报告：`qadata report --baseline runs/eval-m3-50.jsonl --current runs/eval-m4-d.jsonl --types runs/m4-attribution.jsonl`
- 评测基建：每题独立分片＋收口合并的**并发评测**（`--concurrency 5 --qps 8` 全局限速器）＋
  `--skip-respond`（判分只读 answer.sql，省 1 次调用/题，约 -30% 墙钟与 token）。
  提速如实记录：deepseek **单 key 端点按 key 节流**，5 路并发延迟 median 3.0s→10.6s、p90→61s，
  基建压缩 3.1× 被供应商膨胀 2.3× 抵消，墙钟 38m49s ≈ 串行——单 key 并发零收益；
  并发基建保留（多 key / vLLM 本地端点可直接兑现），提速改走减调用路线。
- B 值感知 schema 关联：落地后配对净 -7、预注册靶题命中 0（题面提示直接致错 1436、可复现丢 DISTINCT 440）
  → **回滚**（教训转 M5：取值按题面关键词条件注入，不全列无条件灌）。
- D 口径规则 5（题面明示精度/百分比形态）：跨时段整跑不可用，增量证据改受控探针（唯一变量 A/B）：
  228 判对 **0/3 → 3/3**，且终局跑中该题 `ROUND(...,4)` 形态穿链路生效 → **保留**。
- C 多候选自一致性（精准模式，`--precise-candidates` 配置）：**「先探针后上线」拦下 ¥3.5**——
  temp=0.3 下 4 题×3 发结果级分歧 **0/4**（SQL 文本有多样性但执行结果恒同；错靶三发同错＝确定性错误）。
  机制：历史翻转的噪声源是跨运行时段漂移，而该端点运行内采样噪声≈0，自一致性票决无信号可票。
  帕累托取消，代码＋单测保留、默认关闭。
- E2 题型切片（`report --types`，复用 P0 人工归因标签）：对比 3 题 33.3%｜极值 11 题 54.5%｜
  分布 10 题 70.0%｜明细 25 题 60.0%｜排名 1 题 100%（「对比/极值」是薄弱题型，入 M5 靶子候选）。
- 冒烟基线重建：50% → **90%**（10 题 9/10，唯一失分 51＝B 靶选列歧义已知短板；
  绑定 D 后代码与 deepseek-v4-flash-0731，见 `tests/smoke_baseline.json`）。
- 工具链修复：`--variants` 分支透传 settings/并发/限速/skip-respond（此前变体候选数会静默回退 1）；
  非 UTF-8 描述 CSV、孤儿分片等亦各有 fix。
- 成本：**~¥4.9 / ¥10**（逐行总账 `runs/m4-budget.md`；帕累托 ¥3.5 因探针负结果未花，砍线规则首战生效）。
- 复现：`bash runs/m4-d-run.sh`（内嵌模型强制与真跑统一参数）。

## M5 指标层 Text-to-Metrics（2026-09-08 ~ 09-10，已收尾）

- 交付：意图结构化载体（understand 同调六字段，宁空勿造）／**18 条人审原子指标注册表**（`metrics/financial.yaml`，
  防泄漏护栏：构建会话禁读 gold SQL，逐条 owner 拍板）／metric_match 两级匹配＋⑨ 极值机制闸／填槽渲染走既有沙箱
  零旁路／E1 三节答案（唯一 LLM 产出＝【结论】一句话）／分路径评测（`report --paths`）。**默认关**（`QADATA_METRIC_LAYER=1` 开），
  关态与纯 Text-to-SQL 在路由/调用数/账本/评测字段上逐行为一致。389 测试全绿。
- 条款④（意图尾段注入喂 generate）**判负成文**（票 02，kimi 同日配对 32/50 vs 32/50、翻转 3/3 对消、
  1466 分离探针证注入信号≤改写漂移噪声带）：软用途入墓地，载体保留、消费者改为 metric_match 填槽，
  注入线拆除并由 `test_generate_never_reads_intent` 钉死防回流（owner 签字回炉裁决 C）。
- 轨道①判卷（票 06）：financial 冻结题集 50 题（票 01，seed=42 分层 29/18/3，ADR-0001 单库子集不代表 dev 全量）。
  模型跟随 .env＝kimi-k2.7-code——票 01 深基线 27/50 跨端点不可比，票 06 按裁决**同时段配对**
  （18:2x–19:0x 串行两轮，同 commit `efea1cf`）：对照轮（纯 SQL）**28/50**，指标轮 **29/50**。
  三个数：命中率 **0/50**；命中路径准确率 **无数据**；兜底路径 29/50。判卷（Q9 三条款，`report --paths`）：
  **① 无结论、② 样本不足（命中 0 <10）、③ 判过**（兜底 29 ≥ 28−2）。→ **metric_layer 默认值保持 False**
  （默认位须由证据挣得，①未判过即不翻；功能保留可开关演示＝条款⑤形态，负结果成文、不追加烧钱重跑）。
- 零命中的在体归因（全部 50 题逐题可溯 `runs/traces.jsonl`）：L2 判 NONE 36 题（33 题题面确无注册表口径词，
  3 题属组合条件形态——branch/region/占比，模板本就答不了）；L1/L2 命中后撤销 14 题（**⑨ 极值闸在体拦 8 题**
  ＝票 04 §F.4 担忧实锤，含 Q98/134/138——补闸前这些题会拿户均/合计 AVG 模板答「谁最低/第二大」错数字；
  填槽宁空勿造拦 6 题）。**结论：BIRD financial 题面普遍是组合约束查询，与人审原子口径的覆盖交集在本考卷上≈0**
  ——「指标优先」在轨道①既未被证实也未被证伪；价值叙事＝口径治理的可演示性（开关节下命中题直出
  「人审口径＋血缘」，`QADATA_METRIC_LAYER=1 qadata ask … "1995年批准的贷款的违约率"` 可复现）。
- 配对噪声带校准（kimi 端点）：兜底路径与对照轮**同码路径**，逐题翻转仍 5 题（救回 98/102/189、改坏 91/108）
  ——同时段同代码 Δ±5 属端点噪声，Δ+1 无统计含义；后续 kimi 上的配对按此带解读（glm 端点未实测校准，勿直接套用）。
- 冒烟基线重建（票 08）：understand 链路已变（载体 A 复活）＋端点两度切换（deepseek→kimi→glm）双触发——
  10 题固定集 **10/10**（glm-5.2，`--skip-respond` 同真跑参数），绑定 f16fc7a；51（B 靶选列歧义）本轮亦对，
  如实标注为换绑当轮表现、非 B 靶修复证据（见 `tests/smoke_baseline.json`）。
- 分路径报告：`qadata report --baseline runs/eval-m5-track1-off.jsonl --current runs/eval-m5-track1-on.jsonl --types runs/m5-track1-types.jsonl --paths`；
  跑分脚本 `runs/m5-track1-pair.sh`。
- 成本：**~¥5.8 / ¥10**（影子折算口径——百炼 coding plan 订阅逐行无现金实付可计，按唯一价格锚 ~¥3.9/M tokens 折算，
  逐行总账与收尾对账 `runs/m5-budget.md`）；单次 ≤¥3 红线全行满足、砍线规则未触发；④判负省回炉配对 ~¥1.3-2.6，
  B v2（票 09）经余量裁决留白未启动。
- 复现：`.env` 设 `QADATA_METRIC_LAYER=1` 后
  `qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --ids tests/m5_financial_ids.json --out runs/eval-m5-track1-on.jsonl`，
  对照轮去开关；判卷表自动复算（判卷数字绑实跑模型——本段为 kimi 轮，换端点须同时段重配对方可解读）。
