import { addRxPlugin, createRxDatabase } from 'rxdb'
import { RxDBQueryBuilderPlugin } from 'rxdb/plugins/query-builder'
import { RxDBUpdatePlugin } from 'rxdb/plugins/update'
import { getRxStorageDexie } from 'rxdb/plugins/storage-dexie'

addRxPlugin(RxDBQueryBuilderPlugin)
addRxPlugin(RxDBUpdatePlugin)

const companySchema = {
  title: 'company browser cache',
  version: 0,
  primaryKey: 'id',
  type: 'object',
  properties: {
    id: { type: 'string', maxLength: 140 },
    name: { type: 'string' },
    domain: { type: 'string' },
    sourceUrl: { type: 'string' },
    mappedAt: { type: 'string' },
    pagesProcessed: { type: 'number', minimum: 0 },
    updatedAt: { type: 'number', minimum: 0 },
    bundle: { type: 'string' }
  },
  required: ['id', 'name', 'domain', 'sourceUrl', 'mappedAt', 'pagesProcessed', 'updatedAt', 'bundle'],
  indexes: ['updatedAt']
}

let databasePromise

export function initDatabase() {
  if (!databasePromise) {
    if (!globalThis.indexedDB) {
      return Promise.reject(new Error('O IndexedDB não está disponível neste navegador.'))
    }
    databasePromise = createRxDatabase({
        name: 'companyresumebrowser',
        storage: getRxStorageDexie(),
        multiInstance: true
      })
      .then(async (db) => {
        await db.addCollections({ companies: { schema: companySchema } })
        if (!db.collections.companies) {
          throw new Error('A coleção local de empresas não pôde ser criada.')
        }
        return db
      })
      .catch((error) => {
        databasePromise = undefined
        throw error
      })
  }
  return databasePromise
}

export function companyDocument(bundle) {
  const metadata = bundle.metadata || {}
  return {
    id: metadata.slug,
    name: metadata.name || 'Empresa',
    domain: metadata.domain || '',
    sourceUrl: metadata.source_url || '',
    mappedAt: metadata.mapped_at || new Date().toISOString(),
    pagesProcessed: metadata.pages_processed || 0,
    updatedAt: Date.now(),
    bundle: JSON.stringify(bundle)
  }
}
