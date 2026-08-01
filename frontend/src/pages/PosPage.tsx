import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { customersApi, productsApi, salesApi } from '../api/domain'
import { useCurrencyFormatter } from '../lib/currency'
import type { CustomerOut, PaymentMethod, ProductOut, SaleOut } from '../types/api'
import { ApiError } from '../api/client'

interface CartLine {
  product: ProductOut
  quantity: number
}

export function PosPage() {
  const formatCurrency = useCurrencyFormatter()
  const [query, setQuery] = useState('')
  const searchInputRef = useRef<HTMLInputElement>(null)
  const [results, setResults] = useState<ProductOut[]>([])
  const [searching, setSearching] = useState(false)
  const [cart, setCart] = useState<CartLine[]>([])
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('CASH')
  const [discount, setDiscount] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<SaleOut | null>(null)
  const [checkingOut, setCheckingOut] = useState(false)
  const [customerPhone, setCustomerPhone] = useState('')
  const [attachedCustomer, setAttachedCustomer] = useState<CustomerOut | null>(null)
  const [customerLookupError, setCustomerLookupError] = useState<string | null>(null)
  const [lookingUpCustomer, setLookingUpCustomer] = useState(false)

  const estimatedSubtotal = cart.reduce(
    (sum, line) => sum + line.product.default_selling_price * line.quantity,
    0,
  )
  const estimatedTotal = Math.max(0, estimatedSubtotal - discount)

  // Live search: results update automatically as the cashier types, no
  // button press or Enter required. Debounced by 300ms so a fast typist
  // (or a barcode scanner, which types the whole code near-instantly)
  // doesn't fire a request per keystroke -- one request lands shortly
  // after typing pauses. With nothing typed, this shows every in-stock
  // product instead of an empty screen -- already sorted most-stocked
  // first by the backend, so browsing and searching share one list
  // and one click-to-add behavior, never two different code paths.
  async function searchProducts(text: string): Promise<ProductOut[]> {
    if (!text) {
      const all = await productsApi.list('')
      return all.filter((p) => p.total_qty_available > 0)
    }
    const nameResults = await productsApi.list(text)
    if (nameResults.length > 0) return nameResults
    // Nothing matched by name -- try an exact barcode match before
    // giving up, since a scanned code often has nothing in common
    // with the product's name text.
    try {
      const byBarcode = await productsApi.getByBarcode(text)
      return [byBarcode]
    } catch {
      return nameResults // genuinely no match either way
    }
  }

  useEffect(() => {
    setSearching(true)
    setError(null)
    const trimmed = query.trim()
    const timer = setTimeout(() => {
      searchProducts(trimmed)
        .then(setResults)
        .catch((err: unknown) => {
          setError(err instanceof ApiError ? err.message : 'Search failed.')
        })
        .finally(() => setSearching(false))
    }, 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  useEffect(() => {
    const SCANNER_MAX_GAP_MS = 50
    const MIN_SCAN_LENGTH = 3
    let buffer = ''
    let lastKeyTime = 0
    let sawSlowGap = false

    function handleGlobalKeyDown(e: globalThis.KeyboardEvent) {
      const active = document.activeElement
      const activeIsRealInput =
        active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement
      if (activeIsRealInput) {
        // Someone deliberately focused a real field -- their typing
        // is never touched, full stop, regardless of its speed.
        buffer = ''
        sawSlowGap = false
        return
      }

      const now = Date.now()
      const gap = now - lastKeyTime
      lastKeyTime = now

      if (e.key === 'Enter') {
        if (buffer.length >= MIN_SCAN_LENGTH && !sawSlowGap) {
          e.preventDefault()
          void handleEnterToAdd(buffer)
        }
        buffer = ''
        sawSlowGap = false
        return
      }

      if (e.key.length !== 1) return // ignore Shift, Tab, arrow keys, etc.

      if (buffer && gap > SCANNER_MAX_GAP_MS) {
        sawSlowGap = true // too slow anywhere in the sequence to be a real scan
      }
      buffer += e.key
    }

    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => window.removeEventListener('keydown', handleGlobalKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function addToCart(product: ProductOut) {
    setCart((prev) => {
      const existing = prev.find((l) => l.product.id === product.id)
      if (existing) {
        return prev.map((l) =>
          l.product.id === product.id ? { ...l, quantity: l.quantity + 1 } : l,
        )
      }
      return [...prev, { product, quantity: 1 }]
    })
  }

  // A real barcode scan sends its digits and an Enter keystroke
  // within milliseconds -- far faster than the 300ms debounce above
  // ever gets a chance to fire. Acting on `results` here would mean
  // acting on whatever was on screen *before* the scan even started,
  // not the scanned item. Searching directly, right here, guarantees
  // this always acts on the real, current answer regardless of
  // typing or scanning speed. Only adds when there's exactly one
  // match -- with several results, this does nothing rather than
  // risk adding the wrong one, so speed here never costs accuracy.
  async function handleEnterToAdd(text: string) {
    const trimmed = text.trim()
    if (!trimmed) return
    try {
      const matches = await searchProducts(trimmed)
      setResults(matches)
      if (matches.length !== 1) return
      const only = matches[0]
      if (only.total_qty_available <= 0) return
      addToCart(only)
      setQuery('')
      searchInputRef.current?.focus()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Search failed.')
    }
  }

  function handleSearchKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== 'Enter') return
    e.preventDefault()
    void handleEnterToAdd(query)
  }

  function updateQuantity(productId: number, quantity: number) {
    if (quantity <= 0) {
      setCart((prev) => prev.filter((l) => l.product.id !== productId))
      return
    }
    setCart((prev) => prev.map((l) => (l.product.id === productId ? { ...l, quantity } : l)))
  }

  const submittingRef = useRef(false)

  async function handleCustomerLookup() {
    if (!customerPhone.trim()) return
    setLookingUpCustomer(true)
    setCustomerLookupError(null)
    try {
      const customer = await customersApi.getByPhone(customerPhone.trim())
      setAttachedCustomer(customer)
    } catch (err) {
      setAttachedCustomer(null)
      setCustomerLookupError(
        err instanceof ApiError && err.status === 404
          ? 'No customer with that phone number. Sale will be walk-in unless you register them.'
          : 'Could not look up that customer.',
      )
    } finally {
      setLookingUpCustomer(false)
    }
  }

  async function handleCheckout() {
    if (cart.length === 0) return
    if (submittingRef.current) return // synchronous guard against a very fast double-click
    submittingRef.current = true
    setCheckingOut(true)
    setError(null)
    try {
      const sale = await salesApi.create({
        items: cart.map((l) => ({ product_id: l.product.id, quantity: l.quantity })),
        payments: [{ method: paymentMethod, amount: estimatedTotal }],
        discount_amount: discount,
        customer_id: attachedCustomer?.id ?? null,
      })
      setReceipt(sale)
      setCart([])
      setDiscount(0)
      setResults([])
      setQuery('')
      setAttachedCustomer(null)
      setCustomerPhone('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Checkout failed. Nothing was charged.')
    } finally {
      submittingRef.current = false
      setCheckingOut(false)
    }
  }

  if (receipt) {
    return <Receipt sale={receipt} onNewSale={() => setReceipt(null)} />
  }

  return (
    <div className="grid h-screen grid-cols-[1fr_360px]">
      <div className="overflow-y-auto p-6">
        <h1 className="mb-4 font-display text-2xl text-ink">Point of Sale</h1>
        <form onSubmit={(e: FormEvent) => e.preventDefault()} className="mb-4">
          <input
            ref={searchInputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleSearchKeyDown}
            placeholder="Search products"
            className="w-full border border-rule bg-panel px-3 py-2 outline-none focus-visible:border-brass"
            autoFocus
          />
        </form>

        {error && (
          <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
            {error}
          </p>
        )}

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {results.map((product) => (
            <button
              key={product.id}
              onClick={() => addToCart(product)}
              disabled={product.total_qty_available <= 0}
              className="ledger-panel p-3 text-left hover:border-brass disabled:cursor-not-allowed disabled:opacity-40"
            >
              <p className="truncate text-sm font-medium">{product.name}</p>
              <p className="figure mt-1 text-sm text-ink-soft">
                {formatCurrency(product.default_selling_price)}
              </p>
              <p className="text-xs text-ink-soft">{product.total_qty_available} in stock</p>
              {product.margin_percent !== null && (
                <p className="text-xs text-stamp-green">
                  {product.margin_percent.toFixed(0)}% margin
                </p>
              )}
            </button>
          ))}
          {searching && results.length === 0 && (
            <p className="col-span-full text-sm text-ink-soft">Searching…</p>
          )}
          {!searching && query.trim() && results.length === 0 && (
            <p className="col-span-full text-sm text-ink-soft">No products match "{query}".</p>
          )}
          {!searching && !query.trim() && results.length === 0 && (
            <p className="col-span-full text-sm text-ink-soft">
              No products in stock yet — receive some stock to start selling.
            </p>
          )}
        </div>
      </div>

      <aside className="flex flex-col border-l border-rule bg-panel">
        <div className="flex-1 overflow-y-auto p-4">
          <h2 className="mb-3 text-xs uppercase tracking-wide text-ink-soft">Cart</h2>
          {cart.length === 0 && <p className="text-sm text-ink-soft">Nothing added yet.</p>}
          <ul className="space-y-2">
            {cart.map((line) => (
              <li key={line.product.id} className="ruled-row pb-2">
                <div className="flex justify-between text-sm">
                  <span className="truncate pr-2">{line.product.name}</span>
                  <span className="figure">
                    {formatCurrency(line.product.default_selling_price * line.quantity)}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <button
                    aria-label={`Decrease ${line.product.name} quantity`}
                    onClick={() => updateQuantity(line.product.id, line.quantity - 1)}
                    className="h-6 w-6 border border-rule text-sm"
                  >
                    −
                  </button>
                  <span className="figure w-6 text-center text-sm">{line.quantity}</span>
                  <button
                    aria-label={`Increase ${line.product.name} quantity`}
                    onClick={() => updateQuantity(line.product.id, line.quantity + 1)}
                    className="h-6 w-6 border border-rule text-sm"
                  >
                    +
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="border-t border-rule p-4">
          <label className="block text-xs uppercase tracking-wide text-ink-soft">
            Customer (optional)
          </label>
          {attachedCustomer ? (
            <div className="mt-1 flex items-center justify-between border border-rule bg-panel px-2 py-1.5 text-sm">
              <span>{attachedCustomer.name}</span>
              <button
                onClick={() => {
                  setAttachedCustomer(null)
                  setCustomerPhone('')
                }}
                className="text-xs text-stamp-red underline decoration-dotted"
              >
                Remove
              </button>
            </div>
          ) : (
            <div className="mt-1 flex gap-2">
              <input
                value={customerPhone}
                onChange={(e) => {
                  setCustomerPhone(e.target.value)
                  setCustomerLookupError(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    void handleCustomerLookup()
                  }
                }}
                placeholder="Phone number"
                className="flex-1 border border-rule bg-paper px-2 py-1.5 text-sm"
              />
              <button
                onClick={() => void handleCustomerLookup()}
                disabled={lookingUpCustomer || !customerPhone.trim()}
                className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass disabled:opacity-50"
              >
                {lookingUpCustomer ? 'Looking up…' : 'Find'}
              </button>
            </div>
          )}
          {customerLookupError && (
            <p className="mt-1 text-xs text-stamp-red">{customerLookupError}</p>
          )}

          <label className="mt-3 block text-xs uppercase tracking-wide text-ink-soft">
            Discount
            <input
              type="number"
              min={0}
              step={0.01}
              value={discount}
              onChange={(e) => setDiscount(Math.max(0, Number(e.target.value)))}
              className="mt-1 w-full border border-rule bg-paper px-2 py-1 figure"
            />
          </label>

          <label className="mt-3 block text-xs uppercase tracking-wide text-ink-soft">
            Payment method
            <select
              value={paymentMethod}
              onChange={(e) => setPaymentMethod(e.target.value as PaymentMethod)}
              className="mt-1 w-full border border-rule bg-paper px-2 py-1"
            >
              <option value="CASH">Cash</option>
              <option value="CARD">Card</option>
              <option value="MPESA">M-Pesa</option>
            </select>
          </label>

          <div className="mt-4 flex justify-between font-display text-lg">
            <span>Total</span>
            <span className="figure">{formatCurrency(estimatedTotal)}</span>
          </div>
          <p className="mt-1 text-xs text-ink-soft">
            Estimate — final total is confirmed by the server at checkout.
          </p>

          <button
            onClick={() => void handleCheckout()}
            disabled={cart.length === 0 || checkingOut}
            className="mt-3 w-full border border-stamp-green bg-stamp-green py-2 font-medium text-paper disabled:opacity-50"
          >
            {checkingOut ? 'Charging…' : 'Charge & complete sale'}
          </button>
        </div>
      </aside>
    </div>
  )
}

function Receipt({ sale, onNewSale }: { sale: SaleOut; onNewSale: () => void }) {
  const formatCurrency = useCurrencyFormatter()
  const [busy, setBusy] = useState(false)

  async function fetchReceiptBlob(): Promise<Blob> {
    return salesApi.receiptBlob(sale.id)
  }

  async function handlePrintOrPreview() {
    setBusy(true)
    try {
      const blob = await fetchReceiptBlob()
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
    } finally {
      setBusy(false)
    }
  }

  async function handleDownload() {
    setBusy(true)
    try {
      const blob = await fetchReceiptBlob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `Receipt-${sale.id}.pdf`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } finally {
      setBusy(false)
    }
  }

  async function handleShare() {
    setBusy(true)
    try {
      const blob = await fetchReceiptBlob()
      const file = new File([blob], `Receipt-${sale.id}.pdf`, { type: 'application/pdf' })
      if (navigator.share && navigator.canShare?.({ files: [file] })) {
        await navigator.share({ files: [file], title: `Receipt #${sale.id}` })
      } else {
        // No native share support on this device -- download is the
        // fallback so the receipt is still in the person's hands.
        await handleDownload()
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-paper p-6">
      <div className="ledger-panel w-full max-w-sm p-6">
        <p className="text-center text-xs uppercase tracking-wide text-ink-soft">Sale complete</p>
        <p className="figure mt-1 text-center text-3xl text-stamp-green">
          #{sale.id.toString().padStart(6, '0')}
        </p>
        <ul className="mt-4 space-y-1">
          {sale.items.map((item) => (
            <li
              key={`${item.product_id}-${item.batch_id}`}
              className="ruled-row flex justify-between py-1 text-sm"
            >
              <span>
                {item.quantity} × {item.product_name}
              </span>
              <span className="figure">{formatCurrency(item.line_total)}</span>
            </li>
          ))}
        </ul>
        <div className="mt-3 space-y-1 text-sm">
          <div className="flex justify-between">
            <span>Subtotal</span>
            <span className="figure">{formatCurrency(sale.subtotal)}</span>
          </div>
          <div className="flex justify-between">
            <span>Discount</span>
            <span className="figure">−{formatCurrency(sale.discount_amount)}</span>
          </div>
          <div className="flex justify-between font-display text-base text-ink">
            <span>Total</span>
            <span className="figure">{formatCurrency(sale.total_amount)}</span>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-3 gap-2">
          <button
            onClick={() => void handlePrintOrPreview()}
            disabled={busy}
            className="border border-rule py-1.5 text-xs text-ink-soft hover:border-brass disabled:opacity-50"
          >
            Print
          </button>
          <button
            onClick={() => void handleDownload()}
            disabled={busy}
            className="border border-rule py-1.5 text-xs text-ink-soft hover:border-brass disabled:opacity-50"
          >
            Download
          </button>
          <button
            onClick={() => void handleShare()}
            disabled={busy}
            className="border border-rule py-1.5 text-xs text-ink-soft hover:border-brass disabled:opacity-50"
          >
            Share
          </button>
        </div>

        <button
          onClick={onNewSale}
          className="mt-3 w-full border border-ink bg-ink py-2 font-medium text-paper"
        >
          Start next sale
        </button>
      </div>
    </div>
  )
}
