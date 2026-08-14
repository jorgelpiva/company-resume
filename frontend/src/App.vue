<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { companyDocument, initDatabase } from './db/rxdb-setup'
import { askCompany, mapCompany } from './services/api'

const companies = ref([])
const routeSlug = ref(readSlug())
const url = ref('')
const status = ref('')
const busy = ref(false)
const databaseReady = ref(false)
const databaseError = ref('')
const question = ref('')
const answer = ref('Pode me perguntar coisas como: Quem é a empresa? O que ela faz? Quais serviços oferece?')
const sources = ref([])
const history = ref([])
let db
let subscription
let databaseInitialization

const defaults = ['Quem é esta empresa?', 'O que ela faz?', 'Quais serviços oferece?', 'Quais diferenciais ela declara?', 'O que devo saber antes de uma entrevista?']
const activeCompany = computed(() => companies.value.find((item) => item.id === routeSlug.value))
const activeBundle = computed(() => {
  try { return JSON.parse(activeCompany.value?.bundle || '{}') } catch { return {} }
})
const suggestions = computed(() => activeBundle.value.metadata?.suggested_questions?.length
  ? activeBundle.value.metadata.suggested_questions
  : defaults)

function readSlug() {
  return decodeURIComponent(window.location.pathname.replace(/^\/+|\/+$/g, ''))
}

function navigate(slug = '') {
  history.value = []
  answer.value = 'Pode me perguntar coisas como: Quem é a empresa? O que ela faz? Quais serviços oferece?'
  sources.value = []
  window.history.pushState({}, '', slug ? `/${encodeURIComponent(slug)}` : '/')
  routeSlug.value = slug
}

async function getCompanyCollection() {
  if (!databaseInitialization) databaseInitialization = initDatabase()
  try {
    db = await databaseInitialization
    const collection = db?.collections?.companies || db?.companies
    if (!collection) throw new Error('A coleção local de empresas não está disponível.')
    databaseReady.value = true
    databaseError.value = ''
    return collection
  } catch (error) {
    databaseInitialization = undefined
    databaseReady.value = false
    databaseError.value = `Não foi possível abrir o armazenamento local: ${error.message}`
    throw new Error(databaseError.value)
  }
}

async function persistBundle(bundle, collection) {
  const companiesCollection = collection || await getCompanyCollection()
  await companiesCollection.incrementalUpsert(companyDocument(bundle))
}

async function mapNewCompany() {
  if (!url.value.trim() || busy.value) return
  busy.value = true
  status.value = 'Mapeando empresa... isso pode levar alguns minutos.'
  try {
    const collection = await getCompanyCollection()
    const bundle = await mapCompany(url.value.trim())
    await persistBundle(bundle, collection)
    status.value = '✓ Empresa pronta e salva somente neste navegador.'
    url.value = ''
  } catch (error) {
    status.value = error.message
  } finally {
    busy.value = false
  }
}

async function refreshCompany(company) {
  if (busy.value) return
  busy.value = true
  status.value = `Atualizando ${company.name}...`
  answer.value = 'Atualizando mapeamento...'
  try {
    const collection = await getCompanyCollection()
    const bundle = await mapCompany(company.sourceUrl)
    await persistBundle(bundle, collection)
    status.value = '✓ Empresa atualizada neste navegador.'
    answer.value = 'Empresa atualizada com sucesso.'
  } catch (error) {
    status.value = error.message
    answer.value = error.message
  } finally {
    busy.value = false
  }
}

async function removeCompany(company) {
  if (!window.confirm(`Excluir ${company.name} deste navegador?`)) return
  try {
    const collection = await getCompanyCollection()
    const document = await collection.findOne(company.id).exec()
    if (document) await document.remove()
    if (routeSlug.value === company.id) navigate()
  } catch (error) {
    status.value = error.message
  }
}

async function ask(suggestedQuestion) {
  const text = (suggestedQuestion || question.value).trim()
  if (!text || !activeCompany.value || busy.value) return
  busy.value = true
  answer.value = 'Consultando o perfil corporativo...'
  sources.value = []
  try {
    const result = await askCompany(activeBundle.value, text, history.value)
    answer.value = result.answer
    sources.value = result.sources || []
    history.value.push({ role: 'user', content: text }, { role: 'assistant', content: result.answer })
    question.value = ''
  } catch (error) {
    answer.value = error.message
  } finally {
    busy.value = false
  }
}

function onPopState() { routeSlug.value = readSlug() }

onMounted(async () => {
  window.addEventListener('popstate', onPopState)
  try {
    const collection = await getCompanyCollection()
    subscription = collection.find().sort({ updatedAt: 'desc' }).$.subscribe({
      next(documents) {
        companies.value = documents.map((document) => document.toJSON())
      },
      error(error) {
        databaseError.value = `Falha ao ler os dados locais: ${error.message}`
      }
    })
  } catch (error) {
    status.value = error.message
  }
})

onBeforeUnmount(() => {
  subscription?.unsubscribe()
  window.removeEventListener('popstate', onPopState)
})
</script>

<template>
  <main class="shell">
    <template v-if="!routeSlug">
      <header class="hero">
        <p class="eyebrow">INTELIGÊNCIA CORPORATIVA LOCAL</p>
        <h1>Company Resume</h1>
        <p>Transforme o conteúdo público de uma empresa em um perfil conversacional.</p>
      </header>

      <section class="panel mapper">
        <h2>Mapear nova empresa</h2>
        <div class="input-row">
          <input v-model="url" type="url" placeholder="https://empresa.com.br" @keyup.enter="mapNewCompany" />
          <button :disabled="busy || !databaseReady" @click="mapNewCompany">{{ busy ? 'MAPEANDO...' : (databaseReady ? 'MAPEAR EMPRESA' : 'PREPARANDO...') }}</button>
        </div>
        <p v-if="status" class="notice">{{ status }}</p>
        <p v-if="databaseError" class="error-notice">{{ databaseError }}</p>
        <p class="privacy">Os resultados ficam no armazenamento local deste navegador e somem quando os dados do site forem limpos.</p>
      </section>

      <section class="panel">
        <h2>Empresas neste navegador</h2>
        <div v-if="companies.length" class="grid">
          <article v-for="company in companies" :key="company.id" class="company-card">
            <h3>{{ company.name }}</h3>
            <p>{{ company.domain }}</p>
            <p>{{ company.pagesProcessed }} páginas processadas</p>
            <p>Atualizado em {{ new Date(company.mappedAt).toLocaleDateString('pt-BR') }}</p>
            <span class="status-pill">ready</span>
            <div class="actions">
              <button @click="navigate(company.id)">ABRIR</button>
              <button class="secondary" :disabled="busy" @click="refreshCompany(company)">Atualizar</button>
              <button class="icon secondary" @click="removeCompany(company)" aria-label="Excluir">🗑</button>
            </div>
          </article>
        </div>
        <p v-else class="empty">Nenhuma empresa mapeada neste navegador.</p>
      </section>
    </template>

    <template v-else-if="activeCompany">
      <header class="detail-header">
        <div><h1>{{ activeCompany.name }}</h1><p>{{ activeCompany.domain }}</p></div>
        <button class="secondary" @click="navigate()">← Voltar</button>
      </header>
      <section class="panel">
        <p>{{ activeCompany.pagesProcessed }} páginas processadas · atualizado em {{ new Date(activeCompany.mappedAt).toLocaleDateString('pt-BR') }}</p>
        <div class="actions">
          <button :disabled="busy" @click="refreshCompany(activeCompany)">Atualizar</button>
          <button class="danger" @click="removeCompany(activeCompany)">Excluir</button>
        </div>
        <div class="chat-card">
          <div class="suggestions">
            <button v-for="item in suggestions" :key="item" :disabled="busy" @click="ask(item)">{{ item }}</button>
          </div>
          <textarea v-model="question" placeholder="Pergunte algo sobre a empresa..." @keyup.ctrl.enter="ask()"></textarea>
          <div class="actions"><button :disabled="busy" @click="ask()">Perguntar</button></div>
          <div class="answer">{{ answer }}</div>
          <div v-if="sources.length" class="sources">
            <strong>Fontes</strong>
            <a v-for="source in sources" :key="source" :href="source" target="_blank" rel="noopener noreferrer">{{ source }}</a>
          </div>
        </div>
      </section>
    </template>

    <section v-else class="panel not-found">
      <h1>Empresa não encontrada neste navegador</h1>
      <p>Ela pode ter sido salva em outro navegador ou os dados locais podem ter sido limpos.</p>
      <button @click="navigate()">Voltar</button>
    </section>
  </main>
</template>
