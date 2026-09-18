import * as echarts from 'echarts'
import type { LineViewSpec, BarViewSpec } from '../types'

export const AXIS_COLOR = '#8b8fa3'
export const SPLIT_COLOR = '#262a38'
export const COLORS = ['#f59e42', '#5b9bd5', '#56b881', '#e0556a', '#b392f0', '#e8d26a']

export function baseGrid() {
  return { left: 48, right: 16, top: 36, bottom: 28 }
}

export function lineOptions(spec: LineViewSpec): echarts.EChartsOption {
  return {
    color: COLORS,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#262a38', textStyle: { color: '#e8eaed', fontSize: 12 } },
    legend: { textStyle: { color: AXIS_COLOR, fontSize: 11 }, top: 0 },
    grid: baseGrid(),
    xAxis: { type: 'category', data: spec.x, axisLine: { lineStyle: { color: SPLIT_COLOR } }, axisLabel: { color: AXIS_COLOR, fontSize: 10 } },
    yAxis: {
      type: 'value', name: spec.y_name || '', nameTextStyle: { color: AXIS_COLOR },
      axisLabel: { color: AXIS_COLOR, fontSize: 10 },
      splitLine: { lineStyle: { color: SPLIT_COLOR } },
    },
    series: spec.series.map(s => ({
      // 科学趋势图不使用曲线插值，避免视觉平滑制造不存在的中间峰值/谷值。
      name: s.name, type: 'line' as const, data: s.data, smooth: false,
      connectNulls: !!spec.connect_nulls, symbolSize: 5,
      lineStyle: { width: 2 },
      ...(spec.mark_line ? {
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: '#e0556a', type: 'dashed', width: 1 },
          label: { color: '#e0556a', fontSize: 10 },
          data: [{ yAxis: spec.mark_line }],
        },
      } : {}),
      ...(spec.mark_area ? {
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(86,184,129,0.07)' },
          label: { color: AXIS_COLOR, fontSize: 9, position: 'insideTop' },
          data: [[{ yAxis: spec.mark_area[0], name: '参考区间' }, { yAxis: spec.mark_area[1] }]],
        },
      } : {}),
    })),
  }
}

export function barOptions(spec: BarViewSpec): echarts.EChartsOption {
  return {
    color: COLORS,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#1e2230', borderColor: '#262a38', textStyle: { color: '#e8eaed', fontSize: 12 } },
    legend: { textStyle: { color: AXIS_COLOR, fontSize: 11 }, top: 0 },
    grid: baseGrid(),
    xAxis: { type: 'category', data: spec.x, axisLine: { lineStyle: { color: SPLIT_COLOR } }, axisLabel: { color: AXIS_COLOR, fontSize: 10 } },
    yAxis: {
      type: 'value', name: spec.y_name || '', nameTextStyle: { color: AXIS_COLOR },
      axisLabel: { color: AXIS_COLOR, fontSize: 10 },
      splitLine: { lineStyle: { color: SPLIT_COLOR } },
    },
    series: spec.series.map(s => ({
      name: s.name, type: 'bar' as const, data: s.data,
      stack: spec.stack ? 'total' : undefined,
      barMaxWidth: 28,
    })),
  }
}
