// Opt-in warm worker only. Existing services/roles remain provisioned by main.bicep.
// Deploy into an isolated environment for measurements; never inherit scaling resources implicitly.
param workerName string
param location string = resourceGroup().location
param managedEnvironmentId string
param managedIdentityId string
param managedIdentityClientId string
@description('Immutable image digest, e.g. registry.example.invalid/fli@sha256:...')
param image string
param registryServer string
param cosmosEndpoint string
param cosmosDatabase string = 'fli'
param blobAccountUrl string
param servicebusNamespace string
param servicebusQueue string = 'ingest'
param aoaiEndpoint string
param embeddingDeployment string
param embeddingModelRevision string
param codeRevision string
param chatDeployment string = ''
param visionDeployment string = ''
param docintelEndpoint string
param workflowEndpoint string = ''
param workflowAudience string = ''
param lanes int = 2
param writeConcurrency int = 2

resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: workerName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${managedIdentityId}': {} }
  }
  properties: {
    managedEnvironmentId: managedEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registryServer, identity: managedIdentityId }]
    }
    template: {
      containers: [{
        name: 'worker'
        image: image
        command: ['fli-worker']
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: [
          { name: 'ENVIRONMENT', value: 'demo' }
          { name: 'AUTH_MODE', value: 'entra' }
          { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
          { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
          { name: 'COSMOS_DATABASE', value: cosmosDatabase }
          { name: 'BLOB_ACCOUNT_URL', value: blobAccountUrl }
          { name: 'SERVICEBUS_NAMESPACE', value: servicebusNamespace }
          { name: 'SERVICEBUS_QUEUE', value: servicebusQueue }
          { name: 'AOAI_ENDPOINT', value: aoaiEndpoint }
          { name: 'AOAI_EMBEDDING_DEPLOYMENT', value: embeddingDeployment }
          { name: 'AOAI_EMBEDDING_MODEL_REVISION', value: embeddingModelRevision }
          { name: 'FLI_CODE_REVISION', value: codeRevision }
          { name: 'AOAI_CHAT_DEPLOYMENT', value: chatDeployment }
          { name: 'AOAI_VISION_DEPLOYMENT', value: visionDeployment }
          { name: 'DOCINTEL_ENDPOINT', value: docintelEndpoint }
          { name: 'MICROSOFT_WORKFLOW_ENDPOINT', value: workflowEndpoint }
          { name: 'MICROSOFT_WORKFLOW_AUDIENCE', value: workflowAudience }
          { name: 'INGEST_MAX_CONCURRENT_SESSIONS', value: string(lanes) }
          { name: 'COSMOS_WRITE_CONCURRENCY', value: string(writeConcurrency) }
        ]
      }]
      // Persistent session receiver; deliberately warm for comparative timings.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}
output workerId string = worker.id
