import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { aiApi } from '../api/ai'
import { FloatingAiWidget } from './FloatingAiWidget'

vi.mock('../api/ai', () => ({
  aiApi: {
    ask: vi.fn(),
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    deleteConversation: vi.fn(),
  },
}))

const ANSWER = ['**2 products** are low.', '| Product | Left |', '|---|---|', '| Gauze | 3 |'].join(
  '\n',
)

async function openWidget() {
  const user = userEvent.setup()
  render(<FloatingAiWidget />)
  await user.click(screen.getByRole('button', { name: 'Ask the assistant' }))
  return user
}

describe('FloatingAiWidget', () => {
  beforeEach(() => {
    vi.mocked(aiApi.ask).mockReset()
    vi.mocked(aiApi.ask).mockResolvedValue({
      answer: ANSWER,
      provider_used: 'OPENAI',
      fallback_used: false,
      conversation_id: 7,
    })
  })

  it('offers example questions that ask straight away', async () => {
    const user = await openWidget()

    await user.click(screen.getByRole('button', { name: "What's running low on stock?" }))

    expect(aiApi.ask).toHaveBeenCalledWith("What's running low on stock?", null, undefined)
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.getByText('Gauze')).toBeInTheDocument()
  })

  it('hides the examples once a conversation has started', async () => {
    const user = await openWidget()
    await user.click(screen.getByRole('button', { name: 'Who do we owe money to?' }))
    await screen.findByRole('table')

    expect(screen.queryByRole('button', { name: 'Who do we owe money to?' })).toBeNull()
  })

  it('sends a typed question and continues the same conversation', async () => {
    const user = await openWidget()

    await user.type(screen.getByPlaceholderText('Ask something…'), 'first question')
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await screen.findByRole('table')
    await user.type(screen.getByPlaceholderText('Ask something…'), 'and then?')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    await waitFor(() => expect(aiApi.ask).toHaveBeenCalledTimes(2))
    expect(vi.mocked(aiApi.ask).mock.calls[1]).toEqual(['and then?', 7, undefined])
  })

  it('ignores an empty question', async () => {
    const user = await openWidget()

    await user.click(screen.getByRole('button', { name: 'Ask' }))

    expect(aiApi.ask).not.toHaveBeenCalled()
  })
})
