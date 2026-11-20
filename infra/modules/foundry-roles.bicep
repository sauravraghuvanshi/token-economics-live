param foundryAccountName string
param principalId string
param roleIds array

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource assignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for id in roleIds: {
    scope: foundry
    name: guid(foundry.id, principalId, id)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', id)
      principalId: principalId
      principalType: 'ServicePrincipal'
    }
  }
]
