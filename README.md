# 问数 (qadata)

对话式数据分析 Agent：自然语言问题 → SQL → 沙箱执行 → 自纠错 → 可信答案。

- 设计文档：`docs/superpowers/specs/2026-09-02-qadata-agent-design.md`（本地）

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
