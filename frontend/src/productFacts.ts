export function factLabel(key: string): string {
  return ({ material: '材质', waterproof: '防水性' } as Record<string, string>)[key] || key
}

export function factValue(key: string, value: unknown): string {
  if (value === 'needs_confirmation' || value === 'unknown') return '未知，待确认'
  if (value === 'not_claimed') return '不作卖点'
  if (value === 'confirmed') return key === 'waterproof' ? '已确认防水' : '已确认'
  return value == null || value === '' ? '未填写' : String(value)
}
