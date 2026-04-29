import { describe, it, expect } from 'vitest'
import { getCategoryMeta, allCategories } from '@/lib/errorCategory'

describe('errorCategory', () => {
  it('returns meta for each category', () => {
    for (const cat of allCategories()) {
      const meta = getCategoryMeta(cat)
      expect(meta).not.toBeNull()
      expect(meta?.label).toBe(cat)
      expect(meta?.color).toMatch(/^text-/)
      expect(meta?.description).toBeTruthy()
    }
  })

  it('returns null for null', () => {
    expect(getCategoryMeta(null)).toBeNull()
  })
})
