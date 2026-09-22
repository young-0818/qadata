// 与后端 Python 侧契约测试（tests/test_web_api.py）同构的响应形状。
// 票 01 冻结 /api/ask 12 字段（rev2 请求体 db→agent_id，响应形状不动）；
// 票 04 经 owner 裁决新增可选字段 chart（其余形状仍＝跨票改卷）；
// 票 05 会话轮 session_id 出真值（单轮请求照旧 null）；
// 智能体面（CRUD/数据源/口径字典/模型只读卡）由票 02.5 钉死（业务知识 evidence 已随 ADR-0008 撤）。
export interface AskResponse {
  conclusion: string;
  sql: string | null;
  columns: string[] | null;
  rows: unknown[][] | null;
  truncated: boolean | null;
  elapsed_ms: number | null;
  failed: boolean;
  error_summary: string | null;
  // M5 path/metric_name/template_fell_back 三键已随指标层退役删除（ADR-0007）
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

export interface KnowledgeInfo {
  has_entries: boolean;
  entry_count: number;
  error: string | null; // 挂账（有内容未向量化）/坏档如实上报，不装已生效
}

export interface IndexJob {
  state: string; // running | ok | partial | failed
  note: string; // 回执真话（后端单源，前端只贴）
  started: string;
}

export interface IndexInfo {
  embed_configured: boolean;
  cards: string; // missing | broken | stale | ok（现读降级闸同源）
  values: string;
  job: IndexJob | null; // 无＝从未点过建索引
}

export interface AgentDetail {
  id: string;
  name: string;
  description: string;
  preset_questions: string[];
  datasource: DatasourceInfo;
  knowledge: KnowledgeInfo;
  index: IndexInfo;
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
  fields: Partial<Pick<AgentDetail, "name" | "description" | "preset_questions">>,
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

// M10 票 05＋（web 化前提）：口径字典进料 web 门（回执文案后端单源，
// note＝挂账真话，前端只贴不抄）
export interface KnowledgeFeedResult {
  total: number;
  added: number;
  embedded: number;
  note: string;
}

export async function uploadKnowledge(id: string, file: File): Promise<KnowledgeFeedResult> {
  const params = new URLSearchParams({ name: file.name });
  const res = await fetch(`/api/agents/${id}/knowledge?${params.toString()}`, {
    method: "POST",
    body: file,
  });
  if (!res.ok) throw new Error(await errorText(res, "导入失败"));
  return (await res.json()) as KnowledgeFeedResult;
}

// M11 票 02：建索引 web 门（ADR-0005：显式管理动作，点击才触发一次后台构建；
// 200＝已受理，进度经详情面 index.job 轮询现读，不开第二通道）
export async function startIndexBuild(id: string): Promise<void> {
  const res = await fetch(`/api/agents/${id}/index-build`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "索引构建未启动"));
}

// 票 03：进度流帧三型（M8 票 06 喂厚，文案后端单源，前端只贴标签）：
// start 帧三字段不动；结果帧加 ok/duration_ms/tokens_in/tokens_out（chart 可选字段
// 先例——旧消费者忽略即得）；tool 子事件帧无 attempt/status，kind:"tool" 判别。
// 实时工具链追加 tool start 活口帧（第四型）：kind:"tool" 且 status:"start"＝工具开跑、
// 尚无 ok/耗时——前端当场成行转圈，收口帧原位配对翻终态（同 node 同 tool 最近未收口行）。
// 联合类型＝编译器即帧型钉：忘判 kind 直接取 status 过不了 tsc。
export interface StepEvent {
  kind?: undefined; // 判别位（tool 帧专属 "tool"，此处显式 undefined 供联合收窄）
  node: string;
  attempt: number; // 该时刻已入账的 SQL 尝试数（重试环上单调递增）
  status: string; // "start"＝该步开跑，其余＝该步结果一行中文
  ok?: boolean; // 结果帧：该步收口红绿（降级/可疑不算失败）
  duration_ms?: number; // 该步耗时
  tokens_in?: number; // 该步 LLM 调用 token 累计（纯码节点恒 0＝如实的零）
  tokens_out?: number;
}

export interface ToolEvent {
  node: string; // 归属步骤（explore/execute/respond）
  kind: "tool";
  status?: undefined; // 判别位：收口帧无 status（start 活口帧专属 "start"）
  tool: string; // list_tables/get_schema/select_tables/value_samples/execute_sql…
  ok: boolean; // 绿点成功/红点失败（owner 截图语义）
  duration_ms: number;
  detail?: string; // 可选尾字段（chart 同族）：值链「实际取值」命中明细（列→库内实际值）
}

// tool start 活口帧：工具开跑即发（无 ok/耗时——还没跑完，如实半开）。
// budget_fuse 等即时入账帧无计时区间＝不发此型（发射侧裁决）。
export interface ToolStartEvent {
  node: string;
  kind: "tool";
  status: "start";
  tool: string;
}

// M8 票 08 思考流：understand/generate 流式旁路的 reasoning_content 增量帧（后端逐
// chunk 转发、单流累计 ≤2000 字截断）。前端把同节点相邻 thinking 帧并成一块累积文本，
// 渲进进度面板（治 12~30s 死寂转圈）；不参与「N 步」计数、不进控制台时间线。
export interface ThinkingEvent {
  node: string; // 归属步骤（恒 understand/generate）
  kind: "thinking";
  text: string; // 该 chunk 的思考文本增量（前端拼接）
}

export type ProgressEvent = StepEvent | ToolEvent | ToolStartEvent | ThinkingEvent;

// 判别拆两半（帧型钉延续）：收口帧 ok/duration_ms 必在场、start 活口帧必无——
// 忘判 status 直接取 ok 过不了 tsc
export const isToolEvent = (ev: ProgressEvent): ev is ToolEvent =>
  ev.kind === "tool" && ev.status !== "start";

export const isToolStartEvent = (ev: ProgressEvent): ev is ToolStartEvent =>
  ev.kind === "tool" && ev.status === "start";

export const isThinkingEvent = (ev: ProgressEvent): ev is ThinkingEvent =>
  ev.kind === "thinking";

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
  answer: AskResponse; // 契约 payload 原样回放
  feedback?: Vote; // 票 04 可选尾键（chart 先例）：该轮末票；缺省＝没投过
  trail?: ProgressEvent[]; // M9 票 02 可选尾键：步骤＋tool 帧留痕（thinking 不入档），回放控制台复形用
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
