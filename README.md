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
