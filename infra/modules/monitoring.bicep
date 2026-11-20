param name string
param location string
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-tokecon-${name}'
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    sku: { name: 'PerGB2018' }
    workspaceCapping: { dailyQuotaGb: 1 }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-tokecon-${name}'
  kind: 'web'
  location: location
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableLocalAuth: true
  }
}

output id string = insights.id
output name string = insights.name
output workspaceId string = workspace.id
