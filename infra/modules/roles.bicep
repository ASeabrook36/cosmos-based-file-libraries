// ---------------------------------------------------------------------------
// roles.bicep — the spec §3 role set for one principal.
//
// Called once for the API/worker managed identity, and once for the operator
// who runs scripts/canary.py and the local demo (they run as the operator's
// `az login` identity, so they need the same data-plane access).
// ---------------------------------------------------------------------------

param principalId string

@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param principalType string

param cosmosAccountName string
param storageAccountName string
param serviceBusNamespaceName string
param openAiAccountName string
param docIntelAccountName string

// Built-in role definition ids (stable across tenants).
var roles = {
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  storageBlobDelegator: 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
  serviceBusDataSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  serviceBusDataReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
  cognitiveServicesOpenAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
}

// Cosmos DB Built-in Data Contributor: a data-plane role, assigned on the
// account itself rather than through Azure RBAC.
var cosmosDataContributor = '00000000-0000-0000-0000-000000000002'

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2025-10-15' existing = {
  name: cosmosAccountName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiAccountName
}

resource docIntel 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: docIntelAccountName
}

resource cosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-10-15' = {
  parent: cosmos
  name: guid(cosmos.id, principalId, cosmosDataContributor)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    principalId: principalId
    scope: cosmos.id
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, principalId, roles.storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
    principalId: principalId
    principalType: principalType
  }
}

resource blobDelegator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, principalId, roles.storageBlobDelegator)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDelegator)
    principalId: principalId
    principalType: principalType
  }
}

resource busSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: serviceBus
  name: guid(serviceBus.id, principalId, roles.serviceBusDataSender)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataSender)
    principalId: principalId
    principalType: principalType
  }
}

resource busReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: serviceBus
  name: guid(serviceBus.id, principalId, roles.serviceBusDataReceiver)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataReceiver)
    principalId: principalId
    principalType: principalType
  }
}

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, principalId, roles.cognitiveServicesOpenAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAiUser)
    principalId: principalId
    principalType: principalType
  }
}

resource docIntelUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: docIntel
  name: guid(docIntel.id, principalId, roles.cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
    principalId: principalId
    principalType: principalType
  }
}
