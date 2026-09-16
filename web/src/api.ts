// 与后端 Python 侧契约测试（tests/test_web_api.py）同构的响应形状。
// 票 01 冻结 /api/ask 12 字段（rev2 请求体 db→agent_id，响应形状不动）；
// 票 04 经 owner 裁决新增可选字段 chart（其余形状仍＝跨票改卷）；
// 票 05 会话轮 session_id 出真值（单轮请求照旧 null）；
// 智能体面（CRUD/数据源/业务知识/模型只读卡）由票 02.5 钉死。
export interface AskResponse {
  conclusion: string;
  sql: string | null;
  columns: string[] | null;
  rows: unknown[][] | null;
  truncated: boolean | null;
  elapsed_ms: number | null;
  failed: boolean;
  error_summary: string | null;
  path: "metric" | "fallback"; // 与 Answer.path 取值域对齐（M5 票 05）
  metric_name: string | null;
  template_fell_back: boolean;
  session_id: string | null; // 票 05：带会话即回显请求所带 sid；单轮＝null
  chart: ChartSpec | null; // 票 04：后端规则纯函数判定的图型，null＝表格
  clarification: string | null; // M8 票 03（默认关恒 null）：澄清轮问句本体——非失败非答案；
  // owner 改判 2026-09-16＝经典 HITL：暂停态存服务端 checkpoint，下一条消息自动续答
  // （前端不再本地合成）；澄清轮不落盘，回放经会话详情 pending 字段恢复
}

// 票 04 图型判定契约（web/charts.py::decide_chart）：列下标寻址 columns/rows
// （SQL 列名可重复，当不了键）；前端只管画，不自判形状。
export interface ChartSpec {
  type: "line" | "bar" | "number";
  x: number | null; // x 轴列下标（大数卡为 null）
  series: number[]; // 数值系列列下标（大数卡即单值所在列）
}

export interface ModelCard {
  model: string | null;
  source: string;
  writable: boolean; // 恒 false：模型真源是 .env，UI 只读（票 02.5 裁）
}

export interface AgentSummary {
  id: string;
  name: string;
  description: string;
  has_datasource: boolean;
}

export interface DatasourceInfo {
  has_file: boolean;
  table_count: number | null;
  error: string | null; // 库打不开如实上报，不装正常
}

export interface AgentDetail {
  id: string;
  name: string;
  description: string;
  evidence: string; // 手动业务知识（后端字段名沿用 evidence，UI 叫业务知识）
  metrics_ref: string; // 非空＝引用态：业务读取期派生，手动编辑被后端拒绝
  business_knowledge: string; // 实际生效的业务知识（引用态＝派生文本）
  business_knowledge_error: string | null;
  preset_questions: string[];
  datasource: DatasourceInfo;
}

// 非 2xx 时把后端诚实的 detail 文案取出来展示（永不编造错误说明）
async function errorText(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (body && typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    /* 非 JSON 错误体：回落状态码说明 */
  }
  return `${fallback}（HTTP ${res.status}）`;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(await errorText(res, `${path} 失败`));
  return (await res.json()) as T;
}

async function sendJson<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await errorText(res, `${path} 失败`));
  return (await res.json()) as T;
}

export function getModel(): Promise<ModelCard> {
  return getJson<ModelCard>("/api/model");
}

export function listRegistries(): Promise<string[]> {
  return getJson<{ registries: string[] }>("/api/metrics-registries").then(
    (b) => b.registries,
  );
}

export function listAgents(): Promise<AgentSummary[]> {
  return getJson<{ agents: AgentSummary[] }>("/api/agents").then((b) => b.agents);
}

export function getAgent(id: string): Promise<AgentDetail> {
  return getJson<AgentDetail>(`/api/agents/${id}`);
}

export function createAgent(name: string, description: string): Promise<AgentDetail> {
  return sendJson<AgentDetail>("/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description }),
  });
}

// 部分更新：只发显式字段（PATCH 语义由后端 model_fields_set 钉）
export function patchAgent(
  id: string,
  fields: Partial<Pick<AgentDetail, "name" | "description" | "evidence" | "metrics_ref" | "preset_questions">>,
): Promise<AgentDetail> {
  return sendJson<AgentDetail>(`/api/agents/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
}

export async function deleteAgent(id: string): Promise<void> {
  const res = await fetch(`/api/agents/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await errorText(res, "删除失败"));
}

// 数据源上传：raw-body 字节流（spec 冻结依赖，不走 multipart），原始名仅供扩展名校验
export async function uploadDatasource(id: string, file: File): Promise<void> {
  const params = new URLSearchParams({ name: file.name });
  const res = await fetch(`/api/agents/${id}/datasource?${params.toString()}`, {
    method: "POST",
    body: file,
  });
  if (!res.ok) throw new Error(await errorText(res, "上传失败"));
}

// 票 03：进度流帧（node/attempt/status 三字段，文案后端单源，前端只贴标签）
export interface ProgressEvent {
  node: string;
  attempt: number; // 该时刻已入账的 SQL 尝试数（重试环上单调递增）
  status: string; // "start"＝该步开跑，其余＝该步结果一行中文
}

// 票 05：会话面（tests/test_web_sessions.py 同构）。sid＝客户端生成的 hex12，
// 懒建档——"＋ 新建会话"＝换新 sid、下一问开新档；列表只认有轮次的会话。
export interface SessionHead {
  id: string;
  title: string; // 首轮问题（展示名，无第二真源）
  updated: string; // 末轮落盘时刻（北京时区 ISO，排序键）
  turn_count: number;
}

// M8 票 04：反馈票值（后端 VOTES 单源）
export type Vote = "up" | "down";

export interface SessionTurn {
  question: string;
  failed: boolean;
  ts: string | null;
  answer: AskResponse; // 契约 payload 原样回放（trail 属现场观察，不入档）
  feedback?: Vote; // 票 04 可选尾键（chart 先例）：该轮末票；缺省＝没投过
}

export interface SessionDetail {
  id: string;
  turns: SessionTurn[];
  // M8 票 03 改判（经典 HITL）：在途澄清（服务端指针）——刷新/回放后据此恢复
  // 「待你补充」形态；null＝无待答。单轮关态恒 null（字段后端只在会话面出）
  pending: { question: string; clarification: string } | null;
}

export function listSessions(agentId: string): Promise<SessionHead[]> {
  return getJson<{ sessions: SessionHead[] }>(`/api/agents/${agentId}/sessions`).then(
    (b) => b.sessions,
  );
}

export function getSession(agentId: string, sid: string): Promise<SessionDetail> {
  return getJson<SessionDetail>(`/api/agents/${agentId}/sessions/${sid}`);
}

// M8 票 04：一票评价进旁挂票档（不进会话主档、不进答案契约——裁决旁挂维持）。
// 同轮再投＝追加末票覆盖（回放取末票），前端投后禁用不再有机会。
export async function postFeedback(
  agentId: string,
  sessionId: string,
  ts: string,
  vote: Vote,
): Promise<void> {
  await sendJson<{ ok: boolean }>(`/api/agents/${agentId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ts, vote }),
  });
}

// 票 03 问数主通道：POST /api/ask/stream（SSE）。EventSource 不支持 POST，
// 故 fetch＋ReadableStream 手解帧——不引新依赖（状态路由同款纪律）。
// onProgress 逐帧回调；末帧 event:answer resolve；非 2xx 拒在起流前如实抛出。
// sessionId（票 05）＝当前会话号：带即装载三层记忆并落盘，缺省单轮。
// discardPending（M8 票 03 改判）＝有待答澄清时放弃续答、本条按新话题问。
export async function askStream(
  agentId: string,
  question: string,
  evidence: string,
  onProgress: (ev: ProgressEvent) => void,
  sessionId?: string,
  discardPending: boolean = false,
): Promise<AskResponse> {
  const res = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      agent_id: agentId,
      question,
      evidence,
      session_id: sessionId ?? null,
      discard_pending: discardPending,
    }),
  });
  if (!res.ok) throw new Error(await errorText(res, "问数失败"));
  if (!res.body) throw new Error("当前浏览器不支持流式读取");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let answer: AskResponse | null = null;
  const consume = (block: string) => {
    let event: string | null = null;
    let data: string | null = null;
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data = line.slice(6);
    }
    // 契约＝后端每帧必带 data 行；缺 data 属形状违规，不静默吞（解析即炸→如实报错）
    if (event === "answer") answer = JSON.parse(data as string) as AskResponse;
    else onProgress(JSON.parse(data as string) as ProgressEvent);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      if (block.trim()) consume(block);
    }
  }
  buf += decoder.decode();
  if (buf.trim()) consume(buf); // 后端每帧带 \n\n 收口，此为尾帧防御
  if (!answer) throw new Error("进度流已断，未收到答案——以上方失败说明为准");
  return answer;
}
