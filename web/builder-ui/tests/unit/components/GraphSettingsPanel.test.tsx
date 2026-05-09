import { describe, it, expect, beforeEach } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { GraphSettingsPanel } from '@/properties/GraphSettingsPanel'
import { useGraphStore } from '@/store/graphStore'

function seed(meta: Partial<NonNullable<ReturnType<typeof useGraphStore.getState>['graphMeta']>> = {}) {
  useGraphStore.setState({
    graphId: 'paste',
    graphKind: 'command',
    graphMeta: {
      schema_version: 1,
      name: 'paste',
      kind: 'command',
      description: '',
      synonyms: [],
      enabled: true,
      llm_visible: true,
      inputs: [],
      ...meta,
    },
    nodes: [],
    edges: [],
    selectedNodeId: null,
    dirty: false,
    llmVisible: true,
    runStatusByNodeId: {},
  })
}

describe('GraphSettingsPanel', () => {
  beforeEach(() => seed())

  it('renders graph kind and name in the subhead', () => {
    render(<GraphSettingsPanel />)
    expect(screen.getByText(/command · paste/)).toBeInTheDocument()
  })

  it('renders existing description in the textarea', () => {
    seed({ description: 'sends ctrl+v' })
    render(<GraphSettingsPanel />)
    const textarea = screen.getByLabelText('Description') as HTMLTextAreaElement
    expect(textarea.value).toBe('sends ctrl+v')
  })

  it('updates description and flips dirty', () => {
    render(<GraphSettingsPanel />)
    const textarea = screen.getByLabelText('Description') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'pastes from clipboard' } })
    const s = useGraphStore.getState()
    expect(s.graphMeta?.description).toBe('pastes from clipboard')
    expect(s.dirty).toBe(true)
  })

  it('renders existing synonyms as chips', () => {
    seed({ synonyms: ['pact', 'paste that'] })
    render(<GraphSettingsPanel />)
    expect(screen.getByText('pact')).toBeInTheDocument()
    expect(screen.getByText('paste that')).toBeInTheDocument()
  })

  it('committing a phrase updates the store and flips dirty', () => {
    render(<GraphSettingsPanel />)
    const input = screen.getByLabelText('Synonyms') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'pact' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    const s = useGraphStore.getState()
    expect(s.graphMeta?.synonyms).toEqual(['pact'])
    expect(s.dirty).toBe(true)
  })

  it('handles missing synonyms (legacy graph) by rendering empty editor', () => {
    seed({ synonyms: undefined })
    render(<GraphSettingsPanel />)
    // Editor input should be present and empty; no chip elements
    const input = screen.getByLabelText('Synonyms') as HTMLInputElement
    expect(input.value).toBe('')
  })

  it('renders the primitives editor link', () => {
    render(<GraphSettingsPanel />)
    const link = screen.getByText(/Edit primitive phrases/i).closest('a')
    expect(link).toHaveAttribute('href', '/page/primitives')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('renders a friendly fallback when no graph is loaded', () => {
    useGraphStore.setState({
      graphId: null,
      graphKind: null,
      graphMeta: null,
      nodes: [],
      edges: [],
      selectedNodeId: null,
      dirty: false,
      llmVisible: false,
      runStatusByNodeId: {},
    })
    render(<GraphSettingsPanel />)
    expect(screen.getByText('No graph loaded.')).toBeInTheDocument()
  })
})
