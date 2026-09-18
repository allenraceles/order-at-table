export type Item = { id: string; name: string; desc: string; price: number; image: string; tag?: string; options?: string[] }
export type Section = { category: string; items: Item[] }
export type MenuData = { categories: string[]; menu: Section[] }

type CategoryRow = { id: string; label: string; display_order: number }
type ItemRow = {
  id: string; category_id: string; name: string; description: string; price: number;
  image_url: string; tag: string | null; options: string[]; display_order: number
}

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || 'https://ftdzbagcesvujvrbaqdl.supabase.co'
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY

async function readRows<T>(table: string, select: string): Promise<T[]> {
  const url = new URL(`/rest/v1/${table}`, supabaseUrl)
  url.searchParams.set('select', select)
  url.searchParams.set('order', 'display_order.asc,id.asc')
  const response = await fetch(url, { headers: { apikey: publishableKey }, cache: 'no-store' })
  if (!response.ok) throw new Error(`Supabase menu request failed (${response.status})`)
  return response.json() as Promise<T[]>
}

export async function loadMenu(): Promise<MenuData> {
  if (!publishableKey) throw new Error('Supabase publishable key is not configured')
  const [categories, items] = await Promise.all([
    readRows<CategoryRow>('order_at_table_menu_categories', 'id,label,display_order'),
    readRows<ItemRow>('order_at_table_menu_items', 'id,category_id,name,description,price,image_url,tag,options,display_order'),
  ])
  const byCategory = new Map(categories.map(category => [category.id, { category: category.label, items: [] as Item[] }]))
  for (const item of items) {
    byCategory.get(item.category_id)?.items.push({
      id: item.id, name: item.name, desc: item.description, price: item.price,
      image: item.image_url || 'https://resizer.otstatic.com/v2/photos/huge/1/79194476.jpg', tag: item.tag || undefined, options: item.options,
    })
  }
  return { categories: ['All', ...categories.map(category => category.label)], menu: [...byCategory.values()] }
}
