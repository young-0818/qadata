import { Fragment, useState, type ReactNode } from "react";
import { AskResponse, ProgressEvent, isThinkingEvent, isToolEvent, isToolStartEvent } from "./api";

// M8 票 06 任务控制台：把票 03 既有进度帧喂厚的纯展示面——架构不动（固定状态机
// 不是 tool loop），本组件只把帧流摆成 owner 截图形态：概览四卡＋追踪时间线。
// 边界（owner 裁 2026-09-15）：token 只活在控制台，答案报告内容里不出现。
// M9 票 02：回放态复形——档案轮尾键 trail（步骤＋tool 帧、thinking 不入档）与直播
// 帧同形同源，buildSteps 原样消费；旧档（无尾键）照实显示空态。零新依赖。
// 2026-09-22 owner 裁：删「影子折算」费用估算（coding plan 无现金逐行可计、锚价估算
// 无实际决策价值），token 卡改列输入/输出两行——真金白银的成本账在 runs 成本账本人审，不在演示壳里估。
// 实时工具链（2026-09-22 owner 裁，讨论档案见会话）：tool start 帧＝工具开跑的活口，
// 当场成行转圈（execute_sql 撞 5s 超时线、value_link 走网络 embed 的等待期不再装死），
// 收口帧原位配对翻终态。胶囊行升级为清单行（一行一发、英文机器名直显）＋「N 个工具」
// 徽标折叠（running 步自动展开、终态默认全折）；脊柱线串步；重试环显形标「· 第 N 次」
// （按节点在链上出现次序，非帧自带 attempt——那是全局 SQL 账本计数，实跑截图实锤虚标后改判）；
// 0 工具步不挂徽标（死把手不造）。旧档回放（trail 无 start 帧）＝收口帧直接成行，
// 终态形状与升级前逐字节同形（trail_entry 同形同源纪律，零特判）。

// 步名贴签层（后端单源文案；未知节点直显原文，不硬翻译）。工具名不在此表——
// 实时工具链裁决（Q8）：清单行/步头直显英文机器名，左栏分家后无中文消费者，
// 原工具中文贴签映射已整族删除（回锅即红，钉见 tests/test_budget_fuse.py 布线测）。
export const NODE_LABELS: Record<string, string> = {
  understand: "理解问题",
  explore: "探查库表",
  generate: "生成 SQL",
  execute: "执行 SQL",
  verify: "校验结果",
  respond: "组织答案",
};

interface ToolChip {
  tool: string;
  running: boolean; // start 未收口＝如实半开（断流也停在此形，不假完成）
  ok: boolean;
  ms: number | null; // running 无计时量
  detail?: string; // 值链「实际取值」命中明细（列→库内实际值），有则行内再展开
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

// 帧流 → 步骤视图模型（start 开步、结果帧收步、tool 帧挂到最近开步——到达序即执行序；
// tool start 开行、tool 收口帧按「同 node 步内最近未收口同名行」原位配对——不引入 call-id，
// 单请求内帧流有序串行，重试环同名 execute_sql 两次＝两开两收按序各关各的）
export function buildSteps(trail: ProgressEvent[]): Step[] {
  const steps: Step[] = [];
  for (const ev of trail) {
    if (isThinkingEvent(ev)) continue; // 票 08 思考帧属进度面板，不进控制台步骤/时间线（不配当"一步"）
    if (isToolEvent(ev) || isToolStartEvent(ev)) {
      // 归属认帧自带 node（开步中同 node 者）——不靠到达序赌时序（双轴评审 (c)2）
      const target = [...steps].reverse().find((s) => s.running && s.node === ev.node);
      if (!target) continue;
      if (isToolStartEvent(ev)) {
        target.tools.push({ tool: ev.tool, running: true, ok: true, ms: null });
      } else {
        const chip = [...target.tools].reverse().find((t) => t.running && t.tool === ev.tool);
        if (chip) {
          chip.running = false;
          chip.ok = ev.ok;
          chip.ms = ev.duration_ms;
          chip.detail = ev.detail;
        } else {
          // 无开环可关＝旧档回放（升级前的 trail 只有收口帧）→ 直接成终态行
          target.tools.push({ tool: ev.tool, running: false, ok: ev.ok, ms: ev.duration_ms, detail: ev.detail });
        }
      }
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
      // 步已收口而工具行还开着（断流掐在调用中途）＝行保持半开如实说——与步的半开同纪律
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

// 「N 个工具」徽标开合（步骤级）＋值链明细开合（行级）＝两级折叠，各一枚 Set
const toggle = (prev: Set<string | number>, key: string | number) => {
  const n = new Set(prev);
  if (n.has(key)) n.delete(key);
  else n.add(key);
  return n;
};

export function Console({
  trail,
  resp,
  streaming,
  replay,
}: {
  trail: ProgressEvent[];
  resp: AskResponse | null;
  streaming: boolean;
  replay: boolean; // 空 trail 的两种成因分开说：新会话（还没跑过）≠ 历史回放（旧档未含留痕）
}) {
  const steps = buildSteps(trail);
  const [openSteps, setOpenSteps] = useState<Set<number>>(new Set());
  // 「实际取值」命中明细默认收起、点行展开（key＝步序号:工具序号）
  const [openTools, setOpenTools] = useState<Set<string>>(new Set());
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
            {steps.map((s, i) => {
              // 步头实时叙事：running 步若挂着未收口的工具行＝「正在 X…」（活口帧自带真话）。
              // 工具名直显英文机器名（Q8 裁决：清单行不给中文贴签，未知天然直显＝诚实）；
              // 重试标注认「该节点在链上第几次出现」而非帧自带 attempt（那是 SQL 尝试账本计数，
              // 全局量——verify/respond 拿它标会虚标「第 2 次」，实跑截图实锤后改判）。
              const live = s.running ? [...s.tools].reverse().find((t) => t.running) : undefined;
              const rowsOpen = s.running || openSteps.has(i);
              const seen = steps.slice(0, i + 1).filter((x) => x.node === s.node).length;
              const retry = seen > 1 ? ` · 第 ${seen} 次` : "";
              return (
                <div className="c-step" key={i}>
                  <div className="c-step-head">
                    <span className={dot(s.ok, s.running)} />
                    <b>{NODE_LABELS[s.node] ?? s.node}{retry}</b>
                    <i>
                      {s.running
                        ? live
                          ? `正在 ${live.tool}…`
                          : "进行中…"
                        : s.status}
                      {s.ms !== null ? ` · ${s.ms}ms` : ""}
                    </i>
                    {s.tools.length > 0 && !s.running && (
                      <span
                        className="c-badge"
                        role="button"
                        tabIndex={0}
                        aria-expanded={rowsOpen}
                        onClick={() => setOpenSteps((p) => toggle(p, i) as Set<number>)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setOpenSteps((p) => toggle(p, i) as Set<number>);
                          }
                        }}
                      >
                        {s.tools.length} 个工具<span className="c-caret">{rowsOpen ? "▾" : "▸"}</span>
                      </span>
                    )}
                  </div>
                  {rowsOpen && s.tools.length > 0 && (
                    <div className="c-rows">
                      {s.tools.map((t, j) => {
                        const label = t.tool; // 英文机器名直显（Q8 裁决；中文贴签归聊天进度面板）
                        const clickable = !!t.detail; // 带明细的行（值链）可再点开命中明细
                        const open = openTools.has(`${i}:${j}`);
                        return (
                          <Fragment key={j}>
                            <div
                              className={`c-trow${t.running ? " live" : ""}${clickable ? " c-trow-click" : ""}`}
                              role={clickable ? "button" : undefined}
                              tabIndex={clickable ? 0 : undefined}
                              aria-expanded={clickable ? open : undefined}
                              onClick={clickable ? () => setOpenTools((p) => toggle(p, `${i}:${j}`) as Set<string>) : undefined}
                              onKeyDown={
                                clickable
                                  ? (e) => {
                                      if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        setOpenTools((p) => toggle(p, `${i}:${j}`) as Set<string>);
                                      }
                                    }
                                  : undefined
                              }
                            >
                              <span className={t.running ? "dot dot-run" : dot(t.ok, false)} />
                              <span>{label}</span>
                              <span className="dur">
                                {t.running ? "进行中…" : `${t.ms}ms`}
                                {clickable ? <span className="c-caret">{open ? "▾" : "▸"}</span> : null}
                              </span>
                            </div>
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
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
