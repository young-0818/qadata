// 与后端 Python 侧契约测试（tests/test_web_api.py）同构的响应形状。
// 票 01 冻结 /api/ask 12 字段（rev2 请求体 db→agent_id，响应形状不动）；
// 智能体面（CRUD/数据源/业务知识/模型只读卡）由票 02.5 钉死。改形状＝跨票改卷。
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
  session_id: null; // 票 04 起才有真值
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

// 票 03 问数主通道：POST /api/ask/stream（SSE）。EventSource 不支持 POST，
// 故 fetch＋ReadableStream 手解帧——不引新依赖（状态路由同款纪律）。
// onProgress 逐帧回调；末帧 event:answer resolve；非 2xx 拒在起流前如实抛出。
export async function askStream(
  agentId: string,
  question: string,
  evidence: string,
  onProgress: (ev: ProgressEvent) => void,
): Promise<AskResponse> {
  const res = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id: agentId, question, evidence }),
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
