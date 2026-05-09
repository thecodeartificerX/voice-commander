import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { KeyRecorder } from '@/properties/KeyRecorder'

describe('KeyRecorder', () => {
  let onChange: ReturnType<typeof vi.fn>

  beforeEach(() => {
    onChange = vi.fn()
  })

  // 1. Renders (unset) placeholder when value is empty and not recording.
  it('renders (unset) placeholder when value is empty and not recording', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    expect(screen.getByRole('button', { name: /\(unset\)/ })).toBeTruthy()
  })

  // 2. Renders current value on the button when non-empty and not recording.
  it('renders current value on the record button when value is non-empty', () => {
    render(<KeyRecorder value="ctrl+c" onChange={onChange} />)
    expect(screen.getByRole('button', { name: 'ctrl+c' })).toBeTruthy()
  })

  // 3. Click record button → enters recording state.
  it('click record button enters recording state', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    const btn = screen.getByRole('button', { name: /\(unset\)/ })
    fireEvent.click(btn)
    expect(screen.getByText('recording… press combo')).toBeTruthy()
  })

  // 4. Pressing a single non-modifier key while recording calls onChange and exits.
  it('pressing a single non-modifier key calls onChange with lowercase key and exits recording', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))
    // Confirm we're recording
    expect(screen.getByText('recording… press combo')).toBeTruthy()

    fireEvent.keyDown(window, { key: 'c', code: 'KeyC', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).toHaveBeenCalledWith('c')
    // Should exit recording state
    expect(screen.queryByText('recording… press combo')).toBeNull()
  })

  // 5. Modifier + key calls onChange with the combo string.
  it('pressing Ctrl+C while recording calls onChange with ctrl+c', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))

    fireEvent.keyDown(window, { key: 'c', code: 'KeyC', ctrlKey: true, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).toHaveBeenCalledWith('ctrl+c')
  })

  // 6. Pressing Esc with no modifiers cancels recording without calling onChange.
  it('pressing Esc with no modifiers cancels recording without calling onChange', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))
    expect(screen.getByText('recording… press combo')).toBeTruthy()

    fireEvent.keyDown(window, { key: 'Escape', code: 'Escape', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText('recording… press combo')).toBeNull()
  })

  // 7. Click ✏ toggle → flips into type-mode → renders an <input> with placeholder containing "win+l".
  it('clicking the type toggle flips into type-mode and shows an input with win+l placeholder', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    const typeToggle = screen.getByRole('button', { name: /✏ type/ })
    fireEvent.click(typeToggle)

    const input = screen.getByRole('textbox') as HTMLInputElement
    expect(input).toBeTruthy()
    expect(input.placeholder).toContain('win+l')
  })

  // 8. In type-mode, typing into the input calls onChange with the typed string.
  it('typing in type-mode calls onChange with the current input value', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /✏ type/ }))

    const input = screen.getByRole('textbox') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'win+l' } })

    expect(onChange).toHaveBeenCalledWith('win+l')
  })

  // 9. Click ✏ toggle again → flips back to record mode.
  it('clicking the record toggle flips back to record mode', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    // Enter type mode
    fireEvent.click(screen.getByRole('button', { name: /✏ type/ }))
    expect(screen.getByRole('textbox')).toBeTruthy()

    // Exit type mode via "🎙 record" button
    fireEvent.click(screen.getByRole('button', { name: /🎙 record/ }))
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByRole('button', { name: /\(unset\)/ })).toBeTruthy()
  })

  // 10. Click ✕ clear button calls onChange("").
  it('clicking clear button calls onChange with empty string', () => {
    render(<KeyRecorder value="ctrl+c" onChange={onChange} />)
    const clearBtn = screen.getByRole('button', { name: /✕ clear/ })
    fireEvent.click(clearBtn)
    expect(onChange).toHaveBeenCalledWith('')
  })

  // 11. Mousedown outside the container while recording exits recording without calling onChange.
  it('mousedown outside container while recording exits recording without calling onChange', () => {
    const { container } = render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))
    expect(screen.getByText('recording… press combo')).toBeTruthy()

    // Dispatch a mousedown on an element outside the component container
    const outsideEl = document.createElement('div')
    document.body.appendChild(outsideEl)
    fireEvent.mouseDown(outsideEl)
    document.body.removeChild(outsideEl)

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText('recording… press combo')).toBeNull()
  })

  // 12. Function keys produce lowercase f1-f12.
  it('F1 in recording mode produces f1', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))

    fireEvent.keyDown(window, { key: 'F1', code: 'F1', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).toHaveBeenCalledWith('f1')
  })

  it('F12 in recording mode produces f12', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))

    fireEvent.keyDown(window, { key: 'F12', code: 'F12', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).toHaveBeenCalledWith('f12')
  })

  // 13. Arrow keys produce up/down/left/right.
  it.each([
    ['ArrowUp', 'up'],
    ['ArrowDown', 'down'],
    ['ArrowLeft', 'left'],
    ['ArrowRight', 'right'],
  ])('arrow key %s produces %s', (key, expected) => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))

    fireEvent.keyDown(window, { key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).toHaveBeenCalledWith(expected)
  })

  // 14. Special-key mapping.
  it.each([
    ['Enter', 'enter'],
    ['Tab', 'tab'],
    ['Backspace', 'backspace'],
    [' ', 'space'],
  ])('special key %s maps to %s', (key, expected) => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))

    fireEvent.keyDown(window, { key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).toHaveBeenCalledWith(expected)
  })

  it('Escape with a modifier (e.g. ctrl) maps to ctrl+esc instead of cancelling', () => {
    render(<KeyRecorder value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /\(unset\)/ }))

    fireEvent.keyDown(window, { key: 'Escape', code: 'Escape', ctrlKey: true, altKey: false, shiftKey: false, metaKey: false })

    expect(onChange).toHaveBeenCalledWith('ctrl+esc')
  })
})
