import { useId } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Datum } from "./types";
import { compact, dateText, money } from "./lib";
import { ru } from "./ru";

export function Trend({
  days,
  compare = false,
}: {
  days: { date: string; net: Datum; previous?: Datum }[];
  compare?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  const data = days.map((d) => ({
    date: d.date,
    net: d.net == null ? null : Number(d.net),
    previous: d.previous == null ? null : Number(d.previous),
  }));
  return (
    <div className="trend" aria-label={ru.chart} role="img">
      <ResponsiveContainer
        width="100%"
        height="100%"
        minWidth={0}
        initialDimension={{ width: 650, height: 195 }}
      >
        <ComposedChart
          data={data}
          accessibilityLayer
          margin={{ top: 14, right: 20, bottom: 0, left: 0 }}
        >
          <defs>
            <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#dadadd" stopOpacity={0.28} />
              <stop offset="100%" stopColor="#a1a1a8" stopOpacity={0.008} />
            </linearGradient>
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="#2b2b2e"
            strokeDasharray="2 5"
          />
          <XAxis
            dataKey="date"
            tickFormatter={(d) => dateText(String(d))}
            tickLine={false}
            axisLine={false}
            minTickGap={34}
            tickMargin={12}
            height={32}
            interval="preserveStartEnd"
            tick={{ fontSize: 11, fill: "#aaaab0" }}
          />
          <YAxis
            tickFormatter={compact}
            tickLine={false}
            axisLine={false}
            width={61}
            tickMargin={9}
            tickCount={5}
            tick={{ fontSize: 11, fill: "#aaaab0" }}
          />
          <Tooltip
            labelFormatter={(v) => dateText(String(v), true)}
            formatter={(v, name) => [
              money(Number(v)),
              name === ru.previous ? ru.previous : ru.current,
            ]}
            itemStyle={{ color: "#dedee1" }}
            labelStyle={{ color: "#adadb2", marginBottom: 4 }}
            cursor={{ stroke: "#dedee1", strokeWidth: 1 }}
            contentStyle={{
              background: "#232326",
              boxShadow: "0 5px 22px #0006",
              color: "#f1f1f3",
              border: "1px solid #434349",
              borderRadius: 9,
              fontSize: 12,
            }}
          />
          <Area
            dataKey="net"
            name={ru.current}
            type="linear"
            stroke="#d8d8de"
            strokeWidth={1.65}
            fill={`url(#${id})`}
            isAnimationActive={false}
            dot={data.length === 1}
            activeDot={{
              r: 4,
              fill: "#f4f4f5",
              stroke: "#161619",
              strokeWidth: 2,
            }}
          />
          {compare && (
            <Line
              dataKey="previous"
              name={ru.previous}
              type="linear"
              stroke="#92929c"
              strokeDasharray="5 5"
              strokeWidth={1.35}
              dot={false}
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
