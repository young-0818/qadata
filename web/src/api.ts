// 与后端 Python 侧契约测试（tests/test_web_api.py）同构的响应形状。
// 票 01 冻结 12 字段；改形状＝跨票改卷。
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

export async function listDbs(): Promise<string[]> {
  const res = await fetch("/api/dbs");
  if (!res.ok) throw new Error(`/api/dbs 失败（HTTP ${res.status}）`);
  const body = (await res.json()) as { dbs: string[] };
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
  if (!res.ok) throw new Error(`/api/ask 失败（HTTP ${res.status}）`);
  return (await res.json()) as AskResponse;
}
