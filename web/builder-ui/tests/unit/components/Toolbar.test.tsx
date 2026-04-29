import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { Toolbar } from '@/toolbar/Toolbar'
import { useGraphStore } from '@/store/graphStore'
import { useUiStore } from '@/store/uiStore'

// PromptInspectorDialog hits an API on mount; mock it out.
vi.mock('@/toolbar/PromptInspectorDialog', () => ({
  PromptInspectorDialog: () => null,
}))

function resetUiStore() {
  useUiStore.setState({ promptInspectorOpen: false })
}

describe('Toolbar — back button + draft name', () => {
  beforeEach(() => {
    resetUiStore()
    useGraphStore.setState({
      graphId: null,
      graphKind: null,
      graphMeta: null,
      nodes: [],
      edges: [],
      selectedNodeId: null,
      dirty: false,
      llmVisible: false,
      draft: false,
      runStatusByNodeId: {},
    })
  })

  it('renders a Back link to /page/commands when graphKind is command', () => {
    useGraphStore.setState({
      graphId: 'foo',
      graphKind: 'command',
      graphMeta: {
        schema_version: 1,
        name: 'foo',
        kind: 'command',
        description: '',
        enabled: true,
        llm_visible: true,
        inputs: [],
      },
    })
    const { getByLabelText } = render(<Toolbar />)
    const link = getByLabelText('Back') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/page/commands')
  })

  it('renders a Back link to /page/workflows when graphKind is workflow', () => {
    useGraphStore.setState({
      graphId: 'foo',
      graphKind: 'workflow',
      graphMeta: {
        schema_version: 1,
        name: 'foo',
        kind: 'workflow',
        description: '',
        enabled: true,
        llm_visible: true,
        inputs: [],
      },
    })
    const { getByLabelText } = render(<Toolbar />)
    const link = getByLabelText('Back') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/page/workflows')
  })

  it('shows an editable name input in draft mode', () => {
    useGraphStore.getState().initBlank('command', 'untitled_command_abcd')
    const { getByLabelText } = render(<Toolbar />)
    const input = getByLabelText('Graph name') as HTMLInputElement
    expect(input.value).toBe('untitled_command_abcd')
    fireEvent.change(input, { target: { value: 'my_new_command' } })
    expect(useGraphStore.getState().graphId).toBe('my_new_command')
    expect(useGraphStore.getState().graphMeta?.name).toBe('my_new_command')
  })

  it('shows a read-only title (no input) on saved graphs', () => {
    useGraphStore.setState({
      graphId: 'show_commands',
      graphKind: 'command',
      graphMeta: {
        schema_version: 1,
        name: 'show_commands',
        kind: 'command',
        description: '',
        enabled: true,
        llm_visible: true,
        inputs: [],
      },
      draft: false,
      dirty: false,
    })
    const { queryByLabelText, getByText } = render(<Toolbar />)
    expect(queryByLabelText('Graph name')).toBeNull()
    expect(getByText('show_commands')).toBeInTheDocument()
  })

  it('confirms before navigating away when dirty', () => {
    useGraphStore.setState({
      graphId: 'foo',
      graphKind: 'command',
      graphMeta: {
        schema_version: 1,
        name: 'foo',
        kind: 'command',
        description: '',
        enabled: true,
        llm_visible: true,
        inputs: [],
      },
      dirty: true,
      draft: false,
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { getByLabelText } = render(<Toolbar />)
    const link = getByLabelText('Back') as HTMLAnchorElement
    const event = new MouseEvent('click', { bubbles: true, cancelable: true })
    const prevented = !link.dispatchEvent(event)
    expect(confirmSpy).toHaveBeenCalled()
    expect(prevented).toBe(true)
    confirmSpy.mockRestore()
  })
})
