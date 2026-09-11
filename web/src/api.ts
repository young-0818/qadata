// 与后端 Python 侧契约测试（tests/test_web_api.py）同构的响应形状。
// 票 01 冻结 /api/ask 12 字段；票 02 起 /api/dbs 条目含默认口径与来源轨。
// 改形状＝跨票改卷。
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

export interface DbOption {
  name: string;
  evidence: string; // 默认口径（预置 YAML/指标派生；导入库恒空）
  source: "preset" | "import";
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

export async function listDbs(): Promise<DbOption[]> {
  const res = await fetch("/api/dbs");
  if (!res.ok) throw new Error(await errorText(res, "/api/dbs 失败"));
  const body = (await res.json()) as { dbs: DbOption[] };
  return body.dbs;
}

export async function ask(
  db: string,
  question: string,
  evidence: string,
): Promise<AskResponse> {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ db, question, evidence }),
  });
  if (res.status === 404) throw new Error("未知数据库（404）");
  if (!res.ok) throw new Error(await errorText(res, "/api/ask 失败"));
  return (await res.json()) as AskResponse;
}

// B 轨上传：raw-body 字节流（spec 冻结依赖，不走 multipart），名/别名在查询串。
// 后端另有 POST /api/dbs/local（路径直连）——票面双入口归契约测试，UI 按 owner 裁只留上传。
export async function uploadDb(file: File, alias: string): Promise<DbOption> {
  const params = new URLSearchParams({ name: file.name });
  if (alias.trim()) params.set("alias", alias.trim());
  const res = await fetch(`/api/dbs/upload?${params.toString()}`, {
    method: "POST",
    body: file,
  });
  if (!res.ok) throw new Error(await errorText(res, "上传失败"));
  return (await res.json()) as DbOption;
}
