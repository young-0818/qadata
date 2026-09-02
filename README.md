# 问数 (qadata)

对话式数据分析 Agent：自然语言问题 → SQL → 沙箱执行 → 自纠错 → 可信答案。

- 设计文档：`docs/superpowers/specs/2026-09-02-qadata-agent-design.md`（本地）

## M1 基线（2026-09-02）

- 模型：qwen3.7-flash（OpenAI 兼容接口），BIRD dev 随机 100 题（seed=42，分层抽样前的朴素随机）
- 总执行准确率：**63.0%**（63/100）
  - simple 69.4%（43/62）｜ moderate 53.6%（15/28）｜ challenging 50.0%（5/10）
- 系统状态：M1 最小闭环（线性图、无自纠错、临时沙箱），后续里程碑按评测驱动迭代
- 成本：输出 ~0.51M tokens（推理模型 thinking 开销为主），单题均 ~5K 输出 tokens
- 复现：`qadata eval --questions data/bird/dev/dev.json --db-dir data/bird/dev/dev_databases --sample 100`
