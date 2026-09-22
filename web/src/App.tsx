import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import {
  AgentDetail,
  AgentSummary,
  AskResponse,
  ModelCard,
  ProgressEvent,
  SessionHead,
  Vote,
  askStream,
  isThinkingEvent,
  isToolEvent,
  createAgent,
  deleteAgent,
  getAgent,
  getSession,
  getModel,
  listAgents,
  listSessions,
  patchAgent,
  postFeedback,
  uploadDatasource,
  uploadKnowledge,
} from "./api";
import { ResultChart } from "./Chart";
// M8 票 07 答案报告化：【结论】＝自由成文的 markdown 小报告，走子集安全渲染器
// （纯文本插值、零 raw HTML——纪律钉 tests/test_web_markdown.py）。
import { Markdown } from "./Markdown";
// M8 票 06：帧词汇表（NODE/TOOL 标签）与任务控制台同单一源（Console.tsx），
// 聊天气泡里的「工作过程」折叠与控制台读同一份帧、贴同一套签——两处字面漂移即违宪。
import { Console, NODE_LABELS, TOOL_LABELS } from "./Console";

// 渲染纪律（spec）：一律 React 文本插值（＝textContent），全文件禁 dangerouslySetInnerHTML。
// M7-rev2 票 02.5：三视图状态路由（首页/智能体详情/对话页），不引 router 依赖。
// 对话页于 2026-09-12 改版为通高侧栏＋中栏滚动的 agent 布局（首页/详情页仍窄版）。
// 票 05 多轮：会话号前端自生成（hex12）、懒建档；侧栏历史会话真落点。
// 会话级运行时通道全部裁撤（owner 裁 2026-09-15）：口径叠加框（不配第二入口）、
// ↺ 新话题按钮（票 09：真实用户直接打字换题，话题连续性由模型隐式判）。

// 后端拒绝理由一律原样展示（永不编造错误说明）
const errMsg = (e: unknown): string =>
  e instanceof Error ? e.message : String(e);

type View =
  | { page: "home" }
  | { page: "agent"; id: string }
  | { page: "chat"; id: string };

// 票 04：agent 气泡带 ts（回放轮自带；当场轮问完回查会话档补上）——反馈票的锚点，
// null＝投不了（单轮/澄清暂停/回查失败），按钮不出现
type Msg =
  | { role: "user"; text: string }
  | { role: "agent"; resp: AskResponse; trail: ProgressEvent[]; ts: string | null }
  | { role: "error"; text: string; trail: ProgressEvent[] };

// 票 03 进度行＋M8 票 06 喂厚：tool 子事件渲染成缩进胶囊（绿/红点＝owner 截图语义），
// 结果行带耗时与红绿标记。status 文案后端单源，未知节点/工具名直显不硬翻译
// （永不编造纪律的展示面）。
function ProgressRow({ ev }: { ev: ProgressEvent }) {
  // M8 票 08 思考流：合并后的思考块（同节点相邻 thinking 已在 onProgress 并成一块）
  // 渲成 muted 折行文本——治那 12~30s 死寂转圈「看着它想」，不参与步骤计数。
  if (ev.kind === "thinking") {
    return (
      <div className="progress-row thinking">
        <b>{NODE_LABELS[ev.node] ?? ev.node} · 思考</b>
        <pre>{ev.text}</pre>
      </div>
    );
  }
  if (ev.kind === "tool") {
    return (
      <div className="progress-row tool">
        <span className={ev.ok ? "dot dot-ok" : "dot dot-bad"} />
        <span>{TOOL_LABELS[ev.tool] ?? ev.tool}</span>
        <i>{ev.duration_ms}ms</i>
      </div>
    );
  }
  const running = ev.status === "start";
  return (
    <div className="progress-row">
      <b>{NODE_LABELS[ev.node] ?? ev.node}</b>
      <span className={running ? "running" : ev.ok === false ? "row-bad" : undefined}>
        {running ? "进行中…" : ev.status}
      </span>
      {ev.duration_ms !== undefined && <i>{ev.duration_ms}ms</i>}
      {ev.attempt > 0 && <i>已试 {ev.attempt} 次</i>}
    </div>
  );
}

// 票 06 后 trail 含 tool 子事件——「N 步」只数步帧（start＋结果成对，ceil 容断流半对），
// 混计会稀释"步"语义（双轴评审 (c)1）
// 票 08 thinking 帧同 tool 帧：不是「步」，计入会稀释步语义（思考块与重试步无关）。
const stepCount = (trail: ProgressEvent[]) =>
  Math.ceil(trail.filter((e) => !isToolEvent(e) && !isThinkingEvent(e)).length / 2);

interface Section {
  title: string;
  body: string;
}

// 对齐 compose_conclusion（票 07）排版：每节以【节头】起行。
// M8 票 07 报告化后结论正文自由成文——分节白名单**只认后端实际产出的四个节头**
// （单源一致钉见 tests/test_web_markdown.py）：模型违令在报告里模仿【】或写
// 【已完成】式叙事，也不再被误切成节（机制闸不靠指令，M5 ⑨闸教训同款）。
const SECTION_RE = /^【(结论|数据依据|口径说明|校验标注)】(.*)$/;

// 票 04 分节折叠：结论与校验标注常开（诚实信息不打折），数据依据/口径说明可折
// （支撑细节收起来让答案可读）。M8 票 07 白名单收紧后「未知节头」一径已不存在——
// 白名单外的【…】不再成节、并入前节正文＝折叠名单之外的内容照样不许消失。
const FOLD_SECTIONS = new Set(["数据依据", "口径说明"]);

function parseSections(conclusion: string): Section[] {
  const out: Section[] = [];
  let cur: Section = { title: "", body: "" };
  for (const line of conclusion.split("\n")) {
    const m = line.match(SECTION_RE);
    if (m) {
      if (cur.title || cur.body.trim()) out.push(cur);
      cur = { title: m[1], body: m[2] };
    } else {
      cur.body += (cur.body ? "\n" : "") + line;
    }
  }
  if (cur.title || cur.body.trim()) out.push(cur);
  return out;
}

function ResultTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return (
    <table className="result-table">
      <thead>
        <tr>
          {columns.map((c, i) => (
            <th key={i}>{String(c)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((v, j) => (
              <td key={j}>{v === null ? "NULL" : String(v)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AnswerBubble({
  resp,
  trail,
  feedback,
  onFeedback,
}: {
  resp: AskResponse;
  trail: ProgressEvent[];
  feedback?: Vote;
  onFeedback?: (v: Vote) => void; // 缺省＝不可投（无 ts 锚点：单轮/澄清暂停/回查失败）
}) {
  const sections = parseSections(resp.conclusion);
  // 单一守卫：图型判定存在且行列在场才画（answer_to_payload 失败态三者同 null，
  // 此处只兜形状完整性，不做第二处复测）
  const viz =
    resp.chart && resp.columns && resp.rows
      ? { spec: resp.chart, columns: resp.columns, rows: resp.rows }
      : null;
  return (
    <div className={`bubble agent${resp.failed ? " failed" : ""}`}>
      {/* 票 04：校验旗标以徽标呈现（label 承载语义，不靠颜色单传）；
          M8 票 03：澄清轮徽标——直接打字回答即可（下一问前端自动合成补充说明） */}
      {(resp.failed || resp.truncated || resp.clarification) && (
        <div className="badges">
          {resp.failed && <span className="badge danger">✗ 失败</span>}
          {resp.truncated && <span className="badge warn">⚠ 已截断</span>}
          {resp.clarification && <span className="badge warn">？ 待澄清</span>}
        </div>
      )}
      {sections.map((s, i) =>
        !s.title ? (
          <div className="section-body" key={i}>
            {s.body}
          </div>
        ) : s.title === "结论" ? (
          // 票 07：结论＝报告式总结走 markdown 子集渲染器；其余节是代码组装纯文本照旧
          <div className="section" key={i}>
            <span className="section-title">结论</span>
            <div className="section-body md">
              <Markdown text={s.body} />
            </div>
          </div>
        ) : FOLD_SECTIONS.has(s.title) ? (
          <details className="section-fold" key={i}>
            <summary>{s.title}</summary>
            <div className="section-body">{s.body}</div>
          </details>
        ) : (
          <div className={`section${s.title === "校验标注" ? " warn" : ""}`} key={i}>
            <span className="section-title">{s.title}</span>
            <span className="section-body">{s.body}</span>
          </div>
        ),
      )}
      {viz && <ResultChart {...viz} />}
      {resp.columns && resp.rows &&
        (viz ? (
          // 有图时表格收进折叠：图是首读，表是查证通道（判分与不信任者都走它）
          <details className="section-fold">
            <summary>数据表格</summary>
            <ResultTable columns={resp.columns} rows={resp.rows} />
          </details>
        ) : (
          <ResultTable columns={resp.columns} rows={resp.rows} />
        ))}
      {resp.sql && (
        <>
          {/* 票 09：SQL 常开＝信任锚，眉标清身份（灰块别被误读成表格内容） */}
          <span className="sql-label">查询语句</span>
          <pre className="sql">
            <code>{resp.sql}</code>
          </pre>
        </>
      )}
      <div className="meta">
        {resp.elapsed_ms !== null && <span>· {resp.elapsed_ms} ms</span>}
        {/* 票 05：session_id 出真值＝本轮活在会话里；null＝单轮请求照旧 */}
        <span>· {resp.session_id === null ? "单轮" : "会话"}</span>
      </div>
      {/* M8 票 04：一票评价进旁挂票档（错题可攒卷）；投后禁用＋高亮＝改票走后端追加末票，
          前端不做二次入口 */}
      {onFeedback && (
        <div className="feedback">
          <button
            type="button"
            className={feedback === "up" ? "on" : ""}
            disabled={feedback !== undefined}
            onClick={() => onFeedback("up")}
            title="答案对了"
          >
            👍
          </button>
          <button
            type="button"
            className={feedback === "down" ? "on" : ""}
            disabled={feedback !== undefined}
            onClick={() => onFeedback("down")}
            title="答案不对"
          >
            👎
          </button>
        </div>
      )}
      {/* 票 03：当场看过的自纠错不随答案落地而蒸发——收成折叠留档 */}
      {trail.length > 0 && (
        <details className="trail">
          <summary>自纠错过程（{stepCount(trail)} 步）</summary>
          {trail.map((ev, i) => (
            <ProgressRow key={i} ev={ev} />
          ))}
        </details>
      )}
    </div>
  );
}

// ── 首页：模型只读卡＋智能体列表（真空启动）＋创建 ─────────────────

function HomePage({
  onAgent,
  onChat,
}: {
  onAgent: (id: string) => void;
  onChat: (id: string) => void;
}) {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [model, setModel] = useState<ModelCard | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [err, setErr] = useState("");

  async function refresh() {
    try {
      setAgents(await listAgents());
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  useEffect(() => {
    refresh();
    getModel().then(setModel).catch(() => setModel(null)); // 只读卡拿不到不拦主流程
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setErr("");
    try {
      const a = await createAgent(name.trim(), desc.trim());
      setName("");
      setDesc("");
      onAgent(a.id);
    } catch (err) {
      setErr(errMsg(err));
    }
  }

  return (
    <div className="page">
      {model && (
        <div className="model-card">
          当前模型 <b>{model.model ?? "（未配置）"}</b> · 来源 {model.source} · 只读展示
        </div>
      )}
      <form className="create-agent" onSubmit={onCreate}>
        <input
          placeholder="智能体名称，如：金融分析师"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          placeholder="一句话描述（选填）"
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
        />
        <button type="submit" disabled={!name.trim()}>
          ＋ 创建智能体
        </button>
      </form>
      {err && <div className="note">操作未完成：{err}</div>}
      {agents.length === 0 ? (
        <div className="hint center">还没有智能体——从上方创建一个开始。</div>
      ) : (
        <div className="cards">
          {agents.map((a) => (
            <div className="card" key={a.id}>
              <div className="card-title">{a.name}</div>
              {a.description && <div className="card-desc">{a.description}</div>}
              <div className="card-state">
                {a.has_datasource ? "数据源已配置" : "未配置数据源"}
              </div>
              <div className="card-actions">
                <button
                  type="button"
                  disabled={!a.has_datasource}
                  title={a.has_datasource ? "" : "先在详情配置数据源"}
                  onClick={() => onChat(a.id)}
                >
                  进入对话
                </button>
                <button type="button" onClick={() => onAgent(a.id)}>
                  配置
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 智能体详情页：四节管理 ─────────────────────────────────────────

function AgentPage({
  id,
  onChat,
  onHome,
}: {
  id: string;
  onChat: () => void;
  onHome: () => void;
}) {
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [presets, setPresets] = useState<string[]>([]);
  const [newQ, setNewQ] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [kbFile, setKbFile] = useState<File | null>(null);
  const [kbKey, setKbKey] = useState(0);
  const [kbMsg, setKbMsg] = useState("");

  async function reload() {
    try {
      const a = await getAgent(id);
      setAgent(a);
      setName(a.name);
      setDesc(a.description);
      setPresets(a.preset_questions);
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  useEffect(() => {
    reload();
    // 依赖只认 id：换智能体重载，reload 闭包引用是稳定的
  }, [id]);

  async function run(fn: () => Promise<unknown>) {
    setErr("");
    try {
      await fn();
      await reload();
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  if (!agent) {
    return (
      <div className="page">
        <div className="note">{err || "加载中…"}</div>
        <button type="button" onClick={onHome}>
          返回首页
        </button>
      </div>
    );
  }
  return (
    <div className="page detail">
      <div className="detail-head">
        <h2>{agent.name}</h2>
        <div className="detail-nav">
          <button type="button" onClick={onChat} disabled={!agent.datasource.has_file}>
            进入对话
          </button>
          <button
            type="button"
            onClick={() => {
              if (window.confirm(`删除智能体「${agent.name}」及其数据源？此操作不可撤销。`)) {
                deleteAgent(id).then(onHome).catch((e: Error) => setErr(e.message));
              }
            }}
          >
            删除智能体
          </button>
          <button type="button" onClick={onHome}>
            返回
          </button>
        </div>
      </div>
      {err && <div className="note">操作未完成：{err}</div>}

      <section className="panel">
        <h3>基本信息</h3>
        <div className="row">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="名称" />
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="描述（选填）"
          />
          <button
            type="button"
            onClick={() => run(() => patchAgent(id, { name, description: desc }))}
          >
            保存
          </button>
        </div>
      </section>

      <section className="panel">
        <h3>数据源</h3>
        <p className="sub">
          {agent.datasource.has_file
            ? agent.datasource.table_count !== null
              ? `已配置 · ${agent.datasource.table_count} 张表/视图（只读打开）`
              : `已配置，但打开失败：${agent.datasource.error}`
            : "未配置——上传 .sqlite 文件（浏览器唯一入口）"}
        </p>
        <div className="row">
          <input
            key={fileKey}
            type="file"
            accept=".sqlite,.sqlite3,.db"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            disabled={!file}
            onClick={() =>
              file &&
              run(async () => {
                await uploadDatasource(id, file);
                setFile(null);
                setFileKey((k) => k + 1);
              })
            }
          >
            {agent.datasource.has_file ? "替换数据源" : "上传"}
          </button>
        </div>
      </section>

      <section className="panel">
        <h3>口径字典（回答本题口径的唯一来源，按题面检索注入）</h3>
        <p className="sub">
          进料认 md/txt/csv：一条口径一个空行块（csv 一行一条）；重复导入＝合并去重、
          只增不删——字典是累积资产，清库＝删智能体
        </p>
        <p className="sub">
          {agent.knowledge.has_entries && agent.knowledge.error
            ? `已导入 ${agent.knowledge.entry_count} 条，但未生效：${agent.knowledge.error}`
            : agent.knowledge.has_entries
              ? `已配置 · ${agent.knowledge.entry_count} 条（按题面检索注入）`
              : agent.knowledge.error
                ? `字典异常：${agent.knowledge.error}`
                : "未配置——用下方导入 md/txt/csv"}
        </p>
        <div className="row">
          <input
            key={kbKey}
            type="file"
            accept=".md,.txt,.csv"
            onChange={(e) => setKbFile(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            disabled={!kbFile}
            onClick={() =>
              kbFile &&
              run(async () => {
                const r = await uploadKnowledge(id, kbFile);
                // 回执文案后端单源（note＝挂账真话，前端只贴不抄第二份字面量）
                setKbMsg(
                  `整档 ${r.total} 条（新增 ${r.added}，向量化 ${r.embedded} 发）` +
                    (r.note ? `；${r.note}` : ""),
                );
                setKbFile(null);
                setKbKey((k) => k + 1);
              })
            }
          >
            导入字典
          </button>
        </div>
        {kbMsg && <p className="sub">{kbMsg}</p>}
      </section>

      <section className="panel">
        <h3>预设问题（≤10 条，对话页点击即发送）</h3>
        <ul className="preset-list">
          {presets.map((q, i) => (
            <li key={i}>
              <span>{q}</span>
              <button type="button" onClick={() => setPresets(presets.filter((_, j) => j !== i))}>
                ×
              </button>
            </li>
          ))}
          {presets.length === 0 && <li className="sub">（暂无预设问题）</li>}
        </ul>
        <div className="row">
          <input
            placeholder="新预设问题"
            value={newQ}
            onChange={(e) => setNewQ(e.target.value)}
          />
          <button
            type="button"
            disabled={!newQ.trim() || presets.length >= 10}
            onClick={() => {
              setPresets([...presets, newQ.trim()]);
              setNewQ("");
            }}
          >
            添加
          </button>
          <button type="button" onClick={() => run(() => patchAgent(id, { preset_questions: presets }))}>
            保存列表
          </button>
        </div>
      </section>
    </div>
  );
}

// ── 对话页：通高侧栏（返回/新会话/历史会话＝票 05 真落点）＋中栏对话 ──
// owner 2026-09-12 改版：业务知识块删除（只活在详情页）、预设 pill 入坞到
// 输入框上方且仅空对话显示、欢迎大字个性化、视口分区本地滚动（agent 布局）。
// 票 05：＋新建会话原地升级＝换新会话号（懒建档，下一问开新档）。
// 会话级运行时通道两度归零（owner 裁 2026-09-15）：口径叠加框（口径活在配置面）、
// ↺ 新话题闸（票 09：换话题＝直接打字，连续性由模型在既有调用内隐式判）。

const newSid = () => crypto.randomUUID().replace(/-/g, "").slice(0, 12);

// M8 票 03 改判（经典 HITL）：待答澄清时回放恢复用的最小应答体——14 字段照契约
// 逐字段摆齐（TS 编译器即钉子：新字段来时这里编译不过，逼两侧同步；契约现 11 字段，ADR-0007/0008 重数）
const pendingAskResp = (ask: string, sid: string): AskResponse => ({
  conclusion: ask, sql: null, columns: null, rows: null, truncated: null,
  elapsed_ms: null, failed: false, error_summary: null,
  session_id: sid, chart: null,
  clarification: ask,
});

// 票 06 控制台数据源：直播＝当前 trail；收口＝最近一条有留痕气泡的 trail
// （M9 票 02 起回放轮自带档案 trail——同组件同形状复形；旧档无留痕照实空态）。
function consoleSource(msgs: Msg[], pending: boolean, progress: ProgressEvent[]) {
  if (pending) return { trail: progress, resp: null as AskResponse | null, replay: false };
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "user" && m.trail.length > 0) {
      return { trail: m.trail, resp: m.role === "agent" ? m.resp : null, replay: false };
    }
  }
  // 空 trail 分两态：有对话消息＝回放载入（旧档未含留痕）；无消息＝还没跑过
  return {
    trail: [] as ProgressEvent[],
    resp: null as AskResponse | null,
    replay: msgs.length > 0,
  };
}

function ChatPage({ id, onHome }: { id: string; onHome: () => void }) {
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [sid, setSid] = useState(newSid); // 票 05：会话号前端自生成，进页＝新会话
  const [sessions, setSessions] = useState<SessionHead[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false); // 在途锁前端侧：流式未结束锁一切发送
  const busyRef = useRef(false); // 同帧双发防呆：setPending 是异步的，闭包 pending 会失效
  const [msgs, setMsgs] = useState<Msg[]>([]);
  // M8 票 04：ts → 票（回放轮从档内 feedback 播种，当场轮投后本地更新）
  const [votes, setVotes] = useState<Record<string, Vote>>({});
  const [progress, setProgress] = useState<ProgressEvent[]>([]);
  const [err, setErr] = useState("");
  // 票 09.5（Q14 改判 Q12）：空态默认收起（回放/还没跑过＝白墙不撞脸），
  // 提问自动展开；折叠钮保留手动否决权，状态不跨会话记忆
  const [consoleOpen, setConsoleOpen] = useState(false);
  const tailRef = useRef<HTMLDivElement>(null);
  // M8 票 03 改判（owner 裁 2026-09-16）：无状态前端合成退役——澄清暂停态住服务端
  // checkpoint，下一条消息**原样**发回即自动续答（服务端合成归档）；本状态只是 UI
  // 提示镜像（composer 提示＋「新话题」按钮），真相以回放接口的 pending 字段为准。
  const [pendingClarify, setPendingClarify] = useState<string | null>(null);

  // 会话号过刷新存活（sessionStorage＝同标签页语义）：不然刷新即换 sid，
  // 服务端那份 pending 就没人认领了。新标签页进页＝新会话，票 05 裁决定语不动。
  function rememberSid(s: string) {
    sessionStorage.setItem("qadata-sid-" + id, s);
    setSid(s);
  }

  async function refreshSessions() {
    try {
      setSessions(await listSessions(id));
    } catch (e) {
      setErr(errMsg(e)); // 侧栏读不动如实报（不静默吞坏会话文件——诚实的另一面）
    }
  }

  useEffect(() => {
    // 换智能体＝新会话（会话绑智能体，spec 票 05）；刷新回来的同一标签页续旧会话
    // （M8 票 03 改判：不续档则服务端那份 pending 无人认领）
    const saved = sessionStorage.getItem("qadata-sid-" + id);
    setSid(saved ?? newSid());
    setMsgs([]);
    setPendingClarify(null);
    if (saved) loadSession(saved, true);
    getAgent(id).then(setAgent).catch((e: Error) => setErr(e.message));
    refreshSessions();
  }, [id]);

  // 对话产品常识行为：新消息/新进度自动滚到最新一条
  useEffect(() => {
    tailRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs, progress, pending]);

  function newConversation() {
    if (busyRef.current) return; // 在途期间不清场（锁的展示面）
    rememberSid(newSid()); // 换会话号：当前会话已在侧栏历史里，下一问开新档
    setMsgs([]);
    setVotes({}); // 票 04：投票镜像随会话换场清零
    setPendingClarify(null); // 新会话不认领上一档的待答澄清（旧 pending 原地过期）
  }

  // M8 票 04：投一票（旁挂档，与在途锁零交叠——回答中也可投票，后端不同文件不同锁）
  async function vote(ts: string, v: Vote) {
    try {
      await postFeedback(id, sid, ts, v);
      setVotes((s) => ({ ...s, [ts]: v }));
    } catch (e) {
      setErr(errMsg(e)); // 后端拒绝如实展示（无档/无轮＝404 文案原样）
    }
  }

  // 会话载入（侧栏点击与刷新恢复共用）：回放＝问答本体（answer 即契约 payload）
  // ＋trail 留痕复形（M9 票 02：档案尾键，旧档无＝空态照实）；在途澄清不落盘，经 pending 字段恢复成
  // 末尾两气泡（原问＋待补充的问句）＝M8 票 03 改判。quiet＝刷新自动恢复不弹错。
  async function loadSession(targetSid: string, quiet: boolean = false) {
    try {
      const d = await getSession(id, targetSid);
      rememberSid(targetSid);
      const seeded: Record<string, Vote> = {};
      for (const t of d.turns) if (t.ts && t.feedback) seeded[t.ts] = t.feedback;
      setVotes(seeded); // 票 04：已投轮高亮回放
      const restored: Msg[] = d.pending
        ? [{ role: "user", text: d.pending.question },
           { role: "agent", resp: pendingAskResp(d.pending.clarification, targetSid), trail: [], ts: null }]
        : [];
      setMsgs([
        ...d.turns.flatMap((t): Msg[] => [
          { role: "user", text: t.question },
          // M9 票 02：trail 入档→回放复形（旧档无尾键＝空，控制台如实显示旧空态）
          { role: "agent", resp: t.answer, trail: t.trail ?? [], ts: t.ts },
        ]),
        ...restored,
      ]);
      setPendingClarify(d.pending?.clarification ?? null);
      setErr("");
    } catch (e) {
      if (!quiet) setErr(errMsg(e));
    }
  }

  function openSession(h: SessionHead) {
    if (busyRef.current) return;
    void loadSession(h.id);
  }

  async function send(text: string, discard: boolean = false) {
    const q = text.trim();
    if (!q || busyRef.current) return;
    busyRef.current = true;
    setConsoleOpen(true); // 票 09.5：真发问才自动展开控制台（空态默认收起的对偶）
    setQuestion("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setPendingClarify(null); // 乐观清提示；若本问又起新澄清（非续答形态）响应后重设
    setPending(true);
    setProgress([]);
    const trail: ProgressEvent[] = [];
    try {
      // 有待答澄清时本条＝补充，服务端合成续跑（M8 票 03 改判，前端不再拼接）；
      // discard＝用户显式放弃续答，本条按新话题问（口径通道＝后端字典，前端零参数，ADR-0008）
      const resp = await askStream(id, q, (ev) => {
        // 票 08：同节点相邻 thinking 帧并成一块（一次思考＝一条折行块，而非几十帧灌满面板）
        const last = trail[trail.length - 1];
        if (
          ev.kind === "thinking" &&
          last &&
          last.kind === "thinking" &&
          last.node === ev.node
        ) {
          trail[trail.length - 1] = { ...last, text: last.text + ev.text };
        } else {
          trail.push(ev);
        }
        setProgress([...trail]);
      }, sid, discard);
      if (resp.clarification) setPendingClarify(resp.clarification);
      // 票 04：当场气泡也要可投——ts 住在后端轮次档案里（契约 14 字段不带，改形状＝改卷），
      // 回查一次会话档拿末轮 ts（本地文件读、零 LLM；同会话在途锁保证末轮即本轮）。
      // 澄清轮不落盘＝没有末轮可锚，跳过。回查失败如实降级＝本轮暂不可投，不拦答案呈现。
      let ts: string | null = null;
      if (resp.session_id && !resp.clarification) {
        try {
          const d = await getSession(id, resp.session_id);
          ts = d.turns.length > 0 ? d.turns[d.turns.length - 1].ts : null;
        } catch {
          /* 拿不到 ts＝不可投，不伪装成功也不炸对话 */
        }
      }
      setMsgs((m) => [...m, { role: "agent", resp, trail: [...trail], ts }]);
      refreshSessions(); // 懒建档：首问落盘后侧栏才有这一档
    } catch (e) {
      // 永不编造：链路错误如实展示，不伪装成答案；半截进度也如实留在错误里
      setMsgs((m) => [...m, { role: "error", text: errMsg(e), trail: [...trail] }]);
    } finally {
      busyRef.current = false;
      setPending(false);
      setProgress([]);
    }
  }

  return (
    <div className="chatshell">
      <aside className="rail">
        <button className="rail-back" type="button" onClick={onHome} title="返回智能体列表">
          ←
        </button>
        <button
          className="rail-new"
          type="button"
          onClick={newConversation}
          disabled={pending || msgs.length === 0}
          title={msgs.length === 0 ? "当前已是新会话" : "开一个新会话（当前会话存入侧栏历史）"}
        >
          ＋ 新建会话
        </button>
        <div className="side-block">
          <h4>历史会话</h4>
          {sessions.length === 0 && <span className="sub">问过话的会话会出现在这里。</span>}
          {sessions.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`hist-item${s.id === sid ? " active" : ""}`}
              disabled={pending}
              onClick={() => openSession(s)}
              title={s.title}
            >
              {s.title}
              <i> {s.turn_count} 轮</i>
            </button>
          ))}
        </div>
      </aside>
      <main className="chatmain">
        {err && <div className="note">未完成：{err}</div>}
        <div className="chatscroll">
          <div className="chatcol">
            {/* 票 09.5 贴底空档：短对话挨着 composer、空档上移；超高时自然缩零不裁顶。
                票 09.6（owner 实拍纠正）：空对话不放——否则 flex-grow 抢光 free space，
                welcome 的 margin:auto 垂直居中失效、被顶到 composer 跟前 */}
            {msgs.length > 0 && <div className="chatfill" />}
            {msgs.length === 0 && !pending &&
              (agent ? (
                <div className="welcome">
                  <h2>有什么想问「{agent.name}」的？</h2>
                  {agent.description && <p className="sub">{agent.description}</p>}
                </div>
              ) : (
                <div className="welcome">
                  <p className="sub">加载中…</p>
                </div>
              ))}
            {msgs.map((m, i) =>
              m.role === "user" ? (
                <div className="bubble user" key={i}>
                  {m.text}
                </div>
              ) : m.role === "error" ? (
                <div className="bubble agent failed" key={i}>
                  请求失败：{m.text}
                  {/* 断流前的半截进度不蒸发（与 catch 注释同真：治黑盒的反面是装干净） */}
                  {m.trail.length > 0 && (
                    <details className="trail">
                      <summary>中断前的进度（{stepCount(m.trail)} 步）</summary>
                      {m.trail.map((ev, j) => (
                        <ProgressRow key={j} ev={ev} />
                      ))}
                    </details>
                  )}
                </div>
              ) : (
                <AnswerBubble
                  resp={m.resp}
                  trail={m.trail}
                  key={i}
                  feedback={m.ts ? votes[m.ts] : undefined}
                  onFeedback={m.ts ? (v) => vote(m.ts as string, v) : undefined}
                />
              ),
            )}
            {pending && (
              <div className="bubble agent progress-live">
                {progress.length === 0 ? (
                  <div className="progress-row">连接进度流…</div>
                ) : (
                  progress.map((ev, i) => <ProgressRow key={i} ev={ev} />)
                )}
              </div>
            )}
            <div ref={tailRef} />
          </div>
        </div>
        <div className="dock">
          {/* 票 09.5（owner 裁 Q17 B，改判票 05 A1「仅空对话＋竖排」）：预设 pill 常驻
              composer 上方横排——对话下方的空白被利用、下一问有引导；
              在途锁同款纪律禁用；点击＝与手输同路 send，零新通道 */}
          {agent && agent.preset_questions.length > 0 && (
            <div className="presets">
              {agent.preset_questions.map((q, i) => (
                <button
                  key={i}
                  type="button"
                  className="preset-pill"
                  disabled={pending}
                  onClick={() => send(q)}
                >
                  {q}
                </button>
              ))}
            </div>
          )}
          <form
            className="composer"
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              send(question);
            }}
          >
            {/* 票 09.6（owner 裁）：提交钮融入输入框——右缘圆形 ↑（ChatGPT 式），
                「提问/补充」语义移入 title 与占位文案（澄清态占位已把玩法说全） */}
            <div className="composer-field">
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={
                  pending
                    ? "正在回答上一个问题…"
                    : pendingClarify
                      ? "回答上方澄清问即自动续答；点右侧按钮可另起新话题…"
                      : agent
                        ? `向 ${agent.name} 提问…`
                        : "加载中…"
                }
                disabled={!agent || pending}
              />
              <button
                type="submit"
                className="send"
                disabled={!agent || pending || !question.trim()}
                title={pendingClarify ? "补充" : "提问"}
                aria-label={pendingClarify ? "补充" : "提问"}
              >
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M8 12.5v-9" />
                  <path d="M3.8 7.2 8 3l4.2 4.2" />
                </svg>
              </button>
            </div>
            {/* M8 票 03 改判：有待答澄清时显式放弃＝本条按新话题问（服务端弃 checkpoint 续档） */}
            {pendingClarify && (
              <button
                type="button"
                className="secondary"
                disabled={!agent || pending || !question.trim()}
                onClick={() => send(question, true)}
                title="放弃这条澄清，把输入作为新话题提问"
              >
                新话题
              </button>
            )}
          </form>
        </div>
      </main>
      {/* M8 票 06：右侧任务控制台抽屉（概览四卡＋追踪时间线，数据源＝既有进度帧）；
          票 09.5：头部右上角面板字形钮手动折叠，收起＝2.25rem 窄轨留镜像钮 */}
      <aside className={`console-drawer${consoleOpen ? "" : " collapsed"}`}>
        <button
          type="button"
          className="console-toggle"
          title={consoleOpen ? "收起控制台" : "展开控制台"}
          aria-label={consoleOpen ? "收起任务控制台" : "展开任务控制台"}
          onClick={() => setConsoleOpen(!consoleOpen)}
        >
          {/* VS Code 式面板字形（内联 SVG，零新依赖）：描边方框＋左分隔条 */}
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2" />
            <line x1="6.25" y1="2.75" x2="6.25" y2="13.25" />
          </svg>
        </button>
        {consoleOpen &&
          (() => {
            const cs = consoleSource(msgs, pending, progress);
            return (
              <Console trail={cs.trail} resp={cs.resp} streaming={pending} replay={cs.replay} />
            );
          })()}
      </aside>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>({ page: "home" });

  if (view.page === "chat") {
    // 对话页＝通高沉浸（无顶部标题栏、无 52rem 容器），owner 2026-09-12 裁
    return <ChatPage id={view.id} onHome={() => setView({ page: "home" })} />;
  }

  let body: ReactNode;
  if (view.page === "home") {
    body = (
      <HomePage
        onAgent={(id) => setView({ page: "agent", id })}
        onChat={(id) => setView({ page: "chat", id })}
      />
    );
  } else {
    body = (
      <AgentPage
        id={view.id}
        onChat={() => setView({ page: "chat", id: view.id })}
        onHome={() => setView({ page: "home" })}
      />
    );
  }

  return (
    <div className="app">
      <header>
        <h1>问数</h1>
        <span className="sub">对话式数据分析 Agent</span>
      </header>
      {body}
    </div>
  );
}
