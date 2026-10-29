// Opt-in lab host. Deploy into a dedicated group; never the demo's deployment.
// First deploy createHost=false, upload the private package with Entra, then true.
param storageName string
param hostName string
param hostLocation string
param storageLocation string = resourceGroup().location
param operatorPrincipalId string
param packageBlobName string
param createHost bool = false

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${hostName}-host'
  location: storageLocation
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: storageLocation
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}

// Host storage uses the UAMI. Workflow connectors use the separate system identity.
var hostRoleNames = [
  '17d1049b-9a84-46fb-8f53-869881c3d3ab' // Storage Account Contributor
  'b7e6dc6d-f1e8-4753-8033-0f276bb0955b' // Storage Blob Data Owner
  '974c5e8b-45b9-4653-ba55-5f855dd0fb88' // Storage Queue Data Contributor
  '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3' // Storage Table Data Contributor
]
resource hostRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in hostRoleNames: {
  name: guid(storage.id, identity.id, role)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]

var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource operatorAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, operatorPrincipalId, blobContributor)
  scope: storage
  properties: {
    principalId: operatorPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'packages'
  properties: {
    publicAccess: 'None'
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = if (createHost) {
  name: '${hostName}-plan'
  location: hostLocation
  kind: 'elastic'
  sku: {
    name: 'WS1'
    tier: 'WorkflowStandard'
    size: 'WS1'
    family: 'WS'
    capacity: 1
  }
  properties: {
    reserved: false
  }
}

resource host 'Microsoft.Web/sites@2023-12-01' = if (createHost) {
  name: hostName
  location: hostLocation
  kind: 'functionapp,workflowapp'
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    serverFarmId: plan!.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: true
      use32BitWorkerProcess: false
      functionsRuntimeScaleMonitoringEnabled: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'APP_KIND', value: 'workflowApp' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'node' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~20' }
        { name: 'AzureWebJobsStorage__managedIdentityResourceId', value: identity.id }
        { name: 'AzureWebJobsStorage__credential', value: 'managedIdentity' }
        { name: 'AzureWebJobsStorage__credentialType', value: 'managedIdentity' }
        { name: 'AzureWebJobsStorage__blobServiceUri', value: storage.properties.primaryEndpoints.blob }
        { name: 'AzureWebJobsStorage__queueServiceUri', value: storage.properties.primaryEndpoints.queue }
        { name: 'AzureWebJobsStorage__tableServiceUri', value: storage.properties.primaryEndpoints.table }
        { name: 'WEBSITE_RUN_FROM_PACKAGE', value: '${storage.properties.primaryEndpoints.blob}packages/${packageBlobName}' }
        { name: 'WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID', value: identity.id }
        { name: 'AzureWebJobsSecretStorageType', value: 'files' }
        { name: 'WORKFLOWS_SUBSCRIPTION_ID', value: subscription().subscriptionId }
        { name: 'WORKFLOWS_RESOURCE_GROUP_NAME', value: resourceGroup().name }
        { name: 'WORKFLOWS_LOCATION_NAME', value: hostLocation }
      ]
    }
  }
  dependsOn: [hostRoles]
}

output hostIdentityId string = identity.id
output workflowPrincipalId string = createHost ? host!.identity.principalId : ''
