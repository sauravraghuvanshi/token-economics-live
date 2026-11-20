targetScope = 'subscription'

@minLength(3)
@maxLength(30)
param environmentName string
param location string
@allowed(['true', 'false'])
param createFoundry string = 'false'
@allowed(['true', 'false'])
param deployModels string = 'false'
param foundryResourceId string = ''
param azureOpenAIBaseUrl string = ''
param foundryEndpoint string = ''
param foundryUserRoleId string
param cognitiveServicesUserRoleId string = ''
param vaultSecretsUserRoleId string
param monitoringPublisherRoleId string
@secure()
@minLength(16)
param demoAccessCode string
param frontierDeployment string = 'gpt-6.1-sol'
param smallDeployment string = 'gpt-4.1-mini'
param routerDeployment string = 'model-router'
param decisionDeployment string = 'decision-1.1'
param embeddingDeployment string = 'text-embedding-3-small'
param tightKillSwitchUsd string = ''
param naiveSimilarityThreshold string = '0.80'

var useNew = createFoundry == 'true'
var suffix = uniqueString(subscription().id, environmentName, location)
var tags = {
  'azd-env-name': environmentName
  application: 'token-economics-live'
  lifecycle: 'demo'
}

resource demoRg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module newFoundry './modules/foundry.bicep' = if (useNew) {
  name: 'tokecon-foundry'
  scope: demoRg
  params: {
    name: 'ai-tokecon-${suffix}'
    location: location
    tags: tags
  }
}

module monitor './modules/monitoring.bicep' = {
  name: 'tokecon-monitoring'
  scope: demoRg
  params: {
    name: suffix
    location: location
    tags: tags
  }
}

module web './modules/appservice.bicep' = {
  name: 'tokecon-web'
  scope: demoRg
  params: {
    name: suffix
    location: location
    tags: tags
    demoAccessCode: demoAccessCode
    vaultSecretsUserRoleId: vaultSecretsUserRoleId
    monitoringPublisherRoleId: monitoringPublisherRoleId
    insightsName: monitor.outputs.name
    configuration: {
      AZURE_OPENAI_BASE_URL: useNew ? newFoundry!.outputs.openAIBaseUrl : azureOpenAIBaseUrl
      FOUNDRY_ENDPOINT: useNew ? newFoundry!.outputs.resourceEndpoint : foundryEndpoint
      FRONTIER_DEPLOYMENT: frontierDeployment
      SMALL_DEPLOYMENT: smallDeployment
      ROUTER_DEPLOYMENT: routerDeployment
      DECISION_DEPLOYMENT: decisionDeployment
      EMBEDDING_DEPLOYMENT: embeddingDeployment
      TIGHT_KILL_SWITCH_USD: tightKillSwitchUsd
      NAIVE_SIMILARITY_THRESHOLD: naiveSimilarityThreshold
      PROMPT_CACHE_MODE: 'explicit'
      PROMPT_CACHE_KEY: 'clouddesk:policies:v1'
      AZURE_TOKEN_SCOPE: 'https://cognitiveservices.azure.com/.default'
      FOUNDRY_MODE: 'live'
      REPLAY_ONLY: 'false'
      DATA_DIR: '/home/data'
      REPLAY_DIR: '/home/data/replays'
      PRICING_PATH: 'config/pricing.json'
      DEFAULT_REQUEST_COUNT: '5'
      MIN_REQUEST_COUNT: '5'
      MAX_REQUEST_COUNT: '10'
      DECISION_CONFIDENCE_MIN: '0.70'
      RETRY_LIMIT: '2'
      SAFETY_CEILING: '6'
      DOC_TOKEN_BUDGET: '8000'
      MAX_SPEND_PER_HOUR_USD: '2.00'
      LOG_LEVEL: 'INFO'
    }
  }
}

// Scope uses only parameters or the deterministic name because Bicep requires a compile-time scope.
var existingParts = split(empty(foundryResourceId) ? '///unused/unused' : foundryResourceId, '/')
var foundryAccountName = useNew ? 'ai-tokecon-${suffix}' : last(existingParts)
var foundrySubscription = useNew ? subscription().subscriptionId : existingParts[2]
var foundryGroup = useNew ? demoRg.name : existingParts[4]

module models './modules/models.bicep' = if (useNew || deployModels == 'true') {
  name: 'tokecon-models'
  scope: resourceGroup(foundrySubscription, foundryGroup)
  params: {
    foundryAccountName: foundryAccountName
    frontierDeployment: frontierDeployment
    smallDeployment: smallDeployment
    routerDeployment: routerDeployment
    decisionDeployment: decisionDeployment
    embeddingDeployment: embeddingDeployment
  }
  dependsOn: [newFoundry]
}

module roles './modules/foundry-roles.bicep' = {
  name: 'tokecon-foundry-roles'
  scope: resourceGroup(foundrySubscription, foundryGroup)
  params: {
    foundryAccountName: foundryAccountName
    principalId: web.outputs.principalId
    roleIds: empty(cognitiveServicesUserRoleId) ? [foundryUserRoleId] : [
      foundryUserRoleId
      cognitiveServicesUserRoleId
    ]
  }
  dependsOn: [newFoundry]
}

output AZURE_RESOURCE_GROUP string = demoRg.name
output AZURE_APP_SERVICE_NAME string = web.outputs.name
output AZURE_KEY_VAULT_NAME string = web.outputs.vaultName
output AZURE_APPLICATION_INSIGHTS_RESOURCE_ID string = monitor.outputs.id
output AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID string = monitor.outputs.workspaceId
output DEPLOYED_FOUNDRY_RESOURCE_ID string = useNew ? newFoundry!.outputs.resourceId : foundryResourceId
output WEB_URI string = web.outputs.url
