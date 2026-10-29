// Dedicated workflow identity: source/result Blob, layout and embeddings only. No Cosmos access.
param location string
param connectionName string
param workflowPrincipalId string
param tenantId string = tenant().tenantId
param docIntelAccountName string
param openAiAccountName string
param storageAccountName string

resource docIntel 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: docIntelAccountName }
resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: openAiAccountName }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageAccountName }

resource layoutRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(docIntel.id, workflowPrincipalId, 'layout')
  scope: docIntel
  properties: {
    principalId: workflowPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}
resource embeddingRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, workflowPrincipalId, 'embedding')
  scope: openAi
  properties: {
    principalId: workflowPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}
resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workflowPrincipalId, 'source-result')
  scope: storage
  properties: {
    principalId: workflowPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource connection 'Microsoft.Web/connections@2016-06-01' = {
  name: connectionName
  location: location
  kind: 'V2'
  properties: {
    displayName: 'Shared layout managed identity'
    api: { id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'formrecognizer') }
    parameterValueSet: {
      name: 'managedIdentityAuth'
      values: { siteUrl: { value: docIntel.properties.endpoint } }
    }
  }
}
resource access 'Microsoft.Web/connections/accessPolicies@2016-06-01' = {
  parent: connection
  name: workflowPrincipalId
  location: location
  properties: {
    principal: { type: 'ActiveDirectory', identity: { objectId: workflowPrincipalId, tenantId: tenantId } }
  }
}
output connectionId string = connection.id
output managedApiId string = subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'formrecognizer')
