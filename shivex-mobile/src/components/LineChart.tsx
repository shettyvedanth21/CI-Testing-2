import { useMemo, useState } from "react";
import { LayoutChangeEvent, StyleSheet, View } from "react-native";
import Svg, { Circle, Line as SvgLine, Path } from "react-native-svg";

import { colors } from "../constants/colors";

type Point = {
  x: number;
  y: number;
};

type LineChartProps = {
  data: number[];
  height?: number;
  strokeColor?: string;
  strokeWidth?: number;
  fillColor?: string;
};

function buildPath(points: Point[]) {
  if (points.length === 0) {
    return "";
  }

  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
}

export function LineChart({
  data,
  height = 180,
  strokeColor = colors.primary,
  strokeWidth = 2,
  fillColor = "transparent",
}: LineChartProps) {
  const [width, setWidth] = useState(0);

  const points = useMemo<Point[]>(() => {
    if (width <= 0 || data.length === 0) {
      return [];
    }

    const valid = data.map((value) => (Number.isFinite(value) ? value : 0));
    const max = Math.max(...valid);
    const min = Math.min(...valid);
    const padding = 8;
    const chartWidth = Math.max(width - padding * 2, 1);
    const chartHeight = Math.max(height - padding * 2, 1);
    const range = max - min || 1;

    return valid.map((value, index) => {
      const x = padding + (index / Math.max(valid.length - 1, 1)) * chartWidth;
      const y = padding + (1 - (value - min) / range) * chartHeight;
      return { x, y };
    });
  }, [data, height, width]);

  function onLayout(event: LayoutChangeEvent) {
    setWidth(event.nativeEvent.layout.width);
  }

  const path = buildPath(points);

  return (
    <View onLayout={onLayout} style={[styles.container, { height }]}>
      {width > 0 && points.length > 0 ? (
        <Svg width={width} height={height}>
          <Path d={path} fill="none" stroke={strokeColor} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
          {fillColor !== "transparent" ? (
            <Path
              d={`${path} L ${points[points.length - 1]?.x ?? 0} ${height - 8} L ${points[0]?.x ?? 0} ${height - 8} Z`}
              fill={fillColor}
              stroke="none"
            />
          ) : null}
          <SvgLine x1={8} y1={height - 8} x2={width - 8} y2={height - 8} stroke={colors.border} strokeWidth={1} opacity={0.5} />
          {points.map((point, index) => (
            <Circle key={index} cx={point.x} cy={point.y} r={2.5} fill={strokeColor} />
          ))}
        </Svg>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: "100%",
  },
});
