async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || 'Não foi possível concluir a operação.')
  }
  return payload
}

export function mapCompany(url) {
  return request('/companies/map', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  })
}

export function askCompany(bundle, question, history) {
  return request('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      history: history.slice(-6),
      company: {
        profile: bundle.company_profile || '',
        chunks: bundle.chunks || [],
        research_context: bundle.research_context || {}
      }
    })
  })
}
