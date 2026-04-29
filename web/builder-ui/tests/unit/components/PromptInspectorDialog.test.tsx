import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import { PromptInspectorDialog } from '@/toolbar/PromptInspectorDialog'
import * as promptApi from '@/api/prompt'

const fakePromptData = {
  model_id: 'qwen2.5-7b-instruct',
  tools_count: 8,
  endpoint_url: 'http://127.0.0.1:1234',
  template_resolved: 'You are an assistant…',
}

vi.mock('@/api/prompt', () => ({
  apiGetPromptData: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(promptApi.apiGetPromptData).mockReset()
  vi.mocked(promptApi.apiGetPromptData).mockResolvedValue(fakePromptData)
})

describe('PromptInspectorDialog — F-M5 Radix migration', () => {
  it('renders title and description (a11y) when open', async () => {
    const { findByText, getByText } = render(
      <PromptInspectorDialog open={true} onOpenChange={() => {}} />,
    )
    expect(getByText('Prompt Inspector')).toBeInTheDocument()
    // Wait for async data load
    await findByText(/qwen2.5/)
  })

  it('renders nothing reachable when closed', () => {
    const { queryByText } = render(
      <PromptInspectorDialog open={false} onOpenChange={() => {}} />,
    )
    expect(queryByText('Prompt Inspector')).toBeNull()
  })

  it('invokes onOpenChange(false) via the Close button', async () => {
    const onOpenChange = vi.fn()
    const { getByLabelText } = render(
      <PromptInspectorDialog open={true} onOpenChange={onOpenChange} />,
    )
    const closeBtn = getByLabelText('Close prompt inspector')
    fireEvent.click(closeBtn)
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })

  it('calls apiGetPromptData when opened', async () => {
    render(<PromptInspectorDialog open={true} onOpenChange={() => {}} />)
    await waitFor(() => {
      expect(promptApi.apiGetPromptData).toHaveBeenCalled()
    })
  })
})
