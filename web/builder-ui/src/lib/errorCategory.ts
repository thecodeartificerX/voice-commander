import type { ErrorCategory } from '@/types/run'

interface CategoryMeta {
  label: string
  color: string
  bgColor: string
  description: string
}

const META: Record<ErrorCategory, CategoryMeta> = {
  program: {
    label: 'program',
    color: 'text-red-400',
    bgColor: 'bg-red-950/50',
    description: 'Tool raised an unhandled exception — fix the tool implementation',
  },
  wiring: {
    label: 'wiring',
    color: 'text-orange-400',
    bgColor: 'bg-orange-950/50',
    description: 'Graph structure error — missing kwarg, dangling port, bad branch',
  },
  infra: {
    label: 'infra',
    color: 'text-gray-400',
    bgColor: 'bg-gray-800/50',
    description: 'Service unreachable — audio device gone, SQLite locked',
  },
}

export function getCategoryMeta(cat: ErrorCategory | null): CategoryMeta | null {
  if (!cat) return null
  return META[cat] ?? null
}

export function allCategories(): ErrorCategory[] {
  return ['program', 'wiring', 'infra']
}
