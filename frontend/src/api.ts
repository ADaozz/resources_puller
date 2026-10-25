export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    const text = await response.text()
    let detail = text || response.statusText
    try {
      const data = JSON.parse(text) as { detail?: unknown }
      if (typeof data.detail === "string") detail = data.detail
    } catch {
      /* 保留原始文本 */
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
