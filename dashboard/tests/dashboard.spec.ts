import { test, expect, type Page } from '@playwright/test'

// ── Mock data ─────────────────────────────────────────────────────────────────

function makePoints(n: number) {
  return Array.from({ length: n }, (_, i) => {
    const t     = i / (n - 1)
    const value = 120 + t * 80            // 120 … 200
    const r     = Math.round(t * 255)
    const g     = Math.round((1 - t) * 200)
    const b     = 50
    return {
      lat:     47.690 + i * 0.00005,
      lon:     12.100 + i * 0.00010,
      alt:     501.5,
      rel_alt: 1.5,
      value,
      time:    `11:0${Math.floor(i / 60)}:${String(i % 60).padStart(2, '0')}`,
      r, g, b,
    }
  })
}

const POINTS = makePoints(40)

const MOCK_DATA = {
  meta: {
    label:        'NIR Mittelwert',
    point_count:  POINTS.length,
    v_min:        120,
    v_max:        200,
    v_mean:       160,
    v_std:        23.1,
    center_lat:   47.6901,
    center_lon:   12.1002,
    generated_at: '2026-05-05T10:00:00.000Z',
  },
  points: POINTS,
  path: POINTS.map(p => ({ lat: p.lat, lon: p.lon, alt: p.alt })),
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function withData(page: Page) {
  await page.route('/api/flight-data', route => route.fulfill({ json: MOCK_DATA }))
}

async function withError(page: Page, msg = 'Keine Flugdaten gefunden') {
  await page.route('/api/flight-data', route =>
    route.fulfill({ status: 404, json: { error: msg } })
  )
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Error state', () => {
  test('shows error message and CLI hint', async ({ page }) => {
    await withError(page)
    await page.goto('/')

    await expect(page.getByText('Keine Flugdaten gefunden')).toBeVisible()
    await expect(page.getByText('python src/mission.py --simulate')).toBeVisible()
  })

  test('shows custom error text from API', async ({ page }) => {
    await withError(page, 'Verbindungsfehler')
    await page.goto('/')

    await expect(page.getByText('Verbindungsfehler')).toBeVisible()
  })
})

test.describe('Header', () => {
  test.beforeEach(async ({ page }) => { await withData(page); await page.goto('/') })

  test('shows brand name', async ({ page }) => {
    await expect(page.getByText('Spraylogic')).toBeVisible()
    await expect(page.getByText('Sensor Dashboard')).toBeVisible()
  })

  test('shows correct point count', async ({ page }) => {
    // point count appears in header banner (may also appear in stats)
    await expect(page.getByRole('banner').getByText(String(MOCK_DATA.meta.point_count))).toBeVisible()
  })

  test('shows sensor label', async ({ page }) => {
    await expect(page.getByRole('banner').getByText(MOCK_DATA.meta.label)).toBeVisible()
  })

  test('shows live indicator dot', async ({ page }) => {
    const dot = page.locator('.animate-ping')
    await expect(dot).toBeVisible()
  })
})

test.describe('View toggle', () => {
  test.beforeEach(async ({ page }) => { await withData(page); await page.goto('/') })

  test('3D view button is active by default', async ({ page }) => {
    const btn = page.getByRole('button', { name: '3D Flugpfad' })
    await expect(btn).toBeVisible()
    // active button has relative positioning for its indicator overlay
    await expect(btn).toHaveCSS('position', 'relative')
  })

  test('heatmap button is visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Heatmap' })).toBeVisible()
  })

  test('clicking Heatmap switches view', async ({ page }) => {
    await page.getByRole('button', { name: 'Heatmap' }).click()
    // Legend with v_min / v_max should appear — use first() because values also appear in StatsPanel
    await expect(page.getByText(MOCK_DATA.meta.v_min.toFixed(1), { exact: true }).first()).toBeVisible()
    await expect(page.getByText(MOCK_DATA.meta.v_max.toFixed(1), { exact: true }).first()).toBeVisible()
  })

  test('switching back to 3D works', async ({ page }) => {
    await page.getByRole('button', { name: 'Heatmap' }).click()
    await page.getByRole('button', { name: '3D Flugpfad' }).click()
    // Three.js canvas should be present
    await expect(page.locator('canvas').first()).toBeVisible()
  })
})

test.describe('3D view', () => {
  test.beforeEach(async ({ page }) => { await withData(page); await page.goto('/') })

  test('renders a canvas element', async ({ page }) => {
    await expect(page.locator('canvas').first()).toBeVisible()
  })
})

test.describe('Heatmap view', () => {
  test.beforeEach(async ({ page }) => {
    await withData(page)
    await page.goto('/')
    await page.getByRole('button', { name: 'Heatmap' }).click()
  })

  test('shows jet legend with min and max', async ({ page }) => {
    await expect(page.getByText(MOCK_DATA.meta.v_min.toFixed(1), { exact: true }).first()).toBeVisible()
    await expect(page.getByText(MOCK_DATA.meta.v_max.toFixed(1), { exact: true }).first()).toBeVisible()
  })

  test('shows sensor label in legend', async ({ page }) => {
    // label appears in both header and legend
    const labels = page.getByText(MOCK_DATA.meta.label)
    await expect(labels.first()).toBeVisible()
  })

  test('Cesium container div is present', async ({ page }) => {
    // Cesium mounts into the container div
    const container = page.locator('.cesium-widget, canvas[data-engine]').or(
      page.locator('div.w-full.h-full').nth(1)
    )
    await expect(container.first()).toBeAttached()
  })
})

test.describe('Stats panel', () => {
  test.beforeEach(async ({ page }) => { await withData(page); await page.goto('/') })

  test('shows section headings', async ({ page }) => {
    await expect(page.getByText('Verteilung')).toBeVisible()
    await expect(page.getByText('Fluginfo')).toBeVisible()
    await expect(page.getByText('Hotspots')).toBeVisible()
    await expect(page.getByText('Coldspots')).toBeVisible()
    await expect(page.getByText('Farbskala')).toBeVisible()
  })

  test('shows correct mean value', async ({ page }) => {
    await expect(page.getByText(MOCK_DATA.meta.v_mean.toFixed(2))).toBeVisible()
  })

  test('shows correct point count', async ({ page }) => {
    await expect(page.locator('aside').getByText(String(MOCK_DATA.meta.point_count))).toBeVisible()
  })

  test('shows center coordinates', async ({ page }) => {
    await expect(page.getByText(MOCK_DATA.meta.center_lat.toFixed(5))).toBeVisible()
    await expect(page.getByText(MOCK_DATA.meta.center_lon.toFixed(5))).toBeVisible()
  })

  test('shows 6 hotspots', async ({ page }) => {
    // SpotRow uses rank numbers 1-6
    for (const rank of [1, 2, 3, 4, 5, 6]) {
      // rank numbers appear in the panel
      const rankEls = page.locator(`text="${rank}"`).filter({ hasText: `${rank}` })
      await expect(rankEls.first()).toBeAttached()
    }
  })

  test('recharts distribution chart is rendered', async ({ page }) => {
    // Recharts renders an SVG
    const svg = page.locator('aside svg').first()
    await expect(svg).toBeVisible()
  })
})
