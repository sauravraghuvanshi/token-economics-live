targetScope = 'resourceGroup'

@description('Existing App Service to publish to.')
param webAppName string

@description('Immutable GitHub OIDC subject for refs/heads/main.')
param githubSubject string

resource site 'Microsoft.Web/sites@2024-04-01' existing = {
  name: webAppName
}

resource deploymentIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${webAppName}-github-actions'
  location: resourceGroup().location
}

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = {
  parent: deploymentIdentity
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: githubSubject
    audiences: ['api://AzureADTokenExchange']
  }
}

var websiteContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'de139f84-1756-47ae-9be6-808fbbe84772')

resource publishRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(site.id, deploymentIdentity.id, websiteContributor)
  scope: site
  properties: {
    principalId: deploymentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: websiteContributor
  }
}

output clientId string = deploymentIdentity.properties.clientId
