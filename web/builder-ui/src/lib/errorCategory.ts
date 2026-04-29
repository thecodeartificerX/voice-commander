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
  llm: {
    label: 'llm',
    color: 'text-purple-400',
    bgColor: 'bg-purple-950/50',
    description: 'Router returned malformed plan or unknown tool — check prompt template',
  },
  infra: {
    label: 'infra',
    color: 'text-gray-400',
    bgColor: 'bg-gray-800/50',
    description: 'Service unreachable — LM Studio down, audio device gone, SQLite locked',
  },
}

export function getCategoryMeta(cat: ErrorCategory | null): CategoryMeta | null {
  if (!cat) return null
  return META[cat] ?? null
}

export function allCategories(): ErrorCategory[] {
  return ['program', 'wiring', 'llm', 'infra']
}
