'use strict'

window.initMonthlyReport = function () {
  const dataNode = document.querySelector('#monthly-report-data')
  if (!dataNode) return
  if (typeof Highcharts === 'undefined') {
    document.querySelectorAll('.monthly-report .chart-300, .monthly-report .chart-420')
      .forEach((chart) => {
        chart.classList.add('chart-fallback')
        chart.textContent = '图表组件加载失败，请检查网络后刷新页面。'
      })
    return
  }

  const data = JSON.parse(dataNode.textContent)
  const primary = '#0052CC'
  const success = '#36b37e'
  const purple = '#5243AA'
  const warning = '#FF991F'
  const errorColor = '#de350b'
  const textColor = '#44546f'
  const textMuted = '#8993a4'
  const gridColor = '#ebecf0'

  Highcharts.chart('chart-daily', {
    chart: { type: 'area', backgroundColor: 'transparent' },
    title: { text: '每日 Credits 消耗趋势', style: { color: '#172b4d', fontSize: '14px', fontWeight: '600' } },
    xAxis: {
      categories: data.daily.categories,
      labels: { style: { color: textColor, fontSize: '10px' }, step: 2 },
      lineColor: gridColor,
    },
    yAxis: {
      title: { text: 'Credits', style: { color: textMuted } },
      labels: { style: { color: textColor } },
      gridLineColor: gridColor,
    },
    tooltip: { shared: true },
    plotOptions: {
      area: { stacking: 'normal', fillOpacity: 0.15, marker: { enabled: false }, lineWidth: 2 },
    },
    colors: [primary, success, purple, warning],
    series: data.daily.series,
    legend: { itemStyle: { color: textColor } },
    credits: { enabled: false },
  })

  Highcharts.chart('chart-model', {
    chart: { type: 'pie', backgroundColor: 'transparent' },
    title: { text: '模型使用分布', style: { color: '#172b4d', fontSize: '14px', fontWeight: '600' } },
    subtitle: data.models.length ? undefined : { text: '暂无模型消息数据' },
    tooltip: { pointFormat: '<b>{point.percentage:.1f}%</b> ({point.y:,.0f})' },
    plotOptions: {
      pie: {
        innerSize: '45%',
        dataLabels: {
          format: '{point.name}<br/>{point.percentage:.0f}%',
          style: { color: textColor, fontSize: '11px' },
          distance: 12,
        },
      },
    },
    colors: [primary, purple, success, warning, errorColor],
    series: [{ name: 'Messages', data: data.models }],
    credits: { enabled: false },
  })

  Highcharts.chart('chart-ide-cli', {
    chart: { type: 'pie', backgroundColor: 'transparent' },
    title: { text: 'IDE vs CLI Credits', style: { color: '#172b4d', fontSize: '14px', fontWeight: '600' } },
    tooltip: { pointFormat: '<b>{point.percentage:.1f}%</b><br/>{point.y:,.1f} credits' },
    plotOptions: {
      pie: {
        innerSize: '55%',
        dataLabels: {
          format: '{point.name}: {point.percentage:.1f}%',
          style: { color: textColor, fontSize: '12px' },
        },
      },
    },
    colors: [primary, success, purple, warning],
    series: [{ name: 'Credits', data: data.clients }],
    credits: { enabled: false },
  })

  Highcharts.chart('chart-user-quota', {
    chart: { type: 'bar', backgroundColor: 'transparent' },
    title: { text: '用户配额利用率', style: { color: '#172b4d', fontSize: '14px', fontWeight: '600' } },
    subtitle: { text: '🔴 已超额 (>100%) | 🟠 >80% | 🔵 正常', style: { color: textMuted, fontSize: '10px' } },
    xAxis: {
      categories: data.quota.categories,
      labels: { style: { color: textColor, fontSize: '10px' } },
      lineColor: gridColor,
    },
    yAxis: {
      title: { text: '利用率 %', style: { color: textMuted } },
      labels: { style: { color: textColor } },
      gridLineColor: gridColor,
      plotLines: [{
        value: 100, color: errorColor, dashStyle: 'Dash', width: 2,
        label: { text: '100% = 基础配额', style: { color: errorColor, fontSize: '10px' } },
      }],
    },
    tooltip: { pointFormat: '利用率: <b>{point.y}%</b>' },
    plotOptions: {
      bar: {
        borderRadius: 3,
        borderWidth: 0,
        dataLabels: {
          enabled: true, format: '{point.y}%', style: { color: textColor, fontSize: '9px' },
        },
      },
    },
    series: [{ name: '利用率', data: data.quota.data }],
    legend: { enabled: false },
    credits: { enabled: false },
  })

  const hasOverage = data.overage.credits.length > 0
  Highcharts.chart('chart-overage', {
    chart: { type: 'column', backgroundColor: 'transparent' },
    title: { text: '超额消耗 & 费用', style: { color: '#172b4d', fontSize: '14px', fontWeight: '600' } },
    subtitle: {
      text: hasOverage
        ? `超出基础配额部分 | 按 $${data.overage.price.toFixed(2)}/credit 估算`
        : '本月暂无超额消耗',
      style: { color: textMuted, fontSize: '10px' },
    },
    xAxis: {
      categories: data.overage.categories,
      labels: { style: { color: textColor, fontSize: '10px' }, useHTML: true },
      lineColor: gridColor,
    },
    yAxis: [
      {
        title: { text: '超额 Credits', style: { color: textMuted } },
        labels: { style: { color: textColor } },
        gridLineColor: gridColor,
      },
      {
        title: { text: '估算费用 ($)', style: { color: textMuted } },
        labels: { style: { color: textColor } },
        opposite: true,
        gridLineColor: 'transparent',
      },
    ],
    tooltip: { shared: true },
    series: [
      {
        name: '超额 Credits', type: 'column', data: data.overage.credits,
        color: errorColor, borderRadius: 4,
      },
      {
        name: '估算超额费用 ($)', type: 'spline', yAxis: 1, data: data.overage.costs,
        color: warning, marker: { radius: 5 }, lineWidth: 2,
      },
    ],
    credits: { enabled: false },
  })

  const table = document.querySelector('.monthly-report table')
  if (!table) return
  const headers = table.querySelectorAll('th')
  const tbody = table.querySelector('tbody')
  headers.forEach((header, index) => {
    header.classList.add('sortable')
    header.dataset.sortDir = ''
    header.addEventListener('click', () => {
      const direction = header.dataset.sortDir === 'asc' ? 'desc' : 'asc'
      headers.forEach((item) => {
        item.dataset.sortDir = ''
        item.classList.remove('asc', 'desc')
      })
      header.dataset.sortDir = direction
      header.classList.add(direction)
      const rows = [...tbody.querySelectorAll('tr')]
      rows.sort((rowA, rowB) => {
        const cellA = rowA.querySelectorAll('td')[index]
        const cellB = rowB.querySelectorAll('td')[index]
        if (!cellA || !cellB) return 0
        const valueA = cellA.textContent.trim().replace(/[$,%,]/g, '')
        const valueB = cellB.textContent.trim().replace(/[$,%,]/g, '')
        const numberA = Number.parseFloat(valueA)
        const numberB = Number.parseFloat(valueB)
        if (!Number.isNaN(numberA) && !Number.isNaN(numberB)) {
          return direction === 'asc' ? numberA - numberB : numberB - numberA
        }
        return direction === 'asc'
          ? valueA.localeCompare(valueB)
          : valueB.localeCompare(valueA)
      })
      rows.forEach((row) => tbody.appendChild(row))
    })
  })
}
