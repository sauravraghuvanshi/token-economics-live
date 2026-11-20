param foundryAccountName string
param frontierDeployment string
param smallDeployment string
param routerDeployment string
param decisionDeployment string
param embeddingDeployment string

resource account 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' existing = {
  name: foundryAccountName
}

var models = [
  { deployment: frontierDeployment, model: 'gpt-6.1-sol', format: 'OpenAI', version: '2026-09-29', capacity: 100 }
  { deployment: smallDeployment, model: 'gpt-4.1-mini', format: 'OpenAI', version: '2025-04-14', capacity: 60 }
  { deployment: routerDeployment, model: 'model-router', format: 'OpenAI', version: '2025-11-18', capacity: 75 }
  { deployment: decisionDeployment, model: 'Microsoft-Decision-1.1', format: 'Microsoft', version: '1', capacity: 30 }
  { deployment: embeddingDeployment, model: 'text-embedding-3-small', format: 'OpenAI', version: '1', capacity: 10 }
]

@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = [
  for item in models: {
    parent: account
    name: item.deployment
    sku: {
      name: 'GlobalStandard'
      capacity: item.capacity
    }
    properties: {
      model: {
        name: item.model
        version: item.version
        format: item.format
      }
      ...(item.model == 'model-router' ? {
        routing: {
          mode: 'balanced'
          models: [
            { name: 'gpt-4.1-mini', version: '2025-04-14', format: 'OpenAI' }
            { name: 'gpt-4.1', version: '2025-04-14', format: 'OpenAI' }
            { name: 'gpt-6.1-sol', version: '2026-09-29', format: 'OpenAI' }
          ]
        }
      } : {})
    }
  }
]
