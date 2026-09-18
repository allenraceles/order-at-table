import { useEffect, useMemo, useState, type SyntheticEvent } from 'react'
import { ShoppingBag, Search, UtensilsCrossed, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { loadMenu, type Item, type MenuData } from '@/lib/menu'

type CartLine = { key: string; item: Item; option: string | null; quantity: number; unitPrice: number }
type OrderStatus = 'awaiting_payment' | 'new' | 'preparing' | 'ready' | 'complete' | 'cancelled'
type Order = { number: string; table_number: number; payment_method: 'counter'; status: OrderStatus; total: number; items: { name: string; option: string | null; quantity: number; unit_price: number }[] }
type Panel = 'item' | 'cart' | 'checkout' | 'success' | 'status' | null

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const defaultImage = 'https://resizer.otstatic.com/v2/photos/huge/1/79194476.jpg'
const money = (amount: number) => new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP', maximumFractionDigits: 0 }).format(amount)
const orderMessages: Record<OrderStatus, { title: string; detail: string }> = {
  awaiting_payment: { title: 'Waiting for counter payment', detail: 'Show your order number at the counter so our team can confirm payment.' },
  new: { title: 'Payment confirmed', detail: 'Your order has reached the kitchen.' },
  preparing: { title: 'Preparing your order', detail: 'The kitchen is working on your food.' },
  ready: { title: 'Ready to serve', detail: 'Our team will bring your order to the table.' },
  complete: { title: 'Order served', detail: 'Enjoy your meal.' },
  cancelled: { title: 'Order cancelled', detail: 'Please ask our team if you need help.' },
}
const optionPrice = (option: string | null) => Number(option?.match(/\+₱(\d+)/)?.[1] || 0)
function useDefaultImage(event: SyntheticEvent<HTMLImageElement>) {
  const image = event.currentTarget
  if (image.src === defaultImage) return
  image.src = defaultImage
  image.alt = 'Harissa shakshuka'
}
const paymentMethods = [
  { id: 'gcash', name: 'GCash', copy: 'Coming soon', logo: 'G', className: 'gcash' },
  { id: 'maya', name: 'Maya', copy: 'Coming soon', logo: 'M', className: 'maya' },
  { id: 'qrph', name: 'QR Ph', copy: 'Coming soon', logo: 'QR', className: 'qr' },
  { id: 'counter', name: 'Pay at the counter', copy: 'For discounts or special billing', logo: '₱', className: 'cash' },
] as const

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export default function App() {
  const [menuData, setMenuData] = useState<MenuData | null>(null)
  const [menuError, setMenuError] = useState('')
  const [apiReady, setApiReady] = useState<boolean | null>(null)
  const [category, setCategory] = useState('All')
  const [query, setQuery] = useState('')
  const [cart, setCart] = useState<CartLine[]>([])
  const [selectedItem, setSelectedItem] = useState<Item | null>(null)
  const [selectedOption, setSelectedOption] = useState<string | null>(null)
  const [quantity, setQuantity] = useState(1)
  const [panel, setPanel] = useState<Panel>(null)
  const [order, setOrder] = useState<Order | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')

  useEffect(() => {
    loadMenu().then(setMenuData).catch(cause => setMenuError(cause instanceof Error ? cause.message : 'Could not load menu'))
    api<{ ok: boolean }>('/api/health').then(() => setApiReady(true)).catch(() => setApiReady(false))
    const saved = sessionStorage.getItem('mesa-order-number')
    if (saved) api<Order>(`/api/orders/${saved}`).then(setOrder).catch(() => sessionStorage.removeItem('mesa-order-number'))
  }, [])

  useEffect(() => {
    if (!order) return
    const timer = window.setInterval(() => {
      api<Order>(`/api/orders/${order.number}`).then(setOrder).catch(() => {})
    }, 5000)
    return () => window.clearInterval(timer)
  }, [order?.number])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 2500)
    return () => window.clearTimeout(timer)
  }, [toast])

  const filtered = useMemo(() => (menuData?.menu || []).map(section => ({ ...section, items: section.items.filter(item =>
    (category === 'All' || category === section.category) && `${item.name} ${item.desc}`.toLowerCase().includes(query.toLowerCase()),
  ) })).filter(section => section.items.length), [menuData, category, query])
  const count = cart.reduce((sum, line) => sum + line.quantity, 0)
  const total = cart.reduce((sum, line) => sum + line.unitPrice * line.quantity, 0)
  const orderMessage = order ? orderMessages[order.status] : null
  const paymentComplete = !!order && !['awaiting_payment', 'cancelled'].includes(order.status)
  const preparationStarted = !!order && ['preparing', 'ready', 'complete'].includes(order.status)
  const readyToServe = !!order && ['ready', 'complete'].includes(order.status)

  function openItem(item: Item) {
    setSelectedItem(item)
    setSelectedOption(item.options?.[0] || null)
    setQuantity(1)
    setPanel('item')
  }

  function addItem() {
    if (!selectedItem) return
    const item = selectedItem
    const option = selectedOption
    const key = `${item.id}:${option || ''}`
    setCart(previous => {
      const existing = previous.find(line => line.key === key)
      return existing
        ? previous.map(line => line.key === key ? { ...line, quantity: line.quantity + quantity } : line)
        : [...previous, { key, item, option, quantity, unitPrice: item.price + optionPrice(option) }]
    })
    setPanel(null)
    setToast(`${item.name} added to your order`)
  }

  function openOrder() {
    if (order) { setPanel('status'); return }
    if (!count) { setToast('Your order is empty'); return }
    setPanel('cart')
  }

  async function placeOrder() {
    setSubmitting(true)
    setError('')
    try {
      const placed = await api<Order>('/api/orders', {
        method: 'POST',
        body: JSON.stringify({ table_number: 18, payment_method: 'counter', items: cart.map(line => ({ item_id: line.item.id, option: line.option, quantity: line.quantity })) }),
      })
      setOrder(placed)
      sessionStorage.setItem('mesa-order-number', placed.number)
      setCart([])
      setPanel('success')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not place your order. Please try again.')
      setApiReady(false)
    } finally { setSubmitting(false) }
  }

  return <>
    <div className="top-strip"><strong>Kitchen open</strong> · Estimated preparation time 15–20 min</div>
    <header className="app-header"><div className="header-inner">
      <div className="brand-lockup" aria-label="Mesa and Co. Greenbelt 5"><div className="brand-mark" aria-hidden="true">M</div><div><div className="brand-name">MESA &amp; CO.</div><div className="brand-location">Greenbelt 5 · Makati</div></div></div>
      <div className="header-actions"><div className="table-chip"><span>Table</span>18</div><Button variant="outline" size="icon" className="icon-button" onClick={openOrder} aria-label="Open your order"><ShoppingBag size={18} /></Button></div>
    </div></header>

    <main className="page">
      {order && <div className="order-status-card"><button type="button" onClick={() => setPanel('status')}><div className="status-top"><strong>Order #{order.number}: {orderMessage?.title}</strong><span>View status →</span></div><div className="status-card-copy">{orderMessage?.detail}</div></button></div>}
      <section className="context-row" aria-labelledby="page-title"><div><div className="eyebrow">Dine-in menu</div><h1 id="page-title">Take your time.<br /><em>We’ll bring it.</em></h1><p className="context-copy">Order from your table whenever you’re ready. Prices already include VAT. Need a special discount or billing request? You can pay at the counter.</p></div><div className="open-badge"><span className="open-dot" aria-hidden="true" /> Open until 10:00 PM</div></section>
      <div className="notice" role="status"><div className="notice-icon" aria-hidden="true">✦</div><div><strong>Welcome to table 18</strong><span>We’ll use this table number to deliver your order.</span></div></div>
      {/* {apiReady === false && <div className="api-notice" role="status">Menu preview is available. Start the FastAPI service to place an order.</div>} */}

      <div className="workspace"><section className="menu-column" aria-label="Restaurant menu">
        <div className="menu-toolbar"><div className="search-wrap"><Search size={18} aria-hidden="true" /><input type="search" placeholder="Search the menu" aria-label="Search the menu" value={query} onChange={event => setQuery(event.target.value)} /></div><Button variant="outline" className="filter-button" onClick={() => setToast('Dietary filters are coming next')}>Dietary</Button></div>
        <nav className="category-bar" aria-label="Menu categories">{(menuData?.categories || []).map(name => <button key={name} type="button" className={`category-button ${category === name ? 'active' : ''}`} onClick={() => setCategory(name)} aria-current={category === name ? 'page' : undefined}>{name}</button>)}</nav>
        {menuError ? <div className="menu-empty" role="alert"><UtensilsCrossed size={28} /><p>Could not load the menu. Please try again.</p><Button variant="outline" onClick={() => window.location.reload()}>Retry</Button></div> : !menuData ? <div className="menu-empty" role="status"><p>Loading menu…</p></div> : filtered.length ? filtered.map(section => <section className="menu-section" key={section.category} id={section.category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}><div className="section-heading"><h2>{section.category}</h2><span>{section.items.length} {section.items.length === 1 ? 'item' : 'items'}</span></div><div className="menu-grid">{section.items.map(item => <article className="menu-card" key={item.id}><div className="menu-card-image"><img src={item.image} alt={item.name} loading="lazy" onError={useDefaultImage} />{item.tag && <span className="card-tag">{item.tag}</span>}</div><div className="menu-card-body"><div className="menu-card-title"><h3>{item.name}</h3><span className="menu-card-price">{money(item.price)}</span></div><p>{item.desc}</p><Button className="add-button" onClick={() => openItem(item)}><span>＋</span> Add to order</Button></div></article>)}</div></section>) : <div className="menu-empty"><UtensilsCrossed size={28} /><p>No menu items match that search.</p></div>}
      </section><aside className="order-rail" aria-label="Your order"><div className="order-card">
        {order ? <><div className="order-card-head"><div><h2>Order #{order.number}</h2><span>{orderMessage?.title}</span></div><span className="status-chip">{order.status === 'awaiting_payment' ? 'Pending' : 'Live'}</span></div><div className="order-items"><div className="order-line"><div className="order-qty">✓</div><div><strong>{orderMessage?.title}</strong><small>{orderMessage?.detail}</small></div></div></div><Button className="checkout-button" onClick={() => setPanel('status')}>View order status</Button></> : <><div className="order-card-head"><div><h2>Your order</h2><span>{count ? `${count} ${count === 1 ? 'item' : 'items'} · ` : ''}Table 18</span></div><ShoppingBag size={22} /></div>{count ? <><div className="order-items">{cart.map(line => <div className="order-line" key={line.key}><div className="order-qty">{line.quantity}</div><div><strong>{line.item.name}</strong><small>{line.option || 'Standard preparation'}</small></div><span className="order-line-price">{money(line.unitPrice * line.quantity)}</span></div>)}</div><div className="order-totals"><div className="total-line"><span>Subtotal</span><strong>{money(total)}</strong></div><div className="total-line"><span>VAT included</span><span>Included</span></div><div className="total-line grand"><span>Total</span><strong>{money(total)}</strong></div></div><Button className="checkout-button" onClick={openOrder}>Review and pay <span>→</span></Button></> : <div className="order-empty"><div><div className="plate-icon"><UtensilsCrossed size={25} /></div><p>Your order is empty.<br />Add something delicious to begin.</p></div></div>}</>}
      </div><div className="rail-help">Need help? <button type="button" onClick={() => setToast('A team member will be with you shortly')}>Ask our team</button></div></aside></div>
    </main>

    {(count > 0 || order) && <div className="mobile-cart-bar"><button className="mobile-cart-button" type="button" onClick={openOrder}><span>{order ? `Order #${order.number}` : `${count} ${count === 1 ? 'item' : 'items'} in your order`}</span><span>{order ? 'View status →' : `${money(total)} →`}</span></button></div>}

    <Dialog open={panel !== null} onOpenChange={open => { if (!open && !submitting) setPanel(null) }}><DialogContent aria-describedby="dialog-description">
      <div className="modal-header"><div><DialogTitle>{panel === 'item' ? selectedItem?.name : panel === 'cart' ? 'Review your order' : panel === 'checkout' ? 'Choose how to pay' : panel === 'success' ? 'Order reserved' : `Order #${order?.number}`}</DialogTitle><DialogDescription id="dialog-description">{panel === 'item' ? selectedItem?.tag || 'Made to order' : panel === 'cart' ? 'Table 18 · Prices include VAT' : panel === 'checkout' ? 'Counter payment is available now' : panel === 'success' ? 'Payment is completed at the counter' : orderMessage?.title || 'Order status'}</DialogDescription></div><button className="modal-close" type="button" onClick={() => setPanel(null)} aria-label="Close"><X size={18} /></button></div>
      {panel === 'item' && selectedItem && <div className="modal-body"><div className="item-modal-image"><img src={selectedItem.image} alt={selectedItem.name} onError={useDefaultImage} /></div><div className="item-copy-row"><h3>{selectedItem.name}</h3><strong>{money(selectedItem.price)}</strong></div><p className="item-description">{selectedItem.desc} Ask our team if you need help with ingredients or allergens.</p>{!!selectedItem.options?.length && <div className="option-group"><h4>Choose your preparation <span>Optional</span></h4><div className="option-list">{selectedItem.options.map(option => <button key={option} type="button" className={`option-pill ${selectedOption === option ? 'selected' : ''}`} onClick={() => setSelectedOption(option)} aria-pressed={selectedOption === option}>{option}</button>)}</div></div>}<div className="quantity-row"><span>Quantity</span><div className="stepper"><button type="button" onClick={() => setQuantity(Math.max(1, quantity - 1))} aria-label="Decrease quantity">−</button><strong>{quantity}</strong><button type="button" onClick={() => setQuantity(Math.min(10, quantity + 1))} aria-label="Increase quantity">＋</button></div></div><Button className="modal-primary" onClick={addItem}>Add to order <span>· {money((selectedItem.price + optionPrice(selectedOption)) * quantity)}</span></Button></div>}
      {panel === 'cart' && <div className="modal-body"><div className="cart-modal-list">{cart.map(line => <div className="cart-modal-line" key={line.key}><div><strong>{line.quantity} × {line.item.name}</strong><small>{line.option || 'Standard preparation'}</small></div><b>{money(line.unitPrice * line.quantity)}</b></div>)}</div><div className="summary-box" style={{ marginTop: 18 }}><div className="total-line"><span>Subtotal</span><strong>{money(total)}</strong></div><div className="total-line"><span>VAT</span><span>Included</span></div><div className="total-line grand"><span>Total</span><strong>{money(total)}</strong></div></div><div className="mini-note">Need a senior citizen/PWD discount, service-charge adjustment, or special billing request? Pay at the counter so our team can help.</div><Button className="modal-primary" onClick={() => setPanel('checkout')}>Continue to payment <span>→</span></Button></div>}
      {panel === 'checkout' && <div className="modal-body"><div className="summary-box"><div className="total-line"><span>Table 18</span><strong>{count} {count === 1 ? 'item' : 'items'}</strong></div><div className="total-line grand"><span>Total to pay</span><strong>{money(total)}</strong></div></div><div className="payment-heading">Payment method</div><div className="payment-options">{paymentMethods.map(method => <div key={method.id} className={`payment-option ${method.id === 'counter' ? 'selected' : 'unavailable'}`} aria-disabled={method.id !== 'counter'}><span className={`payment-logo ${method.className}`}>{method.logo}</span><span><strong>{method.name}</strong><small>{method.copy}</small></span><span className="radio" aria-hidden="true" /></div>)}</div><div className="counter-callout"><strong>How counter payment works</strong>Submit your order, then show the order number at the counter. Our team will verify any discount or billing request, collect payment, and send the order to the kitchen.</div>{error && <p className="submit-error" role="alert">{error}</p>}<Button className="modal-primary" disabled={submitting || apiReady !== true} onClick={placeOrder}>{submitting ? 'Placing order…' : 'Place counter order'} <span>→</span></Button>{apiReady === false && <p className="submit-error">Ordering requires FastAPI and a Supabase connection.</p>}</div>}
      {(panel === 'success' || panel === 'status') && order && <div className="modal-body"><div className="success-panel"><div className="success-mark">{paymentComplete ? '✓' : '₱'}</div><h3>{orderMessage?.title}</h3><p>{orderMessage?.detail}</p><div className="order-code">ORDER #{order.number}</div><div className="timeline"><div className="timeline-step active"><div className="timeline-dot">✓</div><div><strong>Order received</strong><span>Table {order.table_number} · {money(order.total)}</span></div></div><div className={`timeline-step ${paymentComplete ? 'active' : ''}`}><div className="timeline-dot">{paymentComplete ? '✓' : '2'}</div><div><strong>Counter payment</strong><span>{paymentComplete ? 'Confirmed' : 'Show your order number to our team'}</span></div></div><div className={`timeline-step ${preparationStarted ? 'active' : ''}`}><div className="timeline-dot">{preparationStarted ? '✓' : '3'}</div><div><strong>Preparing</strong><span>{preparationStarted ? 'Kitchen is working on your order' : 'Starts after payment confirmation'}</span></div></div><div className={`timeline-step ${readyToServe ? 'active' : ''}`}><div className="timeline-dot">{readyToServe ? '✓' : '4'}</div><div><strong>Ready to serve</strong><span>{readyToServe ? 'Our team will bring it to your table' : 'Waiting for the kitchen'}</span></div></div></div><Button className="modal-primary" onClick={() => setPanel(null)}>Done</Button></div></div>}
    </DialogContent></Dialog>
    <div className={`toast ${toast ? 'show' : ''}`} role="status" aria-live="polite">{toast}</div>
  </>
}
