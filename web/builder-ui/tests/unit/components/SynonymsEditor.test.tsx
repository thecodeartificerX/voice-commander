import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { SynonymsEditor } from '@/properties/SynonymsEditor'

function renderEditor(initial: string[] = []) {
  const onChange = vi.fn()
  let current = initial
  function rerender() {
    return render(
      <SynonymsEditor
        value={current}
        onChange={(next) => {
          current = next
          onChange(next)
        }}
      />,
    )
  }
  const utils = rerender()
  return {
    onChange,
    getInput: () =>
      utils.container.querySelector('input[type="text"]') as HTMLInputElement,
    rerender: () => {
      utils.unmount()
      return rerender()
    },
  }
}

describe('SynonymsEditor', () => {
  it('renders existing chips', () => {
    renderEditor(['paste', 'pact'])
    expect(screen.getByText('paste')).toBeInTheDocument()
    expect(screen.getByText('pact')).toBeInTheDocument()
  })

  it('commits draft on Enter', () => {
    const { onChange, getInput } = renderEditor([])
    const input = getInput()
    fireEvent.change(input, { target: { value: 'pact' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(['pact'])
  })

  it('commits draft on comma', () => {
    const { onChange, getInput } = renderEditor(['paste'])
    const input = getInput()
    fireEvent.change(input, { target: { value: 'pact' } })
    fireEvent.keyDown(input, { key: ',' })
    expect(onChange).toHaveBeenCalledWith(['paste', 'pact'])
  })

  it('trims whitespace on commit', () => {
    const { onChange, getInput } = renderEditor([])
    const input = getInput()
    fireEvent.change(input, { target: { value: '  pact  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(['pact'])
  })

  it('rejects empty / whitespace-only commits silently', () => {
    const { onChange, getInput } = renderEditor([])
    const input = getInput()
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('rejects case-insensitive duplicates silently', () => {
    const { onChange, getInput } = renderEditor(['Paste'])
    const input = getInput()
    fireEvent.change(input, { target: { value: 'paste' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('removes last chip on Backspace when draft is empty', () => {
    const { onChange, getInput } = renderEditor(['paste', 'pact'])
    const input = getInput()
    fireEvent.keyDown(input, { key: 'Backspace' })
    expect(onChange).toHaveBeenCalledWith(['paste'])
  })

  it('does NOT remove a chip on Backspace when draft has content', () => {
    const { onChange, getInput } = renderEditor(['paste'])
    const input = getInput()
    fireEvent.change(input, { target: { value: 'pa' } })
    fireEvent.keyDown(input, { key: 'Backspace' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('removes a specific chip when its ✕ is clicked', () => {
    const { onChange } = renderEditor(['paste', 'pact', 'paist'])
    const removeBtn = screen.getByLabelText('Remove phrase pact')
    fireEvent.click(removeBtn)
    expect(onChange).toHaveBeenCalledWith(['paste', 'paist'])
  })

  it('commits draft on blur', () => {
    const { onChange, getInput } = renderEditor([])
    const input = getInput()
    fireEvent.change(input, { target: { value: 'pact' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith(['pact'])
  })
})
