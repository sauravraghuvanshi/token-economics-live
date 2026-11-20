param name string
param location string
param tags object

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  tags: tags
  properties: {
    allowProjectManagement: true
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: 'token-economics-live'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Token Economics Live'
  }
}

output resourceId string = account.id
output resourceEndpoint string = account.properties.endpoints['AI Foundry API']
output openAIBaseUrl string = '${account.properties.endpoints['Azure OpenAI Legacy API - Latest moniker']}openai/v1/'
