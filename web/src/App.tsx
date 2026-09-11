import { FormEvent, ReactNode, useEffect, useState } from "react";
import {
  AgentDetail,
  AgentSummary,
  AskResponse,
  ModelCard,
  ask,
  createAgent,
  deleteAgent,
  getAgent,
  getModel,
  listAgents,
  listRegistries,
  patchAgent,
  uploadDatasource,
} from "./api";

// 渲染纪律（spec）：一律 React 文本插值（＝textContent），全文件禁 dangerouslySetInnerHTML。
// M7-rev2 票 02.5：三视图状态路由（首页/智能体详情/对话页），不引 router 依赖。

// 后端拒绝理由一律原样展示（永不编造错误说明）
const errMsg = (e: unknown): string =>
  e instanceof Error ? e.message : String(e);

type View =
  | { page: "home" }
  | { page: "agent"; id: string }
  | { page: "chat"; id: string };

type Msg =
  | { role: "user"; text: string }
  | { role: "agent"; resp: AskResponse }
  | { role: "error"; text: string };

interface Section {
  title: string;
  body: string;
}

// 对齐 compose_conclusion（票 07）排版：每节以【节头】起行
const SECTION_RE = /^【(.+?)】(.*)$/;

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

function AnswerBubble({ resp }: { resp: AskResponse }) {
  const sections = parseSections(resp.conclusion);
  return (
    <div className={`bubble agent${resp.failed ? " failed" : ""}`}>
      {sections.map((s, i) =>
        s.title ? (
          <div className="section" key={i}>
            <span className="section-title">{s.title}</span>
            <span className="section-body">{s.body}</span>
          </div>
        ) : (
          <div className="section-body" key={i}>
            {s.body}
          </div>
        ),
      )}
      {resp.columns && resp.rows && (
        <table className="result-table">
          <thead>
            <tr>
              {resp.columns.map((c, i) => (
                <th key={i}>{String(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {resp.rows.map((row, i) => (
              <tr key={i}>
                {row.map((v, j) => (
                  <td key={j}>{v === null ? "NULL" : String(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {resp.truncated && <div className="note">结果已截断（仅展示部分行）</div>}
      {resp.sql && (
        <pre className="sql">
          <code>{resp.sql}</code>
        </pre>
      )}
      <div className="meta">
        <span>{resp.path === "metric" ? "指标命中" : "兜底路线"}</span>
        {resp.metric_name && <span>· {resp.metric_name}</span>}
        {resp.elapsed_ms !== null && <span>· {resp.elapsed_ms} ms</span>}
        {/* 票 01 恒单轮（session_id 恒 null）；票 04 多轮落地时换成会话标识 */}
        {resp.session_id === null && <span>· 单轮</span>}
      </div>
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
  const [registries, setRegistries] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [evidence, setEvidence] = useState("");
  const [refSel, setRefSel] = useState("");
  const [presets, setPresets] = useState<string[]>([]);
  const [newQ, setNewQ] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);

  async function reload() {
    try {
      const a = await getAgent(id);
      setAgent(a);
      setName(a.name);
      setDesc(a.description);
      setEvidence(a.evidence);
      setRefSel(a.metrics_ref);
      setPresets(a.preset_questions);
      setErr(a.business_knowledge_error ? `业务知识：${a.business_knowledge_error}` : "");
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  useEffect(() => {
    reload();
    listRegistries().then(setRegistries).catch(() => setRegistries([]));
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
  const referencing = agent.metrics_ref !== "";

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
        <h3>业务知识（问数时注入的背景口径）</h3>
        {referencing ? (
          <>
            <p className="sub">
              引用指标注册表 <b>{agent.metrics_ref}</b> · 每次问数读取期派生（注册表改则随动，无双写）
            </p>
            <pre className="kb-preview">{agent.business_knowledge}</pre>
            <div className="row">
              <button
                type="button"
                onClick={() => run(() => patchAgent(id, { metrics_ref: "" }))}
              >
                解除引用，改手动
              </button>
            </div>
          </>
        ) : (
          <>
            <textarea
              rows={4}
              placeholder="每行一条，如：总金额 = sum(loan.amount)；违约 = loan.status='B'"
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
            />
            <div className="row">
              <button type="button" onClick={() => run(() => patchAgent(id, { evidence }))}>
                保存业务知识
              </button>
              {registries.length > 0 && (
                <span className="row">
                  或引用指标注册表{" "}
                  <select value={refSel} onChange={(e) => setRefSel(e.target.value)}>
                    <option value="">（选择）</option>
                    {registries.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={!refSel}
                    onClick={() =>
                      // 单发 PATCH：清手动＋设引用一次合并提交（存储层合并后过双写校验），
                      // 两次调用会留"手动口径已抹、引用失败"的中间窗（评审收紧）
                      run(() => patchAgent(id, { evidence: "", metrics_ref: refSel }))
                    }
                  >
                    引用
                  </button>
                </span>
              )}
            </div>
          </>
        )}
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

// ── 对话页：左栏壳（会话历史＝票 04）＋右对话 ──────────────────────

function ChatPage({ id, onHome }: { id: string; onHome: () => void }) {
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    getAgent(id).then(setAgent).catch((e: Error) => setErr(e.message));
  }, [id]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || pending) return;
    setQuestion("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setPending(true);
    try {
      // evidence 恒空＝智能体业务知识兜底（会话级口径叠加框在票 04）
      const resp = await ask(id, q, "");
      setMsgs((m) => [...m, { role: "agent", resp }]);
    } catch (e) {
      // 永不编造：链路错误如实展示，不伪装成答案
      setMsgs((m) => [...m, { role: "error", text: errMsg(e) }]);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="chatlayout">
      <aside className="sidebar">
        <button className="back" type="button" onClick={onHome}>
          ← 所有智能体
        </button>
        <div className="side-agent">
          <b>{agent?.name ?? "加载中…"}</b>
          {agent?.description && <span className="sub">{agent.description}</span>}
        </div>
        {agent && agent.preset_questions.length > 0 && (
          <div className="side-block">
            <h4>预设问题</h4>
            {agent.preset_questions.map((q, i) => (
              <button key={i} type="button" className="chip" disabled={pending} onClick={() => send(q)}>
                {q}
              </button>
            ))}
          </div>
        )}
        <div className="side-block">
          <h4>历史会话</h4>
          <span className="sub">多轮会话（票 04）落地后在这里出现。</span>
        </div>
        {agent && agent.business_knowledge && (
          <div className="side-block">
            <h4>业务知识</h4>
            <span className="sub kb-note">{agent.business_knowledge.slice(0, 200)}</span>
          </div>
        )}
      </aside>
      <main className="chat">
        {err && <div className="note">加载失败：{err}</div>}
        {msgs.length === 0 && !pending && (
          <div className="hint center">用自然语言提问，或点左侧预设问题。</div>
        )}
        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div className="bubble user" key={i}>
              {m.text}
            </div>
          ) : m.role === "error" ? (
            <div className="bubble agent failed" key={i}>
              请求失败：{m.text}
            </div>
          ) : (
            <AnswerBubble resp={m.resp} key={i} />
          ),
        )}
        {pending && (
          <div className="bubble agent pending">查询中…（生成 SQL → 沙箱执行 → 校验）</div>
        )}
        <form
          className="composer"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            send(question);
          }}
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={agent ? `向 ${agent.name} 提问…` : "加载中…"}
            disabled={!agent}
          />
          <button type="submit" disabled={!agent || pending || !question.trim()}>
            提问
          </button>
        </form>
      </main>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>({ page: "home" });

  let body: ReactNode;
  if (view.page === "home") {
    body = (
      <HomePage
        onAgent={(id) => setView({ page: "agent", id })}
        onChat={(id) => setView({ page: "chat", id })}
      />
    );
  } else if (view.page === "agent") {
    body = (
      <AgentPage
        id={view.id}
        onChat={() => setView({ page: "chat", id: view.id })}
        onHome={() => setView({ page: "home" })}
      />
    );
  } else {
    body = <ChatPage id={view.id} onHome={() => setView({ page: "home" })} />;
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
