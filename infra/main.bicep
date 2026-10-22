// ---------------------------------------------------------------------------
// main.bicep — one organization's deployment of the File Library Index.
//
// docs/spec.md §3 and docs/build-plan.md Phase 0. Deploy into an empty
// resource group:
//
//   az deployment group create -g <rg> -f infra/main.bicep -p prefix=<prefix> ...
//
// Everything authenticates with Entra ID: local auth (keys, SAS from account
// keys, connection strings) is disabled on every data plane that allows it
// (CLAUDE.md rule 13).
//
// A successful deployment is not proof the vector index works. Run
// scripts/canary.py against the new account before using it (CLAUDE.md
// rule 11, docs/evidence.md E17).
// ---------------------------------------------------------------------------

@description('Short lowercase letters and digits naming this organization and environment, e.g. contosodemo. Starts every resource name.')
@minLength(3)
@maxLength(12)
param prefix string

@description('Region for everything except Azure OpenAI.')
param location string = resourceGroup().location

@description('Region for Azure OpenAI, when the models are not offered in the main region.')
param openAiLocation string = location

@description('Appended to globally unique names. Stable per resource group by default.')
@maxLength(6)
param suffix string = take(uniqueString(resourceGroup().id), 5)

@description('serverless for the demo; provisioned for production (docs/spec.md §11).')
@allowed([
  'serverless'
  'provisioned'
])
param capacityMode string = 'serverless'

@description('Object id of the person who runs scripts/canary.py and the local demo. Empty to skip. Get it with: az ad signed-in-user show --query id -o tsv')
param operatorPrincipalId string = ''

@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param operatorPrincipalType string = 'User'

@description('Embedding deployment SKU. GlobalStandard or Standard, depending on what the region offers.')
param embeddingSku string = 'GlobalStandard'

@description('Embedding capacity in thousands of tokens per minute. Bulk loads are bound by this (docs/build-plan.md risks).')
param embeddingCapacity int = 150

@description('Vision-capable chat model for image descriptions and proposals (spec §3). Check availability with: az cognitiveservices model list -l <region>')
param chatModelName string = 'gpt-5-mini'

param chatModelVersion string = '2025-08-07'

param chatSku string = 'GlobalStandard'

@description('Chat capacity in thousands of tokens per minute.')
param chatCapacity int = 50

param tags object = {
  application: 'file-library-index'
}

var names = {
  cosmos: '${prefix}-cosmos-${suffix}'
  storage: '${prefix}st${suffix}'
  serviceBus: '${prefix}-sb-${suffix}'
  openAi: '${prefix}-aoai-${suffix}'
  docIntel: '${prefix}-di-${suffix}'
  identity: '${prefix}-id'
  logs: '${prefix}-log'
  appInsights: '${prefix}-appi'
}

// The embedding model and dimensions are fixed by cosmos/chunks.container.json
// (3,072 dimensions, permanent: docs/evidence.md E16).
var embeddingDeploymentName = 'text-embedding-3-large'
var embeddingDimensions = 3072
var chatDeploymentName = chatModelName

// --- Identity ---------------------------------------------------------------

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.identity
  location: location
  tags: tags
}

// --- Cosmos DB --------------------------------------------------------------

module cosmosAccount 'modules/cosmos-account.bicep' = {
  name: 'cosmos-account'
  params: {
    accountName: names.cosmos
    location: location
    capacityMode: capacityMode
  }
}

module cosmosContainers 'modules/cosmos-containers.bicep' = {
  name: 'cosmos-containers'
  params: {
    accountName: cosmosAccount.outputs.accountName
    databaseName: cosmosAccount.outputs.databaseName
    capacityMode: capacityMode
  }
}

// --- Blob storage -----------------------------------------------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: names.storage
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    // Entra only. Read links are user-delegation SAS (spec §6.3), which need no account key.
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource filesContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'files'
  properties: {
    publicAccess: 'None'
  }
}

// --- Service Bus ------------------------------------------------------------

// Standard tier: sessions need it (spec §3, §7.1).
resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: names.serviceBus
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
  }
}

resource ingestQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: 'ingest'
  properties: {
    requiresSession: true
    maxDeliveryCount: 5
    deadLetteringOnMessageExpiration: true
  }
}

// --- Azure OpenAI -----------------------------------------------------------

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: names.openAi
  location: openAiLocation
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    // A custom subdomain is required for Entra authentication.
    customSubDomainName: names.openAi
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: embeddingDeploymentName
  sku: {
    name: embeddingSku
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: chatDeploymentName
  sku: {
    name: chatSku
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
  }
  // Azure OpenAI rejects concurrent deployment operations on one account.
  dependsOn: [
    embeddingDeployment
  ]
}

// --- Document Intelligence --------------------------------------------------

resource docIntel 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: names.docIntel
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: names.docIntel
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

// --- Telemetry --------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: names.logs
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: names.appInsights
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// --- Role assignments (spec §3) ---------------------------------------------

module appRoles 'modules/roles.bicep' = {
  name: 'roles-app'
  params: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    cosmosAccountName: cosmosAccount.outputs.accountName
    storageAccountName: storage.name
    serviceBusNamespaceName: serviceBus.name
    openAiAccountName: openAi.name
    docIntelAccountName: docIntel.name
  }
}

module operatorRoles 'modules/roles.bicep' = if (!empty(operatorPrincipalId)) {
  name: 'roles-operator'
  params: {
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
    cosmosAccountName: cosmosAccount.outputs.accountName
    storageAccountName: storage.name
    serviceBusNamespaceName: serviceBus.name
    openAiAccountName: openAi.name
    docIntelAccountName: docIntel.name
  }
}

// --- Outputs, named after the settings in docs/spec.md §10 -------------------

output COSMOS_ENDPOINT string = cosmosAccount.outputs.endpoint
output COSMOS_DATABASE string = cosmosAccount.outputs.databaseName
output BLOB_ACCOUNT_URL string = storage.properties.primaryEndpoints.blob
output BLOB_CONTAINER string = filesContainer.name
output SERVICEBUS_NAMESPACE string = '${serviceBus.name}.servicebus.windows.net'
output SERVICEBUS_QUEUE string = ingestQueue.name
output AOAI_ENDPOINT string = openAi.properties.endpoint
output AOAI_EMBEDDING_DEPLOYMENT string = embeddingDeployment.name
output AOAI_EMBEDDING_DIMENSIONS int = embeddingDimensions
output AOAI_VISION_DEPLOYMENT string = chatDeployment.name
output AOAI_CHAT_DEPLOYMENT string = chatDeployment.name
output DOCINTEL_ENDPOINT string = docIntel.properties.endpoint
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
output AZURE_CLIENT_ID string = identity.properties.clientId
output COSMOS_CONTAINERS array = cosmosContainers.outputs.containerNames
