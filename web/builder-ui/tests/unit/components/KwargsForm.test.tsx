import { describe, it, expect, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import type { Node } from 'reactflow'
import { KwargsForm } from '@/properties/KwargsForm'
import { useGraphStore } from '@/store/graphStore'

function seedNode(kwargs: Record<string, unknown>): Node {
  return {
    id: 'n1',
    type: 'tool',
    position: { x: 0, y: 0 },
    data: { ref: 'shell.notify', kwargs },
  }
}

function reset(kwargs: Record<string, unknown>) {
  useGraphStore.setState({
    graphId: 'test',
    graphKind: 'command',
    graphMeta: {
      schema_version: 1,
      name: 'test',
      kind: 'command',
      description: '',
      enabled: true,
      llm_visible: false,
      inputs: [],
    },
    nodes: [seedNode(kwargs)],
    edges: [],
    selectedNodeId: 'n1',
    dirty: false,
    llmVisible: false,
    runStatusByNodeId: {},
  })
}

describe('KwargsForm — F-M1 type coercion', () => {
  beforeEach(() => reset({ count: 7, label: 'hi', flag: true }))

  it('coerces numeric kwarg edits back to Number', () => {
    const { getAllByRole } = render(<KwargsForm nodeId="n1" kwargs={{ count: 7 }} />)
    const input = getAllByRole('textbox')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: '42' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['count']).toBe(42)
    expect(typeof data.kwargs['count']).toBe('number')
  })

  it('preserves string kwargs as strings', () => {
    const { getAllByRole } = render(<KwargsForm nodeId="n1" kwargs={{ label: 'hi' }} />)
    const input = getAllByRole('textbox')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: 'world' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['label']).toBe('world')
  })

  it('coerces boolean kwargs from the literal "true"/"false"', () => {
    const { getAllByRole } = render(<KwargsForm nodeId="n1" kwargs={{ flag: true }} />)
    const input = getAllByRole('textbox')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: 'false' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['flag']).toBe(false)
  })
})
