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

resource network 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: 'vnet-tokecon-${name}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.70.0.0/24'] }
  }
}

resource appSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' = {
  parent: network
  name: 'app-integration'
  properties: {
    addressPrefix: '10.70.0.0/26'
    delegations: [
      {
        name: 'app-service'
        properties: { serviceName: 'Microsoft.Web/serverFarms' }
      }
    ]
  }
}

resource endpointSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' = {
  parent: network
  name: 'private-endpoints'
  properties: {
    addressPrefix: '10.70.0.64/26'
    privateEndpointNetworkPolicies: 'Disabled'
  }
  dependsOn: [appSubnet]
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'plan-tokecon-${name}'
  location: location
  kind: 'linux'
  tags: tags
  sku: { name: 'P0v3', tier: 'PremiumV3', capacity: 1 }
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
    publicNetworkAccess: 'Disabled'
    softDeleteRetentionInDays: 7
  }
}

resource vaultEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-tok-vault-${name}'
  location: location
  tags: tags
  properties: {
    subnet: { id: endpointSubnet.id }
    privateLinkServiceConnections: [
      {
        name: 'key-vault'
        properties: {
          privateLinkServiceId: vault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource vaultDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource vaultDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: vaultDns
  name: 'demo-vnet'
  location: 'global'
  properties: {
    virtualNetwork: { id: network.id }
    registrationEnabled: false
  }
}

resource vaultDnsRecords 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: vaultEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: { privateDnsZoneId: vaultDns.id }
      }
    ]
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
    virtualNetworkSubnetId: appSubnet.id
    vnetRouteAllEnabled: true
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
  dependsOn: [vaultRole, monitorRole, vaultDnsLink, vaultDnsRecords]
}

output url string = 'https://${app.properties.defaultHostName}'
output name string = app.name
output principalId string = app.identity.principalId
output vaultName string = vault.name
