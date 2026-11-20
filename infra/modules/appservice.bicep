param name string
param location string
param tags object
param configuration object
param insightsName string
param vaultSecretsUserRoleId string
param monitoringPublisherRoleId string
@secure()
param demoAccessCode string

resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: insightsName
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'plan-tokecon-${name}'
  location: location
  kind: 'linux'
  tags: tags
  sku: { name: 'P0v3', tier: 'Premium0V3', capacity: 1 }
  properties: {
    reserved: true
  }
}

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: 'kv-tok-${name}'
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 7
  }
}

resource accessCode 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: vault
  name: 'demo-access-code'
  properties: {
    contentType: 'text/plain'
    value: demoAccessCode
  }
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: 'app-tokecon-${name}'
  location: location
  kind: 'app,linux'
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: true
      healthCheckPath: '/healthz'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      http20Enabled: true
      appCommandLine: 'gunicorn --workers 1 --worker-class uvicorn.workers.UvicornWorker --timeout 600 --bind 0.0.0.0:8000 app.main:app'
    }
  }
}

resource vaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, app.id, vaultSecretsUserRoleId)
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', vaultSecretsUserRoleId)
  }
}

resource monitorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, app.id, monitoringPublisherRoleId)
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringPublisherRoleId)
  }
}

resource settings 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: app
  name: 'appsettings'
  properties: union(configuration, {
    AZURE_TOKEN_CREDENTIALS: 'ManagedIdentityCredential'
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
    ENABLE_ORYX_BUILD: 'true'
    WEBSITES_ENABLE_APP_SERVICE_STORAGE: 'true'
    APPLICATIONINSIGHTS_CONNECTION_STRING: insights.properties.ConnectionString
    DEMO_ACCESS_CODE: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=${accessCode.name})'
  })
  dependsOn: [vaultRole, monitorRole]
}

output url string = 'https://${app.properties.defaultHostName}'
output name string = app.name
output principalId string = app.identity.principalId
output vaultName string = vault.name
