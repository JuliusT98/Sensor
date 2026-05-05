import { NextResponse } from 'next/server'
import { readFileSync } from 'fs'
import { join } from 'path'

export async function GET() {
  try {
    const filePath = join(process.cwd(), '..', 'output', 'flight_data.json')
    const data = JSON.parse(readFileSync(filePath, 'utf-8'))
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(
      { error: 'Keine Flugdaten gefunden. Starte: python src/mission.py --simulate' },
      { status: 404 }
    )
  }
}
