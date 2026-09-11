import { FormEvent, useEffect, useState } from "react";
import {
  AskResponse,
  DbOption,
  ask,
  importLocalPath,
  listDbs,
  uploadDb,
} from "./api";

// 渲染纪律（spec）：结果值一律走 React 文本插值（＝textContent），
// 全文件禁止 dangerouslySetInnerHTML。
type Msg =
  | { role: "user"; text: string }
  | { role: "agent"; resp: AskResponse }
  | { role: "error"; text: string };

interface Section {
  title: string;
  body: string;
}

// 对齐 compose_conclusion（票 07）排版：每节以【节头】起行，校验节为列表行
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
        {/* 票 01 恒单轮（session_id 恒 null）；票 04 多轮落地时此徽标换成会话标识 */}
        {resp.session_id === null && <span>· 单轮</span>}
      </div>
    </div>
  );
}

// 票 02 B 轨：上传 .sqlite 落盘服务器 web_imports，或本机路径直连登记——
// 两入口都进库列表、可起别名；打开仍走后端只读沙箱唯一入口。
function ImportPanel({ onImported }: { onImported: (entry: DbOption) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [fileAlias, setFileAlias] = useState("");
  const [path, setPath] = useState("");
  const [pathAlias, setPathAlias] = useState("");
  const [busy, setBusy] = useState<"" | "upload" | "local">("");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [fileKey, setFileKey] = useState(0); // 非受控 file input：靠重挂载清空

  async function run(kind: "upload" | "local", fn: () => Promise<DbOption>) {
    setBusy(kind);
    setErr("");
    setOk("");
    try {
      const entry = await fn();
      setOk(`已导入：${entry.name}`);
      setFile(null);
      setFileKey((k) => k + 1);
      setFileAlias("");
      setPath("");
      setPathAlias("");
      onImported(entry);
    } catch (e) {
      // 永不编造：拒绝理由（引擎串/非 sqlite/撞名…）原样展示后端诚实文案
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="imports">
      <div className="import-row">
        <input
          key={fileKey}
          type="file"
          accept=".sqlite,.sqlite3,.db"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <input
          placeholder="别名（选填，缺省取文件名）"
          value={fileAlias}
          onChange={(e) => setFileAlias(e.target.value)}
        />
        <button
          type="button"
          disabled={!file || busy !== ""}
          onClick={() => file && run("upload", () => uploadDb(file, fileAlias))}
        >
          {busy === "upload" ? "上传中…" : "上传导入"}
        </button>
        {file && <span className="import-hint">{file.name}</span>}
      </div>
      <div className="import-row">
        <input
          placeholder="本机 sqlite 文件路径，如 D:/data/my.db"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <input
          placeholder="别名（选填）"
          value={pathAlias}
          onChange={(e) => setPathAlias(e.target.value)}
        />
        <button
          type="button"
          disabled={!path.trim() || busy !== ""}
          onClick={() => run("local", () => importLocalPath(path.trim(), pathAlias))}
        >
          {busy === "local" ? "登记中…" : "路径直连"}
        </button>
      </div>
      {err && <div className="note">导入被拒：{err}</div>}
      {ok && <div className="note">{ok}（口径默认为空，可在上方选填）</div>}
    </div>
  );
}

export default function App() {
  const [dbs, setDbs] = useState<DbOption[]>([]);
  const [db, setDb] = useState("");
  const [question, setQuestion] = useState("");
  const [evidence, setEvidence] = useState("");
  const [pending, setPending] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [dbError, setDbError] = useState("");

  // 切库即预填该库默认口径（A 轨 YAML/指标派生；B 轨导入恒空）——演示场景
  // 以库口径为准，用户可在框内改写；票 04 会话级叠加框再分离这两个职责。
  function selectDb(name: string, opts: DbOption[]) {
    setDb(name);
    setEvidence(opts.find((o) => o.name === name)?.evidence ?? "");
  }

  function pickDefault(opts: DbOption[]): DbOption | undefined {
    return opts.find((o) => o.name === "financial") ?? opts[0];
  }

  useEffect(() => {
    listDbs()
      .then((opts) => {
        setDbs(opts);
        const first = pickDefault(opts);
        if (first) selectDb(first.name, opts);
      })
      .catch((e: Error) => setDbError(e.message));
  }, []);

  async function onImported(entry: DbOption) {
    try {
      const opts = await listDbs();
      setDbs(opts);
      if (opts.some((o) => o.name === entry.name)) selectDb(entry.name, opts);
    } catch (e) {
      setDbError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || pending || !db) return;
    setQuestion("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setPending(true);
    try {
      const resp = await ask(db, q, evidence.trim());
      setMsgs((m) => [...m, { role: "agent", resp }]);
    } catch (err) {
      // 永不编造：链路错误如实展示，不伪装成答案
      setMsgs((m) => [
        ...m,
        { role: "error", text: err instanceof Error ? err.message : String(err) },
      ]);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>问数</h1>
        <span className="sub">对话式数据分析 Agent</span>
      </header>

      <div className="toolbar">
        <label>
          数据库{" "}
          <select value={db} onChange={(e) => selectDb(e.target.value, dbs)} disabled={dbs.length === 0}>
            {dbs.length === 0 && <option value="">（无预置库）</option>}
            {dbs.map((o) => (
              <option key={o.name} value={o.name}>
                {o.name}
                {o.source === "import" ? "（导入）" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="evidence">
          口径（选填）{" "}
          <input
            value={evidence}
            onChange={(e) => setEvidence(e.target.value)}
            placeholder="如：销售额 = sum(loan.amount)"
          />
        </label>
      </div>
      <ImportPanel onImported={onImported} />
      {dbError && <div className="note">库列表加载失败：{dbError}</div>}

      <main className="chat">
        {msgs.length === 0 && !pending && (
          <div className="hint">选一个库，用自然语言提问。例：「贷款金额的平均值是多少」</div>
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
        {pending && <div className="bubble agent pending">查询中…（生成 SQL → 沙箱执行 → 校验）</div>}
      </main>

      <form className="composer" onSubmit={onSubmit}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={db ? `向 ${db} 提问…` : "请先选库"}
          disabled={!db}
        />
        <button type="submit" disabled={!db || pending || !question.trim()}>
          提问
        </button>
      </form>
    </div>
  );
}
