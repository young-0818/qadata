import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartSpec } from "./api";

// 票 04：图型/x 轴/系列全由后端规则纯函数（web/charts.py）判定，这里只管画——
// 不做任何形状自判逻辑（LLM 与前端都不参与图型决策，三节组装判例的延伸）。
//
// 配色＝已验证分类色板固定槽位序（相邻对 CVD 通过），按系列序号取色、绝不循环
// ——系列数 ≤6 由后端判定上限保证，正好落在前六个槽。文本一律墨色不用系列色；
// 柱间隙 2px、线 2px、点径 4（≥8px 判定），网格/坐标退居发丝级。
// 单系列不出图例（标题即名称），多系列图例＋值提示齐上——识别不靠颜色单传。
const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];
const AXIS_INK = "#898781";
const AXIS_LINE = "#c3c2b7";
const GRID_INK = "#e1e0d9";

interface Props {
  spec: ChartSpec;
  columns: string[];
  rows: unknown[][];
}

// 结果行 → recharts 数据对象：x 取轴列原值（时间/类别文本），系列按列下标取值——
// 列名可重复当不了键，下标寻址与后端判定同一契约。非数值（NULL 等）如实 null，
// recharts 断点跳过，不编造插值。
function toData(spec: ChartSpec, rows: unknown[][]): Record<string, unknown>[] {
  return rows.map((r) => {
    const d: Record<string, unknown> = {
      x: spec.x === null ? "" : (r[spec.x] ?? ""),
    };
    spec.series.forEach((ci, k) => {
      const v = r[ci];
      d[`s${k}`] = typeof v === "number" ? v : null;
    });
    return d;
  });
}

const axisTick = { fontSize: 11, fill: AXIS_INK };
const tooltipBox = {
  fontSize: 12,
  borderRadius: 8,
  border: `1px solid ${GRID_INK}`,
  boxShadow: "0 2px 8px rgba(11,11,11,0.08)",
};

export function ResultChart({ spec, columns, rows }: Props) {
  if (spec.type === "number") {
    // 大数卡：值原样呈现（数字纪律：UI 不格式化，格式化过的数就不是查询结果了）
    const ci = spec.series[0];
    const v = rows[0]?.[ci];
    return (
      <div className="number-card">
        <div className="number-value">{v === null || v === undefined ? "NULL" : String(v)}</div>
        <div className="number-label">{columns[ci]}</div>
      </div>
    );
  }
  const data = toData(spec, rows);
  const legend = spec.series.length > 1; // 单系列省图例：轴与标题已给出身份
  // 系列展示名＝列名，重复列名追加列下标消歧（后端契约自证"名字当不了键"，
  // 图例/tooltip 两个同名系列会让识别退回颜色单传——不允许）。
  const raw = spec.series.map((ci) => columns[ci] ?? `列${ci}`);
  const dup = new Map<string, number>();
  raw.forEach((l) => dup.set(l, (dup.get(l) ?? 0) + 1));
  const series = spec.series.map((ci, k) => ({
    key: `s${k}`,
    label: (dup.get(raw[k]) ?? 1) > 1 ? `${raw[k]}（${ci}）` : raw[k],
  }));

  const common = (
    <>
      <CartesianGrid stroke={GRID_INK} vertical={false} />
      <XAxis
        dataKey="x"
        tick={axisTick}
        stroke={AXIS_LINE}
        tickLine={false}
        axisLine={{ stroke: AXIS_LINE }}
        interval="preserveStartEnd"
        minTickGap={20}
      />
      <YAxis tick={axisTick} stroke={AXIS_LINE} tickLine={false} axisLine={false} width={56} />
      <Tooltip contentStyle={tooltipBox} labelStyle={{ color: "#1c1c1e" }} />
      {/* 图例文字穿墨色（色块由 recharts 自带）——系列色不单传文本 */}
      {legend && (
        <Legend
          wrapperStyle={{ fontSize: 12 }}
          formatter={(value) => <span style={{ color: "#52514e" }}>{value}</span>}
        />
      )}
    </>
  );

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height={230}>
        {spec.type === "line" ? (
          <LineChart data={data} margin={{ top: 16, right: 12, bottom: 4, left: 0 }}>
            {common}
            {series.map((s, k) => (
              <Line
                key={s.key}
                dataKey={s.key}
                name={s.label}
                type="linear"
                stroke={SERIES_COLORS[k]}
                strokeWidth={2}
                dot={{ r: 4, fill: SERIES_COLORS[k], strokeWidth: 0 }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        ) : (
          <BarChart data={data} margin={{ top: 16, right: 12, bottom: 4, left: 0 }} barGap={2}>
            {common}
            {series.map((s, k) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.label}
                fill={SERIES_COLORS[k]}
                radius={[4, 4, 0, 0]}
                maxBarSize={36}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
