import { Fragment, useState, type ReactNode } from "react";
import { AskResponse, ProgressEvent, isThinkingEvent, isToolEvent } from "./api";

// M8 票 06 任务控制台：把票 03 既有进度帧喂厚的纯展示面——架构不动（固定状态机
// 不是 tool loop），本组件只把帧流摆成 owner 截图形态：概览四卡＋追踪时间线。
// 边界（owner 裁 2026-09-15）：token 只活在控制台，答案报告内容里不出现。
// M9 票 02：回放态复形——档案轮尾键 trail（步骤＋tool 帧、thinking 不入档）与直播
// 帧同形同源，buildSteps 原样消费；旧档（无尾键）照实显示空态。零新依赖。
// 2026-09-22 owner 裁：删「影子折算」费用估算（coding plan 无现金逐行可计、锚价估算
// 无实际决策价值），token 卡改列输入/输出两行——真金白银的成本账在 runs 成本账本人审，不在演示壳里估。

// 帧词汇的展示映射（后端单源文案的贴签层；未知一律直显原文，不硬翻译）
export const NODE_LABELS: Record<string, string> = {
  understand: "理解问题",
  explore: "探查库表",
  generate: "生成 SQL",
  execute: "执行 SQL",
  verify: "校验结果",
  respond: "组织答案",
};

export const TOOL_LABELS: Record<string, string> = {
  list_tables: "列表",
  get_schema: "取表结构",
  select_tables: "挑选相关表",
  value_samples: "值采样",
  value_link: "值链",
  execute_sql: "执行查询",
  execute_sql_batch: "票决批量",
  budget_fuse: "预算保险丝",
};

interface ToolChip {
  tool: string;
  ok: boolean;
  ms: number;
  detail?: string; // 值链「实际取值」命中明细（列→库内实际值），有则摊开显示
}

interface Step {
  node: string;
  status: string; // 结果帧文案；未收口＝"start"
  running: boolean; // start 未收口（HITL 暂停/断流也停在此形——如实半开，不假完成）
  ok: boolean | null;
  ms: number | null;
  tokensIn: number;
  tokensOut: number;
  tools: ToolChip[];
}

// 帧流 → 步骤视图模型（start 开步、结果帧收步、tool 帧挂到最近开步——到达序即执行序）
export function buildSteps(trail: ProgressEvent[]): Step[] {
  const steps: Step[] = [];
  for (const ev of trail) {
    if (isThinkingEvent(ev)) continue; // 票 08 思考帧属进度面板，不进控制台步骤/时间线（不配当"一步"）
    if (isToolEvent(ev)) {
      // 归属认帧自带 node（开步中同 node 者）——不靠到达序赌时序（双轴评审 (c)2）
      const target = [...steps].reverse().find((s) => s.running && s.node === ev.node);
      target?.tools.push({ tool: ev.tool, ok: ev.ok, ms: ev.duration_ms, detail: ev.detail });
      continue;
    }
    if (ev.status === "start") {
      steps.push({
        node: ev.node, status: "start", running: true,
        ok: null, ms: null, tokensIn: 0, tokensOut: 0, tools: [],
      });
      continue;
    }
    const step = [...steps].reverse().find((s) => s.running && s.node === ev.node);
    if (step) {
      step.running = false;
      step.status = ev.status;
      step.ok = ev.ok ?? true;
      step.ms = ev.duration_ms ?? null;
      step.tokensIn = ev.tokens_in ?? 0;
      step.tokensOut = ev.tokens_out ?? 0;
    }
  }
  return steps;
}

function Card({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="c-card">
      <span className="c-card-label">{label}</span>
      <span className="c-card-value">{value}</span>
      {sub && <span className="c-card-sub">{sub}</span>}
    </div>
  );
}

const dot = (ok: boolean | null, running: boolean) =>
  running ? "dot dot-run" : ok ? "dot dot-ok" : "dot dot-bad";

export function Console({
  trail,
  resp,
  streaming,
  replay,
}: {
  trail: ProgressEvent[];
  resp: AskResponse | null;
  streaming: boolean;
  replay: boolean; // 空 trail 的两种成因分开说：新会话（还没跑过）≠ 历史回放（旧档未入留痕）
}) {
  const steps = buildSteps(trail);
  // 「实际取值」命中明细默认收起、点胶囊展开（key＝步序号:工具序号）
  const [openTools, setOpenTools] = useState<Set<string>>(new Set());
  const toggleTool = (key: string) =>
    setOpenTools((prev) => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  const done = steps.filter((s) => !s.running);
  const passed = done.filter((s) => s.ok).length;
  const tokensIn = steps.reduce((n, s) => n + s.tokensIn, 0);
  const tokensOut = steps.reduce((n, s) => n + s.tokensOut, 0);
  const output = resp
    ? resp.clarification
      ? "待你补充" // 澄清暂停行（14 字段契约的暂停面，不是答案也不是失败）
      : resp.failed
        ? "失败"
        : `${resp.rows ? resp.rows.length : 0} 行` // 显示上限内（沙箱③层 max_rows）
    : streaming
      ? "回答中…"
      : "—";
  return (
    <div className="console">
      <h4>任务控制台</h4>
      {steps.length === 0 ? (
        <p className="sub">
          {streaming
            ? "连接进度流…"
            : replay
              ? "本次为历史回放——该轮会话档未含任务留痕（旧档案）。"
              : "提问后这里逐帧直播任务过程（概览与时间均为现场量）。"}
        </p>
      ) : (
        <>
          <div className="c-cards">
            <Card label="步骤" value={String(done.length)} />
            <Card
              label="成功率"
              value={done.length ? `${Math.round((passed / done.length) * 100)}%` : "—"}
              sub={`${passed}/${done.length} 步通过`}
            />
            <Card
              label="输出"
              value={output}
              sub={resp && !resp.failed && !resp.clarification && resp.rows ? "显示上限内" : undefined}
            />
            <Card
              label="Token 消耗"
              value={
                <>
                  输入 {tokensIn.toLocaleString("en-US")}
                  <br />
                  输出 {tokensOut.toLocaleString("en-US")}
                </>
              }
            />
          </div>
          <div className="c-steps">
            {steps.map((s, i) => (
              <div className="c-step" key={i}>
                <div className="c-step-head">
                  <span className={dot(s.ok, s.running)} />
                  <b>{NODE_LABELS[s.node] ?? s.node}</b>
                  <i>
                    {s.running ? "进行中…" : s.status}
                    {s.ms !== null ? ` · ${s.ms}ms` : ""}
                  </i>
                </div>
                {s.tools.length > 0 && (
                  <div className="c-tools">
                    <span className="c-tools-count">{s.tools.length} 个工具</span>
                    {s.tools.map((t, j) => {
                      const label = TOOL_LABELS[t.tool] ?? t.tool;
                      const clickable = !!t.detail; // 只有带明细的胶囊（值链）可点展开
                      const open = openTools.has(`${i}:${j}`);
                      return (
                        <Fragment key={j}>
                          <span
                            className={`c-chip${t.ok ? "" : " bad"}${clickable ? " c-chip-click" : ""}`}
                            role={clickable ? "button" : undefined}
                            tabIndex={clickable ? 0 : undefined}
                            aria-expanded={clickable ? open : undefined}
                            onClick={clickable ? () => toggleTool(`${i}:${j}`) : undefined}
                            onKeyDown={
                              clickable
                                ? (e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                      e.preventDefault();
                                      toggleTool(`${i}:${j}`);
                                    }
                                  }
                                : undefined
                            }
                          >
                            <span className={dot(t.ok, false)} />
                            {label} {t.ms}ms
                            {clickable ? <span className="c-caret">{open ? "▾" : "▸"}</span> : null}
                          </span>
                          {clickable && (
                            <div className={`c-tools-detail${open ? " open" : ""}`}>
                              {label}：{t.detail}
                            </div>
                          )}
                        </Fragment>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
