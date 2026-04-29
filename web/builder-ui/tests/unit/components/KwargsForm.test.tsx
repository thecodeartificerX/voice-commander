import { describe, it, expect, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import type { Node } from 'reactflow'
import { KwargsForm } from '@/properties/KwargsForm'
import { useGraphStore } from '@/store/graphStore'
import { useSchemaStore } from '@/store/schemaStore'
import type { ToolSchema } from '@/types/graph'

function seedNode(kwargs: Record<string, unknown>, ref = 'shell.notify'): Node {
  return {
    id: 'n1',
    type: 'tool',
    position: { x: 0, y: 0 },
    data: { ref, kwargs },
  }
}

function reset(kwargs: Record<string, unknown>, ref = 'shell.notify') {
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
    nodes: [seedNode(kwargs, ref)],
    edges: [],
    selectedNodeId: 'n1',
    dirty: false,
    llmVisible: false,
    runStatusByNodeId: {},
  })
}

function seedSchemas(schemas: ToolSchema[]) {
  useSchemaStore.getState().setSchemas(schemas)
}

function clearSchemas() {
  useSchemaStore.setState({ schemasByRef: {} })
}

describe('KwargsForm — legacy fallback (no schema)', () => {
  beforeEach(() => {
    reset({ count: 7, label: 'hi', flag: true })
    clearSchemas()
  })

  it('coerces numeric kwarg edits back to Number', () => {
    const { getAllByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{ count: 7 }} nodeRef="shell.notify" />,
    )
    // No schema: int field renders as a number input — type=number elements
    // are reported by jsdom as role="spinbutton".
    const input = getAllByRole('spinbutton')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: '42' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['count']).toBe(42)
    expect(typeof data.kwargs['count']).toBe('number')
  })

  it('preserves string kwargs as strings', () => {
    const { getAllByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{ label: 'hi' }} nodeRef="shell.notify" />,
    )
    const input = getAllByRole('textbox')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: 'world' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['label']).toBe('world')
  })

  it('renders boolean checkbox in fallback mode and toggles to false', () => {
    const { getByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{ flag: true }} nodeRef="shell.notify" />,
    )
    const checkbox = getByRole('checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(true)
    fireEvent.click(checkbox)

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['flag']).toBe(false)
  })
})

describe('KwargsForm — schema-driven', () => {
  beforeEach(() => {
    clearSchemas()
    seedSchemas([
      {
        ref: 'pipeline.wait',
        name: 'wait',
        args: { ms: { type: 'int', required: true, description: 'milliseconds' } },
      },
      {
        ref: 'pipeline.toggle',
        name: 'toggle',
        args: { enabled: { type: 'bool', required: false } },
      },
      {
        ref: 'pipeline.note',
        name: 'note',
        args: { text: { type: 'str', required: true } },
      },
    ])
  })

  it('renders an input for a declared arg even with empty kwargs', () => {
    reset({}, 'pipeline.wait')
    const { getByText, getAllByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{}} nodeRef="pipeline.wait" />,
    )
    expect(getByText('ms')).toBeTruthy()
    // A number input is rendered (role=spinbutton).
    const inputs = getAllByRole('spinbutton')
    expect(inputs.length).toBe(1)
  })

  it('coerces typed "500" into the int field as a number, not a string', () => {
    reset({}, 'pipeline.wait')
    const { getAllByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{}} nodeRef="pipeline.wait" />,
    )
    const input = getAllByRole('spinbutton')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: '500' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['ms']).toBe(500)
    expect(typeof data.kwargs['ms']).toBe('number')
  })

  it('drops the kwarg when the int field is cleared', () => {
    reset({ ms: 200 }, 'pipeline.wait')
    const { getAllByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{ ms: 200 }} nodeRef="pipeline.wait" />,
    )
    const input = getAllByRole('spinbutton')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: '' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect('ms' in data.kwargs).toBe(false)
  })

  it('renders a checkbox for bool args and stores actual boolean', () => {
    reset({}, 'pipeline.toggle')
    const { getByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{}} nodeRef="pipeline.toggle" />,
    )
    const checkbox = getByRole('checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    fireEvent.click(checkbox)

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['enabled']).toBe(true)
    expect(typeof data.kwargs['enabled']).toBe('boolean')
  })

  it('preserves stray non-schema kwargs untouched when editing schema fields', () => {
    reset({ stray: 'leave-me-alone' }, 'pipeline.note')
    const { getAllByRole } = render(
      <KwargsForm nodeId="n1" kwargs={{ stray: 'leave-me-alone' }} nodeRef="pipeline.note" />,
    )
    const input = getAllByRole('textbox')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: 'hello' } })

    const node = useGraphStore.getState().nodes[0]
    const data = node!.data as { kwargs: Record<string, unknown> }
    expect(data.kwargs['text']).toBe('hello')
    expect(data.kwargs['stray']).toBe('leave-me-alone')
  })
})
