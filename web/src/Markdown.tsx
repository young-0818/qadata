// M8 票 07 答案报告化：【结论】节的 markdown 子集安全渲染器。
// 纪律（charts.py 同款文化）：纯函数 parseMarkdown ＋ 渲染不产任何 raw HTML——
// 全文只经 React 文本插值达 DOM，模型吐出 <script> 也只是一串字符
// （textContent 纪律，源扫钉 tests/test_web_markdown.py）。零新依赖。
// 子集＝标题 / 加粗 / 行内码 / 列表（无序·有序）/ 表格 / 围栏代码块（行内码＝冒烟实跑
// 所见：模型逢表名列名必加反引号，字面留背对号＝噪声；钉它）；
// 子集外记号（斜体、链接、嵌套、HTML）一律按普通文字原样呈现——诚实降级，
// 不误解、不吞内容（宁可看，不可丢）。

type Inline = { text: string; bold: boolean; code: boolean };

type Block =
  | { kind: "heading"; level: number; parts: Inline[] }
  | { kind: "para"; parts: Inline[] }
  | { kind: "list"; ordered: boolean; items: Inline[][] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "code"; text: string };

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const UL_RE = /^[-*]\s+(.*)$/;
const OL_RE = /^\d+[.)]\s+(.*)$/;
const FENCE_RE = /^\s*```/;
// 表格分隔行：只由 | - : 空白构成且至少一个 -（表头行须含 |，故裸 --- 不会误判成表）
const SEP_RE = /^\s*[\s|:-]*-[\s|:-]*\s*$/;

// 行内记号两种（**加粗**、`代码`）；不配对的（含裸 HTML）原样留作文字
function inline(text: string): Inline[] {
  const out: Inline[] = [];
  const re = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), bold: false, code: false });
    out.push(
      m[1] !== undefined
        ? { text: m[1], bold: true, code: false }
        : { text: m[2], bold: false, code: true },
    );
    last = m.index + m[0].length;
  }
  if (last < text.length || out.length === 0)
    out.push({ text: text.slice(last), bold: false, code: false });
  return out;
}

// 表格行拆格：去首尾竖线后按 | 切、逐格 trim（格数不齐＝如实按格呈现，不补不裁）
function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length > 0) {
      blocks.push({ kind: "para", parts: inline(para.join("\n")) });
      para = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (FENCE_RE.test(line)) {
      // 围栏代码块整吞至闭合；未闭合＝吞到文末照代码呈现（不拿尸体当正文）
      flushPara();
      const body: string[] = [];
      while (++i < lines.length && !FENCE_RE.test(lines[i])) body.push(lines[i]);
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    if (!line.trim()) {
      flushPara();
      continue;
    }
    const h = line.match(HEADING_RE);
    if (h) {
      flushPara();
      blocks.push({ kind: "heading", level: h[1].length, parts: inline(h[2]) });
      continue;
    }
    const ul = line.match(UL_RE);
    const ol = !ul ? line.match(OL_RE) : null;
    if (ul || ol) {
      // 相邻同类列表项并成一个列表（换行不相邻＝两个列表，如实）
      flushPara();
      const ordered = ol !== null;
      const items: Inline[][] = [inline((ul ?? ol)![1])];
      let j = i + 1;
      while (j < lines.length) {
        const m = ordered ? lines[j].match(OL_RE) : lines[j].match(UL_RE);
        if (!m) break;
        items.push(inline(m[1]));
        j++;
      }
      blocks.push({ kind: "list", ordered, items });
      i = j - 1;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && SEP_RE.test(lines[i + 1])) {
      flushPara();
      const head = splitRow(line);
      const rows: string[][] = [];
      let j = i + 2;
      while (j < lines.length && lines[j].trim() && lines[j].includes("|")) {
        rows.push(splitRow(lines[j]));
        j++;
      }
      blocks.push({ kind: "table", head, rows });
      i = j - 1;
      continue;
    }
    para.push(line);
  }
  flushPara();
  return blocks;
}

function Parts({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.code ? <code key={i}>{p.text}</code> : p.bold ? <b key={i}>{p.text}</b> : p.text,
      )}
    </>
  );
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseMarkdown(text);
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "heading": {
            // 气泡内标题层级压到 h4–h6（页面骨架已占 h1–h3，报告不该喧宾）
            const H = b.level <= 1 ? "h4" : b.level === 2 ? "h5" : "h6";
            return (
              <H key={i}>
                <Parts parts={b.parts} />
              </H>
            );
          }
          case "para":
            return (
              <p key={i}>
                <Parts parts={b.parts} />
              </p>
            );
          case "list": {
            // ul/ol 只差标签（双分支同形复制＝评审收 Duplicated Code）
            const L = b.ordered ? "ol" : "ul";
            return (
              <L key={i}>
                {b.items.map((it, j) => (
                  <li key={j}>
                    <Parts parts={it} />
                  </li>
                ))}
              </L>
            );
          }
          case "table":
            // 复用 .result-table 样式（与权威结果表同一张皮，零新 CSS）。
            // 格内走行内解析（票 09.5 实拍修 bug：报告爱用 **合计** 加粗表尾行，
            // 格不走 inline() 星号就裸奔——与正文同一子集，不加新记号）
            return (
              <table className="result-table" key={i}>
                <thead>
                  <tr>
                    {b.head.map((c, j) => (
                      <th key={j}>
                        <Parts parts={inline(c)} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {b.rows.map((r, j) => (
                    <tr key={j}>
                      {r.map((c, k) => (
                        <td key={k}>
                          <Parts parts={inline(c)} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          case "code":
            return (
              <pre className="sql" key={i}>
                <code>{b.text}</code>
              </pre>
            );
        }
      })}
    </>
  );
}
